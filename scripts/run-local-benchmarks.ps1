param(
    [Parameter(Mandatory = $true)][string]$ResultDirectory
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing virtual environment: $python" }
$output = [System.IO.Path]::GetFullPath((Join-Path $root $ResultDirectory))
if (Test-Path $output) { throw "Refusing to overwrite an existing run: $output" }
New-Item -ItemType Directory -Path $output | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$runs = New-Object System.Collections.Generic.List[object]

function Invoke-Measured {
    param([string]$Name, [string[]]$Arguments)
    $started = [DateTime]::UtcNow
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $log = Join-Path $output "$Name.log"
    Write-Host "Starting $Name at $($started.ToString('o'))"
    & $python @Arguments *> $log
    $code = $LASTEXITCODE
    $watch.Stop()
    $runs.Add([ordered]@{
        name = $Name
        executable = $python
        arguments = $Arguments
        started_at = $started.ToString("o")
        finished_at = [DateTime]::UtcNow.ToString("o")
        wall_seconds = $watch.Elapsed.TotalSeconds
        exit_code = $code
        log = "$Name.log"
    })
    [IO.File]::WriteAllText((Join-Path $output "commands.json"),
        (ConvertTo-Json -InputObject @($runs.ToArray()) -Depth 6), $utf8)
    if ($code -ne 0) { throw "$Name failed (exit $code); see $log" }
    Write-Host "Completed $Name in $([Math]::Round($watch.Elapsed.TotalSeconds, 2)) seconds"
}

$closed = "fcfs,random,eft,heft,heft-ra,heft-ra-cap,k8s-default,legacy,legacy-v1,legacy-tuned,adaptive-no-preempt,adaptive"
$common = "eft,heft,heft-ra,heft-ra-cap,k8s-default,legacy,legacy-v1,legacy-tuned,adaptive-no-preempt,adaptive"
$phases = @(
    @{ Name = "exploratory-s0-19"; Seeds = "0:20"; Floor = "0"; Policies = $closed },
    @{ Name = "confirmatory-s200-249"; Seeds = "200:250"; Floor = "0.1"; Policies = $common },
    @{ Name = "holdout-s400-449"; Seeds = "400:450"; Floor = "0.1"; Policies = $common },
    @{ Name = "holdout2-s600-649"; Seeds = "600:650"; Floor = "0.1"; Policies = $common }
)
foreach ($phase in $phases) {
    foreach ($mode in @("run", "workload")) {
        $prefix = if ($mode -eq "run") { "closed" } else { "workload" }
        $name = "$prefix-$($phase.Name)"
        $arguments = @("run_experiment_lab.py", $mode, "--seeds", $phase.Seeds,
            "--sample-uncertainty", "--uncertainty-floor", $phase.Floor,
            "--policies", $phase.Policies, "--quiet", "--output",
            (Join-Path $output "sim\$name"))
        if ($mode -eq "run" -and $phase.Floor -eq "0") {
            $arguments += @("--oracle-max-tasks", "3")
        }
        Invoke-Measured $name $arguments
    }
}
Invoke-Measured "mechanism-studies" @("run_mechanism_studies.py",
    "--output", (Join-Path $output "mechanism-studies.json"))
Invoke-Measured "legacy-weight-search" @("tune_legacy_weights.py",
    "--candidates", "256", "--finalists", "25", "--quiet",
    "--output", (Join-Path $output "legacy-weight-search.json"))
Invoke-Measured "legacy-factor-ablation" @("run_legacy_ablation.py",
    "--seeds", "300:310", "--output", (Join-Path $output "legacy-factor-ablation.json"))
Invoke-Measured "legacy-factor-stress" @("run_legacy_factor_stress.py",
    "--output", (Join-Path $output "legacy-factor-stress.json"))
Invoke-Measured "contextual-policy-search" @("tune_contextual_policy.py",
    "--candidates", "64", "--thermal-step", "60", "--quiet",
    "--profile-output", (Join-Path $output "contextual-v1.yaml"),
    "--report-output", (Join-Path $output "contextual-policy-search.json"))
Write-Host "All 13 local benchmark suites completed: $output"
