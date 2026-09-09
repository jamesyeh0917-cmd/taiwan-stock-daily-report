# 週報

每週日晚由獨立 routine 執行，彙整當週每日報告，在 Notion 頁「台股研究週報（自動累積）」
（<https://app.notion.com/p/3d660fffda14818c8775e8729f8ec1bc>）**內文最後追加**一段當週小節。不重寫。

## 內容（一段 `## YYYY-MM-DD 週（W36）`）

1. **一週摘要**：3–4 句 —— 台灣/全球方向、本週最重要的事、基準情境機率的一週變化。
2. **情境機率軌跡**：表格，本週每個交易日的基準/樂觀/悲觀機率，指出跳動點與觸發事件。
3. **題材進出**：本週新增為 PRIORITY_RESEARCH 的、被降級/移除的、維持的；各一句原因。
4. **候選股表現**：本週曾入候選池的標的，列本週報酬 + 相對大盤（用 fetch_market_snapshot 的 signals 或 FinMind）。誰對誰錯的粗略回顧。
5. **回測校準**：跑 `scripts/backtest.py`（見 backtest-calibration.md），貼 `by_status` / `by_score_bucket` / `calibration_notes`。
6. **下週看點**：未來事件日曆濃縮版（下週的關鍵資料發布、央行會議、法說）。

## 資料來源

- 「台股每日研究報告」DB 查本週（過去 7 天資料基準日）的所有列 → 屬性（狀態、方向、基準情境機率、優先題材）。
- 「候選股追蹤」DB 查本週列 + 65 日前列（回測）。
- 需要股價時跑 `scripts/fetch_market_snapshot.py --watchlist <本週候選代號>` 或 FinMind。

## 交付

- 追加到週報頁後，發一則 Discord（`notify_discord.py --kind digest`，標「📅 週報」）：一週摘要 + 情境機率變化 + 校準結論 + 週報頁連結。
- 若當週無任何每日報告（連假等），只追加一行「W36：無交易，略過」。
