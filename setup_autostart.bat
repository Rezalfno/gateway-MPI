@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0setup_autostart.ps1"
pause
