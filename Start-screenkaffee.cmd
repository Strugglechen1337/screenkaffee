@echo off
cd /d "%~dp0"
if exist "dist\screenkaffee\screenkaffee.exe" (
  start "" "dist\screenkaffee\screenkaffee.exe" %*
) else if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "launch.py" %*
) else (
  echo Bitte zuerst das portable Paket entpacken oder scripts\build-windows.ps1 ausfuehren.
  pause
)
