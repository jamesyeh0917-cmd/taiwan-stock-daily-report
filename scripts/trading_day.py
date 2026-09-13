#!/usr/bin/env python3
"""Decide whether today's run should be full / light / skip.

A "full" run happens when there is a Taiwan stock close that the previous
report has not covered yet. Otherwise (weekend, holiday, or the report
already ran for the latest session) the run is "light" (macro + news only)
or "skip".

Also the single canonical source for "what date/title does this run's report
get" — report_date, report_title and notion_date_property should be copied
verbatim by the caller (Notion page title/property, validate_report.py's
--expected-base-date) rather than re-derived from prose each run. This is
what a light-mode report's date/title must use too: it represents the same
trading session as the prior report, not today's wall-clock date.

Usage:
  python scripts/trading_day.py [--last-report-date YYYY-MM-DD] [--allow-light] [--revision N]

Prints a JSON object and exits 0. The caller reads `recommendation`.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

HOLIDAY_URLS = [
    "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&queryYear={roc}",
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
]
TWSE_CLOSE_HOUR = 14  # allow a publishing buffer past the 13:30 close


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


def _get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (taiwan-stock-daily-report/2.0)"})
    with urlopen(req, timeout=20, context=_ctx()) as resp:
        body = resp.read().decode("utf-8-sig", "replace")
    if body.lstrip()[:1] == "<" or "SECURITY REASONS" in body:
        raise RuntimeError("blocked / HTML response")
    return body


def _iso(value: str) -> str | None:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 7:
        return f"{int(digits[:3]) + 1911:04d}-{digits[3:5]}-{digits[5:7]}"
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value).strip() or None


def _closed_dates(now: datetime) -> tuple[set[str], bool]:
    roc = now.year - 1911
    for url in HOLIDAY_URLS:
        try:
            data = json.loads(_get(url.format(roc=roc)))
        except Exception:
            continue
        rows: list[dict] = []
        if isinstance(data, dict) and "data" in data and "fields" in data:
            rows = [dict(zip(data["fields"], r)) for r in data["data"]]
        elif isinstance(data, list):
            rows = data
        closed: set[str] = set()
        for row in rows:
            desc = str(row.get("說明") or row.get("Description") or "")
            name = str(row.get("名稱") or row.get("Name") or "")
            iso = _iso(row.get("日期") or row.get("Date") or "")
            if not iso or "交易" in desc or "交易" in name:
                continue
            if "放假" in desc or "休市" in desc:
                closed.add(iso)
        if closed:
            return closed, True
    return set(), False


def _last_completed_session(now: datetime, closed: set[str]) -> str:
    cursor = now.date()
    if now.hour < TWSE_CLOSE_HOUR + 1:
        cursor -= timedelta(days=1)
    for _ in range(30):
        if cursor.weekday() < 5 and cursor.isoformat() not in closed:
            return cursor.isoformat()
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last-report-date", help="資料基準日 of the most recent prior report (YYYY-MM-DD)")
    parser.add_argument("--allow-light", action="store_true",
                        help="emit 'light' instead of 'skip' when there is no new session")
    parser.add_argument("--revision", type=int, default=1,
                        help="pass N>1 when this is a same-day rerun of an already-published "
                             "report_date; only affects title_suffix, does not change recommendation")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    now = _now_tp()
    closed, had_calendar = _closed_dates(now)
    last_session = _last_completed_session(now, closed)
    today = now.date().isoformat()
    today_is_trading = now.weekday() < 5 and today not in closed

    covered = args.last_report_date == last_session
    if covered:
        recommendation = "light" if args.allow_light else "skip"
        reason = f"最近收盤 {last_session} 已由前一份報告涵蓋"
    else:
        recommendation = "full"
        reason = f"最近收盤 {last_session} 尚未有報告（前一份 = {args.last_report_date or '無'}）"

    # report_date is the single canonical answer to "what date does this run's
    # report represent" — always the last completed trading session, never
    # wall-clock "today", in every mode including light. Everything downstream
    # (page title, Notion 資料基準日 property, validate_report.py's
    # --expected-base-date) should copy these fields rather than re-deriving
    # the same rule from prose each run.
    report_date = last_session
    if recommendation == "light":
        title_suffix = " (輕量)"
    elif args.revision > 1:
        title_suffix = f" (修訂 {args.revision})"
    else:
        title_suffix = ""
    report_title = f"台灣與全球總經投資研究｜{report_date}{title_suffix}"

    print(json.dumps({
        "checked_at": now.isoformat(timespec="seconds"),
        "today": today,
        "today_is_trading_day": today_is_trading,
        "last_completed_session": last_session,
        "prior_report_date": args.last_report_date,
        "holiday_calendar_loaded": had_calendar,
        "recommendation": recommendation,
        "reason": reason,
        "report_date": report_date,
        "title_suffix": title_suffix,
        "report_title": report_title,
        "notion_date_property": {"start": report_date, "is_datetime": 0},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
