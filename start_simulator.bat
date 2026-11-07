@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Mode Dashboard
if errorlevel 1 pause
