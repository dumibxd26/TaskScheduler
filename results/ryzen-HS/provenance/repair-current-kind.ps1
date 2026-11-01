$ErrorActionPreference = "Stop"
Set-Location "C:\work_proj\TaskScheduler"
$result = "results\ryzen-HS"
$session = "C:\Users\bdumitrescu\.copilot\session-state\d1b8eb9e-c336-4612-82cf-776ad8e74a45\files"
$env:KUBECONFIG = Join-Path $session "kind-current-kubeconfig"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$cluster = "ts-pc-8840hs-20261101"
$archive = Join-Path $result "integrity-first-matrix"
if (Test-Path $archive) { throw "Initial matrix has already been archived" }
New-Item -ItemType Directory -Path $archive >$null
foreach ($profile in @("balanced", "homogeneous", "edge")) {
    foreach ($suffix in @("", "-cluster", "-twin", "-twin-release")) {
        Copy-Item -LiteralPath (Join-Path $result "kind-validation\$profile$suffix.json") -Destination $archive
    }
}
$records = New-Object System.Collections.Generic.List[object]
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Invoke-RepairCommand {
    param([string]$Name, [string]$Command, [string[]]$Arguments)
    $start = [DateTime]::UtcNow
    $watch = [Diagnostics.Stopwatch]::StartNew()
    & $Command @Arguments *> (Join-Path $result "$Name.log")
    $code = $LASTEXITCODE
    $watch.Stop()
    $records.Add([ordered]@{
        name=$Name; executable=$Command; arguments=$Arguments; exit_code=$code
        started_at=$start.ToString("o"); finished_at=[DateTime]::UtcNow.ToString("o")
        wall_seconds=$watch.Elapsed.TotalSeconds; log="$Name.log"
    })
    [IO.File]::WriteAllText((Join-Path $result "integrity-repair-commands.json"),
        (ConvertTo-Json -InputObject @($records.ToArray()) -Depth 6), $utf8)
    if ($code -ne 0) { throw "$Name failed with exit $code" }
    Write-Host "$Name passed ($([Math]::Round($watch.Elapsed.TotalSeconds, 2)) s)"
}
try {
    foreach ($profile in @("balanced", "homogeneous", "edge")) {
        Invoke-RepairCommand "repair-create-$profile" $python @("kind_lab.py",
            "--profiles", "$result\kind-profiles.yaml", "create", $profile,
            "--record", "$result\kind-validation\$profile-repair-cluster.json")
        foreach ($image in @("ts-control-plane:v1", "ts-fileserver:v1", "ts-bw-probe:v1",
            "ts-thermal:v1", "ts-task-io:v1", "ts-task-mem:v1", "ts-task-cpu:v1")) {
            Invoke-RepairCommand "repair-load-$profile-$($image.Split(':')[0])" kind @(
                "load", "docker-image", $image, "--name", $cluster)
        }
        Invoke-RepairCommand "repair-install-$profile" kubectl @("apply", "-k", ".")
        foreach ($deployment in @("ts-controller", "ts-scheduler")) {
            Invoke-RepairCommand "repair-ready-$profile-$deployment" kubectl @(
                "-n", "ts-system", "rollout", "status", "deployment/$deployment", "--timeout=240s")
        }
        foreach ($daemon in @("ts-fileserver", "ts-bw-probe", "ts-thermal")) {
            Invoke-RepairCommand "repair-ready-$profile-$daemon" kubectl @(
                "-n", "ts-system", "rollout", "status", "daemonset/$daemon", "--timeout=240s")
        }
        Invoke-RepairCommand "repair-trials-$profile" $python @(
            (Join-Path $session "rerun-invalid-trials.py"), $profile)
    }
    $runtime = [ordered]@{
        node_images = @(docker image ls --filter "reference=kindest/node" --digests --format "{{.Repository}}:{{.Tag}} {{.Digest}} {{.ID}}")
        kubernetes_server = (kubectl version -o json | ConvertFrom-Json).serverVersion
        images = @(docker image inspect ts-control-plane:v1 ts-fileserver:v1 ts-bw-probe:v1 ts-thermal:v1 ts-task-io:v1 ts-task-mem:v1 ts-task-cpu:v1 | ConvertFrom-Json | Select-Object RepoTags,Id,RepoDigests)
    }
    [IO.File]::WriteAllText((Join-Path $result "kubernetes-runtime.json"),
        ($runtime | ConvertTo-Json -Depth 7), $utf8)
    Invoke-RepairCommand "verified-kind-integrity" $python @(
        "analyze_kind_validation.py", "--results-dir", "$result\kind-validation")
} finally {
    & kind delete cluster --name $cluster
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not delete isolated repair cluster" }
}
