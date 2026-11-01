$ErrorActionPreference = "Stop"
Set-Location "C:\work_proj\TaskScheduler"
$result = "results\ryzen-HS"
$session = "C:\Users\bdumitrescu\.copilot\session-state\d1b8eb9e-c336-4612-82cf-776ad8e74a45\files"
$commands = Get-Content (Join-Path $result "commands.json") -Raw | ConvertFrom-Json
if ($commands.Count -ne 13 -or @($commands | Where-Object exit_code -ne 0).Count) {
    throw "All 13 local suites must finish successfully before starting live timing"
}
$env:KUBECONFIG = Join-Path $session "kind-current-kubeconfig"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"
$records = New-Object System.Collections.Generic.List[object]
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Invoke-Recorded {
    param([string]$Name, [string]$Command, [string[]]$Arguments)
    $started = [DateTime]::UtcNow
    $watch = [Diagnostics.Stopwatch]::StartNew()
    & $Command @Arguments *> (Join-Path $result "$Name.log")
    $code = $LASTEXITCODE
    $watch.Stop()
    $records.Add([ordered]@{
        name=$Name; executable=$Command; arguments=$Arguments
        started_at=$started.ToString("o"); finished_at=[DateTime]::UtcNow.ToString("o")
        wall_seconds=$watch.Elapsed.TotalSeconds; exit_code=$code; log="$Name.log"
    })
    [IO.File]::WriteAllText((Join-Path $result "kubernetes-commands.json"),
        (ConvertTo-Json -InputObject @($records.ToArray()) -Depth 6), $utf8)
    if ($code -ne 0) { throw "$Name failed (exit $code); see $result\$Name.log" }
    Write-Host "$Name passed ($([Math]::Round($watch.Elapsed.TotalSeconds, 2)) s)"
}
Invoke-Recorded "build-control-plane" docker @("build", "-t", "ts-control-plane:v1",
    "-f", (Join-Path $session "control-plane-mirror.Dockerfile"), ".")
Invoke-Recorded "build-fileserver" docker @("build", "-t", "ts-fileserver:v1", "images\fileserver")
Invoke-Recorded "build-bw-probe" docker @("build", "-t", "ts-bw-probe:v1",
    "-f", (Join-Path $session "bw-probe-mirror.Dockerfile"), "images\bw-probe")
Invoke-Recorded "build-thermal" docker @("build", "-t", "ts-thermal:v1",
    "-f", (Join-Path $session "thermal-mirror.Dockerfile"), "images\thermal")
foreach ($kind in @("io", "mem", "cpu")) {
    Invoke-Recorded "build-task-$kind" docker @("build", "-t", "ts-task-${kind}:v1",
        "-f", "tasks\task_$kind\Dockerfile", ".")
}
$cluster = "ts-pc-8840hs-20261101"
try {
    Invoke-Recorded "kind-matrix" powershell @("-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "scripts\run-kind-validation.ps1", "-Profiles", "balanced,homogeneous,edge",
        "-Repetitions", "3", "-Seed", "2026", "-SkipBuild",
        "-ProfilesFile", "$result\kind-profiles.yaml",
        "-ResultDirectory", "$result\kind-validation")
    $runtime = [ordered]@{
        node_images = @(docker image ls --filter "reference=kindest/node" --digests --format "{{.Repository}}:{{.Tag}} {{.Digest}} {{.ID}}")
        kubernetes_server = (kubectl version -o json | ConvertFrom-Json).serverVersion
        images = @(docker image inspect ts-control-plane:v1 ts-fileserver:v1 ts-bw-probe:v1 ts-thermal:v1 ts-task-io:v1 ts-task-mem:v1 ts-task-cpu:v1 | ConvertFrom-Json | Select-Object RepoTags,Id,RepoDigests)
    }
    [IO.File]::WriteAllText((Join-Path $result "kubernetes-runtime.json"),
        ($runtime | ConvertTo-Json -Depth 7), $utf8)
} finally {
    & kind delete cluster --name $cluster
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not clean up the isolated cluster $cluster" }
}
