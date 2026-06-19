param(
    [int]$Port = 8010,
    [switch]$NoAdminWeb,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $ScriptDir "..")

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

function Stop-MatchedProcess {
    param(
        [string]$Name,
        [string]$ProcessNamePattern,
        [string]$CommandPattern
    )

    $escapedRoot = [regex]::Escape($Root.Path)
    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like $ProcessNamePattern -and
            $_.CommandLine -match $escapedRoot -and
            $_.CommandLine -match $CommandPattern
        } |
        Sort-Object ProcessId -Unique

    if (-not $processes) {
        Write-Host "No matching $Name process found."
        return
    }

    foreach ($process in $processes) {
        if ($DryRun) {
            Write-Host "Would stop $Name PID $($process.ProcessId)"
            continue
        }

        Write-Host "Stopping $Name PID $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    if ($DryRun) {
        return
    }

    foreach ($process in $processes) {
        try {
            Wait-Process -Id $process.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        } catch {
            # The process may already be gone.
        }
    }
}

function Stop-ProjectProcessOnPort {
    param(
        [int]$TargetPort,
        [string]$Name
    )

    $connections = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
        Sort-Object OwningProcess -Unique
    if (-not $connections) {
        Write-Host "No listener found on $Name port $TargetPort."
        return
    }

    $escapedRoot = [regex]::Escape($Root.Path)
    foreach ($connection in $connections) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)" -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        if ($process.CommandLine -notmatch $escapedRoot) {
            Write-Warning "Skipping PID $($connection.OwningProcess) on $Name port $TargetPort because it is not under this project root."
            continue
        }

        if ($DryRun) {
            Write-Host "Would stop listener on $Name port $TargetPort PID $($connection.OwningProcess)"
            continue
        }

        Write-Host "Stopping listener on $Name port $TargetPort PID $($connection.OwningProcess)"
        Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
    }

    if ($DryRun) {
        return
    }

    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 300
        $remaining = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
            Where-Object {
                $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($_.OwningProcess)" -ErrorAction SilentlyContinue
                $process -and $process.CommandLine -match $escapedRoot
            }
    } while ($remaining -and (Get-Date) -lt $deadline)
}

$AdminWebPortText = $env:ADMIN_WEB_PORT
if (-not $AdminWebPortText) {
    $AdminWebPortText = Get-DotEnvValue "ADMIN_WEB_PORT"
}
if (-not $AdminWebPortText) {
    $AdminWebPortText = "5173"
}
$AdminWebPort = [int]$AdminWebPortText

Write-Host "Stopping local project services..."
if ($DryRun) {
    Write-Host "Dry run mode: no processes will be stopped."
}

Stop-MatchedProcess -Name "API" -ProcessNamePattern "python*" -CommandPattern "uvicorn app\.main:app"
Stop-MatchedProcess -Name "WeCom AiBot worker" -ProcessNamePattern "python*" -CommandPattern "scripts\\run_wecom_aibot_worker\.py"
Stop-MatchedProcess -Name "Message reconcile worker" -ProcessNamePattern "python*" -CommandPattern "scripts\\run_message_reconcile_worker\.py"
Stop-MatchedProcess -Name "AI memory full-test worker" -ProcessNamePattern "python*" -CommandPattern "scripts\\run_ai_memory_full_test_worker\.py"
Stop-ProjectProcessOnPort -TargetPort $Port -Name "API"

if (-not $NoAdminWeb) {
    Stop-MatchedProcess -Name "Admin web" -ProcessNamePattern "node*" -CommandPattern "vite"
    Stop-ProjectProcessOnPort -TargetPort $AdminWebPort -Name "admin web"
}

if ($DryRun) {
    Write-Host "Dry run complete."
} else {
    Write-Host "Local project services stopped."
}
