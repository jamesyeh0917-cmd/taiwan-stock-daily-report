#!/usr/bin/env python3
"""Fetch and normalize the latest Taiwan stock market close snapshot.

Beyond the raw TWSE/TPEx close, this build adds:

* a trading-calendar freshness gate (holiday schedule + weekday math) so a
  stale mirror response is flagged instead of silently treated as "today";
* a market-level aggregate cross-check (TWSE FMTQIK, last 5 sessions);
* valuation ratios (PER / PBR / yield) joined onto watchlist codes;
* optional multi-session liquidity history for watchlist codes, so the
  20-trading-day turnover rule in references/stock-screening.md has data.

Each TWSE source has a fallback chain: openapi.twse.com.tw mirror →
www.twse.com.tw/rwd (the site's own endpoint) → FinMind (per-stock only),
with retry + HTML-block-page detection, because the openapi mirror
intermittently blocks cloud IP ranges with a "FOR SECURITY REASONS" page.

Everything degrades gracefully: a failed source is recorded under "errors"
and the rest of the snapshot is still emitted.
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 fallback
    ZoneInfo = None  # type: ignore[assignment]


# Primary = openapi.twse.com.tw mirror. It intermittently blocks some cloud
# IP ranges with an HTML "FOR SECURITY REASONS" page, so every TWSE source
# also has a www.twse.com.tw/rwd fallback (the site's own endpoint), and
# per-stock history falls back to FinMind.
TWSE_QUOTES_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_QUOTES_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=csv"
TWSE_INDEX_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
TWSE_INDEX_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date={yyyymmdd}&type=IND"
TWSE_MARKET_STATS_URL = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
TWSE_MARKET_STATS_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json&date={yyyymmdd}"
TWSE_VALUATION_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TWSE_VALUATION_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json&date={yyyymmdd}&selectType=ALL"
TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
TWSE_HOLIDAY_RWD = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&queryYear={roc}"
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&stockNo={code}&date={yyyymmdd}"
TWSE_STOCK_DAY_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?response=json&stockNo={code}&date={yyyymmdd}"
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
SIGNAL_HISTORY_DAYS = 130  # calendar days (~85 trading) for momentum / MA / vol signals

# Exchange-listed ETFs and similar products commonly use codes beginning with 0.
# A four-digit code beginning with 1-9 is a conservative common-stock proxy.
COMMON_STOCK_RE = re.compile(r"^[1-9]\d{3}$")

# TWSE close is 13:30; allow a publishing buffer before expecting same-day data.
TWSE_CLOSE_HOUR = 14
MAX_HISTORY_CODES = 15
DEFAULT_HISTORY_DAYS = 20

ALIASES = {
    "date": ["date", "日期", "資料日期", "交易日期"],
    "market": ["market", "市場", "交易所"],
    "code": ["code", "stock_id", "stock_code", "證券代號", "股票代號", "代號"],
    "name": ["name", "stock_name", "證券名稱", "股票名稱", "名稱", "公司名稱"],
    "open": ["open", "openingprice", "開盤價", "開市價"],
    "high": ["high", "highestprice", "最高價"],
    "low": ["low", "lowestprice", "最低價"],
    "close": ["close", "closingprice", "收盤價", "收市價"],
    "change": ["change", "漲跌", "漲跌價差"],
    "volume": ["volume", "tradevolume", "tradingshares", "成交股數", "成交量"],
    "value": ["value", "tradevalue", "transactionamount", "成交金額", "成交值"],
    "transactions": ["transactions", "transaction", "transactionnumber", "成交筆數", "筆數"],
}


def _now_taipei() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Taipei"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8), name="Asia/Taipei"))


def _relaxed_context() -> ssl.SSLContext:
    # TWSE/TPEx certificate chains can trip strict X.509 validation on newer
    # OpenSSL builds. Drop only the strict flag; the chain and hostname are
    # still verified against the system trust store.
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


_SSL_CONTEXTS = [ctx for ctx in (_relaxed_context(), _certifi_context()) if ctx is not None]

_BLOCK_MARKERS = ("SECURITY REASONS", "無法呈現", "Access Denied", "Just a moment")


class BlockedResponse(RuntimeError):
    """The endpoint returned an HTML block / challenge page instead of data."""


def _fetch_text(url: str, retries: int = 3) -> str:
    request = Request(
        url,
        headers={
            "Accept": "application/json, text/csv, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) taiwan-stock-daily-report/2.1",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        for context in _SSL_CONTEXTS:
            try:
                with urlopen(request, timeout=30, context=context) as response:
                    body = response.read().decode("utf-8-sig", "replace")
                stripped = body.lstrip()
                if stripped[:1] == "<" or any(m in body[:600] for m in _BLOCK_MARKERS):
                    raise BlockedResponse(f"HTML block page from {url}")
                return body
            except ssl.SSLError as exc:
                last_error = exc
            except BlockedResponse as exc:
                last_error = exc
            except Exception as exc:  # transient network / 5xx
                last_error = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise last_error if last_error is not None else RuntimeError("fetch failed")


def _fetch_json(url: str) -> Any:
    return json.loads(_fetch_text(url))


def _chain(specs: list[tuple[str, Callable[[str], list[dict[str, Any]]]]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Try each (url, parser) in order; return the first non-empty result.

    parser receives the raw response text and returns rows in the openapi
    dict shape so downstream normalizers are unchanged.
    """
    notes: list[str] = []
    for url, parser in specs:
        try:
            rows = parser(_fetch_text(url))
            if rows:
                if notes:
                    notes.append(f"used fallback: {url}")
                return rows, notes
            notes.append(f"empty from {url}")
        except Exception as exc:
            notes.append(f"failed {url}: {exc}")
    return [], notes


def _roc_slash(now: datetime) -> str:
    return str(now.year - 1911)


def _strip_tags(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("％", "").replace("%", "")
    text = text.replace("＋", "+").replace("－", "-")
    if not text or text in {"--", "---", "N/A", "null", "None"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 7:  # ROC yyyymmdd
        year = int(digits[:3]) + 1911
        return f"{year:04d}-{digits[3:5]}-{digits[5:7]}"
    if len(digits) == 8:  # Gregorian yyyymmdd
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value).strip() or None


def _change_pct(close: float | None, change: float | None) -> float | None:
    if close is None or change is None:
        return None
    previous = close - change
    if previous <= 0:
        return None
    return round(change / previous * 100.0, 4)


def _normalized_row(
    *,
    market: str,
    date: Any,
    code: Any,
    name: Any,
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    change: Any,
    volume: Any,
    value: Any,
    transactions: Any,
) -> dict[str, Any]:
    close_value = _number(close)
    change_value = _number(change)
    return {
        "market": market.upper() or "UPLOADED",
        "date": _iso_date(date),
        "code": str(code).strip(),
        "name": str(name or "").strip(),
        "open": _number(open_),
        "high": _number(high),
        "low": _number(low),
        "close": close_value,
        "change": change_value,
        "change_pct": _change_pct(close_value, change_value),
        "volume_shares": _integer(volume),
        "value_twd": _integer(value),
        "transactions": _integer(transactions),
    }


def _normalize_twse(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _normalized_row(
            market="TWSE",
            date=row.get("Date"),
            code=row.get("Code"),
            name=row.get("Name"),
            open_=row.get("OpeningPrice"),
            high=row.get("HighestPrice"),
            low=row.get("LowestPrice"),
            close=row.get("ClosingPrice"),
            change=row.get("Change"),
            volume=row.get("TradeVolume"),
            value=row.get("TradeValue"),
            transactions=row.get("Transaction"),
        )
        for row in rows
    ]


def _normalize_tpex(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _normalized_row(
            market="TPEX",
            date=row.get("Date"),
            code=row.get("SecuritiesCompanyCode"),
            name=row.get("CompanyName"),
            open_=row.get("Open"),
            high=row.get("High"),
            low=row.get("Low"),
            close=row.get("Close"),
            change=row.get("Change"),
            volume=row.get("TradingShares"),
            value=row.get("TransactionAmount"),
            transactions=row.get("TransactionNumber"),
        )
        for row in rows
    ]


def _key_map(row: dict[str, Any]) -> dict[str, str]:
    return {re.sub(r"[\s_\-]", "", str(key)).lower(): str(key) for key in row}


def _pick(row: dict[str, Any], logical_name: str) -> Any:
    normalized_keys = _key_map(row)
    for alias in ALIASES[logical_name]:
        key = normalized_keys.get(re.sub(r"[\s_\-]", "", alias).lower())
        if key is not None:
            return row.get(key)
    return None


def _load_uploaded(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            if "data" in data:
                data = data["data"]
            elif "rows" in data:
                data = data["rows"]
            else:
                raise ValueError("JSON object must contain a data or rows array.")
        if not isinstance(data, list):
            raise ValueError("JSON must contain a row array, or an object with data/rows.")
        return [dict(row) for row in data if isinstance(row, dict)]
    if suffix == ".xlsx":
        try:
            import openpyxl  # type: ignore
        except ImportError as exc:
            raise RuntimeError("XLSX requires openpyxl; export the sheet as UTF-8 CSV.") from exc
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        values = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(values)]
        return [dict(zip(headers, row)) for row in values]
    raise ValueError("Supported inputs are CSV, JSON, and XLSX.")


def _normalize_uploaded(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        normalized.append(
            _normalized_row(
                market=str(_pick(row, "market") or "UPLOADED"),
                date=_pick(row, "date"),
                code=_pick(row, "code"),
                name=_pick(row, "name"),
                open_=_pick(row, "open"),
                high=_pick(row, "high"),
                low=_pick(row, "low"),
                close=_pick(row, "close"),
                change=_pick(row, "change"),
                volume=_pick(row, "volume"),
                value=_pick(row, "value"),
                transactions=_pick(row, "transactions"),
            )
        )
    return [row for row in normalized if row["code"] not in {"", "None"}]


def _summarize_market(rows: list[dict[str, Any]]) -> dict[str, Any]:
    common = [row for row in rows if COMMON_STOCK_RE.fullmatch(row["code"])]
    valid_changes = [row for row in common if row["change"] is not None]
    dates = sorted({row["date"] for row in common if row["date"]})

    gainers = sorted(
        (row for row in common if row["change_pct"] is not None),
        key=lambda row: (row["change_pct"], row["value_twd"] or 0),
        reverse=True,
    )[:10]
    losers = sorted(
        (row for row in common if row["change_pct"] is not None),
        key=lambda row: (row["change_pct"], -(row["value_twd"] or 0)),
    )[:10]
    turnover = sorted(common, key=lambda row: row["value_twd"] or -1, reverse=True)[:10]
    return {
        "data_date": dates[-1] if dates else None,
        "distinct_dates": dates,
        "all_instruments": len(rows),
        "common_stocks": len(common),
        "advancers": sum(1 for row in valid_changes if row["change"] > 0),
        "decliners": sum(1 for row in valid_changes if row["change"] < 0),
        "unchanged": sum(1 for row in valid_changes if row["change"] == 0),
        "missing_change": len(common) - len(valid_changes),
        "total_volume_shares": sum(row["volume_shares"] or 0 for row in common),
        "total_value_twd": sum(row["value_twd"] or 0 for row in common),
        "top_gainers": [dict(row) for row in gainers],
        "top_decliners": [dict(row) for row in losers],
        "top_turnover": [dict(row) for row in turnover],
    }


def _normalize_indices(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    wanted = {"發行量加權股價指數", "臺灣50指數", "寶島股價指數"}
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in rows:
        name = str(row.get("指數", "")).strip()
        seen_names.add(name)
        if name not in wanted:
            continue
        change = _number(row.get("漲跌點數"))
        sign = str(row.get("漲跌", "")).strip()
        if change is not None and sign == "-":
            change = -abs(change)
        normalized.append(
            {
                "date": _iso_date(row.get("日期")),
                "name": name,
                "close": _number(row.get("收盤指數")),
                "change": change,
                "change_pct": _number(row.get("漲跌百分比")),
            }
        )
    warnings: list[str] = []
    if rows and not normalized:
        warnings.append(
            "MI_INDEX returned rows but none matched the expected index names; "
            "the endpoint schema may have changed."
        )
    return normalized, warnings


def _normalize_market_stats(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append(
            {
                "date": _iso_date(row.get("Date")),
                "taiex_close": _number(row.get("TAIEX")),
                "taiex_change": _number(row.get("Change")),
                "trade_value_twd": _integer(row.get("TradeValue")),
                "trade_volume_shares": _integer(row.get("TradeVolume")),
                "transactions": _integer(row.get("Transaction")),
            }
        )
    return sorted((r for r in out if r["date"]), key=lambda r: r["date"])


def _valuation_lookup(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("Code", "")).strip()
        if not code:
            continue
        lookup[code] = {
            "date": _iso_date(row.get("Date")),
            "pe_ratio": _number(row.get("PEratio")),
            "pb_ratio": _number(row.get("PBratio")),
            "dividend_yield_pct": _number(row.get("DividendYield")),
        }
    return lookup


def _holiday_closed_dates(rows: Iterable[dict[str, Any]]) -> set[str]:
    closed: set[str] = set()
    for row in rows:
        description = str(row.get("Description", ""))
        name = str(row.get("Name", ""))
        iso = _iso_date(row.get("Date"))
        if not iso:
            continue
        # Entries that explicitly describe a trading day are not closures.
        if "交易" in description or "交易" in name:
            continue
        if "放假" in description or "休市" in description or "調整放假" in description:
            closed.add(iso)
    return closed


def _expected_last_trading_day(now_tp: datetime, closed: set[str]) -> str:
    cursor = now_tp.date()
    if now_tp.hour < TWSE_CLOSE_HOUR + 1:
        cursor -= timedelta(days=1)
    for _ in range(20):
        if cursor.weekday() < 5 and cursor.isoformat() not in closed:
            return cursor.isoformat()
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def _trading_day_gap(earlier: str | None, later: str | None, closed: set[str]) -> int | None:
    if not earlier or not later:
        return None
    try:
        start = date.fromisoformat(earlier)
        end = date.fromisoformat(later)
    except ValueError:
        return None
    if start >= end:
        return 0
    gap = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if cursor.weekday() < 5 and cursor.isoformat() not in closed:
            gap += 1
        cursor += timedelta(days=1)
    return gap


def _twse_tabular_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    fields = payload.get("fields") or []
    data = payload.get("data") or []
    return [dict(zip(fields, row)) for row in data if isinstance(row, list)]


# --- fallback parsers: each returns rows in the openapi dict shape ---------

def _p_quotes_openapi(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _p_quotes_rwd_csv(text: str) -> list[dict[str, Any]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        out.append({
            "Date": r.get("日期"), "Code": (r.get("證券代號") or "").strip(), "Name": r.get("證券名稱"),
            "TradeVolume": r.get("成交股數"), "TradeValue": r.get("成交金額"),
            "OpeningPrice": r.get("開盤價"), "HighestPrice": r.get("最高價"),
            "LowestPrice": r.get("最低價"), "ClosingPrice": r.get("收盤價"),
            "Change": r.get("漲跌價差"), "Transaction": r.get("成交筆數"),
        })
    return [r for r in out if r["Code"]]


def _p_indices_openapi(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _p_indices_rwd(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    out: list[dict[str, Any]] = []
    for table in data.get("tables", []) if isinstance(data, dict) else []:
        m = re.search(r"(\d{2,3})\D+(\d{1,2})\D+(\d{1,2})", str(table.get("title", "")))
        iso = None
        if m:
            y = int(m.group(1))
            y = y + 1911 if y < 1911 else y
            iso = f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        for row in table.get("data", []):
            if not isinstance(row, list) or len(row) < 5:
                continue
            change_txt = _strip_tags(row[2])
            pts = _strip_tags(row[3])
            out.append({
                "日期": iso, "指數": str(row[0]).strip(), "收盤指數": row[1],
                "漲跌": "-" if ("green" in str(row[2]) or change_txt == "-") else "+",
                "漲跌點數": pts, "漲跌百分比": _strip_tags(row[4]),
            })
    return out


def _p_stats_openapi(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _p_stats_rwd(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    rows = _twse_tabular_rows(data) if isinstance(data, dict) else []
    out = []
    for r in rows:
        out.append({
            "Date": r.get("日期"), "TradeVolume": r.get("成交股數"),
            "TradeValue": r.get("成交金額"), "Transaction": r.get("成交筆數"),
            "TAIEX": r.get("發行量加權股價指數"), "Change": r.get("漲跌點數"),
        })
    return out


def _p_valuation_openapi(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _make_p_valuation_rwd(iso_date: str | None) -> Callable[[str], list[dict[str, Any]]]:
    def parse(text: str) -> list[dict[str, Any]]:
        data = json.loads(text)
        date_str = _iso_date(data.get("date")) if isinstance(data, dict) else None
        rows = _twse_tabular_rows(data) if isinstance(data, dict) else []
        out = []
        for r in rows:
            out.append({
                "Date": date_str or iso_date,
                "Code": str(r.get("證券代號") or "").strip(),
                "Name": r.get("證券名稱"),
                "PEratio": r.get("本益比"),
                "DividendYield": r.get("殖利率(%)"),
                "PBratio": r.get("股價淨值比"),
            })
        return [r for r in out if r["Code"]]
    return parse


def _p_holiday_openapi(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _p_holiday_rwd(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    rows = _twse_tabular_rows(data) if isinstance(data, dict) else []
    return [{"Date": r.get("日期"), "Name": r.get("名稱"), "Description": r.get("說明")} for r in rows]


def _recent_weekday_yyyymmdd(now: datetime) -> str:
    d = now.date()
    if now.hour < TWSE_CLOSE_HOUR + 1:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _finmind(dataset: str, data_id: str, start: str) -> list[dict[str, Any]]:
    token = os.environ.get("FINMIND_TOKEN", "")
    url = f"{FINMIND_BASE}?dataset={dataset}&data_id={data_id}&start_date={start}"
    if token:
        url += f"&token={token}"
    payload = json.loads(_fetch_text(url))
    if isinstance(payload, dict) and payload.get("msg") not in (None, "success"):
        raise RuntimeError(f"FinMind {dataset}: {payload.get('msg')}")
    return payload.get("data", []) if isinstance(payload, dict) else []


def _finmind_price_history(code: str, start: str) -> list[dict[str, Any]]:
    rows = []
    for r in _finmind("TaiwanStockPrice", code, start):
        iso = str(r.get("date") or "")
        if not iso:
            continue
        rows.append({
            "date": iso,
            "close": _number(r.get("close")),
            "value_twd": _integer(r.get("Trading_money")),
            "volume_shares": _integer(r.get("Trading_Volume")),
        })
    return sorted(rows, key=lambda x: x["date"])


def _fetch_stock_history(code: str, months: int = 2) -> tuple[list[dict[str, Any]], str | None]:
    start = (_now_taipei().date() - timedelta(days=SIGNAL_HISTORY_DAYS)).strftime("%Y-%m-%d")
    error: str | None = None
    # FinMind first: one clean call, ~85 trading days, not IP-blocked.
    try:
        rows = _finmind_price_history(code, start)
        if len(rows) >= 15:
            return rows, None
    except Exception as exc:
        error = str(exc)
    # Fallback: TWSE per-month STOCK_DAY (openapi then rwd).
    anchor = _now_taipei().date().replace(day=1)
    merged: dict[str, dict[str, Any]] = {}
    for offset in range(max(months, 4)):
        month = anchor
        for _ in range(offset):
            month = (month - timedelta(days=1)).replace(day=1)
        yyyymmdd = month.strftime("%Y%m%d")
        payload = None
        for url in (TWSE_STOCK_DAY_URL, TWSE_STOCK_DAY_RWD):
            try:
                payload = _fetch_json(url.format(code=code, yyyymmdd=yyyymmdd))
                break
            except Exception as exc:
                error = str(exc)
        for row in _twse_tabular_rows(payload or {}):
            iso = _iso_date(row.get("日期"))
            if not iso:
                continue
            merged[iso] = {
                "date": iso,
                "close": _number(row.get("收盤價")),
                "value_twd": _integer(row.get("成交金額")),
                "volume_shares": _integer(row.get("成交股數")),
            }
    ordered = [merged[k] for k in sorted(merged)]
    return ordered, (None if len(ordered) >= 15 else error)


def _fetch_index_history() -> list[dict[str, Any]]:
    start = (_now_taipei().date() - timedelta(days=SIGNAL_HISTORY_DAYS)).strftime("%Y-%m-%d")
    try:
        return _finmind_price_history("TAIEX", start)
    except Exception:
        return []


def _pct_return(series: list[float], lookback: int) -> float | None:
    if len(series) <= lookback or series[-1 - lookback] in (None, 0):
        return None
    return round((series[-1] / series[-1 - lookback] - 1.0) * 100.0, 2)


def _compute_signals(hist: list[dict[str, Any]], index_hist: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [r["close"] for r in hist if r["close"] is not None]
    vols = [r["volume_shares"] for r in hist if r["volume_shares"] is not None]
    if len(closes) < 25:
        return {"available": False, "reason": f"only {len(closes)} closes"}
    idx_closes = [r["close"] for r in index_hist if r["close"] is not None]

    def ma(series: list[float], n: int) -> float | None:
        return round(sum(series[-n:]) / n, 2) if len(series) >= n else None

    ma20, ma60 = ma(closes, 20), ma(closes, 60)
    last = closes[-1]
    r20 = _pct_return(closes, 20)
    idx_r20 = _pct_return(idx_closes, 20) if len(idx_closes) >= 21 else None
    daily_rets = [
        (closes[i] / closes[i - 1] - 1.0)
        for i in range(1, len(closes)) if closes[i - 1]
    ][-20:]
    vol_ann = None
    if len(daily_rets) >= 10:
        mean = sum(daily_rets) / len(daily_rets)
        var = sum((x - mean) ** 2 for x in daily_rets) / (len(daily_rets) - 1)
        vol_ann = round((var ** 0.5) * (252 ** 0.5) * 100, 1)
    avg_vol20 = sum(vols[-20:]) / min(len(vols), 20) if vols else None
    return {
        "available": True,
        "as_of": hist[-1]["date"],
        "return_5d_pct": _pct_return(closes, 5),
        "return_20d_pct": r20,
        "return_60d_pct": _pct_return(closes, 60),
        "rel_strength_20d_pct": None if (r20 is None or idx_r20 is None) else round(r20 - idx_r20, 2),
        "vs_ma20_pct": None if ma20 in (None, 0) else round((last / ma20 - 1) * 100, 2),
        "vs_ma60_pct": None if ma60 in (None, 0) else round((last / ma60 - 1) * 100, 2),
        "volume_ratio_vs_20d": None if not avg_vol20 else round(vols[-1] / avg_vol20, 2),
        "realized_vol_20d_annual_pct": vol_ann,
    }


def _liquidity_from_history(history: list[dict[str, Any]], window: int) -> dict[str, Any]:
    tail = history[-window:]
    turnovers = [row["value_twd"] for row in tail if row["value_twd"] is not None]
    if not turnovers:
        return {"available": False, "reason": "history contained no turnover values"}
    return {
        "available": True,
        "window_trading_days": len(tail),
        "as_of": tail[-1]["date"],
        "median_turnover_twd": int(median(turnovers)),
        "min_turnover_twd": min(turnovers),
        "sessions_below_20m": sum(1 for value in turnovers if value < 20_000_000),
    }


def _tpex_quotes(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _official_snapshot(
    watchlist: list[str], history_days: int
) -> dict[str, Any]:
    now = _now_taipei()
    d8 = _recent_weekday_yyyymmdd(now)
    roc = _roc_slash(now)

    jobs: dict[str, list[tuple[str, Callable[[str], list[dict[str, Any]]]]]] = {
        "twse_quotes": [
            (TWSE_QUOTES_URL, _p_quotes_openapi),
            (TWSE_QUOTES_RWD, _p_quotes_rwd_csv),
        ],
        "twse_indices": [
            (TWSE_INDEX_URL, _p_indices_openapi),
            (TWSE_INDEX_RWD.format(yyyymmdd=d8), _p_indices_rwd),
        ],
        "twse_market_stats": [
            (TWSE_MARKET_STATS_URL, _p_stats_openapi),
            (TWSE_MARKET_STATS_RWD.format(yyyymmdd=d8), _p_stats_rwd),
        ],
        "twse_valuation": [
            (TWSE_VALUATION_URL, _p_valuation_openapi),
            (TWSE_VALUATION_RWD.format(yyyymmdd=d8), _make_p_valuation_rwd(None)),
        ],
        "twse_holidays": [
            (TWSE_HOLIDAY_URL, _p_holiday_openapi),
            (TWSE_HOLIDAY_RWD.format(roc=roc), _p_holiday_rwd),
        ],
        "tpex_quotes": [
            (TPEX_QUOTES_URL, _tpex_quotes),
        ],
    }

    results: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {executor.submit(_chain, specs): name for name, specs in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                rows, notes = future.result()
                results[name] = rows
                if not rows:
                    errors.append({"source": name, "error": "; ".join(notes) or "no data"})
                elif any(n.startswith("used fallback") for n in notes):
                    errors.append({"source": name, "note": "; ".join(notes)})
            except Exception as exc:
                results[name] = []
                errors.append({"source": name, "error": str(exc)})

    indices, index_warnings = _normalize_indices(results.get("twse_indices", []))
    closed = _holiday_closed_dates(results.get("twse_holidays", []))

    history: dict[str, Any] = {}
    signals: dict[str, Any] = {}
    codes = [c for c in watchlist if COMMON_STOCK_RE.fullmatch(c)][:MAX_HISTORY_CODES]
    if history_days and codes:
        index_hist = _fetch_index_history()
        with ThreadPoolExecutor(max_workers=min(6, len(codes))) as executor:
            futures = {executor.submit(_fetch_stock_history, code): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    rows, err = future.result()
                except Exception as exc:
                    history[code] = {"available": False, "reason": str(exc)}
                    signals[code] = {"available": False, "reason": str(exc)}
                    continue
                if not rows:
                    history[code] = {"available": False, "reason": err or "no rows"}
                    signals[code] = {"available": False, "reason": err or "no rows"}
                else:
                    history[code] = _liquidity_from_history(rows, history_days)
                    signals[code] = _compute_signals(rows, index_hist)

    return {
        "twse_rows": _normalize_twse(results.get("twse_quotes", [])),
        "tpex_rows": _normalize_tpex(results.get("tpex_quotes", [])),
        "indices": indices,
        "index_warnings": index_warnings,
        "market_stats": _normalize_market_stats(results.get("twse_market_stats", [])),
        "valuation": _valuation_lookup(results.get("twse_valuation", [])),
        "closed_dates": closed,
        "liquidity_history": history,
        "signals": signals,
        "errors": errors,
    }


def build_snapshot(
    input_path: Path | None, watchlist: list[str], history_days: int
) -> dict[str, Any]:
    now_tp = _now_taipei()
    errors: list[dict[str, str]] = []
    indices: list[dict[str, Any]] = []
    market_stats: list[dict[str, Any]] = []
    valuation: dict[str, dict[str, Any]] = {}
    liquidity_history: dict[str, Any] = {}
    signals: dict[str, Any] = {}
    closed_dates: set[str] = set()
    warnings: list[str] = []
    sources: list[dict[str, str]] = []

    if input_path is not None:
        raw_rows = _load_uploaded(input_path)
        rows = _normalize_uploaded(raw_rows)
        sources.append({"name": "uploaded_file", "location": str(input_path.resolve())})
    else:
        official = _official_snapshot(watchlist, history_days)
        rows = official["twse_rows"] + official["tpex_rows"]
        indices = official["indices"]
        market_stats = official["market_stats"]
        valuation = official["valuation"]
        liquidity_history = official["liquidity_history"]
        signals = official["signals"]
        closed_dates = official["closed_dates"]
        errors = official["errors"]
        warnings.extend(official["index_warnings"])
        sources.extend(
            [
                {"name": "TWSE daily trading data", "location": TWSE_QUOTES_URL},
                {"name": "TWSE daily indices", "location": TWSE_INDEX_URL},
                {"name": "TWSE market statistics (FMTQIK)", "location": TWSE_MARKET_STATS_URL},
                {"name": "TWSE valuation ratios (BWIBBU_ALL)", "location": TWSE_VALUATION_URL},
                {"name": "TWSE holiday schedule", "location": TWSE_HOLIDAY_URL},
                {"name": "TPEx closing quotes", "location": TPEX_QUOTES_URL},
            ]
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["market"], []).append(row)
    markets = {market: _summarize_market(market_rows) for market, market_rows in sorted(grouped.items())}
    market_dates = sorted({s["data_date"] for s in markets.values() if s["data_date"]})

    # --- freshness gate -------------------------------------------------------
    expected = _expected_last_trading_day(now_tp, closed_dates) if input_path is None else None
    source_dates = {market: summary["data_date"] for market, summary in markets.items()}
    stats_through = market_stats[-1]["date"] if market_stats else None
    freshness: dict[str, Any] = {
        "checked_at": now_tp.isoformat(timespec="seconds"),
        "expected_last_trading_day": expected,
        "source_dates": source_dates,
        "twse_aggregate_published_through": stats_through,
        "stale": False,
        "notes": [],
    }
    if input_path is None:
        for market, data_date in source_dates.items():
            gap = _trading_day_gap(data_date, expected, closed_dates)
            if gap and gap >= 1:
                freshness["stale"] = True
                freshness["notes"].append(
                    f"{market} close is {gap} trading day(s) behind the expected "
                    f"last session ({data_date} vs {expected})."
                )
        if len(market_dates) > 1:
            freshness["notes"].append(
                "TWSE and TPEx close dates differ; do not aggregate cross-market breadth or turnover."
            )

    for market, summary in markets.items():
        if market in {"TWSE", "TPEX"} and summary["common_stocks"] < 200:
            warnings.append(f"{market} has fewer than 200 four-digit common-stock records.")
    if len(market_dates) > 1:
        warnings.append("Market source dates differ; do not aggregate cross-market statistics.")
    if not rows:
        warnings.append("No market rows were available from the selected sources.")
    if freshness["stale"]:
        warnings.append(
            "Snapshot is stale versus the trading calendar; treat as prior-session data "
            "and disclose the lag (see references/research-method.md)."
        )

    # --- watchlist enrichment ----------------------------------------------
    matches = []
    for row in rows:
        if row["code"] not in watchlist:
            continue
        enriched = dict(row)
        if row["code"] in valuation:
            enriched["valuation"] = valuation[row["code"]]
        if row["code"] in liquidity_history:
            enriched["liquidity_history"] = liquidity_history[row["code"]]
        if row["code"] in signals:
            enriched["signals"] = signals[row["code"]]
        matches.append(enriched)
    unmatched = sorted(set(watchlist) - {row["code"] for row in matches})

    hard_errors = [e for e in errors if e.get("error")]
    status = "ok"
    if not markets:
        status = "empty"
    elif freshness["stale"] or hard_errors:
        status = "degraded"

    return {
        "schema_version": 2,
        "generated_at": now_tp.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "input_mode": "uploaded_file" if input_path else "official_latest",
        "status": status,
        "as_of_date": market_dates[-1] if market_dates else None,
        "sources": sources,
        "freshness": freshness,
        "quality": {
            "date_consistent": len(market_dates) <= 1,
            "market_dates": market_dates,
            "warnings": warnings,
        },
        "errors": errors,
        "indices": indices,
        "market_aggregate_recent": market_stats,
        "markets": markets,
        "watchlist": {
            "requested": watchlist,
            "history_days": history_days,
            "matches": matches,
            "unmatched": unmatched,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, help="Optional CSV, JSON, or XLSX file")
    parser.add_argument("--watchlist", default="", help="Comma-separated security codes")
    parser.add_argument(
        "--history-days",
        type=int,
        default=DEFAULT_HISTORY_DAYS,
        help=(
            "Trailing trading-day window for watchlist liquidity history "
            f"(default {DEFAULT_HISTORY_DAYS}; 0 disables the extra fetches)"
        ),
    )
    parser.add_argument("--output", default="-", help="Output JSON path, or - for stdout")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    watchlist = [item.strip() for item in args.watchlist.split(",") if item.strip()]
    history_days = max(0, args.history_days)
    try:
        snapshot = build_snapshot(args.input, watchlist, history_days)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    text = json.dumps(
        snapshot,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.output == "-":
        print(text)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")

    if not snapshot["markets"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
