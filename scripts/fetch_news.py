#!/usr/bin/env python3
"""Pull recent Taiwan finance/macro headlines from real feeds.

WebSearch is US-region and non-semantic; for a Taiwan-centric report the
news layer is the weakest input. This reads the actual feeds:

  * cnyes (鉅亨網) JSON API — tw_stock, headline, wd_macro categories
  * 經濟日報 (money.udn) RSS
  * 中央社 (CNA) 財經 RSS

Output: deduped headlines from the last N hours with title, summary,
source, publish time, URL, and any stock codes the feed tagged, plus a
rough tally of which report themes / macro topics are being talked about.

This is a DISCOVERY aid — the agent still opens the primary document
(official release, company announcement) before citing anything as fact.
News-feed items are tier D.

Usage:
  python scripts/fetch_news.py --hours 30 --output news.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

CNYES = "https://news.cnyes.com/api/v3/news/category/{cat}?limit=30"
CNYES_CATS = ["tw_stock", "headline", "wd_macro", "tw_macro"]
RSS_FEEDS = [
    ("經濟日報", "https://money.udn.com/rssfeed/news/1001/5591?ch=money"),
    ("經濟日報-產業", "https://money.udn.com/rssfeed/news/1001/5590?ch=money"),
    ("中央社財經", "https://feeds.feedburner.com/rsscna/finance"),
    ("工商時報", "https://ctee.com.tw/feed"),
    ("Yahoo財經", "https://tw.stock.yahoo.com/rss?category=news"),
]
RELEVANCE = [
    "台股", "加權", "上市", "上櫃", "半導體", "晶片", "台積電", "AI", "伺服器", "記憶體",
    "封測", "散熱", "面板", "外資", "投信", "法人", "央行", "利率", "升息", "降息",
    "Fed", "聯準會", "通膨", "CPI", "出口", "外銷訂單", "景氣", "GDP", "PMI",
    "關稅", "匯率", "台幣", "新台幣", "美元", "原油", "殖利率", "財報", "營收", "法說",
]

THEME_KW = {
    "AI 伺服器": ["AI 伺服器", "AI伺服器", "伺服器", "GB200", "GB300", "機櫃", "ODM"],
    "記憶體": ["記憶體", "DRAM", "HBM", "NAND", "模組"],
    "半導體設備/封測": ["封測", "CoWoS", "先進封裝", "設備", "載板", "ABF"],
    "散熱": ["散熱", "液冷", "水冷", "均熱"],
    "網通": ["網通", "乙太網路", "交換器", "光通訊", "CPO", "矽光子"],
    "IC 設計": ["IC 設計", "ASIC", "SoC", "驅動 IC"],
    "金融": ["金控", "銀行", "壽險", "利差"],
}
MACRO_KW = {
    "央行/利率": ["央行", "理監事", "升息", "降息", "政策利率", "貼放"],
    "Fed": ["Fed", "聯準會", "FOMC", "Warsh", "鮑爾"],
    "通膨": ["CPI", "通膨", "物價", "PPI"],
    "出口/景氣": ["出口", "外銷訂單", "工業生產", "景氣", "PMI", "GDP"],
    "關稅/政策": ["關稅", "232", "301", "半導體法案", "出口管制", "MOU"],
    "匯率": ["新台幣", "台幣", "匯率", "升值", "貶值"],
    "法人動向": ["外資", "投信", "三大法人", "買超", "賣超"],
}


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    s = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if s:
        c.verify_flags &= ~s
    return c


_SSL = _ctx()


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url: str, retries: int = 2) -> str:
    for i in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
            with urlopen(req, timeout=25, context=_SSL) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == retries:
                raise
            time.sleep(1.5)
    return ""


def _relevant(it: dict) -> bool:
    if it.get("tagged_stocks"):
        return True
    text = (it.get("title", "") or "") + (it.get("summary", "") or "")
    return any(k in text for k in RELEVANCE)


def _clean(s: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(s or "")).strip()


def _cnyes(cat: str, cutoff: float) -> list[dict]:
    out = []
    try:
        data = json.loads(_get(CNYES.format(cat=cat)))
        for it in data.get("items", {}).get("data", []):
            ts = it.get("publishAt", 0)
            if ts and ts < cutoff:
                continue
            codes = []
            for m in it.get("market", []) or []:
                c = str(m.get("code", "")).strip()
                if re.fullmatch(r"\d{4}", c):
                    codes.append(c)
            out.append({
                "title": _clean(it.get("title")),
                "summary": _clean(it.get("summary")),
                "source": f"鉅亨網/{it.get('categoryName') or cat}",
                "published": datetime.fromtimestamp(ts, timezone.utc).astimezone(
                    timezone(timedelta(hours=8))).isoformat(timespec="minutes") if ts else None,
                "url": f"https://news.cnyes.com/news/id/{it.get('newsId')}",
                "tagged_stocks": sorted(set(codes)),
            })
    except Exception as exc:
        out.append({"_error": f"cnyes {cat}: {exc}"})
    return out


def _rss(name: str, url: str, cutoff_dt: datetime) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(_get(url))
        for item in root.iter("item"):
            title = _clean(item.findtext("title", ""))
            desc = _clean(item.findtext("description", ""))
            link = (item.findtext("link", "") or "").strip()
            pub = item.findtext("pubDate", "")
            when = None
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
                try:
                    when = datetime.strptime(pub.strip(), fmt)
                    break
                except (ValueError, AttributeError):
                    continue
            if when and when.tzinfo and when < cutoff_dt:
                continue
            out.append({
                "title": title, "summary": desc, "source": name,
                "published": when.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="minutes") if when else pub.strip(),
                "url": link, "tagged_stocks": sorted(set(re.findall(r"(?<!\d)(\d{4})(?!\d)", title + desc))),
            })
    except Exception as exc:
        out.append({"_error": f"rss {name}: {exc}"})
    return out


def _tally(items: list[dict], kw_map: dict) -> dict:
    counts: dict[str, int] = {}
    for it in items:
        text = (it.get("title", "") or "") + (it.get("summary", "") or "")
        for label, kws in kw_map.items():
            if any(k in text for k in kws):
                counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def build(hours: int) -> dict:
    now = datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(hours=hours)).timestamp()
    cutoff_dt = now - timedelta(hours=hours)

    raw: list[dict] = []
    errors: list[str] = []
    for cat in CNYES_CATS:
        for r in _cnyes(cat, cutoff_ts):
            (errors.append(r["_error"]) if "_error" in r else raw.append(r))
    for name, url in RSS_FEEDS:
        for r in _rss(name, url, cutoff_dt):
            (errors.append(r["_error"]) if "_error" in r else raw.append(r))

    seen: set[str] = set()
    items: list[dict] = []
    for it in sorted(raw, key=lambda x: x.get("published") or "", reverse=True):
        key = re.sub(r"[\s\W]", "", it["title"])[:24]
        if key in seen or not it["title"]:
            continue
        seen.add(key)
        items.append(it)

    relevant = [it for it in items if _relevant(it)]

    return {
        "schema_version": 2,
        "generated_at": now.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "window_hours": hours,
        "count": len(relevant),
        "count_raw": len(items),
        "sources": sorted({it["source"] for it in items}),
        "errors": errors,
        "theme_mentions": _tally(relevant, THEME_KW),
        "macro_mentions": _tally(relevant, MACRO_KW),
        "items": relevant,
        "items_other": [it for it in items if it not in relevant][:15],
        "note": "tier D discovery aid; open the primary document before citing as fact",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=30)
    ap.add_argument("--output", default="-")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        data = build(args.hours)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(data, ensure_ascii=False, indent=None if args.compact else 2)
    if args.output == "-":
        print(text)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if data["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
