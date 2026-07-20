@echo off
REM ASCII-only launcher. All Chinese messages live in install.ps1,
REM which PowerShell renders correctly regardless of console codepage.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 pause
