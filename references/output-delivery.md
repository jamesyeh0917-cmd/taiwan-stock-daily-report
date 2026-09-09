# 報告交付

依序：Notion（有設定時）→ Markdown 檔（後備）。無論哪種，內容順序都照 report-contract.md。

## 一、交付目標的判定

1. 使用者本次或先前指定了 Notion 頁面 ID 或資料庫 ID → 用 Notion 模式。
2. 未指定，但環境有 `notion-*` 工具 → 詢問使用者要寫到哪；未答覆前先產生 Markdown 檔。
3. 沒有 Notion 工具 → Markdown 檔模式。

不要自行決定寫到某個既有 Notion 頁面。建立新頁面前，如果是自動排程且已有指定的父層資料庫／頁面，可直接在其下建立當日新頁，不需再問。

## 二、Notion 模式

### 頁面標題
`台灣與全球總經投資研究｜YYYY-MM-DD`（YYYY-MM-DD 為資料基準日，非執行日）。同日修訂版加 ` (修訂 N)`。

### 若父層是資料庫，對應屬性（存在才填，不存在不強制建立）
| 屬性 | 值 |
|---|---|
| 日期 / Date | 資料基準日 |
| 狀態 / Status | `完整` `部分` `資料不足` 之一 |
| analysis_as_of | ISO 時間字串 |
| 台灣方向 | 一句話景氣/通膨/金融條件方向 |
| 全球方向 | 一句話全球成長/通膨/政策方向 |
| 基準情境機率 | 數字 |
| 優先題材 | 多選或逗號字串 |
| 前一份報告 | 關聯 / URL |
| 自動產生 | 勾選（未經人工複核時） |

### 內文
- 用 Notion 區塊：H1/H2 對應章節、table 對應報告表格、bullet/toggle 收納長列表。
- 「執行摘要」放最前面，且加一個 callout 標示報告狀態與（若適用）「自動產生‧未複核」。
- 「與前一份報告的變化」章節放一個指向前一頁面的連結。
- 來源連結用內嵌超連結掛在數值或短語上，不要貼裸網址清單（資料品質章節除外）。
- 行情快照 `freshness.stale` 為真時，台股章節開頭放一個 callout：「本節為 YYYY-MM-DD（前 N 個交易日）盤後資料」。

### 建立後
把新頁面 URL 回報給使用者。更新前一份報告的「後續報告」關聯（若該屬性存在）。

### 子資料庫與題材檔案（累積型知識庫）

報告頁的表格是快照；長期價值在這三組累積結構。位置與 ID 由 state-memory.md 的狀態記錄保存，第一次執行時建立、之後沿用。

1. **證據帳本資料庫**（`證據帳本`）
   - schema：`指標`(title)、`區域`(select)、`資料期`(date)、`實際值`(number)、`前值`(number)、`YoY%`(number)、`預期值`(text)、`歷史位置`(text)、`發布時間`(date)、`來源`(url)、`證據層級`(select A/B/C/D)、`報告日`(date)、`判讀`(text)
   - 每個資料點一列，鍵為 `指標 + 資料期`；同一資料期重跑用 update 不新增。
   - 用途：跨月看 CPI／出口／殖利率的實際時間序列，不必翻舊報告。
2. **候選股追蹤資料庫**（`候選股追蹤`）
   - schema：`代號`(title)、`公司`(text)、`報告日`(date)、`題材`(multi_select)、`狀態`(select PRIORITY_RESEARCH/WATCH/ABSTAIN)、`淨分`(number)、`直接曝險`(text)、`估值`(text)、`PER近一年分位`(number)、`月營收YoY_%`(number)、`籌碼面`(text)、`量化訊號`(text)、`流動性窗口`(text)、`主要催化劑`(text)、`失效條件`(text)、`對比前次`(text)、`報告連結`(url)
   - `PER近一年分位`／`月營收YoY_%` 從 fundamentals.json 帶入；`籌碼面`／`量化訊號` 各一句摘要。
   - 一檔一列一報告日；可篩 status、排序淨分、看單一標的估值分位與營收動能歷史。
3. **題材檔案子頁面**：每個曾達 `PRIORITY_RESEARCH` 的題材一個常駐子頁（放在報告資料庫的父頁下，或獨立區塊）。每次報告在該頁**追加**一段日期小節：當日評分、傳導鏈更新、新證據、催化劑進度、風險變化。不重寫整頁。題材降為 ABSTAIN 後保留頁面並標「已淡出（日期＋原因）」。

建立子資料庫時父層用報告資料庫的父頁（與報告資料庫同層）。把三者的 URL／data_source id 寫進狀態記錄。

## 三、Markdown 檔模式

- 檔名：`taiwan-macro-report-YYYY-MM-DD.md`，寫在工作目錄（或使用者指定路徑）。
- 檔案開頭 YAML frontmatter：`report_date`、`analysis_as_of`、`timezone`、`status`、`tools_degraded`、`previous_report`。
- 正文照 report-contract.md。
- 完成後用 SendUserFile 交給使用者，並一行說明狀態與是否 stale。

## 三點五、Discord 摘要推播（排程模式）

`DISCORD_WEBHOOK_URL` 存在時（排程），交付完成後發一段濃縮摘要：

```
python scripts/notify_discord.py --kind digest --message "<摘要>"
```

摘要（繁中、≤1500 字、可用 Discord markdown）：
- 標題行：資料基準日 + 報告狀態 +（stale 時）新鮮度
- 加權指數收盤與日變動、USD/TWD
- 「本期最重要的三件事」三行
- 三情境機率（基準/樂觀/悲觀）
- 優先題材（PRIORITY_RESEARCH）名稱
- 可執行觀察清單前 3 條
- Notion 報告頁連結

輕量模式用 `--kind light`，跳過當日用 `--kind skip`（一行原因），執行失敗用 `--kind alert`。

## 四、兩種模式都適用

- 交付後務必依 state-memory.md 更新狀態記錄。
- 若交付失敗（Notion 寫入錯誤、無寫入權限），退回 Markdown 檔模式並明確告知使用者原因，不要靜默丟棄報告。
- 絕不在報告或頁面中放個人化下單指令、目標價、保證報酬。結尾保留 report-contract.md 第 16 節的研究用途聲明。
