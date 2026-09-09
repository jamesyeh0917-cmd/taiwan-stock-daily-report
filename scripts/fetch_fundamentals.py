#!/usr/bin/env python3
"""Fetch per-stock fundamentals & chip-side data for the watchlist via FinMind.

Fills the gaps the price snapshot cannot:
  * 1-year PER / PBR / dividend-yield history  -> percentile of the latest value
  * 三大法人買賣超 (foreign / trust / dealer)   -> net over 1 / 5 / 20 sessions
  * 融資融券餘額                                 -> balance & 5/20-session change
  * 月營收 YoY / MoM                             -> latest month + 3-month trend
  * 股利                                         -> latest cash / stock dividend, ex-date

FinMind free tier is ~300 requests/hour without a token; a free token
(FINMIND_TOKEN env var) raises the ceiling. Keep the watchlist to ~10 codes.

Output: JSON keyed by security code. Every field degrades to null with a
reason string on failure — the report treats missing data as N/A.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
INVESTOR_LABELS = {
    "Foreign_Investor": "外資",
    "Foreign_Dealer_Self": "外資自營",
    "Investment_Trust": "投信",
    "Dealer_self": "自營商(自行買賣)",
    "Dealer_Hedging": "自營商(避險)",
}


def _load_local_env() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_local_env()


def _now_tp() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Taipei"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8)))


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        c.verify_flags &= ~strict
    return c


_SSL = _ctx()
_TOKEN = os.environ.get("FINMIND_TOKEN", "")


def _finmind(dataset: str, data_id: str, start: str, retries: int = 3) -> list[dict[str, Any]]:
    url = f"{FINMIND_BASE}?dataset={dataset}&data_id={data_id}&start_date={start}"
    if _TOKEN:
        url += f"&token={_TOKEN}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "taiwan-stock-daily-report/2.1"})
            with urlopen(req, timeout=30, context=_SSL) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            msg = payload.get("msg")
            if msg not in (None, "success"):
                raise RuntimeError(f"{dataset}: {msg}")
            return payload.get("data", [])
        except Exception as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))  # FinMind 402 = rate limit
    raise last if last else RuntimeError("finmind failed")


def _num(v: Any) -> float | None:
    try:
        return None if v in (None, "", "-") else float(v)
    except (TypeError, ValueError):
        return None


def _percentile(value: float, sample: list[float]) -> int | None:
    xs = sorted(x for x in sample if x is not None)
    if len(xs) < 20 or value is None:
        return None
    below = sum(1 for x in xs if x <= value)
    return round(below / len(xs) * 100)


def _valuation_history(code: str, start: str) -> dict[str, Any]:
    rows = _finmind("TaiwanStockPER", code, start)
    if not rows:
        return {"available": False, "reason": "no PER rows"}
    per = [_num(r.get("PER")) for r in rows]
    pbr = [_num(r.get("PBR")) for r in rows]
    yld = [_num(r.get("dividend_yield")) for r in rows]
    latest = rows[-1]
    lp, lb, ly = _num(latest.get("PER")), _num(latest.get("PBR")), _num(latest.get("dividend_yield"))
    return {
        "available": True,
        "as_of": latest.get("date"),
        "window_days": len(rows),
        "per": lp, "pbr": lb, "dividend_yield_pct": ly,
        "per_1y_percentile": _percentile(lp, per),
        "pbr_1y_percentile": _percentile(lb, pbr),
        "per_1y_low": min((x for x in per if x is not None), default=None),
        "per_1y_high": max((x for x in per if x is not None), default=None),
        "per_1y_median": round(median([x for x in per if x is not None]), 2) if any(per) else None,
    }


def _institutional(code: str, start: str) -> dict[str, Any]:
    rows = _finmind("TaiwanStockInstitutionalInvestorsBuySell", code, start)
    if not rows:
        return {"available": False, "reason": "no institutional rows"}
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
        d = r.get("date")
        label = INVESTOR_LABELS.get(r.get("name", ""), r.get("name", ""))
        net = (_num(r.get("buy")) or 0) - (_num(r.get("sell")) or 0)
        by_date.setdefault(d, {}).setdefault(label, 0.0)
        by_date[d][label] += net
    dates = sorted(by_date)

    def net_over(days: int, groups: tuple[str, ...]) -> int:
        total = 0.0
        for d in dates[-days:]:
            for g in groups:
                total += by_date[d].get(g, 0.0)
        return int(total)

    foreign = ("外資", "外資自營")
    trust = ("投信",)
    dealer = ("自營商(自行買賣)", "自營商(避險)")
    return {
        "available": True,
        "as_of": dates[-1],
        "unit": "shares (net buy positive)",
        "foreign_net_1d": net_over(1, foreign),
        "foreign_net_5d": net_over(5, foreign),
        "foreign_net_20d": net_over(20, foreign),
        "trust_net_5d": net_over(5, trust),
        "trust_net_20d": net_over(20, trust),
        "dealer_net_5d": net_over(5, dealer),
        "three_investors_net_5d": net_over(5, foreign + trust + dealer),
        "three_investors_net_20d": net_over(20, foreign + trust + dealer),
    }


def _margin(code: str, start: str) -> dict[str, Any]:
    rows = _finmind("TaiwanStockMarginPurchaseShortSale", code, start)
    rows = [r for r in rows if r.get("date")]
    if not rows:
        return {"available": False, "reason": "no margin rows"}
    rows.sort(key=lambda r: r["date"])
    latest = rows[-1]
    mb = _num(latest.get("MarginPurchaseTodayBalance"))
    sb = _num(latest.get("ShortSaleTodayBalance"))

    def bal(idx_from_end: int, key: str) -> float | None:
        return _num(rows[-idx_from_end].get(key)) if len(rows) >= idx_from_end else None

    return {
        "available": True,
        "as_of": latest.get("date"),
        "unit": "thousand shares (張, per TWSE convention)",
        "margin_balance": mb,
        "margin_change_5d": None if mb is None or bal(6, "MarginPurchaseTodayBalance") is None
        else int(mb - bal(6, "MarginPurchaseTodayBalance")),
        "margin_change_20d": None if mb is None or bal(21, "MarginPurchaseTodayBalance") is None
        else int(mb - bal(21, "MarginPurchaseTodayBalance")),
        "short_balance": sb,
        "short_to_margin_pct": None if not mb or sb is None else round(sb / mb * 100, 2),
    }


def _month_revenue(code: str, start: str) -> dict[str, Any]:
    rows = _finmind("TaiwanStockMonthRevenue", code, start)
    if not rows:
        return {"available": False, "reason": "no revenue rows"}
    keyed = {(r["revenue_year"], r["revenue_month"]): _num(r.get("revenue")) for r in rows}
    ordered = sorted(keyed)
    (ly, lm), latest_rev = ordered[-1], keyed[ordered[-1]]
    prev_rev = keyed.get(ordered[-2]) if len(ordered) >= 2 else None
    yoy_rev = keyed.get((ly - 1, lm))

    def pct(a: float | None, b: float | None) -> float | None:
        return None if not a or not b else round((a / b - 1) * 100, 1)

    last3 = [keyed[k] for k in ordered[-3:] if keyed[k]]
    last3_yoy = [
        pct(keyed[k], keyed.get((k[0] - 1, k[1]))) for k in ordered[-3:]
    ]
    last3_yoy = [x for x in last3_yoy if x is not None]
    return {
        "available": True,
        "latest_month": f"{ly}-{lm:02d}",
        "revenue_twd": int(latest_rev) if latest_rev else None,
        "yoy_pct": pct(latest_rev, yoy_rev),
        "mom_pct": pct(latest_rev, prev_rev),
        "trailing_3m_avg_yoy_pct": round(sum(last3_yoy) / len(last3_yoy), 1) if last3_yoy else None,
    }


def _dividend(code: str, start: str) -> dict[str, Any]:
    rows = _finmind("TaiwanStockDividend", code, start)
    rows = [r for r in rows if r.get("CashExDividendTradingDate") or r.get("StockExDividendTradingDate")]
    if not rows:
        return {"available": False, "reason": "no dividend rows"}
    latest = rows[-1]
    cash = (_num(latest.get("CashEarningsDistribution")) or 0) + (_num(latest.get("CashStatutorySurplus")) or 0)
    stock = (_num(latest.get("StockEarningsDistribution")) or 0) + (_num(latest.get("StockStatutorySurplus")) or 0)
    return {
        "available": True,
        "period": latest.get("year"),
        "cash_dividend": round(cash, 4),
        "stock_dividend": round(stock, 4),
        "cash_ex_date": latest.get("CashExDividendTradingDate") or None,
        "announced": latest.get("AnnouncementDate") or None,
    }


def _one_code(code: str) -> dict[str, Any]:
    now = _now_tp().date()
    start_1y = (now - timedelta(days=400)).strftime("%Y-%m-%d")
    start_60d = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    start_18m = (now - timedelta(days=560)).strftime("%Y-%m-%d")
    start_2y = (now - timedelta(days=760)).strftime("%Y-%m-%d")
    out: dict[str, Any] = {"code": code}
    for key, fn, start in [
        ("valuation_history", _valuation_history, start_1y),
        ("institutional", _institutional, start_60d),
        ("margin_short", _margin, start_60d),
        ("month_revenue", _month_revenue, start_18m),
        ("dividend", _dividend, start_2y),
    ]:
        try:
            out[key] = fn(code, start)
        except Exception as exc:
            out[key] = {"available": False, "reason": str(exc)}
    return out


def build(watchlist: list[str]) -> dict[str, Any]:
    now = _now_tp()
    codes = [c.strip() for c in watchlist if c.strip().isdigit()][:12]
    result: dict[str, Any] = {}
    # small pool: FinMind rate-limits; each code is 5 calls
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_one_code, c): c for c in codes}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                result[c] = fut.result()
            except Exception as exc:
                result[c] = {"code": c, "error": str(exc)}
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "source": "FinMind (api.finmindtrade.com)",
        "token_used": bool(_TOKEN),
        "codes": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", default="", help="comma-separated security codes")
    parser.add_argument("--output", default="-")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    watchlist = [x for x in args.watchlist.split(",") if x.strip()]
    if not watchlist:
        print(json.dumps({"ok": False, "error": "no --watchlist"}), file=sys.stderr)
        return 2
    try:
        data = build(watchlist)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    text = json.dumps(data, ensure_ascii=False, indent=None if args.compact else 2)
    if args.output == "-":
        print(text)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
