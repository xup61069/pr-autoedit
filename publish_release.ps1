# =====================================================================
#  把安裝版發佈到 GitHub Releases
#  雙擊「發佈Release.bat」執行,或指定版本:
#      powershell -File publish_release.ps1 -Tag v1.2.0
#
#  不給版本的話,自動用「目前 git 上最新的標籤」。
#  發佈說明要放在 dist\release-notes-<版本>.md(帶版本號,不是通用檔名);
#  沒寫的話腳本會停下來,不會拿舊版的說明來湊。真的不想寫就加 -NoNotes,
#  讓 GitHub 自己從 commit 產生。
#  安裝檔不存在會自動用 git archive 產生,不用自己先跑一次。
#  GitHub Releases 服務不穩時會自動重試(2026-07-20 首次發佈就遇過 502)。
# =====================================================================
param([string]$Tag = "", [switch]$NoNotes)

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

# 發佈說明必須是「這一版的」。
#
# 以前這裡固定指向 dist\release-notes.md,不管你發的是哪個標籤。那個檔會
# 停在上一版的內容(改完程式很少有人記得回頭改它),於是發 v1.2.0 會把
# v1.1.0 的說明公開貼到 GitHub 上,而腳本照樣回報「[完成] 已發佈!」。
# 這是對外的東西,錯了是別人看到,所以寧可停下來也不要猜。
$notes = Join-Path $root "dist\release-notes-$tag.md"
if ($NoNotes) { $notes = $null }
elseif (-not (Test-Path $notes)) {
    $legacy = Join-Path $root "dist\release-notes.md"
    Write-Host "  [停下來] 找不到這一版的發佈說明:" -ForegroundColor Yellow
    Write-Host "     $notes"
    Write-Host ""
    if (Test-Path $legacy) {
        Write-Host "  旁邊有一份沒有版本號的 dist\release-notes.md,"
        Write-Host "  但那多半是上一版留下來的,直接拿來發會把舊說明貼到新版上。"
        Write-Host "  確認過內容就是這一版的話,把它改名成 release-notes-$tag.md 再跑一次。"
    } else {
        Write-Host "  請先寫一份 dist\release-notes-$tag.md 再跑這支。"
    }
    Write-Host ""
    Write-Host "  真的不想寫,就用 -NoNotes 讓 GitHub 自動從 commit 產生說明:"
    Write-Host "     powershell -File publish_release.ps1 -Tag $tag -NoNotes"
    Write-Host ""
    Read-Host "按 Enter 關閉"
    exit 1
}

$ok = $false
for ($i = 1; $i -le 20; $i++) {
    # 已經有 release 就只補附件,沒有就整個建立
    & $gh release view $tag 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $out = & $gh release upload $tag $zip --clobber 2>&1
    } elseif ($notes -and (Test-Path $notes)) {
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
