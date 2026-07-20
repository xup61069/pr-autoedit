# =====================================================================
#  PR 剪教學一條龍 —— 移除面板
#  請雙擊「移除面板.bat」執行。
#  注意:這只會把面板從 Premiere 移除,不會刪掉你的專案資料夾與剪輯成果。
# =====================================================================
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "==============================================="
Write-Host "  PR 剪教學一條龍 —— 移除面板"
Write-Host "==============================================="
Write-Host ""
Write-Host "這會把面板從 Premiere 拿掉。"
Write-Host "你的影片、剪輯成果、設定都不會被刪除。"
Write-Host ""

$dst = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.prautoedit.panel"
if (Test-Path $dst) {
    cmd /c rmdir "$dst" 2>$null
    if (Test-Path $dst) { cmd /c rmdir /S /Q "$dst" }
    if (Test-Path $dst) {
        Write-Host "  [失敗] 移除不掉,請先把 Premiere 關掉再試一次" -ForegroundColor Red
    } else {
        Write-Host "  [OK] 面板已移除" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] 面板本來就不在" -ForegroundColor Green
}

Write-Host ""
Write-Host "重新開啟 Premiere 後,選單裡就不會再有這個面板了。"
Write-Host "想重新安裝,再跑一次「安裝.bat」即可。"
Write-Host ""
Read-Host "按 Enter 關閉"
