# 雲端排程執行說明（cloud routine entrypoint）

這份給排程雲端代理讀。本倉庫 = taiwan-stock-daily-report skill 本體。

## 執行步驟

1. **依 [SKILL.md](SKILL.md) 的流程完整執行**。參考檔在 `references/`，腳本在 `scripts/`。
   本倉庫未安裝為具名 skill，直接讀 SKILL.md 當作業指示，逐步照做。
2. `cadence = daily`。時間基準見 [references/research-method.md](references/research-method.md)：以最近一個已收盤台股交易日為準。
3. **總經數據**：先 `export FRED_API_KEY`（金鑰由 routine prompt 提供），再
   `python scripts/fetch_macro_snapshot.py --output /tmp/macro.json`
4. **台股行情**：`python scripts/fetch_market_snapshot.py --watchlist 2330,2317,2454,2382,2408,2344,3711,2308 --output /tmp/market.json`
5. **台／中／日總經 + consensus**：依 [references/macro-fetch.md](references/macro-fetch.md) 用 WebFetch 補。
6. **跨日比較**：依 [references/state-memory.md](references/state-memory.md)。雲端每次是全新 checkout，`scripts/state/latest.json` 不存在屬正常 → 直接查 Notion 報告資料庫最新一筆（日期早於今日）取得前次結論與子資料庫 id。
7. **交付**：依 [references/output-delivery.md](references/output-delivery.md)，用 Notion 連接器工具寫入。目標：

   | 用途 | data source |
   |---|---|
   | 報告資料庫「台股每日研究報告」 | `collection://12f5e0a1-2f79-4eb0-abbc-49c468c1cd3f` |
   | 證據帳本 | `collection://f4318726-ba81-41f8-ad7f-de908be1f8ba` |
   | 候選股追蹤 | `collection://b9dee6fc-0ab8-4ee3-a48c-eac1ab8a22ff` |

8. 報告標題與開頭 callout 標「自動產生‧未複核」。
9. 非台股交易日隔天（`market.json` 的 `freshness` 顯示無新收盤、與前次同日）仍產生一份，但開頭註明「資料與前次相同，僅更新可得的總經與新聞」。
10. **全程非互動**：不要問問題。不確定就依 skill 的降級規則處理並在報告中記錄。

## 環境需求

- Python 3（標準庫即可；`openpyxl` 僅在讀 xlsx 時需要，排程不需要）
- 網路：FRED API、TWSE/TPEx OpenAPI、美國財政部、各國官方新聞稿、WebSearch/WebFetch
- Notion 連接器（claude.ai connector）已授權且可存取上述三個資料庫
- `FRED_API_KEY` 環境變數（routine prompt 會 export）

## 找不到 Notion 工具時

回報「Notion 連接器未附加，無法交付」並把完整報告輸出成 Markdown 檔到工作目錄，結束。不要靜默丟棄。
