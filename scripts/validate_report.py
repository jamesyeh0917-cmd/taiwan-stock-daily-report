#!/usr/bin/env python3
"""Post-generation QA on the report Markdown before it is published.

Mechanical checks only — it does not judge the analysis, it checks that
the report obeys report-contract.md: sections present, scenario
probabilities sum to 100, candidate rows filled, no bare URLs outside the
sources section, dates consistent, disclaimer present.

Exit code 0 = pass, 1 = warnings, 2 = fail (blocking issues).
Prints a JSON verdict. The daily run must run this on its draft and:
  * fail  -> fix, or publish with 複核狀態 = 有疑慮 and flag in the Discord digest
  * warn  -> publish, note the warnings in §14
  * pass  -> publish, 複核狀態 = 待複核

Usage:
  python scripts/validate_report.py --report draft.md [--market market.json] [--mode full|light]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_FULL = [
    "關鍵數字看板", "本期最重要", "報告識別", "執行摘要", "與前一份",
    "證據帳本", "台灣總經", "全球總經", "政策分析", "新聞解讀",
    "跨資產", "三情境", "題材排名", "個股篩選", "未入選",
    "未來事件", "可執行觀察清單", "反證", "資料品質", "結論",
]
REQUIRED_LIGHT = ["關鍵數字看板", "本期最重要", "報告識別", "與前一份", "未來事件"]
STATUS_VALUES = {"完整", "部分", "資料不足"}
CALL_STATUS = {"PRIORITY_RESEARCH", "WATCH", "ABSTAIN"}


def _tables(md: str) -> list[list[str]]:
    """Return each markdown table as a list of row-strings."""
    tables, cur = [], []
    for line in md.splitlines():
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cur.append(line.strip())
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def _find_scenario_probs(md: str) -> list[float]:
    probs: list[float] = []
    for tbl in _tables(md):
        header = tbl[0]
        if "情境" in header and ("機率" in header or "機率" in "".join(tbl[:2])):
            for row in tbl[2:]:
                cells = [c.strip() for c in row.strip("|").split("|")]
                for c in cells:
                    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%?", c)
                    if m and ("%" in c or "." in c or c.replace("%", "").strip().isdigit()):
                        v = float(m.group(1))
                        if 0 <= v <= 100:
                            probs.append(v)
                            break
    return probs


def _key_figures(market: dict | None, macro: dict | None) -> list[tuple[str, float | None, float]]:
    """(label, value, relative tolerance) triples the report should reflect."""
    out: list[tuple[str, float | None, float]] = []
    if market:
        for idx in market.get("indices", []):
            if idx.get("name") == "發行量加權股價指數" and idx.get("close"):
                out.append(("加權指數", round(idx["close"], 2), 0.002))
        agg = market.get("market_aggregate_recent") or []
        if agg and agg[-1].get("taiex_close"):
            out.append(("加權指數收盤", round(agg[-1]["taiex_close"], 2), 0.002))
    if macro:
        for r in (macro.get("fred", {}) or {}).get("series", []):
            k = r.get("key")
            if k == "twd_usd" and r.get("actual"):
                out.append(("USD/TWD", round(r["actual"], 2), 0.01))
            elif k == "ust_10y" and r.get("actual"):
                out.append(("美國 10Y 公債殖利率", round(r["actual"], 2), 0.03))
            elif k == "brent" and r.get("actual"):
                out.append(("Brent 原油", round(r["actual"], 1), 0.03))
            elif k == "us_cpi" and r.get("yoy_pct"):
                out.append(("美國 CPI 年增", round(r["yoy_pct"], 1), 0.15))
        t = macro.get("us_treasury") or {}
        if t.get("curve_pct", {}).get("10y"):
            out.append(("10Y 殖利率(財政部)", round(t["curve_pct"]["10y"], 2), 0.03))
    return out


def _report_has_number_near(md: str, label: str, value: float, tol: float) -> bool:
    """True if any number in the report is within tol of value (relative)."""
    lo, hi = value * (1 - tol) - 0.01, value * (1 + tol) + 0.01
    for m in re.finditer(r"[-+]?\d[\d,]*\.?\d*", md):
        try:
            n = float(m.group(0).replace(",", ""))
        except ValueError:
            continue
        if lo <= n <= hi:
            return True
    return False


def check(md: str, market: dict | None, macro: dict | None, mode: str) -> dict:
    issues: list[dict] = []

    def add(sev: str, msg: str) -> None:
        issues.append({"severity": sev, "message": msg})

    required = REQUIRED_LIGHT if mode == "light" else REQUIRED_FULL
    for token in required:
        if token not in md:
            add("fail", f"缺少必要章節/關鍵字：{token}")

    if "自動產生" not in md or "未複核" not in md:
        add("fail", "開頭未標「自動產生‧未複核」")

    # scenario probabilities sum to 100 (full mode)
    if mode == "full":
        probs = _find_scenario_probs(md)
        if len(probs) < 3:
            add("warn", f"三情境機率解析到 {len(probs)} 個（預期 3）；請人工確認 §9")
        else:
            total = sum(probs[:3])
            if abs(total - 100) > 0.5:
                add("fail", f"三情境機率合計 {total}（應為 100）")

    # status value
    m = re.search(r"(?:報告狀態|狀態)[：:\s|]+([完整部分資料不足]{2,4})", md)
    if m and m.group(1) not in STATUS_VALUES:
        add("warn", f"報告狀態值「{m.group(1)}」不在 {STATUS_VALUES}")

    # candidate table rows filled (full mode)
    if mode == "full":
        cand_tbls = [t for t in _tables(md) if "代號" in t[0] and ("淨分" in t[0] or "狀態" in t[0])]
        if not cand_tbls:
            add("warn", "找不到 §11 個股篩選表")
        for tbl in cand_tbls:
            for row in tbl[2:]:
                cells = [c.strip() for c in row.strip("|").split("|")]
                if len(cells) < 5:
                    continue
                empty = sum(1 for c in cells if c in ("", "-", "—"))
                if empty > len(cells) * 0.4:
                    add("warn", f"候選股列多數欄位空白：{row[:60]}")
                if not any(s in row for s in CALL_STATUS):
                    add("fail", f"候選股列缺合法狀態值：{row[:60]}")

    # bare URLs outside the last section
    body, _, tail = md.rpartition("資料品質")
    scan = body or md
    bare = re.findall(r"(?<![(\[`])\bhttps?://[^\s)\]]+", scan)
    bare = [u for u in bare if not re.search(r"\]\(" + re.escape(u), md)]
    if bare:
        add("warn", f"正文有 {len(bare)} 個裸網址（應內嵌超連結，來源清單除外）：{bare[0][:60]}")

    # date consistency vs market snapshot
    if market:
        as_of = market.get("as_of_date")
        if as_of and as_of not in md:
            add("warn", f"報告未出現行情快照 as_of_date {as_of}")
        if market.get("freshness", {}).get("stale") and "落後" not in md and "前一交易日" not in md and "前 " not in md:
            add("fail", "行情快照 stale=true 但報告未揭露落後")
        if market.get("status") == "degraded" and "degraded" not in md and "降級" not in md and "部分" not in md:
            add("warn", "market snapshot status=degraded 但報告未反映")

    # number cross-check: key figures the report states must trace to a snapshot
    for label, value, tol in _key_figures(market, macro):
        if value is None:
            continue
        if not _report_has_number_near(md, label, value, tol):
            add("warn", f"報告未見與快照一致的「{label}」（快照值 ≈ {value}）；可能漏引或寫錯")

    # exec summary bullet count (full)
    if mode == "full":
        seg = re.search(r"執行摘要(.+?)(?:\n#{1,3}\s|\Z)", md, re.S)
        if seg:
            bullets = len(re.findall(r"^\s*[-*]\s+\S", seg.group(1), re.M))
            if bullets and not (5 <= bullets <= 12):
                add("warn", f"執行摘要 {bullets} 點（建議 6–10）")

    fails = [i for i in issues if i["severity"] == "fail"]
    warns = [i for i in issues if i["severity"] == "warn"]
    verdict = "fail" if fails else ("warn" if warns else "pass")
    return {
        "verdict": verdict,
        "mode": mode,
        "fail_count": len(fails),
        "warn_count": len(warns),
        "issues": issues,
        "review_status_suggestion": {"pass": "待複核", "warn": "待複核", "fail": "有疑慮"}[verdict],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", required=True, help="path to the draft report markdown")
    ap.add_argument("--market", help="path to market.json (optional cross-check)")
    ap.add_argument("--macro", help="path to macro.json (optional cross-check)")
    ap.add_argument("--mode", choices=["full", "light"], default="full")
    ap.add_argument("--output", default="-")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    md = Path(args.report).read_text(encoding="utf-8")

    def _load(p):
        if p and Path(p).exists():
            try:
                return json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    result = check(md, _load(args.market), _load(args.macro), args.mode)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return {"pass": 0, "warn": 1, "fail": 2}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
