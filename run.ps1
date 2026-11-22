$ErrorActionPreference = 'Stop'
$pythonForWhucad = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonForWhucad)) { throw 'Run .\setup.ps1 first.' }
& $pythonForWhucad (Join-Path $PSScriptRoot 'run.py') @args
exit $LASTEXITCODE
