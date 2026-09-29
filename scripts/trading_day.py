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
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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
STATIC_FALLBACK_PATH = Path(__file__).resolve().parent / "data" / "holiday_calendar_fallback.json"
_BLOCK_MARKERS = ("SECURITY REASONS", "無法呈現", "Access Denied", "Just a moment")
# Rows whose name/description mention one of these are still trading days
# (informational markers TWSE includes in the same feed), so they must never
# be added to the closed set even though they sit inside a holiday block.
_OPEN_MARKERS = ("開始交易", "最後交易")
# Rows matching any of these are actual non-trading days. "補假" (compensatory
# day off) and "無交易"/settlement-only rows do NOT contain "放假", so a
# filter that only checks for "放假" silently drops them — that was the bug:
# five 2026 dates (02-27, 04-03, 04-06, 10-09, 10-26) are 補假 rows and were
# being treated as ordinary trading days even when the live fetch succeeded.
_CLOSED_MARKERS = ("放假", "補假", "休市", "無交易")


def _now_tp() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Taipei"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8)))


def _relaxed_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        context.verify_flags &= ~strict_flag
    return context


def _certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


_SSL_CONTEXTS = [c for c in (_relaxed_context(), _certifi_context()) if c is not None]


def _get(url: str, retries: int = 3) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (taiwan-stock-daily-report/2.1)"})
    last_error: Exception | None = None
    for attempt in range(retries):
        for context in _SSL_CONTEXTS:
            try:
                with urlopen(request, timeout=20, context=context) as resp:
                    body = resp.read().decode("utf-8-sig", "replace")
                if body.lstrip()[:1] == "<" or any(m in body[:600] for m in _BLOCK_MARKERS):
                    raise RuntimeError("blocked / HTML response")
                return body
            except Exception as exc:
                last_error = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise last_error if last_error is not None else RuntimeError("fetch failed")


def _iso(value: str) -> str | None:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 7:
        return f"{int(digits[:3]) + 1911:04d}-{digits[3:5]}-{digits[5:7]}"
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value).strip() or None


def _is_closed_entry(name: str, desc: str) -> bool:
    text = f"{name}{desc}"
    if any(m in text for m in _OPEN_MARKERS):
        return False
    return any(m in text for m in _CLOSED_MARKERS)


def _parse_closed_rows(data) -> set[str]:
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
        if iso and _is_closed_entry(name, desc):
            closed.add(iso)
    return closed


def _load_static_fallback(year: int) -> set[str]:
    try:
        payload = json.loads(STATIC_FALLBACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return set(payload.get(str(year), []))


def _closed_dates(now: datetime) -> tuple[set[str], bool, str]:
    roc = now.year - 1911
    year_prefix = f"{now.year:04d}-"
    # Early-January lookbacks cross into last year; the live feed only covers
    # the requested year, so last year's dates always come from the fallback.
    prior_year = _load_static_fallback(now.year - 1)
    for url in HOLIDAY_URLS:
        try:
            data = json.loads(_get(url.format(roc=roc)))
        except Exception:
            continue
        # TWSE answers a not-yet-published year with the CURRENT year's rows
        # (queryYear=116 returned 2026 dates on 2026-09-29), and openapi has no
        # year parameter at all. Counting those as "live" would silently leave
        # the new year's holidays out, so only the requested year counts.
        closed = {d for d in _parse_closed_rows(data) if d.startswith(year_prefix)}
        if closed:
            return closed | prior_year, True, "live"
    # Both live TWSE endpoints failed (they share the same origin, so a
    # cloud-IP block takes both out together). Fall back to the calendar
    # baked into the repo instead of silently assuming every weekday is a
    # trading day — that assumption is what mis-tagged 2026-09-25 (中秋節)
    # as a trading day on a day the live fetch happened to fail.
    fallback = _load_static_fallback(now.year)
    if fallback:
        return fallback | prior_year, True, "static_fallback"
    return prior_year, False, "none"


def _maintenance_warning(now: datetime) -> str | None:
    """From November on, nag until next year's holidays are in the fallback file:
    once January arrives without them, a failed live fetch means holiday_source=none
    and every weekday — Lunar New Year included — is treated as a trading day."""
    if now.month < 11 or _load_static_fallback(now.year + 1):
        return None
    return (f"scripts/data/holiday_calendar_fallback.json 尚無 {now.year + 1} 年休市日；"
            f"證交所公布後請在本機補上並 push（做法見該檔 _comment）。")


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
    closed, had_calendar, holiday_source = _closed_dates(now)
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
        "holiday_source": holiday_source,
        "recommendation": recommendation,
        "reason": reason,
        "report_date": report_date,
        "title_suffix": title_suffix,
        "report_title": report_title,
        "notion_date_property": {"start": report_date, "is_datetime": 0},
        "maintenance_warning": _maintenance_warning(now),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
