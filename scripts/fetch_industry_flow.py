#!/usr/bin/env python3
"""Rank official TWSE industry categories by 三大法人 (institutional) net-buy flow.

fetch_fundamentals.py only covers ~12 codes (each costs 5 FinMind calls), far
too narrow a sample to aggregate meaningfully across ~30 industry categories.
This script builds its own wider-but-cheap sample instead:

  1. FinMind TaiwanStockInfo (one bulk call) -> official industry_category
     per code. No hand-maintained mapping file.
  2. TWSE STOCK_DAY_ALL (one bulk call, same pattern as screen_universe.py)
     -> close price + turnover for the whole market.
  3. Per industry, take the top N codes by turnover (liquidity proxy) so
     every official category gets some representation.
  4. For that universe (~90-150 codes), one FinMind call each (not five) for
     TaiwanStockInstitutionalInvestorsBuySell, converted from raw shares to
     NT$ notional via the close price already fetched in step 2.

Output is a ranking of industries by 5-day net-buy notional, with a
secondary "% of turnover" intensity measure (raw NT$ ranking alone always
favors mega-cap-heavy industries like 半導體業), and an observation_only
confidence flag when an industry has fewer than 2 sampled stocks (borrowed
convention, see references/industry-flow.md). Degrades to an empty
industries list + errors on partial failure rather than crashing, so a bad
day on this step never blocks the rest of the report pipeline.

Usage:
  python scripts/fetch_industry_flow.py --per-industry 3 --min-turnover 10000000 --output industry_flow.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

COMMON = re.compile(r"^[1-9]\d{3}$")
QUOTES = [
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=csv",
]
BLOCK = ("SECURITY REASONS", "無法呈現", "Just a moment")

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
INVESTOR_LABELS = {
    "Foreign_Investor": "外資",
    "Foreign_Dealer_Self": "外資自營",
    "Investment_Trust": "投信",
    "Dealer_self": "自營商(自行買賣)",
    "Dealer_Hedging": "自營商(避險)",
}
# non-operating-company categories that would otherwise pollute the ranking
EXCLUDE_CATEGORIES = {"ETF", "ETN", "受益證券", "存託憑證", "指數投資證券", "認購權證", "認售權證"}


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


def _get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 taiwan-stock-daily-report/2.1"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30, context=_SSL) as r:
                body = r.read().decode("utf-8-sig", "replace")
            if body.lstrip()[:1] == "<" or any(b in body[:600] for b in BLOCK):
                raise RuntimeError("blocked")
            return body
        except Exception:
            if attempt == 2:
                raise
    return ""


def _num(v: Any) -> float | None:
    try:
        return None if v in (None, "", "-", "--") else float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _finmind_bulk(dataset: str, retries: int = 3) -> list[dict[str, Any]]:
    url = f"{FINMIND_BASE}?dataset={dataset}"
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


def _finmind_series(dataset: str, data_id: str, start: str, retries: int = 3) -> list[dict[str, Any]]:
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
            time.sleep(2.0 * (attempt + 1))
    raise last if last else RuntimeError("finmind failed")


def _industry_map() -> dict[str, str]:
    rows = _finmind_bulk("TaiwanStockInfo")
    out: dict[str, str] = {}
    for r in rows:
        code = str(r.get("stock_id", "")).strip()
        cat = (r.get("industry_category") or "").strip()
        if not COMMON.fullmatch(code):
            continue
        if not cat or cat in EXCLUDE_CATEGORIES:
            continue
        out[code] = cat
    return out


def _quotes() -> list[dict]:
    for u in QUOTES:
        try:
            raw = _get(u)
            if raw.lstrip()[:1] == "[":
                data = json.loads(raw)
                out = []
                for r in data:
                    out.append({
                        "code": str(r.get("Code", "")).strip(),
                        "name": r.get("Name"),
                        "close": _num(r.get("ClosingPrice")),
                        "turnover": _num(r.get("TradeValue")),
                    })
                return out
            rows = list(csv.DictReader(io.StringIO(raw)))
            return [{
                "code": str(r.get("證券代號", "")).strip(),
                "name": r.get("證券名稱"),
                "close": _num(r.get("收盤價")),
                "turnover": _num(r.get("成交金額")),
            } for r in rows]
        except Exception:
            continue
    return []


def _build_universe(
    per_industry: int, min_turnover: float, industry_map: dict[str, str], quotes: list[dict]
) -> tuple[list[dict], list[str]]:
    by_industry: dict[str, list[dict]] = {}
    for q in quotes:
        code = q["code"]
        industry = industry_map.get(code)
        if not industry:
            continue
        turnover = q.get("turnover") or 0
        close = q.get("close")
        if turnover < min_turnover or close is None:
            continue
        by_industry.setdefault(industry, []).append({
            "code": code, "name": q.get("name"), "industry": industry,
            "close": close, "turnover": turnover,
        })

    universe: list[dict] = []
    for members in by_industry.values():
        universe.extend(sorted(members, key=lambda x: x["turnover"], reverse=True)[:per_industry])
    return universe, sorted(by_industry.keys())


def _institutional_one(code: str, start: str) -> dict[str, Any]:
    try:
        rows = _finmind_series("TaiwanStockInstitutionalInvestorsBuySell", code, start)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
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
        "foreign_net_5d": net_over(5, foreign),
        "trust_net_5d": net_over(5, trust),
        "dealer_net_5d": net_over(5, dealer),
        "three_investors_net_5d": net_over(5, foreign + trust + dealer),
        "three_investors_net_20d": net_over(20, foreign + trust + dealer),
    }


def _fetch_institutional(codes: list[str], start: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_institutional_one, c, start): c for c in codes}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                result[c] = fut.result()
            except Exception as exc:
                result[c] = {"available": False, "reason": str(exc)}
    return result


def _aggregate(universe: list[dict], institutional: dict[str, dict]) -> list[dict]:
    by_industry: dict[str, list[dict]] = {}
    for member in universe:
        by_industry.setdefault(member["industry"], []).append(member)

    industries: list[dict] = []
    for industry, members in by_industry.items():
        # latest single trading day's turnover (STOCK_DAY_ALL has no history) -
        # used as a liquidity-scale reference for the 5d cumulative flow below,
        # not a literal "share of 5-day trading value"
        latest_day_turnover_sum = sum(m["turnover"] or 0 for m in members)
        stock_rows = []
        for m in members:
            inst = institutional.get(m["code"], {})
            if not inst.get("available"):
                continue
            value_5d = (inst.get("three_investors_net_5d") or 0) * m["close"]
            value_20d = (inst.get("three_investors_net_20d") or 0) * m["close"]
            stock_rows.append({
                "code": m["code"], "name": m["name"],
                "net_buy_value_5d_twd": int(value_5d),
                "net_buy_value_20d_twd": int(value_20d),
            })
        stock_count = len(stock_rows)
        net_5d_total = sum(r["net_buy_value_5d_twd"] for r in stock_rows)
        net_20d_total = sum(r["net_buy_value_20d_twd"] for r in stock_rows)
        positive = sum(1 for r in stock_rows if r["net_buy_value_5d_twd"] > 0)
        industries.append({
            "industry": industry,
            "stock_count": stock_count,
            "net_buy_value_5d_twd": net_5d_total,
            "net_buy_value_20d_twd": net_20d_total,
            "net_buy_5d_vs_daily_turnover_pct": round(net_5d_total / latest_day_turnover_sum * 100, 2)
            if latest_day_turnover_sum else None,
            "positive_ratio": round(positive / stock_count, 2) if stock_count else None,
            "confidence": "observation_only" if stock_count < 2 else "normal",
            "top_stocks": sorted(stock_rows, key=lambda r: r["net_buy_value_5d_twd"], reverse=True)[:3],
        })
    industries.sort(key=lambda x: x["net_buy_value_5d_twd"], reverse=True)
    return industries


def build(per_industry: int, min_turnover: float) -> dict[str, Any]:
    now = _now_tp()
    errors: list[str] = []
    industry_map: dict[str, str] = {}
    quotes: list[dict] = []
    universe: list[dict] = []
    industry_names: list[str] = []
    industries: list[dict] = []

    try:
        industry_map = _industry_map()
        if not industry_map:
            errors.append("industry_map empty (TaiwanStockInfo fetch failed or no usable rows)")
    except Exception as exc:
        errors.append(f"industry_map: {exc}")

    try:
        quotes = _quotes()
        if not quotes:
            errors.append("quotes empty (TWSE STOCK_DAY_ALL fetch failed)")
    except Exception as exc:
        errors.append(f"quotes: {exc}")

    if industry_map and quotes:
        universe, industry_names = _build_universe(per_industry, min_turnover, industry_map, quotes)
        if universe:
            start = (now.date() - timedelta(days=60)).strftime("%Y-%m-%d")
            codes = sorted({m["code"] for m in universe})
            institutional = _fetch_institutional(codes, start)
            try:
                industries = _aggregate(universe, institutional)
            except Exception as exc:
                errors.append(f"aggregate: {exc}")
        else:
            errors.append("universe empty after industry/turnover filters")

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "source": "FinMind TaiwanStockInfo + TaiwanStockInstitutionalInvestorsBuySell; TWSE STOCK_DAY_ALL",
        "notional_method": "net_shares(5d/20d) x latest close (approximation, not day-by-day priced)",
        "token_used": bool(_TOKEN),
        "universe": {
            "per_industry": per_industry,
            "min_turnover_twd": min_turnover,
            "stock_count": len(universe),
            "industry_count": len(industry_names),
        },
        "industries": industries,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-industry", type=int, default=3)
    parser.add_argument("--min-turnover", type=float, default=10_000_000)
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        data = build(args.per_industry, args.min_turnover)
    except Exception as exc:
        data = {
            "schema_version": 1, "generated_at": _now_tp().isoformat(timespec="seconds"),
            "timezone": "Asia/Taipei", "industries": [], "errors": [f"fatal: {exc}"],
        }

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
