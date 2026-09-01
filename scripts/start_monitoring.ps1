[CmdletBinding()]
param(
    [ValidateSet("Start", "Stop", "Status", "Check")]
    [string]$Action = "Start",
    [int]$ApiPort = 8000,
    [int]$PrometheusPort = 9090,
    [int]$GrafanaPort = 3300,
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$WorkspaceRoot = Split-Path -Parent $PSScriptRoot
$MonitoringRoot = Join-Path $WorkspaceRoot "deploy\monitoring"
$ComposePath = Join-Path $MonitoringRoot "compose.yml"
$RuntimeRoot = Join-Path $WorkspaceRoot "artifacts\monitoring"
$TargetPath = Join-Path $RuntimeRoot "prometheus-targets.json"
$ComposeExe = Get-Command "docker-compose.exe" -ErrorAction SilentlyContinue
$env:STYLEFORGE_PROMETHEUS_PORT = "$PrometheusPort"
$env:STYLEFORGE_GRAFANA_PORT = "$GrafanaPort"

function Assert-DockerAvailable {
    if (-not (Get-Command "docker.exe" -ErrorAction SilentlyContinue)) {
        throw "docker.exe was not found. Install or start Docker Desktop first."
    }
    & docker.exe info --format "{{.ServerVersion}}" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon is unavailable. Start Docker Desktop first."
    }
    if ($null -eq $ComposeExe) {
        & docker.exe compose version | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose is unavailable. Install the Compose plugin first."
        }
    }
}

function Invoke-MonitoringCompose([string[]]$ComposeArguments) {
    if ($null -ne $ComposeExe) {
        & $ComposeExe.Source --project-directory $MonitoringRoot -f $ComposePath @ComposeArguments
    } else {
        & docker.exe compose --project-directory $MonitoringRoot -f $ComposePath @ComposeArguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($ComposeArguments -join ' ')"
    }
}

function Wait-ForEndpoint([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

Assert-DockerAvailable

switch ($Action) {
    "Check" {
        Invoke-MonitoringCompose -ComposeArguments @("config", "--quiet")
        Write-Host "Monitoring configuration is valid."
    }
    "Status" {
        Invoke-MonitoringCompose -ComposeArguments @("ps")
    }
    "Stop" {
        Invoke-MonitoringCompose -ComposeArguments @("down")
        Write-Host "Monitoring stopped. Historical data remains in $RuntimeRoot"
    }
    "Start" {
        New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot "prometheus") -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot "grafana") -Force | Out-Null
        $targetGroups = @(
            @{
                targets = @("host.docker.internal:$ApiPort")
                labels = @{ service = "styleforge-api"; deployment = "local" }
            }
        )
        $targetConfig = ConvertTo-Json -InputObject $targetGroups -Depth 4
        [System.IO.File]::WriteAllText(
            $TargetPath,
            $targetConfig,
            [System.Text.UTF8Encoding]::new($false)
        )
        Invoke-MonitoringCompose -ComposeArguments @("config", "--quiet")
        Invoke-MonitoringCompose -ComposeArguments @("up", "-d")
        if (-not (Wait-ForEndpoint -Url "http://127.0.0.1:$PrometheusPort/-/ready" -TimeoutSeconds $StartupTimeoutSeconds)) {
            throw "Prometheus startup timed out. Run this script with -Action Status and inspect Docker logs."
        }
        if (-not (Wait-ForEndpoint -Url "http://127.0.0.1:$GrafanaPort/api/health" -TimeoutSeconds $StartupTimeoutSeconds)) {
            throw "Grafana startup timed out. Run this script with -Action Status and inspect Docker logs."
        }
        Write-Host "StyleForge monitoring started."
        Write-Host "API target: host.docker.internal:$ApiPort"
        Write-Host "Prometheus: http://127.0.0.1:$PrometheusPort"
        Write-Host "Grafana:    http://127.0.0.1:$GrafanaPort/d/styleforge-system-overview"
    }
}
