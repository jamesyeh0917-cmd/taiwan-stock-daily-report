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


_FRED_YOY = {"us_cpi": "美國 CPI", "us_core_cpi": "美國核心 CPI", "us_pce_core": "美國核心 PCE",
             "euro_hicp": "歐元區 HICP"}
_FRED_LEVEL = {"us_unemployment": "美國失業率", "us_fed_funds": "聯邦資金利率",
               "ust_2y": "美國 2Y 殖利率", "ust_10y": "美國 10Y 殖利率",
               "ust_10y_breakeven": "10Y 損益兩平通膨", "brent": "Brent 原油", "wti": "WTI 原油",
               "twd_usd": "USD/TWD", "cny_usd": "CNY/USD", "jpy_usd": "JPY/USD"}


def _snapshot_figures(market, macro, fundamentals) -> list[dict]:
    """Every checkable figure: {names:[...aliases], value, tol, source}."""
    figs: list[dict] = []

    def add(names, value, tol, source):
        if value is not None:
            figs.append({"names": [n for n in names if n], "value": float(value), "tol": tol, "source": source})

    if market:
        for idx in market.get("indices", []):
            add([idx.get("name"), "加權指數" if "加權" in (idx.get("name") or "") else None,
                 "台灣50" if "50" in (idx.get("name") or "") else None], idx.get("close"), 0.003, "指數")
        agg = market.get("market_aggregate_recent") or []
        if agg:
            add(["加權指數", "加權指數收盤", "TAIEX", "發行量加權"], agg[-1].get("taiex_close"), 0.003, "FMTQIK")
        for m in market.get("watchlist", {}).get("matches", []):
            code, name = m.get("code"), m.get("name")
            add([f"{code} 收盤", f"{name} 收盤", f"{name}股價"], m.get("close"), 0.01, f"{code} 收盤")
            v = m.get("valuation") or {}
            add([f"{code} PER", f"{name} PER", f"{name} 本益比", f"{code} 本益比"], v.get("pe_ratio"), 0.06, f"{code} 當日PER")
            add([f"{code} PBR", f"{name} PBR", f"{name} 股價淨值比"], v.get("pb_ratio"), 0.06, f"{code} PBR")

    if macro:
        for r in (macro.get("fred", {}) or {}).get("series", []):
            k = r.get("key")
            if k in _FRED_YOY and r.get("yoy_pct") is not None:
                add([_FRED_YOY[k], f"{_FRED_YOY[k]}年增", f"{_FRED_YOY[k]} YoY"], r["yoy_pct"], 0.12, f"FRED {k}")
            if k in _FRED_LEVEL and r.get("actual") is not None:
                add([_FRED_LEVEL[k]], r["actual"], 0.03, f"FRED {k}")
        t = (macro.get("us_treasury") or {}).get("curve_pct") or {}
        add(["美國 10Y 殖利率", "10Y 公債殖利率"], t.get("10y"), 0.03, "美財政部")
        add(["美國 2Y 殖利率"], t.get("2y"), 0.03, "美財政部")

    if fundamentals:
        for code, d in (fundamentals.get("codes") or {}).items():
            vh = d.get("valuation_history") or {}
            add([f"{code} PER", f"{code} 本益比"], vh.get("per"), 0.05, f"{code} FinMind PER")
            add([f"{code} PER 分位", f"{code} 本益比分位"], vh.get("per_1y_percentile"), 0.10, f"{code} PER分位")
            mr = d.get("month_revenue") or {}
            add([f"{code} 月營收", f"{code} 營收年增", f"{code} YoY"], mr.get("yoy_pct"), 0.15, f"{code} 月營收YoY")
    return figs


def _lines_mentioning(md: str, names: list[str]) -> list[str]:
    hits = []
    for line in md.splitlines():
        if any(n and n in line for n in names):
            hits.append(line)
    return hits


def _nums_in(text: str) -> list[float]:
    out = []
    for m in re.finditer(r"[-+]?\d[\d,]*\.?\d*", text):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def _figure_issue(md: str, fig: dict) -> str | None:
    """If the report mentions this figure's name but no nearby number, warn."""
    lines = _lines_mentioning(md, fig["names"])
    if not lines:
        return None  # not mentioned — fine, not every figure must appear
    v, tol = fig["value"], fig["tol"]
    lo, hi = v * (1 - tol) - abs(v) * 0.002 - 0.01, v * (1 + tol) + abs(v) * 0.002 + 0.01
    for ln in lines:
        for n in _nums_in(ln):
            if lo <= n <= hi:
                return None  # found a consistent number
    return (f"「{fig['names'][0]}」在報告中被提及，但鄰近沒有與快照一致的數字"
            f"（快照 {fig['source']} ≈ {round(v, 3)}）— 可能寫錯或幻覺")


def check(md: str, market: dict | None, macro: dict | None, mode: str, fundamentals: dict | None = None) -> dict:
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

    # number cross-check: every figure the report cites must trace to a snapshot
    figs = _snapshot_figures(market, macro, fundamentals)
    mismatches = [m for m in (_figure_issue(md, f) for f in figs) if m]
    for msg in mismatches[:12]:
        add("warn", msg)
    if len(mismatches) > 12:
        add("warn", f"另有 {len(mismatches) - 12} 個數字與快照對不上（略）")

    # 資料基準日 consistency
    if market and market.get("as_of_date"):
        m = re.search(r"資料基準日[：:\s|]*([\d]{4}-[\d]{2}-[\d]{2})", md)
        if m and m.group(1) != market["as_of_date"] and not market.get("freshness", {}).get("stale"):
            add("warn", f"報告資料基準日 {m.group(1)} != 行情快照 as_of {market['as_of_date']}")

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
    ap.add_argument("--fundamentals", help="path to fundamentals.json (optional cross-check)")
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

    result = check(md, _load(args.market), _load(args.macro), args.mode, _load(args.fundamentals))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return {"pass": 0, "warn": 1, "fail": 2}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
