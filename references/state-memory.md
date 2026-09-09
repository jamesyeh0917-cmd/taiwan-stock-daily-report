# 跨日記憶與狀態

每日報告不是獨立文件，而是連續序列。每次執行都要先讀前一份、再產生差異、最後更新狀態。

## 一、狀態記錄的位置（依序嘗試）

1. **Notion 資料庫**（有設定時）：查詢使用者指定的資料庫，取 `日期` 屬性最新、且早於今日的一筆頁面，讀其內文。
2. **本機狀態檔**：`scripts/state/latest.json`（腳本目錄下）。若 skill 目錄唯讀，改用工作目錄的 `taiwan-stock-report-state.json`。
3. **使用者手動提供**：請使用者貼上前一份報告連結或內容。

三者皆無 → 標記「首次執行，無前期基準」，照常產生報告，並在結尾建立狀態記錄。

## 二、要從前一份擷取的欄位

```text
report_date                前一份報告的資料基準日
analysis_as_of
notion_page_url            前一份報告頁面
notion_database_url        報告資料庫
evidence_db                證據帳本子資料庫 {url, data_source_id}
candidates_db              候選股追蹤子資料庫 {url, data_source_id}
theme_dossier_db           題材檔案子資料庫 {url, data_source_id}
theme_pages                {題材名: 題材檔案頁 url}
last_backtest_date         上次跑回測校準的日期
backtest_evaluated_upto    回測已評估到的最大報告日（避免重複評估）
taiwan_regime              前次對台灣景氣/通膨/金融條件的方向判斷
global_regime              前次對全球成長/通膨/央行的方向判斷
scenarios                  [{name, probability, key_drivers, invalidation}]
themes                     [{name, status, net_score, one_line_thesis}]
candidates                 [{code, company, theme, status, net_score}]
key_ledger_watch           前次列為「下一個驗證點」的指標與事件
open_questions             前次未解、待後續資料的問題
```

`evidence_db`、`candidates_db`、`theme_pages` 第一次執行時建立並寫入狀態記錄，之後每次沿用同一組，不重建。

擷取後存為結構化物件，供報告的「與前一份報告的變化」章節使用。

## 三、差異規則

- **情境機率**：列出每個情境的前值 → 新值與變動幅度；任何 ≥5 個百分點的調整都要指名觸發事件。
- **題材**：分「新增」「移除」「升降級」「維持」四類；移除或降級要寫原因（失效條件觸發？被價格反映？資料轉弱？）。
- **候選股**：新進榜、出榜、狀態變化各自列出；出榜要寫是評分下降、硬性排除、還是題材整體移除。
- **證據帳本**：前次「待驗證」的指標這次是否已公布？公布值與當時預期方向是否一致？
- **未解問題**：逐條更新為「已解決／仍未解／新增」。

若前一份報告的資料基準日與這次相同（同一交易日重跑），只做「修訂與補充」，不重算情境，並在標題註明是同日修訂版。

## 四、更新狀態記錄

報告交付後，寫入本次的狀態物件（欄位同第二節），欄位值取本次報告的最終結論。

- Notion 模式：狀態即當日頁面本身，不需另存檔；但仍寫一份 `latest.json` 作為離線備援。
- 檔案模式：覆寫 `latest.json`，並另存 `history/<report_date>.json` 保留序列。

狀態記錄只存結論摘要與可追溯欄位，不存完整報告內文。
