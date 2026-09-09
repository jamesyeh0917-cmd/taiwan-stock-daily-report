# 官方資料來源與證據優先順序

所有來源在使用時都要記錄資料期、發布時間、擷取時間與 URL。優先開啟該次發布的新聞稿、統計表或決策文件，不要只引用入口首頁。

機器可讀的端點清單（含 API URL、資料格式、更新頻率、延遲註記）在 [sources.json](sources.json)。本文件是敘述性說明與證據分級規則。

## 證據層級

1. `A`：政府統計機關、央行、監理機關、交易所、法規與正式政策文件。
2. `B`：公司公告、財報、法說資料、公開資訊觀測站與正式產業組織資料。
3. `C`：IMF、OECD、BIS、世界銀行等國際機構研究與資料。
4. `D`：具編採與更正制度的可信媒體；只用於即時脈絡或尚未取得第一手文件的事件。
5. `E`：社群、論壇、未具名消息與二手轉述；只能當線索，不得單獨支持報告結論。

關鍵結論至少需要一個 A/B 級來源。只有 D/E 級資料時，降低信心並輸出 `WATCH` 或 `ABSTAIN`。

## 台灣總經與政策

- 行政院主計總處：GDP、CPI、PPI、就業、薪資與國民所得，<https://www.stat.gov.tw/>
- 中央銀行：政策利率、理監事會、匯率、利率、貨幣與信用統計，<https://www.cbc.gov.tw/>
- 國家發展委員會：景氣燈號、領先／同時指標、PMI／NMI、重大政策，<https://www.ndc.gov.tw/>
- 經濟部統計處：外銷訂單、工業生產、批發零售與製造業調查，<https://www.moea.gov.tw/MNS/dos/home/Home.aspx>
- 財政部統計處：進出口與財政統計，<https://www.mof.gov.tw/>
- 勞動部：勞動市場與勞動政策，<https://www.mol.gov.tw/>
- 金融監督管理委員會：金融監理、重大政策與統計，<https://www.fsc.gov.tw/>
- 全國法規資料庫：法律、命令、生效日期與沿革，<https://law.moj.gov.tw/>

## 台灣市場與公司

- TWSE OpenAPI：<https://openapi.twse.com.tw/>
- TWSE 上市證券每日行情：<https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL>
- TWSE 市場指數：<https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX>
- TWSE 交易日曆：<https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule>
- TPEx OpenAPI：<https://www.tpex.org.tw/openapi/>
- TPEx 上櫃收盤行情：<https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes>
- 公開資訊觀測站：財報、重大訊息、法說與公司公告，<https://mops.twse.com.tw/mops/web/index>

交易所行情常使用民國年月日；轉換西元年時加 1911。OpenAPI 最新行情通常是最近已發布交易日，不得稱為即時資料，也不得把假日的最近交易日寫成今天。行情腳本先用首位 1–9 的四位數代號排除多數 ETF／ETN 等非普通股工具；個股研究仍須再以交易所或公司資料確認證券類型。

## 全球總經與跨國比較

- IMF Data 與 World Economic Outlook：<https://data.imf.org/>
- OECD 指標與 Economic Outlook：<https://www.oecd.org/en/topics/economic-outlook.html>
- World Bank Data：<https://data.worldbank.org/>
- Bank for International Settlements：信貸、金融條件與銀行統計，<https://www.bis.org/statistics/>

國際機構資料適合跨國一致比較與預測情境；當期單一國家數據仍優先採該國官方統計機關。

## 美國

- Federal Reserve 與 FOMC：<https://www.federalreserve.gov/monetarypolicy/fomc.htm>
- FRED：官方與授權總經時間序列，<https://fred.stlouisfed.org/>
- Bureau of Economic Analysis：GDP、PCE、國民所得與國際收支，<https://www.bea.gov/>
- Bureau of Labor Statistics：CPI、PPI、就業、薪資與生產力，<https://www.bls.gov/>
- U.S. Treasury：財政、債務、制裁與殖利率資料，<https://home.treasury.gov/>
- U.S. Census Bureau：零售、耐久財、住宅與貿易，<https://www.census.gov/>

## 中國

- 國家統計局：GDP、工業、零售、投資、物價與人口，<https://www.stats.gov.cn/>
- 中國人民銀行：貨幣政策、利率、信用與社會融資，<https://www.pbc.gov.cn/>
- 海關總署：進出口與貿易資料，<http://www.customs.gov.cn/>
- 國務院與各部委：政策原文與生效範圍，<https://www.gov.cn/>

## 歐元區與日本

- European Central Bank 貨幣政策決策：<https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html>
- Eurostat：GDP、通膨、就業與產業統計，<https://ec.europa.eu/eurostat/>
- Bank of Japan：貨幣政策決議、展望與統計，<https://www.boj.or.jp/en/>
- Statistics Bureau of Japan：CPI、就業與家庭支出，<https://www.stat.go.jp/english/>
- Cabinet Office Japan：GDP、景氣與內閣府統計，<https://www.esri.cao.go.jp/en/>

## 市場預期與新聞

- 市場預期必須能追溯到發布前已存在的調查或資料服務，並記錄時間戳與樣本說明。
- 無法合法或可靠取得共識時填 `N/A`；不得拿事後評論代替事前預期。
- 政策與公司新聞先找官方文件或公司公告；媒體用於補充即時背景與不同觀點。
- 重大但尚無第一手文件的新聞至少以兩個獨立可信來源交叉確認，並標為未完全驗證。
