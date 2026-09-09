# 雲端排程執行說明（cloud routine entrypoint）

這份給排程雲端代理讀。本倉庫 = taiwan-stock-daily-report skill 本體。

## 執行步驟

### 0. 決定今天要不要跑（省 token）

先查 Notion「台股每日研究報告」資料庫中日期早於今日的最新一筆，取其資料基準日，然後：

```
python scripts/trading_day.py --last-report-date <前一份的資料基準日> --allow-light
```

讀輸出的 `recommendation`：

- `full` → 有新的台股收盤尚未有報告 → 照下方完整流程跑。
- `light` → 最近收盤已被前一份涵蓋（週末、假日、或今天稍早已跑過）→ **只做輕量更新**：
  跑 `fetch_macro_snapshot.py` + macro-fetch.md 的新聞掃描 → 在報告資料庫新增一頁，標題加 `(輕量)`，
  只填「關鍵數字看板」「本期最重要三件事」「與前一份的變化」「未來事件日曆」，其餘章節寫「非交易日，未更新」。
  不做行情抓取、不重算情境、不做個股篩選。
- 若 `trading_day.py` 本身失敗（抓不到日曆）→ 當作 `full` 處理。

### 1–10（`full` 模式）

1. **依 [SKILL.md](SKILL.md) 的流程完整執行**。參考檔在 `references/`，腳本在 `scripts/`。
2. `cadence = daily`。時間基準見 [references/research-method.md](references/research-method.md)。
3. **總經數據**：先 `export FRED_API_KEY`（金鑰由 routine prompt 提供），再
   `python scripts/fetch_macro_snapshot.py --output /tmp/macro.json`
4. **台股行情**：`python scripts/fetch_market_snapshot.py --watchlist 2330,2317,2454,2382,2408,2344,3711,2308 --output /tmp/market.json`
   腳本已內建 openapi → www.twse.com.tw/rwd → FinMind 的多層備援與重試。讀 `status` / `freshness` / `errors`：仍 `degraded` 或某來源全掛，照 research-method.md 的新鮮度規則揭露。
5. **台／中／日總經 + consensus**：依 [references/macro-fetch.md](references/macro-fetch.md) 用 WebFetch 補。
6. **跨日比較**：依 [references/state-memory.md](references/state-memory.md)。雲端每次全新 checkout，`scripts/state/latest.json` 不存在屬正常 → 用步驟 0 已查到的 Notion 前一筆。
7. **交付**：依 [references/output-delivery.md](references/output-delivery.md)。目標：

   | 用途 | data source |
   |---|---|
   | 報告資料庫「台股每日研究報告」 | `collection://12f5e0a1-2f79-4eb0-abbc-49c468c1cd3f` |
   | 證據帳本 | `collection://f4318726-ba81-41f8-ad7f-de908be1f8ba` |
   | 候選股追蹤 | `collection://b9dee6fc-0ab8-4ee3-a48c-eac1ab8a22ff` |

8. 報告標題與開頭 callout 標「自動產生‧未複核」。
9. 全程非互動：不要問問題。不確定就依 skill 降級規則處理並在報告中記錄。

### 11. 推播 Discord 摘要（每種模式結束時都要做）

`DISCORD_WEBHOOK_URL` 由 routine prompt 提供。組一段簡短摘要（≤1500 字），用：

```
python scripts/notify_discord.py --kind <digest|light|skip> --message "<內容>"
```

`digest` / `light` 摘要包含：資料基準日、報告狀態、加權指數與台幣、本期最重要三件事、
三情境機率、優先題材、可執行觀察清單前 3 條、Notion 報告頁連結。
`skip` 只需一行說明為何跳過。

### 12. 失敗處理

任何步驟發生無法繼續的錯誤時，在放棄前先發告警：

```
python scripts/notify_discord.py --kind alert --message "<哪一步、什麼錯誤、已完成到哪>"
```

Notion 連接器工具不存在時：把完整報告輸出成 Markdown 檔到工作目錄、發一則 alert 說明、結束。

> 註：正常成功的日子一定會有一則 Discord 訊息。**某天沒收到訊息 = 那次執行整個掛了**（沙箱層級失敗，連告警都發不出），需人工查 routine 執行紀錄。

## 環境需求

- Python 3（標準庫即可）
- 網路：FRED、TWSE openapi + www.twse.com.tw、FinMind、TPEx、美國財政部、各國官方新聞稿、WebSearch/WebFetch、Discord webhook
- Notion 連接器（claude.ai connector）已授權且可存取上述三個資料庫
- 環境變數（routine prompt export）：`FRED_API_KEY`、`DISCORD_WEBHOOK_URL`
