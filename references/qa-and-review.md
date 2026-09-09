# 產出品質驗證與複核流程

三道關卡，讓「LLM 有沒有照規則做」「有沒有靜默壞掉」不再是盲區。

## 一、產出後 QA（每次執行，交付前）

把報告草稿寫成 Markdown 檔（`/tmp/draft.md`），交付 Notion **之前**跑：

```
python scripts/validate_report.py --report /tmp/draft.md --market /tmp/market.json --mode <full|light>
```

機械檢查：18 章關鍵字是否齊、「自動產生‧未複核」是否標、三情境機率是否合計 100、候選股列是否有合法狀態值且欄位不過度空白、正文有無裸網址（來源清單除外）、報告是否揭露 `stale`／`degraded`、執行摘要點數。

依 `verdict` 動作：

| verdict | 動作 | 報告 `複核狀態` 屬性 |
|---|---|---|
| `pass` | 正常交付 | `待複核` |
| `warn` | 交付，把 warnings 併進報告 §14「資料缺口」 | `待複核` |
| `fail` | **仍交付**（不要丟失報告），但：頁面開頭加 callout 列出 fail 項；Discord 摘要標「⚠️ QA 未通過」 | `有疑慮` |

把 `validate_report.py` 的完整 JSON 貼進報告 §15「資料品質」的一個 toggle。

## 二、人工複核（非同步）

- 報告 DB 有 `複核狀態` 屬性：`待複核`／`已複核`／`有疑慮`／`已駁回`。
- 排程產出一律 `待複核`（QA fail → `有疑慮`）。
- 你（或本機 `/discord` 對話）看過後手動改成 `已複核`。
- Discord digest 一定提醒目前是「未複核」狀態，並附 Notion 連結。
- **報告永遠先交付再複核** —— 不因待複核而不寫入；複核是事後品質標記，不是發布 gate。

## 三、獨立健康檢查（另一個 routine，主流程後 2 小時）

主流程若在「能發告警」之前就死掉（沙箱崩潰、clone 失敗、Claude 認證失效），它自己發不出 alert。獨立 routine 補這個洞。

健康檢查 routine 的代理：
1. 算今天預期的資料基準日（最近已收盤交易日）。
2. 查「台股每日研究報告」DB 最新一筆：資料基準日對不對、`狀態` 是不是 `資料不足`、建立時間是不是 26 小時內。
3. 查「證據帳本」「候選股追蹤」今日 `報告日` 有沒有列（≥5 / ≥3）。
4. 把這些事實寫成 `facts.json`，跑 `python scripts/healthcheck.py --facts facts.json`。
5. `healthcheck.py` 判斷有無異常，有 → 自動發 Discord alert。無 → 靜默。

健康檢查**不重跑報告**、不修任何東西，只驗證與告警。

## 四、資料快取後備

每次 `full` 執行，把 `market.json`、`macro.json`、`fundamentals.json` 的**關鍵欄位**（不是整包）貼進報告頁 §15 的一個 toggle（標題「原始快照」）。

下次執行若某來源三層鏈全掛：讀**前一份報告**的「原始快照」toggle，取該來源前一日的值，在證據帳本標「來源不可用，採前一交易日值（YYYY-MM-DD）」，信心降一級。優於直接留空。
