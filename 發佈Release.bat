@echo off
REM ASCII-only launcher; Chinese messages live in publish_release.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_release.ps1"
pause
