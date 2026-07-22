$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot
$backendProcess = $null
$frontendProcess = $null
$locationPushed = $false

function Stop-ProcessTree {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) {
        return
    }

    $Process.Refresh()
    if ($Process.HasExited) {
        return
    }

    try {
        # `/T` includes child processes spawned by `uv run`, including the
        # independent API and Worker services. This mirrors the Unix launcher.
        & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
    }
    catch {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

try {
    $uvCommand = Get-Command uv -ErrorAction Stop
    $pnpmCommand = Get-Command pnpm -ErrorAction Stop

    Push-Location $scriptRoot
    $locationPushed = $true

    Write-Host "`n[1/3] Installing backend dependencies..."
    & $uvCommand.Source sync --project backend
    if ($LASTEXITCODE -ne 0) {
        throw "Backend dependency installation failed."
    }

    Write-Host "`n[2/3] Installing frontend dependencies..."
    & $pnpmCommand.Source --dir frontend install
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend dependency installation failed."
    }

    Write-Host "`n[3/3] Starting CodeLens..."
    $backendProcess = Start-Process `
        -FilePath $uvCommand.Source `
        -ArgumentList @("run", "--project", "backend", "codelens-review", "start") `
        -WorkingDirectory $scriptRoot `
        -NoNewWindow `
        -PassThru
    $frontendProcess = Start-Process `
        -FilePath $pnpmCommand.Source `
        -ArgumentList @("--dir", "frontend", "dev", "--host", "127.0.0.1", "--strictPort") `
        -WorkingDirectory $scriptRoot `
        -NoNewWindow `
        -PassThru

    Start-Sleep -Seconds 1
    $backendProcess.Refresh()
    $frontendProcess.Refresh()
    if ($backendProcess.HasExited) {
        throw "Backend failed to start. Make sure port 8765 is available."
    }
    if ($frontendProcess.HasExited) {
        throw "Frontend failed to start. Make sure port 5173 is available."
    }

    Write-Host "`nCodeLens is starting. Open these addresses:"
    Write-Host "  Frontend:  http://127.0.0.1:5173"
    Write-Host "  Backend:   http://127.0.0.1:8765"
    Write-Host "  OpenAPI:   http://127.0.0.1:8765/docs"
    Write-Host "`nAll locally accessible Git repositories are allowed by default."
    Write-Host "Choose a repository and configure model gateways in the Web UI."
    Write-Host "Press Ctrl+C to stop both services.`n"

    while (-not $backendProcess.HasExited -and -not $frontendProcess.HasExited) {
        Start-Sleep -Seconds 1
        $backendProcess.Refresh()
        $frontendProcess.Refresh()
    }

    if ($backendProcess.HasExited) {
        throw "Backend process stopped with exit code $($backendProcess.ExitCode)."
    }
    throw "Frontend process stopped with exit code $($frontendProcess.ExitCode)."
}
catch [System.Management.Automation.CommandNotFoundException] {
    Write-Error "uv and pnpm are required. Install uv from https://docs.astral.sh/uv/ and pnpm from https://pnpm.io/installation."
    exit 1
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Stop-ProcessTree -Process $frontendProcess
    Stop-ProcessTree -Process $backendProcess
    if ($locationPushed) {
        Pop-Location
    }
}
