param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $localPython)) {
    & $Python -c "import sys,struct; assert (3,11) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') == 8, 'Use 64-bit Python 3.11-3.13'"
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python' }
    & $Python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
Push-Location $PSScriptRoot
try {
    & $localPython -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & $localPython -m pip install -e '.[stateful,verification]'
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed' }
    & $localPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
} finally { Pop-Location }
Write-Host 'Ready: .\run.ps1 validate .\examples\00000000.json'
