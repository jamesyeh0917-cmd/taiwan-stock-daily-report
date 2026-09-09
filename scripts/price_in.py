#!/usr/bin/env python3
"""Simple event study: has a stock already priced in a piece of news?

Given a security code (or the index) and an event date, pull the daily
close series around it from FinMind and report:

  * pre-event drift   (t-5 .. t-1 cumulative return)
  * event-day return  (t0)
  * post-event return (t+1 .. t+5, as far as data allows)
  * abnormal return   (stock return minus TAIEX return, same window)
  * a 0-100 "priced_in" heuristic

This is a coarse, mechanical read — not a factor model. It answers
"did the move already happen?" so the report does not treat a stale
catalyst as fresh upside.

Usage:
  python scripts/price_in.py --code 2454 --event-date 2026-09-01
  python scripts/price_in.py --code 2454 --event-date 2026-09-01 --post-days 10
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"


def _load_local_env() -> None:
    p = Path(__file__).with_name(".env")
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_local_env()
_TOKEN = os.environ.get("FINMIND_TOKEN", "")


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        c.verify_flags &= ~strict
    return c


def _prices(code: str, start: str, end: str) -> list[tuple[str, float]]:
    url = f"{FINMIND_BASE}?dataset=TaiwanStockPrice&data_id={code}&start_date={start}&end_date={end}"
    if _TOKEN:
        url += f"&token={_TOKEN}"
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "taiwan-stock-daily-report/2.1"})
            with urlopen(req, timeout=30, context=_ctx()) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            if payload.get("msg") not in (None, "success"):
                raise RuntimeError(payload.get("msg"))
            rows = [(r["date"], float(r["close"])) for r in payload.get("data", []) if r.get("close")]
            return sorted(rows)
        except Exception as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise last if last else RuntimeError("finmind failed")


def _cum_return(series: list[float]) -> float | None:
    if len(series) < 2 or series[0] == 0:
        return None
    return round((series[-1] / series[0] - 1.0) * 100.0, 2)


def study(code: str, event_date: str, pre_days: int, post_days: int) -> dict:
    ev = date.fromisoformat(event_date)
    start = (ev - timedelta(days=pre_days * 2 + 15)).isoformat()
    end = (ev + timedelta(days=post_days * 2 + 15)).isoformat()
    stock = _prices(code, start, end)
    index = _prices("TAIEX", start, end)
    if not stock:
        return {"available": False, "reason": f"no price data for {code}"}

    dates = [d for d, _ in stock]
    # locate the first trading day on/after the event
    t0 = next((i for i, d in enumerate(dates) if d >= event_date), None)
    if t0 is None or t0 == 0:
        return {"available": False, "reason": "event date outside available price window"}
    closes = [c for _, c in stock]
    idx = {d: c for d, c in index}
    idx_closes = [idx.get(d) for d in dates]

    def window(a: int, b: int, src: list) -> list:
        return [x for x in src[max(a, 0):b] if x is not None]

    pre = _cum_return(window(t0 - pre_days - 1, t0, closes))          # t-5 .. t-1 (close before event to close t-1)
    event_ret = None
    if closes[t0 - 1]:
        event_ret = round((closes[t0] / closes[t0 - 1] - 1.0) * 100.0, 2)
    post_end = min(t0 + post_days, len(closes) - 1)
    post = _cum_return([closes[t0]] + closes[t0 + 1:post_end + 1]) if post_end > t0 else None

    # abnormal (stock minus index) over the full pre..post window we have
    lo, hi = max(t0 - pre_days - 1, 0), post_end
    stock_full = _cum_return(window(lo, hi + 1, closes))
    idx_full = _cum_return(window(lo, hi + 1, idx_closes))
    abnormal = None if (stock_full is None or idx_full is None) else round(stock_full - idx_full, 2)

    # heuristic: how much of the plausible move is already behind us?
    realized = sum(abs(x) for x in (pre or 0, event_ret or 0) if x)
    ahead = abs(post or 0)
    priced_in = None
    if realized or ahead:
        priced_in = round(realized / (realized + ahead) * 100) if (realized + ahead) else None

    return {
        "available": True,
        "code": code,
        "event_date": event_date,
        "t0_trading_day": dates[t0],
        "pre_event_drift_pct": pre,
        "event_day_return_pct": event_ret,
        "post_event_return_pct": post,
        "abnormal_return_vs_taiex_pct": abnormal,
        "post_days_available": post_end - t0,
        "priced_in_heuristic_0_100": priced_in,
        "reading": _reading(pre, event_ret, post, abnormal),
    }


def _reading(pre, event_ret, post, abnormal) -> str:
    big = lambda x: x is not None and abs(x) >= 4
    if big(pre) or big(event_ret):
        if post is not None and abs(post) < 2:
            return "催化劑當下已大幅反映，事件後續動能有限 — 視為已 price-in"
        return "催化劑當下大幅反映，但事件後仍有延續 — 部分 price-in"
    if abnormal is not None and abnormal > 5:
        return "相對大盤明顯超額報酬，題材溢價已顯著累積"
    if (pre is None and event_ret is None) or (abs(pre or 0) < 2 and abs(event_ret or 0) < 2):
        return "價格對此事件反應平淡 — 可能尚未反映，或市場不認為重要"
    return "反映程度中等，需搭配基本面驗證判斷後續空間"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--code", required=True, help="security code, or TAIEX")
    ap.add_argument("--event-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--pre-days", type=int, default=5)
    ap.add_argument("--post-days", type=int, default=5)
    ap.add_argument("--output", default="-")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = study(args.code, args.event_date, args.pre_days, args.post_days)
    except Exception as exc:
        print(json.dumps({"available": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
