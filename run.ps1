# CivitMatrix launcher for Windows PowerShell
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q -e .

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
New-Item -ItemType Directory -Force -Path "downloads\Lora" | Out-Null

if (-not (Test-Path ".env")) {
    Write-Error "Missing .env — copy .env.example to .env and set CIVITAI_API_KEY"
    exit 1
}

# Default: Win95 UI. Headless: .\run.ps1 --cli …
if ($args.Count -ge 1 -and $args[0] -eq "--cli") {
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    & $venvPython -m civitmatrix --cli @rest
    exit $LASTEXITCODE
}

if ($args.Count -gt 0) {
    & $venvPython -m civitmatrix --cli @args
    exit $LASTEXITCODE
}

& $venvPython -m civitmatrix --ui
exit $LASTEXITCODE
