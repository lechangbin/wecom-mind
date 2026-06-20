param(
    [int]$Port = 8010,
    [switch]$NoStopExisting,
    [switch]$NoAdminWeb,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $ScriptDir "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Logs = Join-Path $Root "logs"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Stop-ExistingProjectProcess {
    param([string]$Pattern)

    $escapedRoot = [regex]::Escape($Root.Path)
    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like "python*" -and
            $_.CommandLine -match $escapedRoot -and
            $_.CommandLine -match $Pattern
        }

    foreach ($process in $processes) {
        Write-Host "Stopping existing process $($process.ProcessId): $Pattern"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    foreach ($process in $processes) {
        try {
            Wait-Process -Id $process.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        } catch {
            # The process may already be gone.
        }
    }
}

function Stop-ExistingNodeProjectProcess {
    param([string]$Pattern)

    $escapedRoot = [regex]::Escape($Root.Path)
    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like "node*" -and
            $_.CommandLine -match $escapedRoot -and
            $_.CommandLine -match $Pattern
        }

    foreach ($process in $processes) {
        Write-Host "Stopping existing node process $($process.ProcessId): $Pattern"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    foreach ($process in $processes) {
        try {
            Wait-Process -Id $process.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        } catch {
            # The process may already be gone.
        }
    }
}

function Stop-ProcessOnPort {
    param(
        [int]$TargetPort,
        [string]$Name
    )

    $connections = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        Write-Host "Stopping process on $Name port $TargetPort`: $($connection.OwningProcess)"
        Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 300
        $remaining = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue
    } while ($remaining -and (Get-Date) -lt $deadline)
}

function Get-LogPath {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return $Path
    }

    try {
        Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
        return $Path
    } catch {
        $directory = Split-Path -Parent $Path
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($Path)
        $extension = [System.IO.Path]::GetExtension($Path)
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        return Join-Path $directory "$stem.$timestamp$extension"
    }
}

function Start-ExecutableProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$OutLog,
        [string]$ErrLog
    )

    $ResolvedOutLog = Get-LogPath $OutLog
    $ResolvedErrLog = Get-LogPath $ErrLog

    Write-Host "Starting $Name..."
    if ($DryRun) {
        Write-Host "  $FilePath $($Arguments -join ' ')"
        Write-Host "  stdout: $ResolvedOutLog"
        Write-Host "  stderr: $ResolvedErrLog"
        return [pscustomobject]@{
            Process = $null
            OutLog = $ResolvedOutLog
            ErrLog = $ResolvedErrLog
        }
    }

    $Process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $ResolvedOutLog `
        -RedirectStandardError $ResolvedErrLog `
        -PassThru

    return [pscustomobject]@{
        Process = $Process
        OutLog = $ResolvedOutLog
        ErrLog = $ResolvedErrLog
    }
}

function Start-ServiceProcess {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [string]$OutLog,
        [string]$ErrLog
    )

    return Start-ExecutableProcess `
        -Name $Name `
        -FilePath $Python `
        -Arguments $Arguments `
        -WorkingDirectory $Root `
        -OutLog $OutLog `
        -ErrLog $ErrLog
}

function Get-DotEnvValue {
    param([string]$Key)

    $envPath = Join-Path $Root ".env"
    if (-not (Test-Path $envPath)) {
        return $null
    }

    $line = Get-Content -LiteralPath $envPath |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" } |
        Select-Object -Last 1
    if (-not $line) {
        return $null
    }

    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

$AdminWebHost = $env:ADMIN_WEB_HOST
if (-not $AdminWebHost) {
    $AdminWebHost = Get-DotEnvValue "ADMIN_WEB_HOST"
}
if (-not $AdminWebHost) {
    $AdminWebHost = "127.0.0.1"
}

$AdminWebPortText = $env:ADMIN_WEB_PORT
if (-not $AdminWebPortText) {
    $AdminWebPortText = Get-DotEnvValue "ADMIN_WEB_PORT"
}
if (-not $AdminWebPortText) {
    $AdminWebPortText = "5173"
}
$AdminWebPort = [int]$AdminWebPortText

if (-not $NoStopExisting -and -not $DryRun) {
    Stop-ExistingProjectProcess "uvicorn app\.main:app"
    Stop-ExistingProjectProcess "scripts\\run_wecom_aibot_worker\.py"
    Stop-ExistingProjectProcess "scripts\\run_message_reconcile_worker\.py"
    Stop-ExistingProjectProcess "scripts\\run_ai_memory_full_test_worker\.py"
    Stop-ProcessOnPort -TargetPort $Port -Name "API"
    if (-not $NoAdminWeb) {
        Stop-ExistingNodeProjectProcess "vite"
        Stop-ProcessOnPort -TargetPort $AdminWebPort -Name "admin web"
    }
    Start-Sleep -Seconds 1
}

$ApiOut = Join-Path $Logs "api.out.log"
$ApiErr = Join-Path $Logs "api.err.log"
$WorkerOut = Join-Path $Logs "wecom-aibot-worker.out.log"
$WorkerErr = Join-Path $Logs "wecom-aibot-worker.err.log"
$ReconcileOut = Join-Path $Logs "message-reconcile-worker.out.log"
$ReconcileErr = Join-Path $Logs "message-reconcile-worker.err.log"
$AiMemoryOut = Join-Path $Logs "ai-memory-full-test-worker.out.log"
$AiMemoryErr = Join-Path $Logs "ai-memory-full-test-worker.err.log"
$AdminWebOut = Join-Path $Logs "admin-web.out.log"
$AdminWebErr = Join-Path $Logs "admin-web.err.log"

$Api = Start-ServiceProcess `
    -Name "API" `
    -Arguments @("-m", "uvicorn", "app.main:app", "--app-dir", "src", "--host", "127.0.0.1", "--port", "$Port") `
    -OutLog $ApiOut `
    -ErrLog $ApiErr

$Worker = Start-ServiceProcess `
    -Name "WeCom AiBot worker" `
    -Arguments @("scripts\run_wecom_aibot_worker.py") `
    -OutLog $WorkerOut `
    -ErrLog $WorkerErr

$ReconcileEnabled = $env:WECOM_MESSAGE_RECONCILE_ENABLED
if (-not $ReconcileEnabled) {
    $ReconcileEnabled = Get-DotEnvValue "WECOM_MESSAGE_RECONCILE_ENABLED"
}

$Reconcile = $null
if ($ReconcileEnabled -and $ReconcileEnabled.ToLowerInvariant() -eq "true") {
    $Reconcile = Start-ServiceProcess `
        -Name "Message reconcile worker" `
        -Arguments @("scripts\run_message_reconcile_worker.py") `
        -OutLog $ReconcileOut `
        -ErrLog $ReconcileErr
}

$AiMemoryEnabled = $env:AI_MEMORY_FULL_TEST_ENABLED
if (-not $AiMemoryEnabled) {
    $AiMemoryEnabled = Get-DotEnvValue "AI_MEMORY_FULL_TEST_ENABLED"
}

$AiMemory = $null
if ($AiMemoryEnabled -and $AiMemoryEnabled.ToLowerInvariant() -eq "true") {
    $AiMemory = Start-ServiceProcess `
        -Name "AI memory full-test worker" `
        -Arguments @("scripts\run_ai_memory_full_test_worker.py") `
        -OutLog $AiMemoryOut `
        -ErrLog $AiMemoryErr
}

$AdminWeb = $null
if (-not $NoAdminWeb) {
    $AdminWebRoot = Join-Path $Root "apps\admin-web"
    if (-not (Test-Path $AdminWebRoot)) {
        throw "Admin web app not found: $AdminWebRoot"
    }

    $Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $Npm) {
        $Npm = (Get-Command npm -ErrorAction Stop).Source
    }

    $PreviousViteApiProxyTarget = $env:VITE_API_PROXY_TARGET
    $env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$Port"
    try {
        $AdminWeb = Start-ExecutableProcess `
            -Name "Admin web" `
            -FilePath $Npm `
            -Arguments @("run", "dev", "--", "--host", $AdminWebHost, "--port", "$AdminWebPort") `
            -WorkingDirectory $AdminWebRoot `
            -OutLog $AdminWebOut `
            -ErrLog $AdminWebErr
    } finally {
        if ($null -eq $PreviousViteApiProxyTarget) {
            Remove-Item Env:\VITE_API_PROXY_TARGET -ErrorAction SilentlyContinue
        } else {
            $env:VITE_API_PROXY_TARGET = $PreviousViteApiProxyTarget
        }
    }
}

if ($DryRun) {
    Write-Host "Dry run complete."
    exit 0
}

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "Services started:"
Write-Host "  API PID:    $($Api.Process.Id)  http://127.0.0.1:$Port"
Write-Host "  Worker PID: $($Worker.Process.Id)"
if ($Reconcile) {
    Write-Host "  Reconcile PID: $($Reconcile.Process.Id)"
} else {
    Write-Host "  Reconcile worker: disabled"
}
if ($AiMemory) {
    Write-Host "  AI memory PID: $($AiMemory.Process.Id)"
} else {
    Write-Host "  AI memory worker: disabled"
}
if ($AdminWeb) {
    Write-Host "  Admin web PID: $($AdminWeb.Process.Id)  http://$($AdminWebHost):$($AdminWebPort)"
} else {
    Write-Host "  Admin web: disabled"
}
Write-Host ""
Write-Host "Logs:"
Write-Host "  API stdout:    $($Api.OutLog)"
Write-Host "  API stderr:    $($Api.ErrLog)"
Write-Host "  Worker stdout: $($Worker.OutLog)"
Write-Host "  Worker stderr: $($Worker.ErrLog)"
if ($Reconcile) {
    Write-Host "  Reconcile stdout: $($Reconcile.OutLog)"
    Write-Host "  Reconcile stderr: $($Reconcile.ErrLog)"
}
if ($AiMemory) {
    Write-Host "  AI memory stdout: $($AiMemory.OutLog)"
    Write-Host "  AI memory stderr: $($AiMemory.ErrLog)"
}
if ($AdminWeb) {
    Write-Host "  Admin web stdout: $($AdminWeb.OutLog)"
    Write-Host "  Admin web stderr: $($AdminWeb.ErrLog)"
}
