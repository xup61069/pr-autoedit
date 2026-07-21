@echo off
REM Link this panel folder into Premiere's CEP extensions dir.
REM Uses a junction (no admin needed). Edits to the panel take effect
REM without reinstalling. Messages kept ASCII-only to avoid codepage issues.
setlocal

set "SRC=%~dp0"
set "SRC=%SRC:~0,-1%"
set "DST=%APPDATA%\Adobe\CEP\extensions\com.prautoedit.panel"

echo Source: %SRC%
echo Target: %DST%
echo.

if exist "%DST%" (
    echo Removing old link...
    rmdir "%DST%" 2>nul
    if exist "%DST%" rmdir /S /Q "%DST%"
)

mklink /J "%DST%" "%SRC%"
if errorlevel 1 (
    echo.
    echo [FAILED] Could not create junction.
    pause
    exit /b 1
)

echo.
echo [DONE] Panel linked.
echo Next: 1) run enable-debug-mode.reg once  2) restart Premiere Pro
echo       3) Window ^> Extensions ^> (the panel; its menu name is in Chinese)
echo.
pause
