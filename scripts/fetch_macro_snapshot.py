#!/usr/bin/env python3
"""Fetch a verified macro snapshot: FRED series + US Treasury yield curve.

This replaces ad-hoc web search for the data that has a clean, timestamped,
revision-aware source. Taiwan / China / Japan macro that FRED no longer keeps
current is left to the WebFetch playbook in references/macro-fetch.md.

FRED needs a free API key: https://fred.stlouisfed.org/docs/api/api_key.html
Pass it as --fred-key or the FRED_API_KEY environment variable. Without a key
the script still emits the US Treasury curve and an empty, clearly-flagged
FRED section so the report can degrade rather than fail.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

FRED_BASE = "https://api.stlouisfed.org/fred"
TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&_format=csv"
)

# kind: "index" -> level series, compute YoY vs same period last year.
#       "rate"  -> percent series, report level + change vs prev / ~1m / ~1y.
FRED_SERIES: list[dict[str, Any]] = [
    {"key": "us_cpi", "id": "CPIAUCSL", "region": "US", "indicator": "CPI headline (SA)", "kind": "index", "tier": "A"},
    {"key": "us_core_cpi", "id": "CPILFESL", "region": "US", "indicator": "CPI core ex food & energy (SA)", "kind": "index", "tier": "A"},
    {"key": "us_pce_core", "id": "PCEPILFE", "region": "US", "indicator": "PCE core price index (SA)", "kind": "index", "tier": "A"},
    {"key": "us_payrolls", "id": "PAYEMS", "region": "US", "indicator": "Nonfarm payrolls (level, thousands)", "kind": "index", "tier": "A"},
    {"key": "us_unemployment", "id": "UNRATE", "region": "US", "indicator": "Unemployment rate", "kind": "rate", "tier": "A"},
    {"key": "us_fed_funds", "id": "FEDFUNDS", "region": "US", "indicator": "Effective fed funds rate (monthly avg)", "kind": "rate", "tier": "A"},
    {"key": "ust_2y", "id": "DGS2", "region": "US", "indicator": "2Y Treasury yield", "kind": "rate", "tier": "A"},
    {"key": "ust_10y", "id": "DGS10", "region": "US", "indicator": "10Y Treasury yield", "kind": "rate", "tier": "A"},
    {"key": "ust_10y_breakeven", "id": "T10YIE", "region": "US", "indicator": "10Y breakeven inflation", "kind": "rate", "tier": "A"},
    {"key": "brent", "id": "DCOILBRENTEU", "region": "Global", "indicator": "Brent crude (USD/bbl)", "kind": "rate", "tier": "A"},
    {"key": "wti", "id": "DCOILWTICO", "region": "Global", "indicator": "WTI crude (USD/bbl)", "kind": "rate", "tier": "A"},
    {"key": "euro_hicp", "id": "CP0000EZ19M086NEST", "region": "Euro Area", "indicator": "HICP all-items", "kind": "index", "tier": "C"},
    {"key": "twd_usd", "id": "DEXTAUS", "region": "Taiwan", "indicator": "TWD per USD (FRED, spot)", "kind": "rate", "tier": "A"},
    {"key": "cny_usd", "id": "DEXCHUS", "region": "China", "indicator": "CNY per USD", "kind": "rate", "tier": "A"},
    {"key": "jpy_usd", "id": "DEXJPUS", "region": "Japan", "indicator": "JPY per USD", "kind": "rate", "tier": "A"},
]


def _load_local_env() -> None:
    """Best-effort: read scripts/.env (KEY=VALUE lines) into os.environ if unset.

    Lets local runs pick up FRED_API_KEY without exporting it every shell.
    Scheduled/cloud runs should use a real environment variable or secret.
    """
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_local_env()


def _now_taipei() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Taipei"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8), name="Asia/Taipei"))


def _ctx() -> ssl.SSLContext:
    context = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        context.verify_flags &= ~strict
    return context


_SSL = _ctx()


def _get(url: str, timeout: int = 20) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (taiwan-stock-daily-report/2.0)"})
    with urlopen(request, timeout=timeout, context=_SSL) as response:
        return response.read()


def _num(value: Any) -> float | None:
    if value in (None, "", ".", "N/A"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new / old - 1.0) * 100.0, 3)


def _fred_call(path: str, key: str, **params: str) -> Any:
    params.update({"api_key": key, "file_type": "json"})
    return json.loads(_get(f"{FRED_BASE}/{path}?{urlencode(params)}"))


def _fetch_series(spec: dict[str, Any], key: str) -> dict[str, Any]:
    limit = "16" if spec["kind"] == "index" else "400"
    obs_raw = _fred_call(
        "series/observations", key, series_id=spec["id"],
        sort_order="desc", limit=limit,
    ).get("observations", [])
    points = [(o["date"], _num(o["value"])) for o in obs_raw]
    points = [(d, v) for d, v in points if v is not None]
    if not points:
        raise ValueError("no non-missing observations")

    meta = _fred_call("series", key, series_id=spec["id"]).get("seriess", [{}])[0]

    latest_date, latest = points[0]
    prev = points[1][1] if len(points) > 1 else None
    record: dict[str, Any] = {
        "key": spec["key"],
        "region": spec["region"],
        "indicator": spec["indicator"],
        "series_id": spec["id"],
        "observation_period": latest_date,
        "actual": latest,
        "previous": prev,
        "unit": meta.get("units_short") or meta.get("units"),
        "fred_last_updated": meta.get("last_updated"),
        "fred_title": meta.get("title"),
        "source_url": f"https://fred.stlouisfed.org/series/{spec['id']}",
        "tier": spec["tier"],
        "warnings": [],
    }

    if spec["kind"] == "index":
        year_ago = next((v for d, v in points if d[:7] != latest_date[:7] and d[:4] == str(int(latest_date[:4]) - 1) and d[5:7] == latest_date[5:7]), None)
        if year_ago is None and len(points) >= 13:
            year_ago = points[12][1]
        record["yoy_pct"] = _pct(latest, year_ago)
        record["mom_pct"] = _pct(latest, prev)
    else:
        by_date = {d: v for d, v in points}
        target_1m = (datetime.fromisoformat(latest_date) - timedelta(days=30)).date()
        target_1y = (datetime.fromisoformat(latest_date) - timedelta(days=365)).date()
        record["change_vs_prev"] = round(latest - prev, 3) if prev is not None else None
        record["value_1m_ago"] = _closest(points, target_1m.isoformat())
        record["value_1y_ago"] = _closest(points, target_1y.isoformat())

    # staleness of the underlying release
    if meta.get("last_updated"):
        try:
            updated = datetime.fromisoformat(meta["last_updated"].replace(" ", "T").split("-0")[0])
            if (datetime.utcnow() - updated).days > 45:
                record["warnings"].append(f"FRED series last updated {meta['last_updated']}; may be discontinued or delayed.")
        except Exception:
            pass
    return record


def _closest(points: list[tuple[str, float]], target_iso: str) -> float | None:
    best = None
    best_gap = None
    for d, v in points:
        gap = abs((datetime.fromisoformat(d) - datetime.fromisoformat(target_iso)).days)
        if best_gap is None or gap < best_gap:
            best, best_gap = v, gap
    return best if best_gap is not None and best_gap <= 20 else None


def _fetch_fred(key: str) -> dict[str, Any]:
    series: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_series, spec, key): spec for spec in FRED_SERIES}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                series.append(future.result())
            except Exception as exc:
                errors.append({"series_id": spec["id"], "key": spec["key"], "error": str(exc)})
    series.sort(key=lambda r: (r["region"], r["key"]))
    return {"available": True, "series": series, "errors": errors}


def _fetch_treasury() -> dict[str, Any]:
    year = _now_taipei().year
    text = _get(TREASURY_CSV.format(year=year), timeout=20).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty Treasury CSV")
    latest = rows[0]  # Treasury CSV is newest-first
    wanted = {"1 Mo": "1m", "3 Mo": "3m", "6 Mo": "6m", "1 Yr": "1y", "2 Yr": "2y",
              "3 Yr": "3y", "5 Yr": "5y", "7 Yr": "7y", "10 Yr": "10y", "20 Yr": "20y", "30 Yr": "30y"}
    curve = {short: _num(latest.get(col)) for col, short in wanted.items()}
    prev = rows[1] if len(rows) > 1 else {}
    spread_10y_2y = None
    if curve.get("10y") is not None and curve.get("2y") is not None:
        spread_10y_2y = round(curve["10y"] - curve["2y"], 2)
    return {
        "date": latest.get("Date"),
        "prev_date": prev.get("Date"),
        "curve_pct": curve,
        "spread_10y_2y_bp": None if spread_10y_2y is None else round(spread_10y_2y * 100),
        "prev_10y": _num(prev.get("10 Yr")),
        "source_url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve",
        "tier": "A",
    }


def build(fred_key: str | None) -> dict[str, Any]:
    now = _now_taipei()
    errors: list[dict[str, str]] = []
    notes: list[str] = [
        "Taiwan / China / Japan CPI, GDP, exports, PMI and policy-rate decisions are NOT in this "
        "snapshot; fetch them via references/macro-fetch.md (official press releases).",
    ]

    if fred_key:
        try:
            fred = _fetch_fred(fred_key)
        except Exception as exc:
            fred = {"available": False, "reason": f"FRED fetch failed: {exc}"}
            errors.append({"source": "fred", "error": str(exc)})
    else:
        fred = {"available": False, "reason": "no FRED_API_KEY provided; US yields/CPI/oil and Euro HICP unavailable from this script"}
        notes.append("Running without a FRED key: get one free at https://fred.stlouisfed.org/docs/api/api_key.html")

    try:
        treasury = _fetch_treasury()
    except Exception as exc:
        treasury = {"available": False, "reason": str(exc)}
        errors.append({"source": "us_treasury", "error": str(exc)})

    if fred.get("errors"):
        for e in fred["errors"]:
            notes.append(f"FRED series {e['series_id']} ({e['key']}) failed: {e['error']} — cover via macro-fetch.md if material.")

    status = "ok"
    if not fred.get("available") or not treasury.get("date"):
        status = "degraded"

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "status": status,
        "fred": fred,
        "us_treasury": treasury,
        "release_calendar_hint": "See references/macro-fetch.md for the monthly release schedule and consensus-forecast fetch targets.",
        "errors": errors,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fred-key", default=os.environ.get("FRED_API_KEY"), help="FRED API key (or set FRED_API_KEY)")
    parser.add_argument("--output", default="-", help="Output JSON path, or - for stdout")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        snapshot = build(args.fred_key)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    text = json.dumps(snapshot, ensure_ascii=False, indent=None if args.compact else 2)
    if args.output == "-":
        print(text)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if snapshot["status"] != "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
