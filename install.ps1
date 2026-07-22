# =====================================================================
#  PR 剪教學一條龍 —— 一鍵安裝
#  請不要直接執行這個檔,雙擊「安裝.bat」即可(它會用正確方式叫起這支)。
# =====================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($t)  { Write-Host $t }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [注意] $t" -ForegroundColor Yellow }
function Bad($t)  { Write-Host "  [失敗] $t" -ForegroundColor Red }

function Have($name) {
    try { return [bool](Get-Command $name -ErrorAction Stop) } catch { return $false }
}

function RealPy($exe) {
    # 這個指令是不是「真的能用的 Python 3」?
    # Windows 內建一個假的 python 捷徑(給市集用的):叫得動、Get-Command 也找得到,
    # 但 --version 什麼都不印、python -m venv 會直接失敗。只有真的印得出
    # "Python 3.x" 才算數;印不出來就回 $null,當它沒裝。
    try { $v = (& $exe --version 2>&1 | Out-String).Trim() } catch { return $null }
    if ($v -match 'Python\s+3\.\d+') { return $v }
    return $null
}

Say ""
Say "==============================================="
Say "  PR 剪教學一條龍 —— 安裝程式"
Say "==============================================="
Say ""
Say "這個程式會幫你把需要的東西裝好,大約 10~30 分鐘"
Say "(主要時間都花在下載,你可以先去泡杯咖啡)。"
Say ""
Say "安裝位置:$root"
Say ""

# ---------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------
Say "[1/6] 檢查 Python..."
# 決定用哪個指令當底層 Python。優先用 py 啟動器 —— 它不會指到市集的假捷徑,
# 最可靠;沒有 py 才退回 python(而且要驗過真的印得出版本,見 RealPy)。
$basePy = $null; $pyver = $null
if (Have "py")    { $pyver = RealPy "py";     if ($pyver) { $basePy = "py" } }
if (-not $basePy) { $pyver = RealPy "python"; if ($pyver) { $basePy = "python" } }

if (-not $basePy) {
    Warn "找不到可用的 Python(或被 Windows 的假 python 捷徑擋住),嘗試自動安裝..."
    if (Have "winget") {
        winget install --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "Machine")
    }
    if (Have "py")    { $pyver = RealPy "py";     if ($pyver) { $basePy = "py" } }
    if (-not $basePy) { $pyver = RealPy "python"; if ($pyver) { $basePy = "python" } }
}

if (-not $basePy) {
    Bad "Python 沒裝成功,或被 Windows 的假 python 捷徑擋住。"
    Say ""
    Say "  兩個做法擇一,做完重新執行「安裝.bat」:"
    Say "  A. 到 https://www.python.org/downloads/ 下載安裝,"
    Say "     安裝畫面第一頁一定要勾 [Add python.exe to PATH]。"
    Say "  B. 關掉 Windows 的假 python 捷徑:設定 > 應用程式 > 進階應用程式設定"
    Say "     > 應用程式執行別名 > 把 python.exe 和 python3.exe 兩個都關掉。"
    Say ""
    Read-Host "按 Enter 關閉"
    exit 1
}
Ok "$pyver"

# ---------------------------------------------------------------------
# 2. ffmpeg(處理影片音訊用)
# ---------------------------------------------------------------------
Say ""
Say "[2/6] 檢查 ffmpeg..."
if (-not (Have "ffmpeg")) {
    Warn "找不到 ffmpeg,嘗試自動安裝..."
    if (Have "winget") {
        winget install --id Gyan.FFmpeg --scope user --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "Machine")
    }
}
if (Have "ffmpeg") { Ok "ffmpeg 已就緒" }
else {
    Warn "ffmpeg 還是沒裝好。安裝會繼續,但沒有它就不能處理影片。"
    Say "  之後請到 https://www.gyan.dev/ffmpeg/builds/ 下載 ffmpeg-release-full.7z,"
    Say "  解壓縮後把裡面的 bin 資料夾加進系統 PATH,再重開電腦。"
}

# ---------------------------------------------------------------------
# 3. 專屬 Python 環境(不污染你電腦原本的 Python)
# ---------------------------------------------------------------------
Say ""
Say "[3/6] 建立專屬環境..."
$venv = Join-Path $root "venv"
$vpy = Join-Path $venv "Scripts\python.exe"
$venvLog = ""
if (-not (Test-Path $vpy)) {
    # 用驗過的 $basePy 建環境,並把它吐的錯誤全接住 ——
    # 失敗時要講得出「為什麼」,不能只丟一句「環境建立失敗」讓人卡在這。
    # try/catch 連 PowerShell 自己拋的例外也一起收(ErrorActionPreference=Stop)。
    try { $venvLog = (& $basePy -m venv $venv 2>&1 | Out-String).Trim() }
    catch { $venvLog = $_.Exception.Message }
}
if (-not (Test-Path $vpy)) {
    Bad "環境建立失敗"
    if ($venvLog) {
        Say ""
        Say "  Python 回報的原因:"
        foreach ($ln in ($venvLog -split "`n")) { Say ("    " + $ln.TrimEnd()) }
    }
    Say ""
    Say "  常見原因:"
    Say "  - Windows 的假 python 捷徑擋住(設定 > 應用程式 > 應用程式執行別名,關掉 python.exe)"
    Say "  - 防毒軟體擋住建立資料夾(暫時關閉,或把這個資料夾加進白名單)"
    Say "  - 安裝位置沒有寫入權限(換到像 C:\pr-autoedit 這種簡單路徑再試)"
    Say ""
    Read-Host "按 Enter 關閉"
    exit 1
}
& $vpy -m pip install --upgrade pip --quiet
Ok "專屬環境完成"

# ---------------------------------------------------------------------
# 4. PyTorch(語音辨識的引擎;有 NVIDIA 顯卡就裝顯卡加速版)
# ---------------------------------------------------------------------
Say ""
Say "[4/6] 安裝語音辨識引擎(這步最久,可能 10 分鐘以上)..."
$hasNvidia = $false
try {
    $gpu = Get-CimInstance Win32_VideoController -ErrorAction Stop |
           Where-Object { $_.Name -match "NVIDIA" }
    if ($gpu) { $hasNvidia = $true; Say "  偵測到顯示卡:$($gpu[0].Name)" }
} catch { }

if ($hasNvidia) {
    Say "  有 NVIDIA 顯卡 -> 裝顯卡加速版(辨識快很多)"
    & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cu128
} else {
    Warn "沒有偵測到 NVIDIA 顯卡 -> 裝一般版(能用,但辨識會慢很多)"
    & $vpy -m pip install torch
}

# ---------------------------------------------------------------------
# 5. 其餘套件
# ---------------------------------------------------------------------
Say ""
Say "[5/6] 安裝其餘套件..."
& $vpy -m pip install -r (Join-Path $root "requirements.txt")
Ok "套件安裝完成"

# 把路徑寫給面板,面板就不用你手動改任何一行程式
$cfgDir = Join-Path $root "config"
if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory $cfgDir | Out-Null }
@{ project_dir = $root; python = $vpy } | ConvertTo-Json |
    Out-File (Join-Path $cfgDir "panel.json") -Encoding utf8

# ---------------------------------------------------------------------
# 6. Premiere 面板
# ---------------------------------------------------------------------
Say ""
Say "[6/6] 安裝 Premiere 面板..."

# (a) 允許 Premiere 載入自製面板
foreach ($v in 10..12) {
    $key = "HKCU:\Software\Adobe\CSXS.$v"
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    Set-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1"
}
Ok "已允許 Premiere 載入面板"

# (b) 把面板連結到 Premiere 的擴充功能資料夾
$src = Join-Path $root "premiere-panel"
$dst = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.prautoedit.panel"
$dstParent = Split-Path -Parent $dst
if (-not (Test-Path $dstParent)) { New-Item -ItemType Directory $dstParent -Force | Out-Null }
if (Test-Path $dst) { cmd /c rmdir "$dst" 2>$null; if (Test-Path $dst) { cmd /c rmdir /S /Q "$dst" } }
cmd /c mklink /J "$dst" "$src" | Out-Null
if (Test-Path $dst) { Ok "面板已安裝" } else { Bad "面板連結失敗" }

# ---------------------------------------------------------------------
Say ""
Say "==============================================="
Say "  安裝完成!"
Say "==============================================="
Say ""
Say "接下來:"
Say "  1. 把 Premiere Pro 完全關掉,再重新打開"
Say "  2. 上方選單:視窗 > 擴充功能 > PR剪教學一條龍"
Say "  3. 面板出現後,按「選擇影片」挑一支錄好的影片,再按「一鍵自動剪輯」"
Say ""
Say "第一次剪輯會下載語音辨識模型(約 3GB),請耐心等,只有第一次。"
Say "看不懂哪裡的話,打開「新手指南.md」。"
Say ""
Read-Host "按 Enter 關閉"
