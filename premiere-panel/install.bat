@echo off
chcp 65001 >nul
setlocal

REM 把這個面板資料夾「連結」到 Premiere 的擴充目錄。
REM 用 junction(不需系統管理員),之後改面板程式會即時反映,不用重裝。

set "SRC=%~dp0"
set "SRC=%SRC:~0,-1%"
set "DST=%APPDATA%\Adobe\CEP\extensions\com.prautoedit.panel"

echo 來源:%SRC%
echo 目標:%DST%
echo.

if exist "%DST%" (
    echo 目標已存在,先移除舊連結...
    rmdir "%DST%" 2>nul
    if exist "%DST%" rmdir /S /Q "%DST%"
)

mklink /J "%DST%" "%SRC%"
if errorlevel 1 (
    echo.
    echo [失敗] 建立連結失敗。
    pause
    exit /b 1
)

echo.
echo [完成] 面板已連結。
echo 接下來:
echo   1. 執行 enable-debug-mode.reg(只需一次)
echo   2. 重新啟動 Premiere Pro
echo   3. 視窗 ^> 擴充功能 ^> PR 自動剪輯
echo.
pause
