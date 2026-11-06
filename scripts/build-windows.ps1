$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt pyinstaller pytest
if ($LASTEXITCODE -ne 0) { throw 'Abhängigkeiten konnten nicht installiert werden.' }
& '.\.venv\Scripts\python.exe' -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests fehlgeschlagen.' }
& '.\.venv\Scripts\python.exe' -m scripts.package_windows
if ($LASTEXITCODE -ne 0) { throw 'Build fehlgeschlagen.' }
