#!/usr/bin/env python3
"""MOPS 重大訊息 (material disclosures) for one session, filtered to a code list.

data-sources.md has long listed 公開資訊觀測站 重大訊息 as a tier-B source, but
nothing actually fetched it — company-level events (法說會、處分資產、董監異動、
訴訟、私募…) only surfaced if a news outlet happened to cover them. This script
pulls the day's full MOPS feed, keeps listed (sii) / OTC (otc) companies, and
returns the ones in --codes (the watchlist + shortlist) with a rough keyword
category and, for those hits, the first part of the full disclosure text.

Endpoint (found via the MOPS SPA network trace, verified live):
  POST https://mops.twse.com.tw/mops/api/t05st02   body {"companyId":"","year":"115","month":"10","day":"08"}
  - year is the ROC year as a string; month/day zero-padded.
  - A WAF blocks header-less clients with a "FOR SECURITY REASONS" HTML page;
    a browser-like User-Agent + Referer + Origin gets through, no cookie needed.
  - One "day" query is NOT cut at the close: it returns the previous evening's
    filings (from ~17:30) plus the whole calendar day up to 24:00 — measured on
    2026-10-08, 162 of 209 rows were filed after the 15:00 cutoff. So this script
    queries every calendar day in the window, de-duplicates, and cuts by time to
    (since 15:00, report_date 15:00], which is the repo's analysis_as_of rule.
    Filings after the cutoff are only counted (after_cutoff_count), never listed;
    the next report's window picks them up.
Rows: [date(ROC), time, code, name, title, {"apiName", "parameters": {..., "marketKind"}}]

category is a keyword label meant to help a reader scan, not a judgment of
whether the news is good or bad — that interpretation stays with the report.

Degrades to a valid JSON (status ok/degraded/empty + errors) on any failure,
including the WAF page, so this step never blocks the rest of the pipeline.

Usage:
  python scripts/fetch_material_news.py --date 2026-10-08 --since 2026-10-07 \n    --codes 2330,2317,... --output material_news.json
  (--since = prior report's report_date, i.e. trading_day.py's prior_report_date;
   default is the day before --date)
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
from typing import Any
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

BASE = "https://mops.twse.com.tw/mops/api/"
LIST_API = BASE + "t05st02"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://mops.twse.com.tw/mops/",
    "Origin": "https://mops.twse.com.tw",
    "Accept": "application/json, text/plain, */*",
}
WAF_MARKERS = ("FOR SECURITY REASONS", "安全性考量", "THIS PAGE CAN NOT BE ACCESSED")
KEEP_MARKETS = {"sii": "上市", "otc": "上櫃"}
DETAIL_EXCERPT_CHARS = 500

# First match wins, so riskier / more specific labels come first. Rough labels
# only — see the module docstring.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("訴訟／裁罰／營運風險", ("訴訟", "裁罰", "罰鍰", "違約", "重整", "停工", "火災", "災害", "破產",
                         "跳票", "退票", "處置", "停止買賣", "終止上市", "下市", "假扣押")),
    ("澄清媒體報導", ("澄清", "媒體報導", "報導")),
    ("法說會／投資論壇", ("法人說明會", "法說會", "投資論壇", "投資人說明會", "業績發表")),
    ("庫藏股", ("庫藏股", "買回")),
    ("股利／除權息", ("股利", "除息", "除權", "盈餘分派", "配息", "配股")),
    ("增資／私募／發行", ("私募", "現金增資", "增資", "公司債", "可轉換", "海外存託", "發行", "募集")),
    ("資金貸與／背書保證", ("資金貸與", "背書保證")),
    ("併購／分割／股份轉換", ("合併", "收購", "併購", "分割", "股份轉換", "讓與", "購併")),
    ("資產取得／處分", ("取得", "處分", "有價證券", "不動產", "土地", "廠房", "機器設備", "設備")),
    ("董監／經理人異動", ("董事", "監察人", "經理人", "總經理", "財務長", "發言人", "辭任", "解任",
                      "就任", "任命", "異動", "獨立董事", "薪酬委員", "審計委員")),
    ("重大合約／投資", ("合約", "訂單", "合資", "策略聯盟", "簽約", "投資")),
    ("財務／營收", ("自結", "財務報告", "營業收入", "營收", "損益", "盈餘", "虧損", "減損")),
    ("股東會／章程", ("股東常會", "股東臨時會", "股東會", "章程")),
]


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


def _post(url: str, body: dict, retries: int = 3) -> Any:
    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, data=data, headers=HEADERS, method="POST")
            with urlopen(req, timeout=40, context=_SSL) as resp:
                raw = resp.read().decode("utf-8-sig", "replace")
            if any(m in raw[:2000] for m in WAF_MARKERS) or raw.lstrip()[:1] == "<":
                raise RuntimeError("blocked by MOPS WAF (security-reasons page)")
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("code") not in (None, 200):
                raise RuntimeError(f"MOPS code={payload.get('code')} {payload.get('message')}")
            return payload
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("post failed")


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def categorize(title: str) -> str:
    for label, words in CATEGORY_RULES:
        if any(w in title for w in words):
            return label
    return "其他"


def _rows_of(payload: Any) -> list:
    result = payload.get("result") if isinstance(payload, dict) else payload
    if isinstance(result, dict):
        result = result.get("data")
    return result if isinstance(result, list) else []


def _market_kind(detail: Any) -> str | None:
    if isinstance(detail, dict):
        params = detail.get("parameters")
        if isinstance(params, dict):
            return params.get("marketKind")
    return None


def _detail_excerpt(detail: dict) -> str | None:
    api = detail.get("apiName")
    params = detail.get("parameters")
    if not api or not isinstance(params, dict):
        return None
    payload = _post(BASE + api, params)
    rows = _rows_of(payload)
    texts = [str(c) for row in rows if isinstance(row, list) for c in row if isinstance(c, str)]
    if not texts:
        return None
    longest = max(texts, key=len)
    return _clean(longest)[:DETAIL_EXCERPT_CHARS]


CUTOFF_HOUR = 15  # analysis_as_of is the report_date's 15:00 Asia/Taipei close
MAX_WINDOW_DAYS = 10


def _filed_at(date_roc: str, time_s: str) -> datetime | None:
    m = re.fullmatch(r"(\d{2,3})/(\d{2})/(\d{2})", str(date_roc).strip())
    t = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", str(time_s).strip())
    if not (m and t):
        return None
    try:
        return datetime(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)),
                        int(t.group(1)), int(t.group(2)), int(t.group(3)))
    except ValueError:
        return None


def build(report_date: date, since: date, codes: set[str], detail_limit: int) -> dict[str, Any]:
    now = _now_tp()
    errors: list[str] = []
    cutoff_end = datetime(report_date.year, report_date.month, report_date.day, CUTOFF_HOUR)
    cutoff_start = datetime(since.year, since.month, since.day, CUTOFF_HOUR)

    days: list[date] = []
    cursor = max(since, report_date - timedelta(days=MAX_WINDOW_DAYS))
    while cursor <= report_date:
        days.append(cursor)
        cursor += timedelta(days=1)

    seen: set[tuple] = set()
    in_window: list[dict[str, Any]] = []
    after_cutoff_count = 0
    queried_ok = 0
    queried_failed = 0
    for day in days:
        body = {"companyId": "", "year": str(day.year - 1911),
                "month": f"{day.month:02d}", "day": f"{day.day:02d}"}
        try:
            rows = _rows_of(_post(LIST_API, body))
            queried_ok += 1
        except Exception as exc:
            queried_failed += 1
            errors.append(f"{day.isoformat()}: fetch failed: {exc}")
            continue
        for row in rows:
            if not (isinstance(row, list) and len(row) >= 5):
                continue
            row_date, row_time, code, name, title = row[:5]
            detail = row[5] if len(row) > 5 else None
            market = _market_kind(detail)
            if market not in KEEP_MARKETS:
                continue
            key = (row_date, row_time, str(code).strip(), _clean(title))
            if key in seen:
                continue
            seen.add(key)
            filed = _filed_at(row_date, row_time)
            if filed is None:
                continue
            if filed > cutoff_end:
                after_cutoff_count += 1
                continue
            if filed <= cutoff_start:
                continue
            in_window.append({
                "code": str(code).strip(), "company": _clean(name), "market": KEEP_MARKETS[market],
                "filed_at": filed.strftime("%Y-%m-%d %H:%M:%S"),
                "title": _clean(title), "category": categorize(_clean(title)),
                "_detail": detail if isinstance(detail, dict) else None,
            })

    if queried_ok == 0:
        status = "empty"
    elif queried_failed:
        status = "degraded"
    elif not in_window:
        status = "empty"
        errors.append("窗口內 0 筆（非交易日或尚未公布）")
    else:
        status = "ok"

    category_counts: dict[str, int] = {}
    for r in in_window:
        category_counts[r["category"]] = category_counts.get(r["category"], 0) + 1

    items = [r for r in in_window if r["code"] in codes]
    items.sort(key=lambda r: r["filed_at"], reverse=True)
    fetched = 0
    for it in items:
        det = it.pop("_detail", None)
        if det and fetched < detail_limit:
            fetched += 1
            try:
                it["detail_excerpt"] = _detail_excerpt(det)
            except Exception as exc:
                it["detail_excerpt"] = None
                errors.append(f"detail {it['code']} {it['filed_at']}: {exc}")
                if status == "ok":
                    status = "degraded"
    for r in in_window:
        r.pop("_detail", None)

    # One company can file a dozen near-identical notices in a day (e.g. routine
    # securities buys/sells); the report should describe those as one line.
    hits_by_company: dict[str, dict[str, Any]] = {}
    for it in items:
        g = hits_by_company.setdefault(it["code"], {"company": it["company"], "count": 0, "categories": {}})
        g["count"] += 1
        g["categories"][it["category"]] = g["categories"].get(it["category"], 0) + 1

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "source": "MOPS 公開資訊觀測站 t05st02 (當日重大訊息)",
        "query_date": report_date.isoformat(),
        "window": {
            "start_exclusive": cutoff_start.strftime("%Y-%m-%d %H:%M"),
            "end_inclusive": cutoff_end.strftime("%Y-%m-%d %H:%M"),
            "days_queried": [d.isoformat() for d in days],
            "note": "依時間契約只採 (since 15:00, report_date 15:00] 內的公告；"
                    "收盤後公布者只計數，留給下一份報告",
        },
        "status": status,
        "watchlist_codes_count": len(codes),
        "all_market_count": len(in_window),
        "watchlist_hit_count": len(items),
        "after_cutoff_count": after_cutoff_count,
        "category_counts_all_market": dict(sorted(category_counts.items(), key=lambda kv: -kv[1])),
        "hits_by_company": hits_by_company,
        "items": items,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="report_date YYYY-MM-DD (default: today, Asia/Taipei). "
                                       "Pass trading_day.py's report_date, never the run date.")
    parser.add_argument("--since", help="prior report's report_date YYYY-MM-DD (trading_day.py's "
                                        "prior_report_date); window opens at its 15:00. "
                                        "Default: the day before --date")
    parser.add_argument("--codes", default="", help="comma-separated stock codes to keep")
    parser.add_argument("--detail-limit", type=int, default=10,
                        help="max hits to fetch full disclosure text for (default 10)")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    codes = {c.strip() for c in args.codes.split(",") if c.strip()}
    try:
        d = date.fromisoformat(args.date) if args.date else _now_tp().date()
        since = date.fromisoformat(args.since) if args.since else d - timedelta(days=1)
        if since >= d:
            since = d - timedelta(days=1)
        data = build(d, since, codes, args.detail_limit)
    except Exception as exc:
        data = {
            "schema_version": 1, "generated_at": _now_tp().isoformat(timespec="seconds"),
            "timezone": "Asia/Taipei", "status": "empty", "items": [],
            "all_market_count": 0, "watchlist_hit_count": 0,
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
