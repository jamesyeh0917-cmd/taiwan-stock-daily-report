#!/usr/bin/env python3
"""Stage-1 whole-market screen: cut ~1000 listed stocks down to a shortlist.

Two cheap all-market calls (TWSE BWIBBU_ALL for PER/PBR/yield, STOCK_DAY_ALL
for price/change/volume), then coarse filters:
  * four-digit common-stock proxy (code 1000-9999)
  * single-day turnover >= NT$50m (thin-liquidity cut)
  * not limit-down / not obviously distressed
  * PER present and 0 < PER <= 60  (drop loss-makers & extreme multiples)
  * ranked by a simple blended score (turnover rank + |day change| + a
    mild value tilt) so the shortlist has both movers and reasonably priced.

Output: a shortlist of codes with the raw fields, for fetch_fundamentals.py
to run stage-2 deep data on. This is a NET to catch candidates outside the
fixed core watchlist — the report still starts from themes, not this list.

Usage:
  python scripts/screen_universe.py --top 30 --core 2330,2317 --output shortlist.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

COMMON = re.compile(r"^[1-9]\d{3}$")
BWIBBU = [
    "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
    "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json&date={d8}&selectType=ALL",
]
QUOTES = [
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=csv",
]
MIN_TURNOVER = 50_000_000
BLOCK = ("SECURITY REASONS", "無法呈現", "Just a moment")


def _now() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Taipei"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8)))


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    s = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if s:
        c.verify_flags &= ~s
    return c


def _get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 taiwan-stock-daily-report/2.1"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30, context=_ctx()) as r:
                body = r.read().decode("utf-8-sig", "replace")
            if body.lstrip()[:1] == "<" or any(b in body[:600] for b in BLOCK):
                raise RuntimeError("blocked")
            return body
        except Exception:
            if attempt == 2:
                raise
    return ""


def _num(v):
    try:
        return None if v in (None, "", "-", "--") else float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _valuation() -> dict[str, dict]:
    d8 = _recent_weekday()
    for u in BWIBBU:
        try:
            raw = _get(u.format(d8=d8))
            data = json.loads(raw)
            if isinstance(data, list):
                return {str(r["Code"]).strip(): {
                    "per": _num(r.get("PEratio")), "pbr": _num(r.get("PBratio")),
                    "yield": _num(r.get("DividendYield"))} for r in data if r.get("Code")}
            if isinstance(data, dict) and data.get("fields"):
                rows = [dict(zip(data["fields"], x)) for x in data["data"]]
                return {str(r["證券代號"]).strip(): {
                    "per": _num(r.get("本益比")), "pbr": _num(r.get("股價淨值比")),
                    "yield": _num(r.get("殖利率(%)"))} for r in rows if r.get("證券代號")}
        except Exception:
            continue
    return {}


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
                        "change": _num(r.get("Change")),
                        "turnover": _num(r.get("TradeValue")),
                        "date": r.get("Date"),
                    })
                return out
            rows = list(csv.DictReader(io.StringIO(raw)))
            return [{
                "code": str(r.get("證券代號", "")).strip(),
                "name": r.get("證券名稱"),
                "close": _num(r.get("收盤價")),
                "change": _num(r.get("漲跌價差")),
                "turnover": _num(r.get("成交金額")),
                "date": r.get("日期"),
            } for r in rows]
        except Exception:
            continue
    return []


def _recent_weekday() -> str:
    d = _now().date()
    if _now().hour < 15:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def screen(top: int, core: list[str]) -> dict:
    val = _valuation()
    quotes = _quotes()
    rows = [q for q in quotes if COMMON.fullmatch(q["code"])]
    n_total = len(rows)

    passed = []
    for q in rows:
        v = val.get(q["code"], {})
        per = v.get("per")
        to = q.get("turnover") or 0
        chg_pct = None
        if q.get("close") is not None and q.get("change") is not None:
            prev = q["close"] - q["change"]
            chg_pct = (q["change"] / prev * 100) if prev else None
        if to < MIN_TURNOVER:
            continue
        if per is None or per <= 0 or per > 60:
            continue
        if chg_pct is not None and chg_pct <= -9.8:  # limit-down
            continue
        q2 = {**q, **v, "change_pct": round(chg_pct, 2) if chg_pct is not None else None}
        passed.append(q2)

    # blended rank: turnover (desc) + |day move| + mild low-PER tilt
    to_sorted = sorted(passed, key=lambda x: x["turnover"], reverse=True)
    to_rank = {x["code"]: i for i, x in enumerate(to_sorted)}
    for x in passed:
        move = abs(x.get("change_pct") or 0)
        per = x.get("per") or 40
        x["_score"] = (
            (len(passed) - to_rank[x["code"]]) / max(len(passed), 1) * 60
            + min(move, 10) * 3
            + max(0, (30 - per)) / 30 * 10
        )
    ranked = sorted(passed, key=lambda x: x["_score"], reverse=True)
    shortlist = ranked[:top]
    codes = [x["code"] for x in shortlist]
    for c in core:
        if c and c not in codes:
            hit = next((x for x in passed if x["code"] == c), None)
            shortlist.append(hit or {"code": c, "name": None, "note": "core (未通過粗篩或無資料)"})
            codes.append(c)

    return {
        "schema_version": 1,
        "generated_at": _now().isoformat(timespec="seconds"),
        "as_of_date": shortlist[0].get("date") if shortlist else None,
        "universe_scanned": n_total,
        "passed_coarse_filters": len(passed),
        "shortlist_size": len(shortlist),
        "filters": {
            "min_turnover_twd": MIN_TURNOVER,
            "per_range": "0 < PER <= 60",
            "exclude": "limit-down, non-common-stock, PER missing",
        },
        "codes": codes,
        "shortlist": [{k: v for k, v in x.items() if not k.startswith("_")} for x in shortlist],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--core", default="", help="always-include codes, comma-separated")
    ap.add_argument("--output", default="-")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    core = [c.strip() for c in args.core.split(",") if c.strip()]
    try:
        result = screen(args.top, core)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if result["shortlist"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
