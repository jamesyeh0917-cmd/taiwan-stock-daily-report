# 台股每日研究報告 — 本機排程執行器
# 由 Windows 工作排程器每天呼叫；headless 執行 taiwan-stock-daily-report skill。

$ErrorActionPreference = 'Continue'

$SkillDir = 'C:\Users\User\.claude\skills\taiwan-stock-daily-report'
$LogDir   = Join-Path $SkillDir 'run\logs'
$Stamp    = Get-Date -Format 'yyyy-MM-dd_HHmm'
$Log      = Join-Path $LogDir "run_$Stamp.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 確保 node / npm 全域 bin 與 python 在 PATH（排程環境可能精簡）
$env:PATH = "C:\Users\User\AppData\Roaming\npm;C:\Users\User\AppData\Local\Programs\Python\Python313;C:\Users\User\AppData\Local\Programs\Python\Python313\Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'

Set-Location $SkillDir

$Prompt = @'
/taiwan-stock-daily-report cadence=daily。本次由 Windows 排程於早上自動執行。

- 交付到 Notion 資料庫「台股每日研究報告」(data_source collection://12f5e0a1-2f79-4eb0-abbc-49c468c1cd3f)。
- 先讀 scripts/state/latest.json 取得前一份報告、證據帳本與候選股子資料庫的 id，依 references/state-memory.md 做跨日比較並累積寫入子資料庫；完成後更新 scripts/state/latest.json。
- FRED 金鑰已放在 scripts/.env（fetch_macro_snapshot.py 會自動讀取）。
- 若當日非台股交易日隔天（fetch_market_snapshot.py 的 freshness 顯示無新收盤、資料與前次相同），仍照流程產生一份，但在報告開頭與 Notion callout 註明「資料與前次相同，僅更新可得的總經與新聞」。
- 報告標題與內文開頭標「自動產生‧未複核」。
- 全程非互動：不要問問題，遇到不確定就依 skill 的降級規則處理並在報告中記錄。
'@

Write-Output "=== taiwan-stock-daily-report run @ $Stamp ===" | Tee-Object -FilePath $Log

& claude -p $Prompt `
  --model claude-sonnet-5 `
  --permission-mode bypassPermissions `
  --add-dir $LogDir `
  --max-budget-usd 10 `
  --output-format text 2>&1 | Tee-Object -FilePath $Log -Append

$code = $LASTEXITCODE
Write-Output "=== exit code: $code @ $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Tee-Object -FilePath $Log -Append

# 保留最近 30 份日誌
Get-ChildItem $LogDir -Filter 'run_*.log' | Sort-Object LastWriteTime -Descending |
  Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
