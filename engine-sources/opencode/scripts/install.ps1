$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) { & py -3 (Join-Path $ScriptDir "install.py") @args; exit $LASTEXITCODE }
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { Write-Error "Python 3 is required."; exit 2 }
& python (Join-Path $ScriptDir "install.py") @args
exit $LASTEXITCODE
