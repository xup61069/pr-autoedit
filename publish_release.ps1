# =====================================================================
#  把安裝版發佈到 GitHub Releases
#  雙擊「發佈Release.bat」執行。
#
#  2026-07-20 首次發佈時遇到 GitHub Releases 服務故障(一直回 502/503),
#  程式碼和標籤都推上去了,只差這一步。這支就是用來把最後一步補完的,
#  等 GitHub 恢復後跑一次即可。之後要發新版也可以用它。
# =====================================================================
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tag = "v1.0.0"
$zip = Join-Path $root "dist\pr-autoedit-$tag.zip"

$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) { $gh = "gh" }

Write-Host ""
Write-Host "==============================================="
Write-Host "  發佈安裝版到 GitHub"
Write-Host "==============================================="
Write-Host ""

if (-not (Test-Path $zip)) {
    Write-Host "  [失敗] 找不到安裝檔:$zip" -ForegroundColor Red
    Write-Host "  請先在專案資料夾執行:"
    Write-Host "    git archive --format=zip --prefix=pr-autoedit/ -o dist\pr-autoedit-$tag.zip $tag"
    Read-Host "按 Enter 關閉"
    exit 1
}

Write-Host "安裝檔:$zip"
Write-Host "標籤  :$tag"
Write-Host ""
Write-Host "嘗試上傳(GitHub 若還在故障會自動重試,最多 20 次)..."
Write-Host ""

$notes = Join-Path $root "dist\release-notes.md"
$ok = $false
for ($i = 1; $i -le 20; $i++) {
    # 已經有 release 就只補附件,沒有就整個建立
    & $gh release view $tag 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $out = & $gh release upload $tag $zip --clobber 2>&1
    } elseif (Test-Path $notes) {
        $out = & $gh release create $tag $zip --title "$tag" --notes-file $notes 2>&1
    } else {
        $out = & $gh release create $tag $zip --title "$tag" --generate-notes 2>&1
    }
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Write-Host ("  第 {0} 次失敗:{1}" -f $i, ($out | Select-Object -First 1))
    Start-Sleep -Seconds 20
}

Write-Host ""
if ($ok) {
    # 若還是草稿,順手發佈成正式版
    & $gh release edit $tag --draft=false 2>$null | Out-Null
    Write-Host "  [完成] 已發佈!" -ForegroundColor Green
    Write-Host "  https://github.com/xup61069/pr-autoedit/releases/tag/$tag"
} else {
    Write-Host "  [失敗] GitHub 目前還是連不上,晚點再跑一次這支就好。" -ForegroundColor Yellow
    Write-Host "  可以先看 https://www.githubstatus.com/ 確認服務是否恢復。"
}
Write-Host ""
Read-Host "按 Enter 關閉"
