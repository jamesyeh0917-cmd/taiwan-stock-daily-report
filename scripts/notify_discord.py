#!/usr/bin/env python3
"""Post a short message to a Discord channel via an incoming webhook.

Used by the daily routine for: the condensed digest on a successful run,
skip/light notices on non-trading days, and failure alerts.

Webhook URL comes from --webhook or the DISCORD_WEBHOOK_URL environment
variable (set in the routine prompt). Never commit the URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

MAX_LEN = 1900  # Discord hard limit is 2000 per message; leave headroom.

PREFIX = {
    "digest": "📈 **台股每日研究**",
    "light": "🟡 **台股每日研究（輕量）**",
    "skip": "⚪ **台股每日研究**",
    "alert": "🔴 **台股每日研究 — 執行異常**",
}


def _load_local_env() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


def _chunks(text: str) -> list[str]:
    out: list[str] = []
    for para in text.split("\n"):
        if not out or len(out[-1]) + len(para) + 1 > MAX_LEN:
            out.append(para)
        else:
            out[-1] += "\n" + para
    # hard-split any single oversized chunk
    final: list[str] = []
    for c in out:
        while len(c) > MAX_LEN:
            final.append(c[:MAX_LEN])
            c = c[MAX_LEN:]
        final.append(c)
    return [c for c in final if c.strip()]


def _normalize_webhook(url: str) -> str:
    # The legacy discordapp.com host 403s POSTs from some clients; discord.com works.
    return url.replace("://discordapp.com/", "://discord.com/").replace(
        "://ptb.discord.com/", "://discord.com/"
    )


def post(webhook: str, text: str) -> None:
    webhook = _normalize_webhook(webhook)
    headers = {
        "Content-Type": "application/json",
        # Discord 403s the default Python-urllib UA; it needs a real one.
        "User-Agent": "taiwan-stock-daily-report (https://github.com/, 2.1)",
    }
    for i, chunk in enumerate(_chunks(text)):
        body = json.dumps({"content": chunk}).encode("utf-8")
        req = Request(webhook, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=20) as resp:
            if resp.status not in (200, 204):
                raise RuntimeError(f"Discord webhook returned {resp.status}")
        if i:
            time.sleep(0.5)  # be gentle if a message was split


def main() -> int:
    _load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(PREFIX), default="digest")
    parser.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK_URL"))
    parser.add_argument("--message", help="message body; if omitted, read from stdin")
    args = parser.parse_args()

    if not args.webhook:
        print("no DISCORD_WEBHOOK_URL / --webhook; skipping Discord notification", file=sys.stderr)
        return 0  # not fatal — the report itself still matters

    message = args.message if args.message is not None else sys.stdin.read()
    message = message.strip()
    if not message:
        print("empty message; nothing to send", file=sys.stderr)
        return 0

    text = f"{PREFIX[args.kind]}\n{message}"
    try:
        post(args.webhook, text)
        print("sent")
        return 0
    except Exception as exc:
        print(f"Discord notification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
