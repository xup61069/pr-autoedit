# =====================================================================
#  把安裝版發佈到 GitHub Releases
#  雙擊「發佈Release.bat」執行,或指定版本:
#      powershell -File publish_release.ps1 -Tag v1.2.0
#
#  不給版本的話,自動用「目前 git 上最新的標籤」。
#  安裝檔不存在會自動用 git archive 產生,不用自己先跑一次。
#  GitHub Releases 服務不穩時會自動重試(2026-07-20 首次發佈就遇過 502)。
# =====================================================================
param([string]$Tag = "")

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Tag)) {
    $Tag = (& git describe --tags --abbrev=0 2>$null)
    if ([string]::IsNullOrWhiteSpace($Tag)) {
        Write-Host "  [失敗] 找不到任何 git 標籤,請先建立版本標籤。" -ForegroundColor Red
        Read-Host "按 Enter 關閉"
        exit 1
    }
}
$tag = $Tag.Trim()
$zip = Join-Path $root "dist\pr-autoedit-$tag.zip"

$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) { $gh = "gh" }

Write-Host ""
Write-Host "==============================================="
Write-Host "  發佈安裝版到 GitHub"
Write-Host "==============================================="
Write-Host ""

if (-not (Test-Path $zip)) {
    Write-Host "找不到安裝檔,現在用 $tag 這個標籤產生一份..."
    New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
    & git archive --format=zip --prefix=pr-autoedit/ -o $zip $tag
    if (-not (Test-Path $zip)) {
        Write-Host "  [失敗] 產生安裝檔失敗,確認標籤 $tag 存在。" -ForegroundColor Red
        Read-Host "按 Enter 關閉"
        exit 1
    }
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
