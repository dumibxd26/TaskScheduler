param(
    [string[]]$Profiles = @("balanced", "homogeneous", "edge"),
    [int]$Repetitions = 3,
    [int]$Seed = 2026,
    [string[]]$Policies = @(),
    [string]$ResultDirectory = "results\kind-validation",
    [switch]$SkipBuild
)

# Same-host Kind validation matrix. For every profile: create the emulated
# cluster (kind_lab.py), install the control plane, run the calibrated
# warm-up and all (batch, policy) trials (run_k8s_experiment.py), and predict
# the same trials with the calibrated simulator twin (kind_twin_predict.py).
# The analyzer then gates integrity and compares live and twin results.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$images = @(
    "ts-control-plane:v1", "ts-fileserver:v1", "ts-bw-probe:v1", "ts-thermal:v1",
    "ts-task-io:v1", "ts-task-mem:v1", "ts-task-cpu:v1"
)
# "powershell -File" passes "-Profiles a,b" as one string; accept both forms.
$Profiles = @($Profiles | ForEach-Object { $_ -split "," } |
    ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Policies = @($Policies | ForEach-Object { $_ -split "," } |
    ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (-not (Test-Path $python)) { throw "Python virtual environment not found at $python" }
if ($Repetitions -lt 1) { throw "Repetitions must be at least 1" }

function Invoke-Checked {
    param([Parameter(Mandatory = $true)][string]$Command,
          [Parameter(Mandatory = $true)][string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

if (-not $SkipBuild) {
    $builds = @(
        @("build", "-t", "ts-control-plane:v1", "-f", "images/control-plane/Dockerfile", "."),
        @("build", "-t", "ts-fileserver:v1", "images/fileserver"),
        @("build", "-t", "ts-bw-probe:v1", "images/bw-probe"),
        @("build", "-t", "ts-thermal:v1", "images/thermal"),
        @("build", "-t", "ts-task-io:v1", "-f", "tasks/task_io/Dockerfile", "."),
        @("build", "-t", "ts-task-mem:v1", "-f", "tasks/task_mem/Dockerfile", "."),
        @("build", "-t", "ts-task-cpu:v1", "-f", "tasks/task_cpu/Dockerfile", ".")
    )
    foreach ($build in $builds) {
        Write-Host "=== docker $($build -join ' ') ==="
        Invoke-Checked docker $build
    }
}

$resultDirectory = Join-Path $root $ResultDirectory
New-Item -ItemType Directory -Force -Path $resultDirectory | Out-Null

foreach ($profileName in $Profiles) {
    Write-Host "=== Creating emulated Kind profile: $profileName ==="
    Invoke-Checked $python @("kind_lab.py", "create", $profileName,
        "--record", (Join-Path $resultDirectory "$profileName-cluster.json"))
    foreach ($image in $images) {
        Invoke-Checked kind @("load", "docker-image", $image, "--name", "ts-cluster")
    }
    Invoke-Checked kubectl @("apply", "-k", ".")
    foreach ($deployment in @("ts-controller", "ts-scheduler")) {
        Invoke-Checked kubectl @("-n", "ts-system", "rollout", "status",
            "deployment/$deployment", "--timeout=240s")
    }
    foreach ($daemonSet in @("ts-fileserver", "ts-bw-probe", "ts-thermal")) {
        Invoke-Checked kubectl @("-n", "ts-system", "rollout", "status",
            "daemonset/$daemonSet", "--timeout=240s")
    }
    $output = Join-Path $resultDirectory "$profileName.json"
    $harnessArguments = @("run_k8s_experiment.py", "--profile", $profileName,
        "--repetitions", "$Repetitions", "--seed", "$Seed", "--output", $output)
    if ($Policies.Count -gt 0) { $harnessArguments += @("--policies") + $Policies }
    Invoke-Checked $python $harnessArguments
    Invoke-Checked $python @("kind_twin_predict.py", $output)
}

$analyzeArguments = @("analyze_kind_validation.py", "--results-dir", $resultDirectory,
    "--profiles") + $Profiles
Invoke-Checked $python $analyzeArguments
Write-Host "Kind validation matrix complete: $resultDirectory"
