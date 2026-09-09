# 總經資料抓取手冊

分工：
- `scripts/fetch_macro_snapshot.py` 抓**乾淨、帶時間戳、可修訂**的來源（FRED API + 美國財政部殖利率曲線）。輸出 `macro_snapshot.json`。
- 本文件列出**必須用 WebFetch 直抓官方新聞稿**的項目（FRED 已不再即時維護台／中／日的多數指標）。
- consensus（事前市場預期）用經濟日曆頁抓，標 D 級。

先跑腳本，再依「本期缺口」對照下表逐項 WebFetch，最後把兩者併進證據帳本。

## 一、腳本已涵蓋（FRED，tier A；Euro HICP tier C）

美國：CPI、核心 CPI、核心 PCE、非農就業（水準）、失業率、有效聯邦資金利率、2Y／10Y 公債殖利率、10Y 損益兩平通膨；全球：Brent／WTI 原油；歐元區：HICP；匯率：TWD、CNY、JPY 兌美元。美國財政部：當日完整殖利率曲線 + 10Y–2Y 利差。

腳本輸出每筆含 `observation_period`、`actual`、`previous`、`yoy_pct`／`mom_pct`、`fred_last_updated`、`source_url`。`fred_last_updated` 即發布時間的可靠代理。

## 二、必須 WebFetch 的官方新聞稿

| 指標 | 機關 | 抓取頁 | 發布時點（約） |
|---|---|---|---|
| 台灣 CPI／核心 CPI | 主計總處 | <https://www.stat.gov.tw/News.aspx?n=2680&sms=10980>（物價新聞稿）或 <https://eng.stat.gov.tw/News.aspx?n=2317&sms=10986> | 每月 5 日前後 |
| 台灣 GDP（初值／修正） | 主計總處 | <https://eng.dgbas.gov.tw/News.aspx?n=4438&sms=235012> | 季後：初值約每季末，修正約 2 月／5 月／8 月／11 月下旬 |
| 台灣進出口 | 財政部統計處 | <https://www.mof.gov.tw/htmlList/103>（進出口貿易統計新聞稿）；英文 <https://www.mof.gov.tw/Eng/htmlList/104> | 每月 7–9 日 |
| 台灣外銷訂單 | 經濟部統計處 | <https://www.moea.gov.tw/Mns/dos/content/wHandMenuFile.ashx> → 或首頁新聞稿 <https://www.moea.gov.tw/MNS/dos/news/News.aspx?kind=4> | 每月 20 日前後 |
| 台灣工業生產 | 經濟部統計處 | 同上新聞稿頁 | 每月 23 日前後 |
| 台灣景氣燈號／領先同時指標 | 國發會 | <https://www.ndc.gov.tw/News.aspx?n=114AAE178CD95D4C>（景氣動向） | 每月 27 日前後 |
| 台灣製造業 PMI（CIER） | 中華經濟研究院 | <https://www.cier.edu.tw/npo/pmi> | 每月 1 日 |
| 台灣 S&P Global 製造業 PMI | S&P Global | <https://www.pmi.spglobal.com/Public/Release/PressReleases>（搜 Taiwan） | 每月 1–2 個工作日 |
| 台灣央行利率決議 | 中央銀行 | <https://www.cbc.gov.tw/tw/lp-370-1.html>（新聞稿）；英文 <https://www.cbc.gov.tw/en/lp-153-2.html> | 季度理監事會（3／6／9／12 月中下旬） |
| 台灣 M1B／M2、放款與投資 | 中央銀行 | <https://www.cbc.gov.tw/tw/lp-645-1.html>（金融統計） | 每月 25 日前後 |
| 中國 CPI／PPI | 國家統計局 | <https://www.stats.gov.cn/sj/zxfb/>（最新發布） | 每月 9–10 日 |
| 中國 GDP／零售／投資／工業增加值 | 國家統計局 | 同上 | 每月中；GDP 每季 |
| 中國官方 PMI | 國家統計局 | 同上 | 每月最後一日 |
| 中國 LPR／MLF | 中國人民銀行 | <http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html> | LPR 每月 20 日 |
| 日本 CPI | 總務省統計局 | <https://www.stat.go.jp/english/data/cpi/index.html> | 每月中下旬 |
| 日本 GDP | 內閣府 | <https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/toukei_top.html> | 季後約 45 天 |
| 日本央行政策 | BoJ | <https://www.boj.or.jp/en/mopo/mpmdeci/index.htm> | 每次 MPM |
| 美國 FOMC 聲明／點陣圖 | Fed | <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm> | 每次會議 |
| 美國財政 / 關稅政策 | 白宮、USTR、商務部 | <https://www.whitehouse.gov/presidential-actions/>、<https://www.commerce.gov/news> | 不定期 |

抓不到英文頁時抓中文頁；抓到後記錄：實際值、前值、資料期、新聞稿發布日、URL。標題與內文不一致時以內文與統計表為準。

**優先順序**：官方統計表／新聞稿 PDF ＞ 政府資料開放平台 data.gov.tw 該資料集的 JSON（若有）＞ 可信媒體轉述（標 D 級，盡量兩個獨立來源交叉）。

## 三、事前市場預期（consensus）

沒有官方 consensus。用經濟日曆頁抓「forecast / 預測」欄，標 **D 級**，並記錄擷取時間與頁面：

- <https://www.investing.com/economic-calendar/>（可依國別、當週篩選；欄位有 Actual / Forecast / Previous）
- <https://tradingeconomics.com/calendar>
- <https://www.forexfactory.com/calendar>

只取「發布前已存在」的 forecast；已公布後頁面上的 forecast 可用（那是發布前的預測留存），但事後分析評論不可當預期。取不到就填 `N/A`，不要臆測。

## 四、Fed 與央行路徑機率

- CME FedWatch：<https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html>（JS 重，WebFetch 可能只拿到殼；拿不到就用下列）
- Polymarket：市場頁 <https://polymarket.com/>（搜 "Fed rate"）；或可信財經媒體轉述的 fed funds futures implied probability，標 D 級並記錄時間。

## 五、degradation

- 無 FRED key：US 殖利率／CPI／油價／Euro HICP 從腳本消失 → 逐項改用 FRED 網頁 <https://fred.stlouisfed.org/series/DGS10> 等 WebFetch，或財經媒體，降信心。
- 某個官方新聞稿頁改版抓不到：改抓該機關「新聞稿列表」頁找最新一篇，或用兩個獨立可信媒體交叉，標未完全驗證。
- 全區域缺 → 對應儀表板格子標「本期未取得」，不影響其他章節完成度，但報告狀態降為 `部分`。
