@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
start "" powershell -NoProfile -ExecutionPolicy Bypass -STA -File "%SCRIPT_DIR%yt-dlp-gui.ps1"
endlocal
