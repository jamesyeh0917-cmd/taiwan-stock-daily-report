#!/usr/bin/env python3
"""Independent daily health check for the report pipeline.

Run by a SEPARATE routine a couple of hours after the main one. It does not
regenerate anything — it just verifies the main run produced a healthy
result and pushes a Discord alert if not. This covers the blind spot where
the main run dies before it can send its own alert.

Checks (the calling agent supplies the facts it gathered from Notion):
  * a report page exists for the expected 資料基準日
  * its 狀態 is not 資料不足
  * it was created within the last ~24h
  * the evidence-ledger and candidate sub-DBs got rows for that 報告日
  * (optional) market/macro snapshots ran clean

Usage:
  python scripts/healthcheck.py --facts facts.json --output verdict.json
where facts.json is:
  {"expected_base_date":"2026-09-09",
   "latest_report":{"base_date":"2026-09-09","status":"部分","created":"2026-09-10T07:12:00Z","url":"..."},
   "evidence_rows_for_date": 14, "candidate_rows_for_date": 9,
   "market_status":"ok", "macro_status":"ok"}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


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


def _discord(msg: str) -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return "no webhook"
    url = url.replace("://discordapp.com/", "://discord.com/")
    body = json.dumps({"content": f"🔴 **台股每日研究 — 健康檢查異常**\n{msg}"}).encode("utf-8")
    req = Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": "taiwan-stock-daily-report (https://github.com/, 2.1)",
    })
    try:
        with urlopen(req, timeout=20) as r:
            return "sent" if r.status in (200, 204) else f"status {r.status}"
    except Exception as exc:
        return f"failed: {exc}"


def evaluate(facts: dict) -> dict:
    problems: list[str] = []
    exp = facts.get("expected_base_date")
    rep = facts.get("latest_report") or {}

    if not rep:
        problems.append("找不到任何報告頁")
    else:
        if exp and rep.get("base_date") != exp:
            problems.append(f"最新報告資料基準日 {rep.get('base_date')} != 預期 {exp}（今天的報告可能沒產出）")
        if rep.get("status") == "資料不足":
            problems.append("最新報告狀態 = 資料不足")
        created = rep.get("created")
        if created:
            try:
                c = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - c > timedelta(hours=26):
                    problems.append(f"最新報告建立於 {created}，超過 26 小時 — 今天可能沒跑")
            except Exception:
                pass

    if facts.get("mode", "full") == "full" and facts.get("evidence_rows_for_date", 0) < 5:
        problems.append(f"證據帳本本日僅 {facts.get('evidence_rows_for_date', 0)} 列（full 模式預期 ≥5）")
    if facts.get("candidate_rows_for_date", 0) < 3 and facts.get("mode", "full") == "full":
        problems.append(f"候選股追蹤本日僅 {facts.get('candidate_rows_for_date', 0)} 列（full 模式預期 ≥3）")
    for k in ("market_status", "macro_status"):
        if facts.get(k) not in (None, "ok", "degraded"):
            problems.append(f"{k} = {facts.get(k)}")

    ok = not problems
    verdict = {"ok": ok, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "problems": problems, "facts": facts}
    if not ok:
        verdict["discord"] = _discord("；".join(problems) + f"\n最新報告：{rep.get('url', 'N/A')}")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts", required=True)
    ap.add_argument("--output", default="-")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    result = evaluate(facts)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
