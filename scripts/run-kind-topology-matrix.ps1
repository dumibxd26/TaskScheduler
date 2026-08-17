param(
    [string[]]$Profiles = @("small", "homogeneous", "skewed"),
    [int]$Repetitions = 3,
    [int]$Seed = 2026,
    [double]$PriorityDelaySeconds = 7,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$gitBash = "C:\Program Files\Git\usr\bin\bash.exe"
$images = @(
    "ts-control-plane:v1",
    "ts-fileserver:v1",
    "ts-bw-probe:v1",
    "ts-thermal:v1",
    "ts-task-io:v1",
    "ts-task-mem:v1",
    "ts-task-cpu:v1"
)

if (-not (Test-Path $python)) {
    throw "Python virtual environment not found at $python"
}
if (-not (Test-Path $gitBash)) {
    throw "Git Bash not found at $gitBash"
}
if ($Repetitions -lt 1) {
    throw "Repetitions must be at least 1"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipBuild) {
    Invoke-Checked docker @(
        "build", "-t", "ts-control-plane:v1",
        "-f", "images/control-plane/Dockerfile", "."
    )
}

$resultDirectory = Join-Path $root "results\k8s-kind-topologies"
New-Item -ItemType Directory -Force -Path $resultDirectory | Out-Null

foreach ($profile in $Profiles) {
    Write-Host "=== Creating Kind topology: $profile ==="
    Invoke-Checked $gitBash @("setup_cluster.sh", $profile)

    foreach ($image in $images) {
        Invoke-Checked kind @(
            "load", "docker-image", $image, "--name", "ts-cluster"
        )
    }

    Invoke-Checked kubectl @("apply", "-k", ".")
    Invoke-Checked kubectl @(
        "-n", "ts-system", "rollout", "status",
        "deployment/ts-controller", "--timeout=180s"
    )
    Invoke-Checked kubectl @(
        "-n", "ts-system", "rollout", "status",
        "deployment/ts-scheduler", "--timeout=180s"
    )
    foreach ($daemonSet in @("ts-fileserver", "ts-bw-probe", "ts-thermal")) {
        Invoke-Checked kubectl @(
            "-n", "ts-system", "rollout", "status",
            "daemonset/$daemonSet", "--timeout=180s"
        )
    }

    $output = Join-Path $resultDirectory "$profile.json"
    Invoke-Checked $python @(
        "run_k8s_experiment.py",
        "--policies", "legacy-v1", "legacy-tuned", "adaptive",
        "--repetitions", "$Repetitions",
        "--seed", "$Seed",
        "--topology-name", $profile,
        "--priority-delay-seconds", "$PriorityDelaySeconds",
        "--timeout-seconds", "900",
        "--output", $output
    )
}

Write-Host "Kind topology matrix complete: $resultDirectory"