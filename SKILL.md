---
name: taiwan-stock-daily-report
description: 產生以台灣為核心、涵蓋全球總經、政策、重大新聞、跨資產與台股題材個股篩選的繁體中文投資研究報告，支援每日排程並寫入 Notion。當使用者要求台灣或全球總經分析、央行與財政政策解讀、經濟新聞解讀、台股每日或週期性研究、題材評估、候選股票篩選，或分析台股 CSV、JSON、XLSX 時使用。優先採官方與第一手資料，保存資料期、發布時間與分析截止時間，執行實際值／市場預期／前值／歷史區間比較，建立基準／樂觀／悲觀情境，與前一份報告比較差異，並將股票結論限制為可追溯的研究候選、觀察或迴避。
---

# 台灣與全球總經投資研究

產生可追溯、可反證的繁體中文研究報告。把總經、政策與新聞轉譯為題材假說，再以公司曝險、基本面、估值、流動性、催化劑與風險篩選台股研究候選。不要把單一新聞、單日漲幅或主觀敘事直接轉成買進建議。

## 寫作語氣

敘述性段落（執行摘要、本期最重要三件事、政策分析、新聞解讀、三情境的假設說明、個股與題材研究摘要、反證段落、Discord 摘要）盡量口語化——像在跟一個懂投資、但沒空盯盤的朋友講重點，直接講「這代表什麼、為什麼重要、接下來要看什麼」，少用制式公文語氣與生硬術語堆疊，句子不用刻意寫長或疊床架屋。

表格欄位、數字、來源標註、證據層級、狀態代號（`PRIORITY_RESEARCH`／`WATCH`／`ABSTAIN`）與情境機率仍須精確可追溯——口語化只改變「怎麼講」，不改變「事實與推論分開」「缺值標記」「不確定性不得隱藏」等實質規則。表格本身維持簡潔的資料呈現，不需要口語化。

## 工具依賴與降級

| 能力 | 用途 | 缺少時 |
|---|---|---|
| WebFetch | 直抓台／中／日官方新聞稿與經濟日曆 consensus（見 [references/macro-fetch.md](references/macro-fetch.md)） | 對應儀表板格標「本期未取得」，報告狀態降 `部分`，不得臆測 |
| WebSearch | 補新聞脈絡、交叉查證、找官方新聞稿列表 | 只用 WebFetch 已知頁 + 腳本；未能查證的區塊明列 |
| `FRED_API_KEY` | `scripts/fetch_macro_snapshot.py` 取美國 CPI／就業／殖利率、油價、Euro HICP、匯率 | 腳本自動降級（仍出財政部殖利率曲線）；缺口逐項改 WebFetch FRED 網頁，降信心 |
| FinMind（免費，無金鑰亦可） | `scripts/fetch_fundamentals.py` 取個股 PER 分位、法人、融資券、月營收、股利 | 個股基本面／籌碼面章節標「未取得」，估值退回當日 PER／PBR，個股狀態上限 WATCH。設 `FINMIND_TOKEN` 可提高額度 |
| Python 3 | 執行 `scripts/*.py`（行情、總經、交易日判斷、Discord 推播） | 改用使用者提供的 CSV／JSON／XLSX；否則台股行情與部分總經章節標為缺資料 |
| `openpyxl` | 讀取 `.xlsx` | 請使用者改存 UTF-8 CSV |
| Notion MCP（`notion-*` 工具） | 交付報告到 Notion（含證據帳本／候選股子資料庫） | 依 [references/output-delivery.md](references/output-delivery.md) 改輸出 Markdown 檔並提示使用者 |
| `DISCORD_WEBHOOK_URL`（排程用） | `scripts/notify_discord.py` 推播摘要與失敗告警 | 無則靜默略過推播，不影響報告本身 |

開始前先確認手上有哪些工具，缺哪一項就在報告「報告識別」欄註明對應的降級。

## 執行流程

1. 定義問題。
   - 讀 [config.json](config.json) 取得 `watchlist_core`、`screen_top`、`news_window_hours`、各門檻與 Notion data source id。**所有 watchlist／門檻的唯一來源是 config.json**，不要用寫死值。
   - 確認國家、資料期間、分析截止時間、指標、分析目的、投資市場與投資期限。
   - 未指定時，採用 [references/research-method.md](references/research-method.md) 的預設範圍與「排程／每日模式」規則。
2. 建立資料截止線。
   - 設定 `analysis_as_of` 與時區；只使用在截止線前已公開的資訊。
   - 每日排程模式下，`analysis_as_of` 以「最近一個已收盤的台股交易日」為基準，不是掛鐘時間。
   - 分別保存資料觀察期、首次發布時間、最新修訂時間與擷取時間。
3. 讀取前一份報告（跨日記憶）。
   - 依 [references/state-memory.md](references/state-memory.md) 找出上一份報告，擷取前次三情境機率、題材清單、關鍵證據帳本與未解問題。
   - 若找不到（首次執行），明確標示「無前期基準」。
4. 蒐集證據。
   - 讀取 [references/data-sources.md](references/data-sources.md) 與 [references/sources.json](references/sources.json)。
   - **台股行情 + 量化訊號**：`python scripts/fetch_market_snapshot.py --watchlist <config.watchlist_core> --output <market.json>`
     讀 `freshness`：若 `stale` 為真或 `status=degraded`，依 research-method 新鮮度規則處理。每檔 watchlist 附 `signals`（5/20/60 日動能、相對大盤強弱、量能比、實現波動）。使用者提供 CSV／JSON／XLSX 時改 `--input <file>`。
   - **全市場粗篩（第一階段）**：`python scripts/screen_universe.py --top <config.screen_top> --core <config.watchlist_core> --output <shortlist.json>` → 取得輪動候選 + 核心清單。
   - **個股基本面與籌碼面（第二階段）**：`python scripts/fetch_fundamentals.py --watchlist <shortlist 的 codes> --output <fundamentals.json>`（FinMind）。近一年 PER／PBR 分位、三大法人買賣超、融資融券、月營收 YoY／MoM、股利。缺值填 N/A。
   - **總經數據**：`python scripts/fetch_macro_snapshot.py --output <macro.json>`（FRED key 由 `scripts/.env` 或環境變數帶入）。讀 `macro_snapshot.json` 作為美國、殖利率、油價、Euro、匯率的一手來源。
   - **台股／總經新聞**：`python scripts/fetch_news.py --hours <config.news_window_hours> --output <news.json>` → 鉅亨網 API + 經濟日報／中央社 RSS 的當日標題、摘要、tagged 個股、題材／總經話題計數。**優先於 WebSearch**（WebSearch 美國區、常拿舊快取）。標 D 級，引用前開原始文件。
   - **台／中／日總經 + consensus**：依 [references/macro-fetch.md](references/macro-fetch.md) 用 WebFetch 直抓官方新聞稿與經濟日曆，補齊腳本未涵蓋項。
   - 先用腳本與官方頁，媒體只補脈絡。事實與推論分開。
5. 建立證據帳本。
   - 對每項關鍵數據記錄：指標、國家、資料期、實際值、預期值、前值、前值是否修訂、歷史區間、發布時間、來源與可用性警告。
   - 無可靠預期值時填 `N/A`，不要猜測市場共識。
6. 比較與解讀。
   - 比較實際值、預期值、前值及歷史分位或標準化區間。
   - 分開標示「可觀察事實」、「推論」與「尚未驗證的假說」。
   - 不從價格變化單獨推斷原因；只有來源直接支持時才做事件歸因。
7. 建立三種情境。
   - 產生基準、樂觀、悲觀情境；機率合計必須為 100%。
   - 為每個情境列出驅動因子、驗證指標、失效條件、可能市場傳導與時間範圍。
   - 對照前次情境，說明機率調整的原因與觸發事件。
8. 形成題材與股票候選。
   - 讀取 [references/stock-screening.md](references/stock-screening.md)。
   - 先評分題材，再評分個股；估值用 `fundamentals.json` 的近一年 PER 分位，籌碼面用三大法人與融資券，市場驗證用 `signals`。
   - 流動性依 stock-screening 的兩段式規則。只輸出 `PRIORITY_RESEARCH`、`WATCH` 或 `ABSTAIN`；沒有經驗證策略時不得輸出 `BUY`。
   - **price-in 判斷**：對每個優先題材的已發生主要催化劑，跑 `python scripts/price_in.py --code <代表股> --event-date <日期>`，判斷市場反映程度（見 [references/backtest-calibration.md](references/backtest-calibration.md)）。
9. 更新題材檔案（累積型知識庫）。
   - 讀取 [references/theme-dossier.md](references/theme-dossier.md)。
   - 對每個 `WATCH` 以上題材：查 Notion「題材檔案」有無既有頁 → 有則內文追加當日小節（只寫變化）、無則建頁含傳導鏈與供應鏈對照。不重寫先前小節。
10. 產生報告並 QA。
   - 讀取 [references/report-contract.md](references/report-contract.md)，依固定順序呈現，含「與前一份報告的變化」章節。
   - **每週一次**（週一或每月 1 日）另做回測校準：依 backtest-calibration.md 跑 `scripts/backtest.py`，寫報告 §13.7。
   - 報告草稿寫成 Markdown 檔後，依 [references/qa-and-review.md](references/qa-and-review.md) 跑 `python scripts/validate_report.py --report <draft.md> --market <market.json> --macro <macro.json> --fundamentals <fundamentals.json> --mode <full|light>`（結構檢查 + 逐數字對快照交叉比對，可抓 PER／月營收／日期寫錯或幻覺）；依 verdict 決定 `複核狀態` 與是否在 Discord 標「QA 未通過」。
11. 交付。
    - 讀取 [references/output-delivery.md](references/output-delivery.md)。Notion 模式建當日頁（含 `複核狀態` 屬性、§15 的 QA JSON 與原始快照 toggle）+ 更新證據帳本／候選股／題材檔案三個子資料庫 + 回連前一份。
    - 依 [references/state-memory.md](references/state-memory.md) 更新狀態記錄。

## 預設研究範圍

- 以台灣為核心，全球部分至少涵蓋美國、中國、歐元區與日本；只有與台灣成長、通膨、資金、匯率、供應鏈或主要商品明顯相關時才擴大其他地區。
- 同時提供戰術期 `1–4 週` 與策略期 `3–12 個月`；不得混用兩種期間的催化劑與風險。
- 股票池預設以代號首位為 1–9 的四位數上市、上櫃證券作為普通股代理；排除代號首位為 0 的 ETF／ETN、權證、債券、停止交易、無可靠價格或流動性明顯不足的標的，並在公司層級再次確認工具類型。
- 每次最多列出 3 個優先題材，每題材最多 5 檔候選；不足時少列，不得為湊數降低門檻。

## 強制研究限制

- 保留時間契約：資料在 T 日何時可知、報告何時產生、最早何時可交易。盤後形成的觀點若需執行，最早只能假設 T+1 開盤後，且必須另計交易成本與滑價。
- 防止未來資訊、存活者偏差、修訂後資料回填、以未來值補缺漏、同日收盤價成交假設與新聞時間穿越。
- 不把官方預測、市場共識或媒體敘事當成已發生的事實。
- 不提供個人化資產配置、保證報酬、目標價或未經驗證的精確報酬預測。
- 缺少公司曝險、最新財務、估值、流動性、事件時間或反證資料時，輸出 `WATCH` 或 `ABSTAIN`。
- 若 TWSE 與 TPEx 資料日不同，不合併市場廣度或成交統計。
- 行情快照 `freshness.stale` 為真時，該日台股章節一律標為「前一交易日資料」並揭露落後天數。
- 若來源互相衝突，呈現差異與可能原因，不自行挑選較符合敘事的數字。
- 自動排程產生、未經人工複核的報告，開頭必須標示「自動產生‧未複核」。

## 完成標準

只有在以下條件都滿足時才完成報告：

- 顯示 `analysis_as_of`、時區、資料期、發布時間、擷取時間、報告狀態與所用工具的降級情形。
- 涵蓋台灣總經、全球總經、政策、新聞、跨資產、三種情境、題材及個股篩選。
- 含「與前一份報告的變化」章節；首次執行則標示「無前期基準」。
- 每項候選都包含證據、催化劑、估值或估值缺口、主要風險、失效條件、流動性（含窗口長度）與評分。
- 另列未入選標的及排除原因，避免只呈現支持結論的證據。
- 來源可追溯，事實與推論分開，缺值明確標記，不確定性沒有被隱藏。
- 已依 output-delivery 交付，並更新跨日狀態記錄。
