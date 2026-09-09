# 一次性：註冊 / 更新「台股每日研究報告」的 Windows 排程工作。
# 用法：在 PowerShell 執行  →  powershell -ExecutionPolicy Bypass -File .\install-schedule.ps1
# 或右鍵此檔 → 用 PowerShell 執行。一般使用者權限即可。

$ErrorActionPreference = 'Stop'

$ps1 = 'C:\Users\User\.claude\skills\taiwan-stock-daily-report\run\daily-report.ps1'
if (-not (Test-Path $ps1)) { throw "找不到執行器：$ps1" }

$action   = New-ScheduledTaskAction -Execute 'powershell.exe' `
              -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`""
$trigger  = New-ScheduledTaskTrigger -Weekly `
              -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At 7:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
              -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
              -MultipleInstances IgnoreNew `
              -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName 'TaiwanStockDailyReport' `
  -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description '台股每日研究報告 — headless claude 跑 taiwan-stock-daily-report skill，寫入 Notion。週二至週六 07:00。' `
  -Force | Out-Null

Write-Host '已註冊排程工作：TaiwanStockDailyReport' -ForegroundColor Green
Get-ScheduledTask -TaskName 'TaiwanStockDailyReport' | Select-Object TaskName, State
(Get-ScheduledTask -TaskName 'TaiwanStockDailyReport' | Get-ScheduledTaskInfo) |
  Select-Object NextRunTime
Write-Host ''
Write-Host '立即測跑： Start-ScheduledTask -TaskName ''TaiwanStockDailyReport''' -ForegroundColor Cyan
Write-Host '看日誌：   explorer C:\Users\User\.claude\skills\taiwan-stock-daily-report\run\logs'
