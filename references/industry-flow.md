# 產業資金流排行（scripts/fetch_industry_flow.py）

**Phase 1（目前狀態）**：只餵報告 §8「產業資金流小結」，純文字呈現。**沒有專屬 Notion DB，不寫入任何資料庫。** Phase 2（延後至累積約 1 個月實際輸出後）：接進 `backtest.py` 驗證「產業排名是否真的能預測後續報酬」，驗證有效再決定要不要建專屬 DB——沿用這個 skill 一貫先擱置未驗證功能、之後回頭評估的作法（見 `ARCHITECTURE.md` §8 已知邊界）。

## 這在解決什麼問題

`fetch_fundamentals.py` 的三大法人數字只涵蓋 shortlist 前 12 檔（每檔要打 5 支 FinMind API，額度考量下硬性上限），對「哪個產業有資金流入」這種跨~30 個官方產業類別的彙總問題而言，樣本太窄——大多數產業會落到 0 檔。`fetch_industry_flow.py` 是獨立腳本，用便宜很多的方式（每檔只打 1 支法人買賣超 API，不是 5 支）涵蓋更寬的樣本。

## 產業分類：官方來源，不手刻對照表

分類來自 FinMind `TaiwanStockInfo` 的 `industry_category` 欄位（TWSE 官方分類，一次批次抓取全市場，不必維護）。排除 ETF／存託憑證／受益證券等非個股類別（`EXCLUDE_CATEGORIES`），`industry_category` 缺值的代號直接排除、計入 `errors`，不歸進無意義的「其他」桶。

沒有選擇手刻對照表：鄰居專案 `tw_market_radar`（`D:\claude use\tw_market_radar`，獨立專案，不合併）用 Notion 股票池 CSV 手刻 `industry`/`themes` 欄位，實際資料停在 6 檔股票的 stub 狀態——官方自動分類避免同樣的維護負擔問題。

## 取樣方法

1. `TaiwanStockInfo`（1 次批次呼叫）→ 全市場代號→產業對照。
2. TWSE `STOCK_DAY_ALL`（1 次批次呼叫，與 `screen_universe.py` 同一端點/備援模式）→ 全市場收盤價＋成交值。
3. 依成交值篩掉 `--min-turnover`（預設 NT$10,000,000）以下的代號，每個產業類別取成交值前 `--per-industry`（預設 3）檔——每個官方產業都有代表性樣本，總樣本約 90-150 檔。
4. 對這批代號，各打 1 次 FinMind `TaiwanStockInstitutionalInvestorsBuySell`（`ThreadPoolExecutor(max_workers=3)`，與其他腳本節流慣例一致），取 5/20 日三大法人（外資+投信+自營商）淨買賣超（原始股數）。

## 金額換算是近似值，不是逐日計價

三大法人數字原始單位是股數，跨產業不可直接比較（股價量級不同）。`net_buy_value_5d_twd = 5日淨買超股數 × 最新收盤價`——這是**當前價位的近似換算**，不是逐日用當日收盤價累加的精確金額。用途是排序與量級比較，不是精確會計。

同時輸出 `net_buy_5d_vs_daily_turnover_pct`（5 日累計淨買超金額 ÷ 樣本股**最新單日**成交值加總）作為強度指標：純看 NT$ 金額排名，半導體業這種台積電坐鎮的產業永遠霸榜，掩蓋掉中小型產業的異常流入比例。**注意分子是 5 日累計、分母是最新單日成交值**（`STOCK_DAY_ALL` 只給最近一個交易日，沒有歷史序列可算 5 日成交值加總）——這不是嚴格的「成交值佔比」，是以「這週淨流入相當於幾天的正常成交量」為概念的流動性尺度強度指標，數值可能超過 100%。報告寫作時兩個指標都要看，不能只看金額排名，且不得把它誤述為「成交值佔比」。

## 已知資料品質怪癖

實測發現 FinMind `TaiwanStockInfo` 把台積電(2330) 分在 `電子工業`，不是直覺預期的 `半導體業`——這是上游資料本身的分類，不是本腳本的 bug。使用官方分類就要接受官方分類的既有不一致；報告寫作時若某檔知名股票沒出現在「預期」的產業類別下，先查 `industry_flow.json` 裡它實際被分到哪一類，不要假設資料錯誤。

## 樣本不足時的信心標註

借用 `tw_market_radar`（`docs/theme_signals.md`）的規則：**產業樣本 < 2 檔時，`confidence` 標 `observation_only`，不做產業定論**——樣本內的一兩檔股票的個別事件可能被誤讀成整個產業的資金流向。

## 與 `fundamentals.json` 口徑不同，避免逐位核對

`fetch_industry_flow.py` 是獨立抓取，與 `fetch_fundamentals.py` 的三大法人數字可能有微小抓取時間差、且本腳本另外乘價轉金額，兩邊算出的數字不會完全一致。報告內只做**量級與方向**比較（例如：兩邊都顯示台積電近期為淨買超），不逐位核對到股數或金額小數點。

## 失敗時如何降級

任一步驟（`TaiwanStockInfo`、`STOCK_DAY_ALL`、個別代號的法人資料）失敗，腳本仍回傳合法 JSON（`industries: []` + `errors` 列出原因），不丟未捕捉例外中斷整條 CLOUD.md pipeline。報告寫作時若 `industries` 為空，§8 該小節寫「本次未取得產業資金流資料」，其餘章節照常產出。

## 與報告的關係

- 日報 §8「產業資金流小結」：列淨流入前 5、淨流出後 3 的產業（含 `net_buy_pct_of_turnover_5d`、`positive_ratio`、`stock_count`、`confidence`）。
- 對淨流入最高的 1-2 個產業，列其 `top_stocks`；**只有在該產業與當期優先題材重疊時**才銜接 §11 個股篩選——產業分類不能取代題材檔案（`theme-dossier.md`）的傳導鏈論證，兩者是不同層次的東西（廣泛靜態分類 vs. 事件驅動因果鏈）。
