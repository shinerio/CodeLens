$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptRoot

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    Write-Error "uv is required: https://docs.astral.sh/uv/"
    exit 1
}

& $uvCommand.Source run --project backend codelens-review @args
exit $LASTEXITCODE
