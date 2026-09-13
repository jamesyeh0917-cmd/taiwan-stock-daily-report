# 雲端排程執行說明（cloud routine entrypoint）

這份給排程雲端代理讀。本倉庫 = taiwan-stock-daily-report skill 本體。

## 執行步驟

### 0. 讀設定 + 決定今天要不要跑（省 token）

先讀 `./config.json`：`watchlist_core`、`screen_top`、`news_window_hours`、Notion data source id 等。後續所有命令用 config 的值，不要用寫死清單。

查 Notion「台股每日研究報告」資料庫中日期早於今日的最新一筆，取其資料基準日，然後：

```
python scripts/trading_day.py --last-report-date <前一份的資料基準日> --allow-light
```

讀輸出的 `recommendation`。**這次執行的頁面標題、`資料基準日` 屬性、以後每一步要引用的日期，一律直接照抄本次輸出的 `report_date`／`report_title`／`notion_date_property` 三個欄位，不要自己重新判斷或算日期**——這是唯一的日期計算來源，包含 `light` 模式：即使今天執行、macro/新聞資料是今天抓的，`report_date` 仍是上一份已涵蓋的交易日，不是今天。填錯會觸發健康檢查誤報。

- `full` → 有新的台股收盤尚未有報告 → 照下方完整流程跑。
- `light` → 最近收盤已被前一份涵蓋（週末、假日、或今天稍早已跑過）→ **只做輕量更新**：
  跑 `fetch_macro_snapshot.py` + macro-fetch.md 的新聞掃描 → 在報告資料庫新增一頁（標題用上面的 `report_title`，已含 `(輕量)` 後綴），
  只填「關鍵數字看板」「本期最重要三件事」「與前一份的變化」「未來事件日曆」，其餘章節寫「非交易日，未更新」。
  不做行情抓取、不重算情境、不做個股篩選。執行日期只放在內文「本次更新時間」一行，不進標題或屬性。
- 同一 `report_date` 需要重跑（例如發現錯誤要修訂）時，帶 `--revision N` 重跑 `trading_day.py`，`report_title` 會自動換成 `(修訂 N)` 後綴。
- 若 `trading_day.py` 本身失敗（抓不到日曆）→ 當作 `full` 處理。

### 1–10（`full` 模式）

1. **依 [SKILL.md](SKILL.md) 的流程完整執行**。參考檔在 `references/`，腳本在 `scripts/`。
2. `cadence = daily`。時間基準見 [references/research-method.md](references/research-method.md)。
3. **總經數據**：先 `export FRED_API_KEY`（金鑰由 routine prompt 提供），再
   `python scripts/fetch_macro_snapshot.py --output /tmp/macro.json`
4. **台股行情 + 量化訊號**：`python scripts/fetch_market_snapshot.py --watchlist <config.watchlist_core> --history-days <config.history_days> --output /tmp/market.json`
   多層備援（openapi → www.twse.com.tw/rwd → FinMind）＋重試。讀 `status` / `freshness` / `errors`。每檔 watchlist 附 `signals`。
4b. **全市場粗篩**：`python scripts/screen_universe.py --top <config.screen_top> --core <config.watchlist_core> --output /tmp/shortlist.json`
4c. **個股深度資料**：`python scripts/fetch_fundamentals.py --watchlist <shortlist.json 的 codes 逗號串> --output /tmp/fundamentals.json`（FinMind；有 `FINMIND_TOKEN` 環境變數則自動用，額度較高）。
4c-2. **產業資金流排行**：`python scripts/fetch_industry_flow.py --per-industry <config.industry_flow_per_industry> --min-turnover <config.industry_flow_min_turnover_twd> --output /tmp/industry_flow.json`（FinMind 官方產業分類 + 全市場成交值取樣 + 三大法人買賣超彙總；見 [references/industry-flow.md](references/industry-flow.md)）。此步驟可容許失敗降級（輸出 `industries: []`），不得中斷全程。
4d. **台股／總經新聞**：`python scripts/fetch_news.py --hours <config.news_window_hours> --output /tmp/news.json`（鉅亨網 + 經濟日報 + 中央社）。優先於 WebSearch。
4e. **彙總降級狀態**：`python scripts/summarize_run_status.py --mode <full|light> --market /tmp/market.json --macro /tmp/macro.json --fundamentals /tmp/fundamentals.json --news /tmp/news.json --industry-flow /tmp/industry_flow.json --output /tmp/run_status.json`（light 模式只帶有跑的檔案）。輸出的 `suggested_report_status` 當報告 §1「報告狀態」與 Notion `狀態` 屬性的預設值、`tools_degraded`／`degraded_sources` 直接用於「工具降級」段落——不要自己重新翻 4-5 個 JSON 檔判斷，除非有更強理由才覆寫預設值並說明原因。
5. **台／中／日總經 + consensus**：依 [references/macro-fetch.md](references/macro-fetch.md) 用 WebFetch 補。
6. **跨日比較**：依 [references/state-memory.md](references/state-memory.md)。雲端每次全新 checkout，`scripts/state/latest.json` 不存在屬正常 → 用步驟 0 已查到的 Notion 前一筆。
6b. **price-in**：對每個優先題材已發生的主要催化劑，跑
   `python scripts/price_in.py --code <代表股> --event-date <日期>`（見 references/backtest-calibration.md）。
6c. **題材檔案**：依 [references/theme-dossier.md](references/theme-dossier.md)，對每個 WATCH 以上題材，在「題材檔案」資料庫查既有頁 → 追加當日小節或建新頁。
6d. **回測校準（僅週一 / 每月 1 日）**：依 references/backtest-calibration.md，從候選股追蹤 DB 匯出 65 日前呼叫 → `python scripts/backtest.py --calls /tmp/calls.json` → 寫報告 §14.5。

7. **交付**：依 [references/output-delivery.md](references/output-delivery.md)。目標：

   | 用途 | data source |
   |---|---|
   | 報告資料庫「台股每日研究報告」 | `collection://12f5e0a1-2f79-4eb0-abbc-49c468c1cd3f` |
   | 證據帳本 | `collection://f4318726-ba81-41f8-ad7f-de908be1f8ba` |
   | 候選股追蹤 | `collection://b9dee6fc-0ab8-4ee3-a48c-eac1ab8a22ff` |
   | 題材檔案 | `collection://608d5a9c-0854-4f21-8f09-9006f57d9b5c` |
   | 產業資金流 | `collection://662bd917-b622-4069-a62b-10b0610f1cbd` |

7a. **產業資金流寫入**：讀 `industry_flow.json` 的**全部** `industries[]`（不是報告顯示的 top5/bottom3 子集）。先查重：
    ```sql
    SELECT "產業", url FROM "collection://662bd917-b622-4069-a62b-10b0610f1cbd"
    WHERE date("資料基準日") = date('<本次資料基準日>')
    ```
    產業名不在查詢結果中 → 批次 `notion-create-pages`（parent 用 `data_source_id`，一次最多 100 筆）新增；已在結果中 → 逐筆 `notion-update-page`（`command: update_properties`）更新，用查到的 `url` 對應的頁面 id。`排名` = 該日 `industries[]` 排序後的 1-based 位置；`報告顯示範圍` 依當日 top5/bottom3 標註（其餘標「未顯示」）；`報告連結` 填本次報告頁 URL。見 [references/output-delivery.md](references/output-delivery.md) 第 4 項、[references/industry-flow.md](references/industry-flow.md)。`industry_flow.json` 的 `industries` 為空時整段跳過，不擋交付。

7b. **產出 QA（交付前）**：把報告草稿寫成 `/tmp/draft.md`，跑
   `python scripts/validate_report.py --report /tmp/draft.md --market /tmp/market.json --macro /tmp/macro.json --fundamentals /tmp/fundamentals.json --mode <full|light> --expected-base-date <步驟0 trading_day.py 輸出的 report_date>`。
   `--expected-base-date` 務必帶，尤其輕量模式下沒有 `market.json` 可比對，這是唯一能抓到「頁面日期誤填執行日」的檢查。
   依 [references/qa-and-review.md](references/qa-and-review.md)：`fail` → 仍交付但頁面加 callout、`複核狀態`=有疑慮、Discord 標「⚠️ QA 未通過」；`warn` → warnings 併進 §14；QA JSON 貼進 §15 toggle。
7c. 把 market/macro/fundamentals/industry_flow 的關鍵欄位貼進報告 §15 的「原始快照」toggle（資料快取後備）。

8. 報告標題用步驟 0 的 `report_title`；開頭 callout 標「自動產生‧未複核」；報告 DB 的 `狀態` 屬性用步驟 4e 的 `suggested_report_status`（除非有更強理由才覆寫）；`複核狀態`（排程一律 `待複核`，QA fail 則 `有疑慮`）。
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
- Notion 連接器（claude.ai connector）已授權且可存取上述四個資料庫
- 環境變數（routine prompt export）：`FRED_API_KEY`、`DISCORD_WEBHOOK_URL`
