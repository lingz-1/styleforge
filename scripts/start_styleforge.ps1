[CmdletBinding()]
param(
    [ValidateSet("Start", "Stop", "Status", "Check")]
    [string]$Action = "Start",
    [ValidateSet("Web", "Streamlit")]
    [string]$Ui = "Web",
    [string]$ImageRoot = "E:\image.tar\image\images",
    [int]$ApiPort = 8000,
    [int]$UiPort = 5173,
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$WorkspaceRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = "D:\anaconda\envs\style\python.exe"
$RuntimeRoot = Join-Path $WorkspaceRoot "artifacts\runtime"
$StatePath = Join-Path $RuntimeRoot "styleforge-processes.json"
$CheckScript = Join-Path $PSScriptRoot "check_environment.py"
$ApiRoot = Join-Path $WorkspaceRoot "apps\api"
$WebRoot = Join-Path $WorkspaceRoot "apps\web"

if ($ImageRoot -and (Test-Path -LiteralPath $ImageRoot -PathType Container)) {
    $env:GARMENTS2LOOK_IMAGE_ROOT = (Resolve-Path -LiteralPath $ImageRoot).Path
}

function Invoke-EnvironmentCheck {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Required Python runtime not found: $PythonExe"
    }
    & $PythonExe $CheckScript
    if ($LASTEXITCODE -ne 0) {
        throw "Environment check failed. Fix the FAIL entries before starting StyleForge."
    }
    if ($Ui -eq "Web" -and -not (Get-Command "npm.cmd" -ErrorAction SilentlyContinue)) {
        throw "npm.cmd was not found. Install Node.js before starting the Web UI."
    }
}

function Assert-PortAvailable([int]$Port) {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    try {
        $listener.Start()
    } catch {
        throw "Port $Port is already in use. Choose another port or stop the existing service."
    } finally {
        $listener.Stop()
    }
}

function Read-ProcessState {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        return $null
    }
    return Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
}

function Get-OwnedProcess([int]$ProcessId, [string]$ExpectedToken) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    if (-not $process.CommandLine -or -not $process.CommandLine.Contains($ExpectedToken)) {
        return $null
    }
    return $process
}

function Show-Status {
    $state = Read-ProcessState
    if ($null -eq $state) {
        Write-Host "StyleForge is not tracked as running."
        return $false
    }
    $api = Get-OwnedProcess -ProcessId ([int]$state.api.pid) -ExpectedToken "styleforge.api:app"
    $ui = Get-OwnedProcess -ProcessId ([int]$state.ui.pid) -ExpectedToken ([string]$state.ui.token)
    Write-Host "API: $($(if ($api) { 'running' } else { 'stopped' })) (PID $($state.api.pid), $($state.api.url))"
    Write-Host "UI:  $($(if ($ui) { 'running' } else { 'stopped' })) (PID $($state.ui.pid), $($state.ui.url))"
    return ($null -ne $api -and $null -ne $ui)
}

function Stop-TrackedProcesses {
    $state = Read-ProcessState
    if ($null -eq $state) {
        Write-Host "No tracked StyleForge processes to stop."
        return
    }
    foreach ($entry in @($state.ui, $state.api)) {
        $owned = Get-OwnedProcess -ProcessId ([int]$entry.pid) -ExpectedToken ([string]$entry.token)
        if ($null -ne $owned) {
            & taskkill.exe /PID ([int]$entry.pid) /T /F | Out-Null
            Write-Host "Stopped $($entry.name) (PID $($entry.pid))."
        }
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
}

function Wait-ForHealth([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            if ($response.status -eq "ok") {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Wait-ForHttp([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

switch ($Action) {
    "Check" {
        Invoke-EnvironmentCheck
        Write-Host "Environment check passed."
    }
    "Status" {
        [void](Show-Status)
    }
    "Stop" {
        Stop-TrackedProcesses
    }
    "Start" {
        if (Show-Status) {
            throw "StyleForge is already running. Use -Action Stop first."
        }
        $env:PYTHONPATH = $ApiRoot
        Invoke-EnvironmentCheck
        Assert-PortAvailable -Port $ApiPort
        if ($Ui -eq "Streamlit" -and $UiPort -eq 5173) { $UiPort = 8501 }
        Assert-PortAvailable -Port $UiPort
        New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null

        $apiStdout = Join-Path $RuntimeRoot "api.stdout.log"
        $apiStderr = Join-Path $RuntimeRoot "api.stderr.log"
        $apiArgs = @(
            "-m", "uvicorn", "styleforge.api:app",
            "--app-dir", $ApiRoot,
            "--host", "127.0.0.1",
            "--port", "$ApiPort"
        )
        $apiProcess = Start-Process -FilePath $PythonExe -ArgumentList $apiArgs `
            -WorkingDirectory $WorkspaceRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $apiStdout -RedirectStandardError $apiStderr

        if ($Ui -eq "Web") {
            # Vite inherits this process environment. Keep its /api proxy in
            # lockstep with a custom -ApiPort instead of silently using :8000.
            $env:STYLEFORGE_API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
            $uiStdout = Join-Path $RuntimeRoot "web.stdout.log"
            $uiStderr = Join-Path $RuntimeRoot "web.stderr.log"
            $uiArgs = @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$UiPort")
            $uiProcess = Start-Process -FilePath "npm.cmd" -ArgumentList $uiArgs `
                -WorkingDirectory $WebRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $uiStdout -RedirectStandardError $uiStderr
            $uiToken = "npm.cmd"
            $uiUrl = "http://127.0.0.1:$UiPort"
        } else {
            $uiStdout = Join-Path $RuntimeRoot "streamlit.stdout.log"
            $uiStderr = Join-Path $RuntimeRoot "streamlit.stderr.log"
            $uiArgs = @(
                "-m", "streamlit", "run", (Join-Path $ApiRoot "styleforge\ui.py"),
                "--server.address", "127.0.0.1",
                "--server.port", "$UiPort"
            )
            $uiProcess = Start-Process -FilePath $PythonExe -ArgumentList $uiArgs `
                -WorkingDirectory $WorkspaceRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $uiStdout -RedirectStandardError $uiStderr
            $uiToken = "streamlit"
            $uiUrl = "http://127.0.0.1:$UiPort"
        }

        $state = @{
            started_at = (Get-Date).ToString("o")
            api = @{ name = "API"; pid = $apiProcess.Id; token = "styleforge.api:app"; url = "http://127.0.0.1:$ApiPort" }
            ui = @{ name = $Ui; pid = $uiProcess.Id; token = $uiToken; url = $uiUrl }
        }
        $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8

        if (-not (Wait-ForHealth -Url "http://127.0.0.1:$ApiPort/health" -TimeoutSeconds $StartupTimeoutSeconds)) {
            Stop-TrackedProcesses
            throw "API health check timed out. Inspect $apiStderr"
        }
        if (-not (Wait-ForHttp -Url $uiUrl -TimeoutSeconds $StartupTimeoutSeconds)) {
            Stop-TrackedProcesses
            throw "UI health check timed out. Inspect $uiStderr"
        }
        Write-Host "StyleForge started."
        Write-Host "UI:  $uiUrl"
        Write-Host "API: http://127.0.0.1:$ApiPort/docs"
        Write-Host "Logs: $RuntimeRoot"
    }
}
