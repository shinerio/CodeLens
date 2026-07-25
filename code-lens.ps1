$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot
$backendProcess = $null
$frontendProcess = $null
$locationPushed = $false

function Stop-ExistingPortProcess {
    param([int]$Port)

    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($connections) {
        $procIds = $connections | ForEach-Object { $_.OwningProcess } | Select-Object -Unique
        foreach ($procId in $procIds) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
}

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
        # `/T` includes the Python process spawned by `uv run`.
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

    $logRoot = Join-Path $scriptRoot "logs"
    $supervisorLog = Join-Path $logRoot "supervisor.log"
    $frontendLog = Join-Path $logRoot "frontend.log"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    Remove-Item -LiteralPath (Join-Path $logRoot "unified.log") -Force -ErrorAction SilentlyContinue

    # 清理占用端口的旧进程
    Stop-ExistingPortProcess -Port 5173
    Stop-ExistingPortProcess -Port 8800

    # 通过 cmd /c 启动以保持进程树连接（解决 uv.exe 包装器立即退出的问题）
    # pnpm 是 .ps1 文件，也需要通过 cmd /c 调用
    $backendCommand = "`"$($uvCommand.Source)`" run --project backend codelens-review start 1>>`"$supervisorLog`" 2>&1"
    $backendProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/s", "/c", $backendCommand) `
        -WorkingDirectory $scriptRoot `
        -NoNewWindow `
        -PassThru
    $frontendCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($pnpmCommand.Source)`" --dir frontend dev --host 127.0.0.1 --strictPort 1>>`"$frontendLog`" 2>&1"
    $frontendProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/s", "/c", $frontendCommand) `
        -WorkingDirectory $scriptRoot `
        -NoNewWindow `
        -PassThru

    Start-Sleep -Seconds 3
    $backendProcess.Refresh()
    $frontendProcess.Refresh()
    if ($backendProcess.HasExited) {
        throw "Backend failed to start (exit=$($backendProcess.ExitCode)). Make sure port 8800 is available."
    }
    if ($frontendProcess.HasExited) {
        throw "Frontend failed to start (exit=$($frontendProcess.ExitCode)). Make sure port 5173 is available."
    }

    Write-Host "`nCodeLens is starting. Open these addresses:"
    Write-Host "  Frontend:  http://127.0.0.1:5173"
    Write-Host "  Backend:   http://127.0.0.1:8800"
    Write-Host "  OpenAPI:   http://127.0.0.1:8800/docs"
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
