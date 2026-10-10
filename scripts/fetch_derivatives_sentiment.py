#!/usr/bin/env python3
"""TAIFEX derivatives sentiment: 臺指選擇權 Put/Call 比 + 臺股期貨三大法人未平倉.

Two official, no-auth TAIFEX pages (HTML tables, not real CSV despite the
"Excel" in the URL), parsed with the stdlib only — this repo deliberately has
no third-party dependencies, so no pandas/bs4:

  1. pcRatioExcel      -> 臺指選擇權 Put/Call 比 (成交量比 + 未平倉量比), one
                          request covers the report date and the prior sessions.
  2. futContractsDate  -> 臺股期貨 (TXF) 自營商／投信／外資 trading + 未平倉
                          long/short/net. Single-day snapshot only, so the prior
                          session is found by stepping back day by day.

Self-checks instead of trusting the column layout: every parsed row must satisfy
long - short == net (both 成交 and 未平倉) and put/call ratios must match
put/call volume; a row that fails is reported as unavailable, never guessed.

Degrades to a valid JSON (status ok/degraded/empty + errors) on any failure so
a bad day on this step never blocks the rest of the report pipeline.

Usage:
  python scripts/fetch_derivatives_sentiment.py --date 2026-10-08 --output derivatives.json
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

PC_URL = "https://www.taifex.com.tw/cht/3/pcRatioExcel?queryStartDate={start}&queryEndDate={end}"
FUT_URL = (
    "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
    "?queryType=2&goDay=&doQuery=1&dateaddcnt=&queryDate={d}&commodityId=TXF"
)
IDENTITIES = {"自營商": "dealer", "投信": "investment_trust", "外資": "foreign"}
TARGET_PRODUCT = "臺股期貨"
# order of the 12 numeric cells after the 身份別 cell, per the page header
FUT_FIELDS = [
    "trading_long", "trading_long_amt", "trading_short", "trading_short_amt",
    "trading_net", "trading_net_amt",
    "oi_long", "oi_long_amt", "oi_short", "oi_short_amt",
    "oi_net", "oi_net_amt",
]
PC_LOOKBACK_DAYS = 10
FUT_PREV_MAX_STEPS = 6


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


def _get(url: str, retries: int = 3) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 taiwan-stock-daily-report/2.2"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30, context=_SSL) as resp:
                body = resp.read().decode("utf-8", "replace")
            if len(body) < 200:
                raise RuntimeError(f"unexpectedly short response ({len(body)} bytes)")
            return body
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("fetch failed")


class _TableParser(HTMLParser):
    """Flatten every <tr> in the document into a list of cell-text lists."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row:
            self.rows.append(self._row)
        self._row = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _rows(html: str) -> list[list[str]]:
    p = _TableParser()
    p.feed(html)
    p._close_row()
    return p.rows


def _plain_text(html: str) -> str:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt)


def _num(text: str) -> float | None:
    t = re.sub(r"[,\s]", "", text or "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    return float(t)


def _iso(slash_date: str) -> str | None:
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", slash_date.strip())
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _slash(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def parse_put_call(html: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cells in _rows(html):
        if len(cells) < 7:
            continue
        iso = _iso(cells[0])
        if not iso:
            continue
        vals = [_num(c) for c in cells[1:7]]
        if any(v is None for v in vals):
            continue
        put_vol, call_vol, vol_ratio, put_oi, call_oi, oi_ratio = vals
        calc_vol = put_vol / call_vol * 100 if call_vol else None
        calc_oi = put_oi / call_oi * 100 if call_oi else None
        check_ok = (
            calc_vol is not None and calc_oi is not None
            and abs(calc_vol - vol_ratio) < 0.05 and abs(calc_oi - oi_ratio) < 0.05
        )
        out[iso] = {
            "date": iso,
            "put_volume": int(put_vol), "call_volume": int(call_vol),
            "volume_ratio_pct": vol_ratio,
            "put_oi": int(put_oi), "call_oi": int(call_oi),
            "oi_ratio_pct": oi_ratio,
            "ratio_check_ok": check_ok,
        }
    return out


def parse_futures(html: str, want_date: str) -> dict[str, Any]:
    plain = _plain_text(html)
    if "查無資料" in plain:
        return {"available": False, "reason": "TAIFEX 查無資料（非交易日或尚未公布）"}
    m = re.search(r"日期\s*[:：]?\s*(\d{4}/\d{1,2}/\d{1,2})", plain)
    page_date = _iso(m.group(1)) if m else None
    if page_date != want_date:
        return {"available": False,
                "reason": f"頁面日期 {page_date} 與查詢日期 {want_date} 不符，不採用"}

    found: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    # The page lists the 臺股期貨 block and then 期貨小計／期貨合計 blocks that repeat
    # the same three 身份別 labels with all-futures totals. Rows are told apart only
    # by the product cell, which rowspan puts on the first row of each block, so
    # carry it forward and accept 臺股期貨 rows only.
    product = ""
    for cells in _rows(html):
        idx = next((i for i, c in enumerate(cells)
                    if any(c == k or c.startswith(k) for k in IDENTITIES)), None)
        if idx is None:
            continue
        if idx >= 1:
            product = cells[idx - 1]
        if product != TARGET_PRODUCT:
            continue
        label = next(k for k in IDENTITIES if cells[idx] == k or cells[idx].startswith(k))
        nums = [_num(c) for c in cells[idx + 1:]]
        if len(nums) != len(FUT_FIELDS) or any(n is None for n in nums):
            problems.append(f"{label}: 預期 {len(FUT_FIELDS)} 個數值欄，實得 {len(nums)}（版面可能改版）")
            continue
        rec = {k: int(v) for k, v in zip(FUT_FIELDS, nums)}
        rec["arithmetic_ok"] = (
            rec["trading_long"] - rec["trading_short"] == rec["trading_net"]
            and rec["oi_long"] - rec["oi_short"] == rec["oi_net"]
        )
        if not rec["arithmetic_ok"]:
            problems.append(f"{label}: 多空淨額與多方−空方不一致，欄位對應可能錯位")
            continue
        found[IDENTITIES[label]] = rec

    if "foreign" not in found:
        return {"available": False,
                "reason": "；".join(problems) or "找不到外資列（版面可能改版）"}
    return {"available": True, "date": want_date, "problems": problems, **found}


def build(report_date: date) -> dict[str, Any]:
    now = _now_tp()
    errors: list[str] = []
    iso = report_date.isoformat()

    put_call: dict[str, Any] = {"available": False, "reason": "not attempted"}
    try:
        start = report_date - timedelta(days=PC_LOOKBACK_DAYS)
        html = _get(PC_URL.format(start=_slash(start), end=_slash(report_date)))
        rows = parse_put_call(html)
        if iso not in rows:
            put_call = {"available": False,
                        "reason": f"TAIFEX 無 {iso} 的 Put/Call 資料（非交易日或尚未公布）"}
        elif not rows[iso]["ratio_check_ok"]:
            put_call = {"available": False,
                        "reason": "Put/Call 比率與買賣權口數自檢不符，不採用"}
        else:
            cur = rows[iso]
            earlier = sorted(d for d in rows if d < iso and rows[d]["ratio_check_ok"])
            previous = rows[earlier[-1]] if earlier else None
            put_call = {"available": True, **cur}
            if previous:
                put_call["previous"] = {
                    "date": previous["date"],
                    "volume_ratio_pct": previous["volume_ratio_pct"],
                    "oi_ratio_pct": previous["oi_ratio_pct"],
                }
                put_call["oi_ratio_change_pp"] = round(cur["oi_ratio_pct"] - previous["oi_ratio_pct"], 2)
                put_call["volume_ratio_change_pp"] = round(cur["volume_ratio_pct"] - previous["volume_ratio_pct"], 2)
    except Exception as exc:
        put_call = {"available": False, "reason": f"fetch/parse failed: {exc}"}
    if not put_call.get("available"):
        errors.append(f"put_call: {put_call.get('reason')}")

    futures: dict[str, Any] = {"available": False, "reason": "not attempted"}
    try:
        futures = parse_futures(_get(FUT_URL.format(d=_slash(report_date))), iso)
        if futures.get("available"):
            futures["contract"] = "臺股期貨 (TXF)"
            futures["unit"] = "口數；金額單位為千元新臺幣（TAIFEX 表頭「千元」）"
            for p in futures.pop("problems", []):
                errors.append(f"futures_oi: {p}")
            prev = None
            cursor = report_date
            for _ in range(FUT_PREV_MAX_STEPS):
                cursor -= timedelta(days=1)
                if cursor.weekday() >= 5:
                    continue
                try:
                    cand = parse_futures(_get(FUT_URL.format(d=_slash(cursor))), cursor.isoformat())
                except Exception:
                    continue
                if cand.get("available"):
                    prev = cand
                    break
            if prev:
                for who in ("foreign", "investment_trust", "dealer"):
                    if who in futures and who in prev:
                        futures[who]["oi_net_previous"] = prev[who]["oi_net"]
                        futures[who]["oi_net_change"] = futures[who]["oi_net"] - prev[who]["oi_net"]
                futures["previous_date"] = prev["date"]
        else:
            futures.pop("problems", None)
    except Exception as exc:
        futures = {"available": False, "reason": f"fetch/parse failed: {exc}"}
    if not futures.get("available"):
        errors.append(f"futures_oi: {futures.get('reason')}")

    ok_count = sum(1 for part in (put_call, futures) if part.get("available"))
    status = "ok" if ok_count == 2 and not errors else ("empty" if ok_count == 0 else "degraded")

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "source": "TAIFEX 臺灣期貨交易所 (pcRatioExcel, futContractsDateExcel)",
        "query_date": iso,
        "status": status,
        "put_call": put_call,
        "futures_oi": futures,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="report_date YYYY-MM-DD (default: today, Asia/Taipei). "
                                       "Pass trading_day.py's report_date, never the run date.")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        d = date.fromisoformat(args.date) if args.date else _now_tp().date()
        data = build(d)
    except Exception as exc:
        data = {
            "schema_version": 1, "generated_at": _now_tp().isoformat(timespec="seconds"),
            "timezone": "Asia/Taipei", "status": "empty",
            "put_call": {"available": False}, "futures_oi": {"available": False},
            "errors": [f"fatal: {exc}"],
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
