$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $env:TEMP "codelens-review-$env:USERNAME"
$logDir = Join-Path $scriptRoot "logs"
$backendPidFile = Join-Path $runtimeDir "backend.pid"
$frontendPidFile = Join-Path $runtimeDir "frontend.pid"

function Fail {
    param([string]$Message)
    Write-Error $Message
    exit 1
}

function Show-Usage {
    Write-Host "Usage: $($MyInvocation.MyCommand.Name) [start|stop|restart]"
}

function Read-Pid {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = Get-Content $PidFile -Raw
    $raw = $raw.Trim()
    if ($raw -match '^\d+$') { return [int]$raw }
    return $null
}

function Test-Running {
    param([int]$ProcId)
    try {
        $proc = Get-Process -Id $ProcId -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if (-not (Test-Running $ProcessId)) { return }

    # Use cmd.exe to run taskkill, isolating its stderr from PowerShell's
    # $ErrorActionPreference="Stop" which converts native-command stderr into
    # a terminating error (caught by try/catch, silently skipping the kill).
    try {
        $null = cmd.exe /c "taskkill /F /PID $ProcessId /T >nul 2>&1"
    } catch { }

    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Test-Running $ProcessId)) { return }
        Start-Sleep -Milliseconds 100
    }

    # Fallback: Stop-Process if taskkill didn't work (e.g. wslrelay.exe
    # cannot be killed by taskkill but can by Stop-Process)
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    } catch { }

    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Test-Running $ProcessId)) { return }
        Start-Sleep -Milliseconds 100
    }
}

function Stop-Services {
    $wasRunning = $false

    foreach ($pidFile in @($backendPidFile, $frontendPidFile)) {
        $procId = Read-Pid $pidFile
        if ($null -eq $procId) { continue }

        if (Test-Running $procId) {
            $wasRunning = $true
            Stop-ProcessTree -ProcessId $procId
        }

        if (Test-Path $pidFile) {
            Remove-Item $pidFile -Force
        }
    }

    # Fallback: kill any process still listening on our ports. Start-Process
    # returns wrapper PIDs; the actual service processes may be detached children
    # that survive taskkill /T on the wrapper.
    foreach ($port in @(8800, 5173)) {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if ($null -eq $conns) { continue }
        $uniquePids = $conns | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique
        foreach ($portProcId in $uniquePids) {
            if ($portProcId -and (Test-Running $portProcId)) {
                $wasRunning = $true
                Stop-ProcessTree -ProcessId $portProcId
            }
        }
    }

    if (Test-Path $runtimeDir) {
        Remove-Item $runtimeDir -Force -ErrorAction SilentlyContinue
    }

    if ($wasRunning) {
        Write-Host "CodeLens stopped."
    } else {
        Write-Host "CodeLens is not running."
    }
}

function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$ProcId,
        [string]$Name
    )

    for ($i = 0; $i -lt 60; $i++) {
        if (-not (Test-Running $ProcId)) {
            Fail "$Name failed to start; inspect the output above"
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
            # Not ready yet
        }

        Start-Sleep -Milliseconds 500
    }

    Fail "$Name did not become ready within 30 seconds"
}

function Get-PortOwningProcessId {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Start-Services {
    # Check if already running
    foreach ($pidFile in @($backendPidFile, $frontendPidFile)) {
        $procId = Read-Pid $pidFile
        if ($null -ne $procId -and (Test-Running $procId)) {
            Fail "CodeLens is already running; use .\code-lens.ps1 restart or .\code-lens.ps1 stop"
        }
    }

    # Clean up old state
    if (Test-Path $runtimeDir) {
        Remove-Item $runtimeDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Check dependencies
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) { Fail "uv is required: https://docs.astral.sh/uv/" }

    $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpmCommand) { Fail "pnpm is required: https://pnpm.io/installation" }

    Write-Host "`n[1/3] Installing backend dependencies..."
    & $uvCommand.Source sync --project backend
    if ($LASTEXITCODE -ne 0) { Fail "Backend dependency installation failed." }

    Write-Host "`n[2/3] Installing frontend dependencies..."
    & $pnpmCommand.Source --dir frontend install
    if ($LASTEXITCODE -ne 0) { Fail "Frontend dependency installation failed." }

    # Create runtime directory
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    # Clean up old logs
    $unifiedLog = Join-Path $logDir "unified.log"
    if (Test-Path $unifiedLog) {
        Remove-Item $unifiedLog -Force
    }

    Write-Host "`n[3/3] Starting CodeLens..."

    # Start backend
    $supervisorLog = Join-Path $logDir "supervisor.log"
    $backendProcess = Start-Process `
        -FilePath $uvCommand.Source `
        -ArgumentList "run --project backend codelens-review start" `
        -WorkingDirectory $scriptRoot `
        -RedirectStandardOutput $supervisorLog `
        -RedirectStandardError (Join-Path $logDir "backend-stderr.log") `
        -PassThru `
        -WindowStyle Hidden

    $backendProcId = $backendProcess.Id
    Set-Content -Path $backendPidFile -Value $backendProcId

    # Start frontend (pnpm is a .ps1 script, need to run via powershell.exe)
    $frontendLog = Join-Path $logDir "frontend.log"
    $pnpmPath = $pnpmCommand.Source
    $frontendProcess = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$pnpmPath`" --dir frontend dev --strictPort" `
        -WorkingDirectory $scriptRoot `
        -RedirectStandardOutput $frontendLog `
        -RedirectStandardError (Join-Path $logDir "frontend-stderr.log") `
        -PassThru `
        -WindowStyle Hidden

    $frontendProcId = $frontendProcess.Id
    Set-Content -Path $frontendPidFile -Value $frontendProcId

    # Wait for services to be ready
    Wait-ForHttp -Url "http://127.0.0.1:8800/api/health" -ProcId $backendProcId -Name "Backend"

    # Replace wrapper PID with actual service process PID (Start-Process returns
    # the uv.exe wrapper, not the Python interpreter that owns port 8800)
    $actualBackendPid = Get-PortOwningProcessId -Port 8800
    if ($actualBackendPid) {
        Set-Content -Path $backendPidFile -Value $actualBackendPid
    }

    Wait-ForHttp -Url "http://127.0.0.1:5173" -ProcId $frontendProcId -Name "Frontend"

    $actualFrontendPid = Get-PortOwningProcessId -Port 5173
    if ($actualFrontendPid) {
        Set-Content -Path $frontendPidFile -Value $actualFrontendPid
    }

    Write-Host "`nCodeLens is ready. Open these addresses:"
    Write-Host "  Frontend:  http://127.0.0.1:5173"
    Write-Host "  Backend:   http://127.0.0.1:8800"
    Write-Host "  OpenAPI:   http://127.0.0.1:8800/docs"
    Write-Host "`nAll locally accessible Git repositories are allowed by default."
    Write-Host "Choose a repository and configure model gateways in the Web UI."
    Write-Host "Run .\code-lens.ps1 stop to stop all services.`n"
}

# Main
$action = if ($args.Count -gt 0) { $args[0] } else { "start" }

switch ($action) {
    "start" {
        Start-Services
    }
    "stop" {
        if ($args.Count -ne 1) {
            Show-Usage
            exit 2
        }
        Stop-Services
    }
    "restart" {
        Stop-Services
        Start-Services
    }
    default {
        Show-Usage
        exit 2
    }
}
