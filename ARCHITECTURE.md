# 專案運作全貌

`taiwan-stock-daily-report` —— 每天早上自動產生一份台灣為核心的總經／政策／新聞／台股題材研究報告，寫進 Notion，摘要推 Discord。

本文是「怎麼運作」的完整說明；規則細節在 `SKILL.md` 與 `references/`，程式在 `scripts/`。

---

## 1. 一頁總覽

```
每天 07:00 Asia/Taipei（cron 0 23 * * * UTC）
        │
        ▼
 claude.ai 排程系統觸發 routine「台股每日研究報告」
        │
        ▼
 開一個隔離雲端沙箱 → git clone 本倉庫 → 啟動 Claude Code（sonnet-5）
        │
        ▼
 Claude 讀 CLOUD.md → SKILL.md → references/*，逐步執行：
   步驟 0   trading_day.py     → full / light / skip
   步驟 3-4 fetch_macro_snapshot.py + fetch_market_snapshot.py + fetch_fundamentals.py
            + WebFetch 台/中/日官方新聞稿 + 經濟日曆 consensus
   步驟 6   查 Notion 前一份報告 → 跨日比較
   步驟 6b  price_in.py（各優先題材催化劑）
   步驟 7-8 三情境 → 題材評分 → 個股評分
   步驟 6c  更新「題材檔案」（追加當日小節）
   步驟 6d  週一/月初：backtest.py 回測校準
   步驟 7a  產業資金流全量寫入 Notion（見下）
   步驟 9-11 產 18 章報告 → 寫 Notion（5 個資料庫）→ 更新狀態
   步驟 11  notify_discord.py 推摘要
        │
        ▼
 沙箱銷毀。跨日記憶靠 Notion（下次執行查回來）
```

失敗時：`notify_discord.py --kind alert`。**沒收到 Discord = 那次整個掛了**。

---

## 2. 元件清單

### 倉庫檔案

| 檔案 | 角色 |
|---|---|
| `config.json` | 唯一設定來源：watchlist、門檻、Notion data source id |
| `CLOUD.md` | 雲端代理的執行入口（步驟 0–12） |
| `scripts/fetch_news.py` | 台股／總經新聞（鉅亨網 API + 經濟日報／中央社 RSS） |
| `SKILL.md` | 完整 11 步流程規範 |
| `references/research-method.md` | 時間契約、`analysis_as_of` 規則、新鮮度閘門、信心等級 |
| `references/data-sources.md` + `sources.json` | 來源清單與證據分級（A–E） |
| `references/macro-fetch.md` | 台/中/日官方新聞稿 WebFetch 對照表 + consensus 抓法 |
| `references/stock-screening.md` | 題材與個股評分規則（配分、折減、狀態門檻） |
| `references/theme-dossier.md` | 題材檔案累積規則 |
| `references/backtest-calibration.md` | 回測與 price-in 方法 |
| `references/report-contract.md` | 報告 18 章固定格式 |
| `references/output-delivery.md` | 交付到 Notion / Markdown 的細節 |
| `references/state-memory.md` | 跨日狀態記錄 |
| `scripts/fetch_market_snapshot.py` | 台股行情 + 量化訊號 |
| `scripts/fetch_macro_snapshot.py` | FRED + 美財政部殖利率曲線 |
| `scripts/fetch_fundamentals.py` | FinMind：PER 分位、法人、融資券、月營收、股利 |
| `scripts/fetch_industry_flow.py` | 官方產業分類 + 全市場取樣 → 三大法人產業資金流排行 |
| `references/industry-flow.md` | 產業資金流方法論、口徑限制、Phase 1/2 範圍 |
| `scripts/trading_day.py` | full / light / skip 決策 + 唯一的日期／標題計算來源（`report_date`／`report_title`／`notion_date_property`） |
| `scripts/summarize_run_status.py` | 彙總 market/macro/fundamentals/news/industry_flow 的降級狀態 → 建議的 `狀態`（完整/部分/資料不足）與 `工具降級` |
| `scripts/price_in.py` | 事件研究：催化劑是否已反映 |
| `scripts/backtest.py` | 過去呼叫 vs 前瞻報酬的命中率 |
| `scripts/screen_universe.py` | 全市場粗篩（第一階段）→ 30 檔 shortlist |
| `scripts/validate_report.py` | 產出後機械 QA（章節/機率/欄位/裸網址） |
| `scripts/healthcheck.py` | 獨立健康檢查 + Discord 告警 |
| `scripts/notify_discord.py` | Discord webhook 推播 |
| `scripts/.env` | 本機密鑰（gitignore；雲端用環境變數） |
| `scripts/state/latest.json` | 離線狀態備份（gitignore；主記憶在 Notion） |
| `run/*.ps1` | 本機 Windows 排程備選方案（目前未用） |

### Notion 資料庫（都在「Claude的台股財報分析資料庫」頁面下）

| 資料庫 | data source | 內容 |
|---|---|---|
| 台股每日研究報告 | `collection://12f5e0a1-…` | 每天一頁完整報告 |
| 證據帳本 | `collection://f4318726-…` | 每個總經/市場資料點一列，累積時間序列 |
| 候選股追蹤 | `collection://b9dee6fc-…` | 每檔候選一列一報告日，含評分/估值分位/籌碼/訊號/進場價 |
| 題材檔案 | `collection://608d5a9c-…` | 每個題材一頁，內文每天追加小節 |
| 產業資金流 | `collection://662bd917-…` | 每個產業每個資料基準日一列，全量 ~30-35 個產業，供未來 backtest 驗證排名預測力，90 天保存上限 |

### 外部服務

| 服務 | 用途 | 金鑰 |
|---|---|---|
| FRED API | 美國 CPI/就業/殖利率、油價、Euro HICP、匯率 | `FRED_API_KEY`（免費） |
| 美國財政部 | 每日公債殖利率曲線 | 無 |
| TWSE openapi + www.twse.com.tw/rwd | 台股行情、指數、大盤統計、本益比、交易日曆 | 無 |
| TPEx openapi | 上櫃行情 | 無 |
| FinMind | 個股歷史 PER/PBR、三大法人、融資券、月營收、股利、個股/指數日 K | 無（`FINMIND_TOKEN` 可提高額度） |
| Notion connector（claude.ai） | 讀寫 5 個資料庫 | routine 自動繼承 |
| Discord webhook | 推摘要與告警 | `DISCORD_WEBHOOK_URL` |
| WebSearch / WebFetch | 台/中/日官方新聞稿、consensus、新聞脈絡 | Claude Code 內建 |

### Routines（3 個，同一 repo，都自動繼承 Notion connector）

| routine | ID | cron (UTC) | 台北時間 | 做什麼 |
|---|---|---|---|---|
| 台股每日研究報告 | `trig_01CccU6EWREggXEy4ngGR6Uj` | `0 23 * * *` | 每天 07:00 | 主流程,產報告 |
| 台股研究報告健康檢查 | `trig_018NyTBHyQW7ByA5CgStqY7A` | `0 1 * * *` | 每天 09:00 | 驗證主流程有沒有成功,異常發 Discord |
| 台股研究週報 | `trig_01MfvQBofrNLnwgzPcDLuS7s` | `0 11 * * 0` | 週日 19:00 | 彙整當週,追加週報頁 + 回測 |

model 都是 `claude-sonnet-5`,環境 `env_01TKmeeSre37E8NmmXh5PWVk`,prompt 內含 `FRED_API_KEY`／`DISCORD_WEBHOOK_URL`。

---

## 3. 完整執行流程（`full` 模式）

### 步驟 0 — 決定要不要跑

`trading_day.py --last-report-date <前一份資料基準日> --allow-light`

運算：
1. 抓交易日曆（rwd → openapi），取「放假/休市」日集合 `closed`。
2. `expected_last_trading_day`：從現在往回，跳過週末與 `closed`；若現在 < 15:00 則從昨天起算。
3. `recommendation`：
   - 前一份報告的資料基準日 == `expected_last_trading_day` → `light`（或 `skip`）
   - 否則 → `full`
4. 日曆抓不到 → 保守當 `full`。

`light` = 只跑 macro + 新聞掃描，加一頁「（輕量）」，不重算情境、不做個股篩選。
`skip` = 只發一則 Discord 說明。

同時輸出 `report_date`／`title_suffix`／`report_title`／`notion_date_property`——這是頁面標題與 `資料基準日` 屬性的唯一計算來源，之後每一步（含 `validate_report.py --expected-base-date`、健康檢查）都直接引用這幾個欄位，不重新判斷日期。這是為了修掉一類真實發生過的 bug：輕量模式的日期規則本來就寫在文件裡，但 2026-09-13 執行時代理沒套用，把執行日寫進了屬性，觸發健康檢查誤報。詳見 `git log` 的 `Fix light-mode date mislabeling` 與 `Move error-prone logic into code` 相關 commit。

### 步驟 1–2 — 定義問題與資料截止線

- `cadence = daily`
- `analysis_as_of` = 「最近一個已收盤台股交易日 15:00 Asia/Taipei」（不是掛鐘時間）
- 全球範圍：美、中、歐元區、日本
- 期間：戰術 1–4 週、策略 3–12 個月
- 股票池：代號首位 1–9 的四位數上市櫃

### 步驟 3–4 — 蒐集證據

**`fetch_macro_snapshot.py`** →
- FRED（15 序列）：美 CPI/核心/核心PCE/非農/失業率/fed funds/2Y/10Y/損益兩平；Brent/WTI；Euro HICP；TWD/CNY/JPY 匯率。每序列取最新值、前值、YoY（level 序列）、`fred_last_updated`（發布時間代理）。
- 美財政部：當日完整殖利率曲線 + 10Y–2Y 利差。
- 無金鑰 → FRED 區塊標不可用，仍出財政部曲線。

**`fetch_market_snapshot.py`** →
- 每個 TWSE 來源三層鏈：`openapi.twse.com.tw` → `www.twse.com.tw/rwd` → FinMind。每次 3 重試 + HTML 擋頁偵測。
- 個股收盤（STOCK_DAY_ALL）、指數（MI_INDEX）、大盤近 5 日統計（FMTQIK）、當日本益比（BWIBBU）、交易日曆、TPEx 上櫃行情。
- watchlist 每檔：近 ~85 交易日 K（FinMind 優先）→ 算 `liquidity_history`（20 日中位成交金額）與 `signals`（見 §4）。
- 輸出 `freshness`（見 §4 新鮮度閘門）、`status`（ok/degraded/empty）、`errors`。

**`fetch_fundamentals.py`**（FinMind，每檔 watchlist 5 個 dataset）→
- `valuation_history`：近一年 PER/PBR/殖利率 → 最新值的**分位數**、1 年高低與中位。
- `institutional`：三大法人 1/5/20 日淨買超（股數）。
- `margin_short`：融資餘額、5/20 日變化、券資比。
- `month_revenue`：最新月營收 YoY、MoM、近 3 月平均 YoY。
- `dividend`：最新現金/股票股利、除息日。

**`fetch_industry_flow.py`**（獨立於上面，樣本更寬但每檔只打 1 個 dataset）→
- 官方產業分類（FinMind `TaiwanStockInfo`）× 全市場取樣（TWSE `STOCK_DAY_ALL`，依成交值每產業取前 N 檔）× 三大法人 5/20 日淨買超彙總（換算 NT$ 名目金額）。
- 輸出依產業排名，含 `net_buy_5d_vs_daily_turnover_pct`（強度）、`positive_ratio`、樣本 <2 檔的 `observation_only` 標註。方法論與限制見 `references/industry-flow.md`；Phase 1 餵報告文字，Phase 2（已啟用）同時全量寫入「產業資金流」Notion 資料庫。

**WebFetch**（`macro-fetch.md` 對照表）→
- 台灣：主計總處 CPI/GDP、財政部進出口、經濟部外銷訂單/工業生產、國發會景氣、CIER + S&P PMI、央行利率/貨幣。
- 中國：NBS CPI/PPI/PMI、PBoC LPR。日本：CPI/GDP/BoJ。美國：FOMC。關稅政策。
- consensus：investing.com / tradingeconomics 經濟日曆的 forecast 欄（標 D 級）。

### 步驟 5 — 證據帳本

每項關鍵數據記：指標／國家／資料期／實際值／預期值／前值／是否修訂／歷史分位／發布時間／來源／證據層級（A–E）／可用性警告。同步寫「證據帳本」Notion 子資料庫（鍵 = 指標 + 資料期）。

### 步驟 6 — 跨日比較

查「台股每日研究報告」資料庫中日期早於今日的最新一筆，讀其內文，擷取：前次三情境機率、題材清單、候選股、待驗證指標、未解問題。產出「§2.5 與前一份報告的變化」。

### 步驟 6b — price-in

對每個優先題材的**已發生**主要催化劑：
`price_in.py --code <代表股> --event-date <日期>` → 見 §4。

### 步驟 7 — 三情境

- 基準／樂觀／悲觀，機率**合計 100%**。
- 每個含：戰術+策略假設、成長/通膨/政策/金融條件方向、對台傳導、2–5 個驗證指標、**明確失效條件**、可能受益/受損題材。
- 對照前次機率，任何 ≥5 個百分點調整指名觸發事件。
- 無法定義失效條件 → 該情境不可用。

### 步驟 8 — 題材與個股評分

見 §4「評分邏輯」。

### 步驟 6c — 題材檔案

對每個 WATCH 以上題材，在「題材檔案」資料庫查既有頁 → 追加當日小節（只寫變化）或建新頁（傳導鏈 + 供應鏈對照 + 相關代號）。**不重寫先前小節。**

### 步驟 6d — 回測校準（僅週一 / 每月 1 日）

從「候選股追蹤」查 65 日前、未評估過的呼叫 → `backtest.py` → 見 §4 → 寫報告 §13.7。

### 步驟 7a — 產業資金流全量寫入

讀 `industry_flow.json` 的全部 `industries[]`（非報告顯示的 top5/bottom3 子集）→ 依 `資料基準日` 查重 → 新產業批次 `notion-create-pages`、既有產業（同日重跑）逐筆 `notion-update-page` → 標註當日 `報告顯示範圍`。見 `output-delivery.md` 第 4 項。`industries` 為空時整段跳過，不擋交付。

### 步驟 9–11 — 產報告、交付、更新狀態

- 18 章（§0 關鍵數字看板 … §17 交付與狀態更新），見 `report-contract.md`。
- 寫 5 個 Notion 資料庫，報告頁回連前一份。
- 更新 `state/latest.json`（離線備份）。
- `notify_discord.py --kind digest`：三件事 + 情境機率 + 優先題材 + 觀察清單前 3 + Notion 連結。

---

## 4. 邏輯分析與運算流程（逐項）

### 4.1 新鮮度閘門（`fetch_market_snapshot.build_snapshot`）

```
expected_last_trading_day = 從 now 往回第一個「工作日且非休市日」
                            （now.hour < 15 → 從昨天起算）
對每個市場 m:
    gap(m) = data_date(m) → expected 之間的交易日數
    gap ≥ 1  → freshness.stale = true，該市場標「落後 N 日」
TWSE 與 TPEx 資料日不同 → 不合併廣度/成交統計

status = empty        (無任何市場列)
       | degraded     (stale 為真，或有 hard_errors — 即某來源三層鏈全掛)
       | ok
```
報告端：`stale` 為真 → 台股章節一律標「YYYY-MM-DD（前 N 交易日）盤後資料」，不得支持「今日」市場敘事。

### 4.2 量化訊號（`_compute_signals`，每檔 watchlist）

以近 ~85 交易日收盤 `C[]`、量 `V[]`、加權指數收盤 `I[]`：

| 訊號 | 公式 |
|---|---|
| `return_Nd_pct` | `(C[-1] / C[-1-N] − 1) × 100`，N = 5, 20, 60 |
| `rel_strength_20d_pct` | 個股 20 日報酬 − 指數 20 日報酬 |
| `vs_ma20_pct` / `vs_ma60_pct` | `(C[-1] / MA_n − 1) × 100`，`MA_n` = 近 n 日收盤均 |
| `volume_ratio_vs_20d` | `V[-1] / (近 20 日均量)` |
| `realized_vol_20d_annual_pct` | `stdev(近 20 日日報酬) × √252 × 100` |

解讀規則（`stock-screening.md`）：RS 明顯負但屬題材股 = 警訊；`return_60d` 已大幅正 + `vs_ma20` 高 = 漲多追價風險，估值維度更嚴；`volume_ratio ≫ 1` + 大漲 = 過熱；訊號互相矛盾（漲但法人賣、量縮）→ 降市場驗證分。

### 4.3 題材評分（`stock-screening.md` 二）

100 分原始分 − 0~30 風險折減：

| 維度 | 配分 |
|---|---:|
| 證據強度（多個 A/B 級來源、時間一致） | 20 |
| 傳導清晰度（可量化連結需求/價格/成本到獲利） | 20 |
| 廣度與持續性（非一次性、戰術+策略都有依據） | 15 |
| 政策確定性（已通過/生效、範圍時程明確） | 15 |
| 企業基本面確認（營收/訂單/法說已支持） | 15 |
| 估值與擁擠度（未完全反映、籌碼估值不極端） | 10 |
| 催化劑可驗證性（有日期/數值/事件） | 5 |

風險折減：政策仍為提案、地緣尾端、原料匯率反向曝險、過度擁擠、估值極端、資料過時、因果脆弱。

狀態：淨分 ≥75 → `PRIORITY_RESEARCH`（需 ≥1 個 A/B 級驅動來源 + ≥1 個公司層級確認）；60–74 → `WATCH`；<60 → `ABSTAIN`。缺政策狀態/受益機制/失效條件 → 最高只能 `WATCH`。

### 4.4 個股評分（`stock-screening.md` 四）

100 分原始分 − 0~30 風險折減：

| 維度 | 配分 | 主要資料來源 |
|---|---:|---|
| 題材直接曝險 | 22 | 產品/地區/客戶/產能/營收占比 |
| 營運與獲利確認 | 18 | `month_revenue` YoY/MoM/3月均 YoY + 訂單毛利展望 |
| 財務品質 | 12 | 現金流/槓桿/資本支出/治理/`dividend` |
| 估值 | 15 | `valuation_history.per_1y_percentile` + 1 年 PER 區間；無 FinMind → 當日 PER/PBR + 降信心 |
| 籌碼面 | 10 | `institutional` 5/20 日淨買超方向、`margin_short` 融資變化、券資比 |
| 市場驗證（量化） | 10 | `signals`：相對強弱、動能、量能、MA 距離 |
| 流動性與可執行性 | 8 | 20 日中位成交金額、交易狀態 |
| 催化劑 | 10 | 財報/法說/投產/訂單/政策/除息日 |

風險折減新增：**PER 近一年分位 ≥90**、**融資餘額 20 日急增**、**近月營收 MoM 大幅轉弱**。

流動性兩段式：有 20 日歷史 → 中位成交金額 < 2,000 萬 硬排除；只有單日 → 單日成交金額代理，2,000 萬–1 億 → 保留但信心低、狀態上限 WATCH。

狀態：淨分 ≥75 + 無硬排除 + **有近一年估值分位佐證** → `PRIORITY_RESEARCH`；60–74 或分數達標但無 FinMind 估值分位 → `WATCH`；<60/硬排除/關鍵缺失 → `ABSTAIN`。任何情況都不輸出 `BUY`。

### 4.5 price-in 事件研究（`price_in.py`）

`t0` = 事件日當天或之後第一個交易日。

| 輸出 | 公式 |
|---|---|
| `pre_event_drift_pct` | `(C[t0-1] / C[t0-6] − 1) × 100`（t-5 → t-1） |
| `event_day_return_pct` | `(C[t0] / C[t0-1] − 1) × 100` |
| `post_event_return_pct` | `(C[t0+5] / C[t0] − 1) × 100`（資料不足則縮短） |
| `abnormal_return_vs_taiex_pct` | 個股全窗（t-6 → t+5）報酬 − TAIEX 同窗報酬 |
| `priced_in_heuristic_0_100` | `realized / (realized + ahead) × 100`，`realized = |pre| + |event|`，`ahead = |post|` |

`reading`：`|pre|` 或 `|event|` ≥4% 且 `|post|` < 2% → 「已 price-in」；abnormal > 5% → 「題材溢價已顯著累積」；反應平淡 → 「尚未反映或市場不認為重要」。

用途：題材排名 §10「已被反映程度」欄、個股「已被反映程度」欄；`priced_in ≥ 70` → 題材分「估值與擁擠度」維度扣分。

### 4.6 回測校準（`backtest.py`，每週一次）

輸入：`[{code, date, status, net_score, entry_close, theme}]`（從候選股追蹤 DB 匯出 65 日前的列）。

每筆：FinMind 抓收盤序列，`i0` = 呼叫日交易日 index：
- `fwd_Nd_pct = (C[i0+N] / C[i0] − 1) × 100`，N = 5, 20, 60
- `abn_Nd_pct = fwd_Nd − TAIEX fwd_Nd`

分桶彙總（by 狀態、by 淨分 75+/60-74/<60）：`n`、`mean_return_pct`、`hit_rate_pct`（>0 佔比）、`mean_abnormal_pct`、`beat_index_rate_pct`。

`calibration_notes`（機械檢查）：
- PRIORITY_RESEARCH 的 20 日超額 ≤ WATCH → 「評分未有效區分」
- 淨分 75+ 命中率 ≤ 60–74 → 「評分與前瞻報酬相關性弱」
- ABSTAIN 事後平均超額 > 2% → 「排除規則過嚴」
- 每桶 < 5 筆 → 不下結論

輸出寫報告 §13.7。連續 2 期指出無區分力 → 建議調整評分配分（**待使用者確認後改檔**，不自動改）。

---

## 5. 報告 18 章（`report-contract.md`）

`§0 關鍵數字看板` → `§0.5 本期最重要三件事` → `§1 報告識別` → `§2 執行摘要` → `§2.5 與前一份的變化` → `§3 證據帳本` → `§4 台灣總經儀表板` → `§5 全球總經儀表板` → `§6 政策分析` → `§7 新聞解讀` → `§8 跨資產與台股市場驗證（含籌碼面、量化訊號小結）` → `§9 三情境` → `§10 題材排名（含已被反映程度、題材檔案連結）` → `§11 題材個股篩選` → `§12 未入選與排除清單` → `§13 未來事件日曆` → `§13.5 可執行觀察清單` → `§13.7 回測校準（週報）` → `§14 反證/不確定性/資料缺口` → `§15 資料品質與來源` → `§16 結論（優先研究/持續觀察/ABSTAIN 三行 + 免責）` → `§17 交付與狀態更新`

---

## 6. 跨日累積機制

沙箱每次全新、無狀態，靠三處累積：

1. **報告資料庫**：每份報告回連前一份 → 序列。下次執行查最新一筆做 diff。
2. **證據帳本 / 候選股追蹤資料庫**：每個資料點/每檔候選一列一報告日 → 時間序列，可查趨勢、算回測。
3. **題材檔案**：每題材一頁，內文每天追加日期小節（供應鏈對照、論述演變、催化劑進度）→ 不必每天重建脈絡。
4. **產業資金流**：每個產業每個資料基準日一列，全量寫入供未來 backtest 用。與前三者不同——這個沒有永久保留層級，90 天硬性保存上限（見 `weekly-report.md`），過窗即刪。

---

## 7. 降級與失敗矩陣

| 情況 | 處理 |
|---|---|
| 非交易日隔天 | `light` 模式，只更新總經/新聞 |
| TWSE openapi 被擋 | 自動切 rwd → FinMind；仍失敗 → 該來源標錯誤、`status=degraded`、對應章節缺資料 |
| 行情落後 | `freshness.stale=true`，台股章節標「前 N 交易日」，情境不倚賴「今日」價量 |
| 無 FRED 金鑰 | FRED 區塊不可用，仍出財政部曲線；缺口逐項 WebFetch |
| 無 FinMind 資料 | 個股基本面/籌碼章節標「未取得」，估值退回當日 PER/PBR，個股狀態上限 WATCH |
| 台灣官方新聞稿頁改版抓不到 | 該指標當期標「未取得」，報告狀態降「部分」 |
| Notion 連接器不存在 | 輸出 Markdown 檔 + 發 alert + 結束 |
| 任何無法繼續的錯誤 | 放棄前 `notify_discord.py --kind alert` |
| 沙箱層級失敗 | 連告警都發不出 → 使用者「當天沒收到 Discord」即為訊號 |

---

## 8. 已知邊界

- 報告未經人工複核（每份標「自動產生‧未複核」），股票結論只到研究候選，不給買賣建議、目標價、報酬預測。
- FinMind 是 TWSE/TPEx/MOPS 資料的整理，非第一手；標 B 級。
- 台灣官方一手數據靠 WebFetch，頁面改版即斷。
- 情境機率由 Claude 判斷，非量化模型；回測是校準輸入，非最佳化。
- 前瞻報酬/事件研究用收盤對收盤，無交易成本與滑價。
- 每次執行消耗 Claude 用量（token），非另計 API 費；週末假日 `light` 也用少量。
