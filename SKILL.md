---
name: taiwan-stock-daily-report
description: 產生以台灣為核心、涵蓋全球總經、政策、重大新聞、跨資產與台股題材個股篩選的繁體中文投資研究報告，支援每日排程並寫入 Notion。當使用者要求台灣或全球總經分析、央行與財政政策解讀、經濟新聞解讀、台股每日或週期性研究、題材評估、候選股票篩選，或分析台股 CSV、JSON、XLSX 時使用。優先採官方與第一手資料，保存資料期、發布時間與分析截止時間，執行實際值／市場預期／前值／歷史區間比較，建立基準／樂觀／悲觀情境，與前一份報告比較差異，並將股票結論限制為可追溯的研究候選、觀察或迴避。
---

# 台灣與全球總經投資研究

產生可追溯、可反證的繁體中文研究報告。把總經、政策與新聞轉譯為題材假說，再以公司曝險、基本面、估值、流動性、催化劑與風險篩選台股研究候選。不要把單一新聞、單日漲幅或主觀敘事直接轉成買進建議。

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
   - **台股行情 + 量化訊號**：`python scripts/fetch_market_snapshot.py --watchlist 2330,2317 --output <market.json>`
     讀 `freshness`：若 `stale` 為真或 `status=degraded`，依 research-method 新鮮度規則處理。每檔 watchlist 附 `signals`（5/20/60 日動能、相對大盤強弱、量能比、實現波動）。使用者提供 CSV／JSON／XLSX 時改 `--input <file>`。
   - **個股基本面與籌碼面**：`python scripts/fetch_fundamentals.py --watchlist 2330,2317 --output <fundamentals.json>`（FinMind）。近一年 PER／PBR 分位、三大法人買賣超、融資融券、月營收 YoY／MoM、股利。缺值填 N/A。
   - **總經數據**：`python scripts/fetch_macro_snapshot.py --output <macro.json>`（FRED key 由 `scripts/.env` 或環境變數帶入）。讀 `macro_snapshot.json` 作為美國、殖利率、油價、Euro、匯率的一手來源。
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
   - 先評分題材，再評分個股；列出納入與排除理由。
   - 流動性依 stock-screening 的兩段式規則：有 20 日歷史用中位成交金額，只有單日快照時用單日代理值並降低信心。
   - 只輸出 `PRIORITY_RESEARCH`、`WATCH` 或 `ABSTAIN`。沒有經驗證策略時不得輸出 `BUY`。
9. 產生報告。
   - 讀取 [references/report-contract.md](references/report-contract.md)，依固定順序呈現，含「與前一份報告的變化」章節。
   - 每個重要結論都附鄰近來源連結與資料日期；結尾列出不確定性、反證、後續驗證事件及研究用途聲明。
10. 交付。
    - 讀取 [references/output-delivery.md](references/output-delivery.md)。有 Notion 目標時建立當日頁面並回連前一份；否則輸出 Markdown 檔。
    - 依 [references/state-memory.md](references/state-memory.md) 更新狀態記錄，供下次執行比較。

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
