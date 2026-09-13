#!/usr/bin/env python3
"""Compute 工具降級 (tools_degraded) and a suggested 報告狀態 from the fetch
scripts' own JSON output, instead of leaving it to the agent to eyeball 4-5
files' status/errors fields from memory each run — the same class of
"rule exists but nobody applied it that day" problem trading_day.py's new
report_date/report_title fields address for dates.

Each fetch script reports degradation its own way (nothing is renamed here —
that would mean touching every fetch script for a cosmetic win):
  - market.json / macro.json: top-level "status" (ok/degraded/empty).
  - news.json / industry_flow.json: no "status" field, only "errors"; treated
    as degraded when errors is non-empty.
  - fundamentals.json: no top-level field at all; degradation is per-code,
    per-dataset ("available": true/false under codes[code][dataset]). Treated
    as degraded when fewer than half of the requested (code, dataset) pairs
    came back available.

Missing files are not degradation in light mode (market/fundamentals/
industry_flow are legitimately skipped) but --mode full flags a missing or
empty market.json as 資料不足 (insufficient), since market data is the
backbone of a full-mode report.

suggested_report_status is a DEFAULT, not a mandate: the agent may still
override it with a stated reason, per report-contract.md §1.

Usage:
  python scripts/summarize_run_status.py --mode full|light
    [--market market.json] [--macro macro.json] [--fundamentals fundamentals.json]
    [--news news.json] [--industry-flow industry_flow.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _check_status_field(name: str, data: dict | None) -> tuple[str | None, list[str]]:
    """market.json / macro.json convention: top-level status + errors."""
    if data is None:
        return None, []
    status = data.get("status")
    errors = data.get("errors") or []
    notes = []
    if status and status != "ok":
        notes.append(f"{name}: status={status}" + (f" ({'; '.join(errors[:3])})" if errors else ""))
    elif errors:
        notes.append(f"{name}: errors={'; '.join(errors[:3])}")
    return status, notes


def _check_errors_only(name: str, data: dict | None) -> list[str]:
    """news.json / industry_flow.json convention: errors list, no status field."""
    if data is None:
        return []
    errors = data.get("errors") or []
    if errors:
        return [f"{name}: errors={'; '.join(errors[:3])}"]
    return []


def _check_fundamentals(data: dict | None) -> list[str]:
    """fundamentals.json convention: per-code, per-dataset "available" flags."""
    if data is None:
        return []
    codes = data.get("codes") or {}
    if not codes:
        return ["fetch_fundamentals: no codes returned"]
    total = 0
    available = 0
    for code, per_code in codes.items():
        if not isinstance(per_code, dict):
            continue
        if "error" in per_code:
            total += 5  # whole-code failure fallback carries no per-dataset detail
            continue
        for key in ("valuation_history", "institutional", "margin_short", "month_revenue", "dividend"):
            ds = per_code.get(key)
            total += 1
            if isinstance(ds, dict) and ds.get("available"):
                available += 1
    if total == 0:
        return ["fetch_fundamentals: no datasets found"]
    ratio = available / total
    if ratio < 0.5:
        return [f"fetch_fundamentals: only {available}/{total} datasets available ({ratio:.0%})"]
    return []


def summarize(mode: str, market: dict | None, macro: dict | None, fundamentals: dict | None,
              news: dict | None, industry_flow: dict | None) -> dict[str, Any]:
    degraded_sources: list[str] = []
    missing_sources: list[str] = []

    market_status, notes = _check_status_field("market_snapshot", market)
    degraded_sources += notes
    macro_status, notes = _check_status_field("macro_snapshot", macro)
    degraded_sources += notes
    degraded_sources += _check_errors_only("fetch_news", news)
    degraded_sources += _check_errors_only("fetch_industry_flow", industry_flow)
    degraded_sources += _check_fundamentals(fundamentals)

    if mode == "full":
        if market is None:
            missing_sources.append("market_snapshot: file missing")
        elif market_status == "empty":
            missing_sources.append("market_snapshot: status=empty")
        if macro is None:
            missing_sources.append("macro_snapshot: file missing")

    market_unusable = mode == "full" and (market is None or market_status == "empty")
    if market_unusable:
        suggested_report_status = "資料不足"
    elif degraded_sources or missing_sources:
        suggested_report_status = "部分"
    else:
        suggested_report_status = "完整"

    return {
        "mode": mode,
        "tools_degraded": bool(degraded_sources or missing_sources),
        "degraded_sources": degraded_sources,
        "missing_sources": missing_sources,
        "suggested_report_status": suggested_report_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["full", "light"], default="full")
    parser.add_argument("--market")
    parser.add_argument("--macro")
    parser.add_argument("--fundamentals")
    parser.add_argument("--news")
    parser.add_argument("--industry-flow")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    result = summarize(
        args.mode,
        _load(args.market),
        _load(args.macro),
        _load(args.fundamentals),
        _load(args.news),
        _load(args.industry_flow),
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
