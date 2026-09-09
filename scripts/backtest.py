#!/usr/bin/env python3
"""Score past research calls against realised forward returns.

Input: a JSON array of prior calls, each
  {"code": "2454", "date": "2026-06-10", "status": "WATCH",
   "net_score": 68, "entry_close": 1234.0, "theme": "AI 伺服器"}
(the daily run exports this from the 候選股追蹤 Notion database — rows whose
報告日 is old enough to have a forward window.)

For each call, fetch the close series from FinMind and compute forward
returns at 5 / 20 / 60 trading days, plus abnormal return vs TAIEX. Then
aggregate hit-rate and mean forward return by status bucket and by
net-score bucket, and emit a coarse weight-adjustment suggestion.

This is calibration input for a human, not an optimiser. Run weekly.

Usage:
  python scripts/backtest.py --calls calls.json --output backtest.json
  python scripts/backtest.py --calls calls.json --horizon 20
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
from statistics import mean
from urllib.request import Request, urlopen

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
HORIZONS = (5, 20, 60)


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


_PRICE_CACHE: dict[str, list[tuple[str, float]]] = {}


def _prices(code: str, start: str) -> list[tuple[str, float]]:
    if code in _PRICE_CACHE:
        return _PRICE_CACHE[code]
    url = f"{FINMIND_BASE}?dataset=TaiwanStockPrice&data_id={code}&start_date={start}"
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
            rows = sorted((r["date"], float(r["close"])) for r in payload.get("data", []) if r.get("close"))
            _PRICE_CACHE[code] = rows
            return rows
        except Exception as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise last if last else RuntimeError("finmind failed")


def _fwd_return(series: list[tuple[str, float]], call_date: str, horizon: int) -> float | None:
    dates = [d for d, _ in series]
    closes = [c for _, c in series]
    i0 = next((i for i, d in enumerate(dates) if d >= call_date), None)
    if i0 is None or i0 + horizon >= len(closes) or closes[i0] == 0:
        return None
    return round((closes[i0 + horizon] / closes[i0] - 1.0) * 100.0, 2)


def _score_bucket(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if s >= 75:
        return "75+"
    if s >= 60:
        return "60-74"
    return "<60"


def evaluate(calls: list[dict]) -> dict:
    earliest = min((c.get("date", "9999") for c in calls), default=None)
    start = (date.fromisoformat(earliest) - timedelta(days=10)).isoformat() if earliest else "2026-01-01"
    try:
        index = _prices("TAIEX", start)
    except Exception:
        index = []

    enriched = []
    errors = []
    for c in calls:
        code = str(c.get("code", "")).strip()
        cdate = c.get("date")
        if not code or not cdate:
            continue
        try:
            series = _prices(code, start)
        except Exception as exc:
            errors.append({"code": code, "error": str(exc)})
            continue
        row = {**c}
        for h in HORIZONS:
            r = _fwd_return(series, cdate, h)
            ri = _fwd_return(index, cdate, h) if index else None
            row[f"fwd_{h}d_pct"] = r
            row[f"abn_{h}d_pct"] = None if (r is None or ri is None) else round(r - ri, 2)
        enriched.append(row)

    def agg(rows: list[dict], h: int) -> dict:
        vals = [r[f"fwd_{h}d_pct"] for r in rows if r.get(f"fwd_{h}d_pct") is not None]
        abn = [r[f"abn_{h}d_pct"] for r in rows if r.get(f"abn_{h}d_pct") is not None]
        if not vals:
            return {"n": 0}
        return {
            "n": len(vals),
            "mean_return_pct": round(mean(vals), 2),
            "hit_rate_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100),
            "mean_abnormal_pct": round(mean(abn), 2) if abn else None,
            "beat_index_rate_pct": round(sum(1 for v in abn if v > 0) / len(abn) * 100) if abn else None,
        }

    by_status = {}
    for st in sorted({r.get("status", "?") for r in enriched}):
        rows = [r for r in enriched if r.get("status") == st]
        by_status[st] = {f"{h}d": agg(rows, h) for h in HORIZONS}

    by_score = {}
    for b in ("75+", "60-74", "<60", "unknown"):
        rows = [r for r in enriched if _score_bucket(r.get("net_score")) == b]
        if rows:
            by_score[b] = {f"{h}d": agg(rows, h) for h in HORIZONS}

    return {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "calls_evaluated": len(enriched),
        "price_errors": errors,
        "by_status": by_status,
        "by_score_bucket": by_score,
        "calls": enriched,
        "calibration_notes": _calibration(by_status, by_score),
    }


def _calibration(by_status: dict, by_score: dict) -> list[str]:
    out: list[str] = []
    pr = by_status.get("PRIORITY_RESEARCH", {}).get("20d", {})
    wa = by_status.get("WATCH", {}).get("20d", {})
    ab = by_status.get("ABSTAIN", {}).get("20d", {})
    if pr.get("n", 0) >= 5 and wa.get("n", 0) >= 5:
        if pr.get("mean_abnormal_pct", 0) is not None and wa.get("mean_abnormal_pct") is not None:
            if pr["mean_abnormal_pct"] <= wa["mean_abnormal_pct"]:
                out.append("PRIORITY_RESEARCH 20 日超額報酬未優於 WATCH — 評分未有效區分，考慮提高門檻或重審題材評分權重。")
            else:
                out.append("PRIORITY_RESEARCH 20 日超額報酬優於 WATCH — 排序有效。")
    if ab.get("n", 0) >= 5 and ab.get("mean_abnormal_pct", 0) is not None and ab["mean_abnormal_pct"] > 2:
        out.append("ABSTAIN 標的事後平均超額為正 — 排除規則可能過嚴，檢視硬性排除條件。")
    hi = by_score.get("75+", {}).get("20d", {})
    lo = by_score.get("60-74", {}).get("20d", {})
    if hi.get("n", 0) >= 5 and lo.get("n", 0) >= 5 and hi.get("hit_rate_pct") is not None:
        if hi["hit_rate_pct"] <= lo.get("hit_rate_pct", 0):
            out.append("淨分 75+ 命中率未優於 60–74 — 評分與前瞻報酬相關性弱，需重新校準維度配分。")
    if not out:
        out.append("樣本不足或結果未達可下結論門檻，繼續累積。")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calls", required=True, help="path to calls JSON array")
    ap.add_argument("--output", default="-")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        calls = json.loads(Path(args.calls).read_text(encoding="utf-8"))
        if not isinstance(calls, list):
            raise ValueError("calls file must be a JSON array")
        result = evaluate(calls)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
