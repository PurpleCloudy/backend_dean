param([ValidateSet('Start','Status')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $projectRoot '.local/prototype_runtime'
$frontend = Join-Path $projectRoot '.local/dean-agent-front/frontend'
$envFile = Join-Path $projectRoot '.local/frontend_verification/runtime.env'
$vite = Join-Path $frontend 'node_modules/vite/bin/vite.js'
$composeArgs = @('compose','--env-file',$envFile,'-f',(Join-Path $projectRoot '.local/frontend_verification/compose.yaml'),'-f',(Join-Path $projectRoot '.local/bge/compose.real.yaml'))
$pidFile = Join-Path $runtime 'frontend.pid'
$loginFile = Join-Path $runtime 'local-login.txt'

function Json-Get([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 } catch { return $null }
}

function Owned-Frontend {
    if (!(Test-Path -LiteralPath $pidFile)) { return $null }
    $processId = [int](Get-Content -LiteralPath $pidFile -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    if ($process -and $process.CommandLine -and $process.CommandLine.Contains($vite) -and $process.CommandLine.Contains('preview')) { return $process }
    return $null
}

function Wait-Http([string]$Url, [int]$Seconds = 90) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Json-Get $Url) { return }
        Start-Sleep -Seconds 2
    }
    throw "Service did not become ready: $Url"
}

function Status {
    $api = Json-Get 'http://127.0.0.1:18000/health/ready'
    $schema = Json-Get 'http://127.0.0.1:18000/openapi.json'
    $bge = Json-Get 'http://127.0.0.1:8231/health'
    $lmApi = Json-Get 'http://127.0.0.1:1234/v1/models'
    try { $loaded = @((& lms ps --json 2>$null | ConvertFrom-Json) | Where-Object { $_.identifier -eq 'qwen2.5-7b-instruct' -and $_.contextLength -ge 16384 }) } catch { $loaded = @() }
    try { $workerRunning = (& docker inspect --format '{{.State.Running}}' deanery-frontend-qa-worker-1 2>$null) -eq 'true' } catch { $workerRunning = $false }
    $ours = Owned-Frontend
    $frontOk = $false
    if ($ours -and (Test-Path -LiteralPath (Join-Path $frontend 'dist/index.html'))) {
        try {
            $html = (Invoke-WebRequest 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 5).Content
            $expected = Get-Content -LiteralPath (Join-Path $frontend 'dist/index.html') -Raw
            $asset = [regex]::Match($expected, '/assets/[^" ]+\.js').Value
            $frontOk = $asset -and $html.Contains($asset)
        } catch { }
    }
    $apiOk = $api.status -eq 'ready' -and $schema.info.title -eq 'Deanery API' -and $schema.paths.'/api/v1/olap/query'
    $bgeOk = $bge.model -eq 'BAAI/bge-m3' -and $bge.dimension -eq 1024
    $lmOk = $lmApi -and @($lmApi.data.id) -contains 'qwen2.5-7b-instruct'
    return [ordered]@{ healthy = [bool]($apiOk -and $bgeOk -and $lmOk -and $loaded.Count -gt 0 -and $workerRunning -and $frontOk)
        url = 'http://127.0.0.1:5173'; frontend = [bool]$frontOk; api_ready = [bool]$apiOk; components = $api.components
        bge = $bge; qwen_loaded = ($loaded.Count -gt 0); worker_running = [bool]$workerRunning; login_file = $loginFile }
}

if ($Action -eq 'Start') {
    foreach ($path in @($envFile,$vite,(Join-Path $projectRoot '.local/bge-venv/Scripts/python.exe'),(Join-Path $projectRoot '.local/bge/model/pytorch_model.bin'))) {
        if (!(Test-Path -LiteralPath $path)) { throw "Local prerequisite missing: $path. See docs/PROTOTYPE.md." }
    }
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    $listener = Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue
    $ours = Owned-Frontend
    if ($listener -and (!$ours -or @($listener.OwningProcess) -notcontains $ours.ProcessId)) {
        throw 'Port 5173 belongs to an unrecognized process. It was not stopped. Close that application explicitly before starting this prototype.'
    }
    & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, then run this command again.' }
    if (!(Json-Get 'http://127.0.0.1:8231/health')) {
        if (Get-NetTCPConnection -State Listen -LocalPort 8231 -ErrorAction SilentlyContinue) { throw 'Port 8231 is occupied but BGE is unhealthy; inspect .local/bge/stderr.log.' }
        $loading = $false
        foreach ($knownPid in @((Join-Path $runtime 'bge.pid'), (Join-Path $projectRoot '.local/bge/process.pid'))) {
            if (Test-Path -LiteralPath $knownPid) {
                $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId='+[int](Get-Content -LiteralPath $knownPid -Raw))
                if ($candidate.CommandLine -and $candidate.CommandLine.Contains('integrations.bge_service')) { $loading = $true }
            }
        }
        if (!$loading) {
            $env:HF_HUB_OFFLINE = '1'; $env:TRANSFORMERS_OFFLINE = '1'
            $bgeProcess = Start-Process -FilePath (Join-Path $projectRoot '.local/bge-venv/Scripts/python.exe') -ArgumentList @('-m','integrations.bge_service') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'bge.stdout.log') -RedirectStandardError (Join-Path $runtime 'bge.stderr.log')
            $bgeProcess.Id | Set-Content -LiteralPath (Join-Path $runtime 'bge.pid')
        }
        Wait-Http 'http://127.0.0.1:8231/health'
    }
    $loaded = @((& lms ps --json | ConvertFrom-Json) | Where-Object { $_.identifier -eq 'qwen2.5-7b-instruct' -and $_.contextLength -ge 16384 })
    if ($loaded.Count -eq 0) {
        & lms load 'lmstudio-community/Qwen2.5-7B-Instruct-GGUF/Qwen2.5-7B-Instruct-Q4_K_M.gguf' --exact --context-length 16384 --gpu max --identifier qwen2.5-7b-instruct --yes
        if ($LASTEXITCODE -ne 0) { throw 'Qwen loading failed; inspect LM Studio.' }
    }
    if (!(Json-Get 'http://127.0.0.1:1234/v1/models')) {
        if (Get-NetTCPConnection -State Listen -LocalPort 1234 -ErrorAction SilentlyContinue) { throw 'Port 1234 is occupied by an unhealthy model API.' }
        & lms server start --port 1234
        if ($LASTEXITCODE -ne 0) { throw 'LM Studio server start failed.' }
    }
    Push-Location $projectRoot
    try {
        & docker @composeArgs up -d postgres s3 qdrant clamav api worker agent
        if ($LASTEXITCODE -ne 0) { throw 'Prototype containers did not start.' }
        Wait-Http 'http://127.0.0.1:18000/health/ready' 180
    } finally { Pop-Location }
    Push-Location $frontend
    try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; existing preview was preserved.' } } finally { Pop-Location }
    $current = Owned-Frontend
    if ($ours -and $current -and $current.CreationDate -eq $ours.CreationDate) { Stop-Process -Id $current.ProcessId }
    $env:VITE_API_TARGET = 'http://127.0.0.1:18000'
    $node = (Get-Command node.exe).Source
    $front = Start-Process -FilePath $node -ArgumentList @("`"$vite`"",'preview','--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $frontend -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'frontend.stdout.log') -RedirectStandardError (Join-Path $runtime 'frontend.stderr.log')
    $front.Id | Set-Content -LiteralPath $pidFile
    $settings = @{}
    Get-Content -LiteralPath $envFile | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { $settings[$matches[1]] = $matches[2] } }
    $lines = @('Local prototype: http://127.0.0.1:5173','Private local credentials. Do not share or commit this file.','')
    foreach ($role in @('ADMIN','STAFF','STUDENT')) {
        if (!$settings["TEST_${role}_LOGIN"] -or !$settings["TEST_${role}_PASSWORD"]) { throw "Missing local $role account credentials." }
        $lines += @($role, ('Login: '+$settings["TEST_${role}_LOGIN"]), ('Password: '+$settings["TEST_${role}_PASSWORD"]), '')
    }
    if (!(Test-Path -LiteralPath $loginFile)) { New-Item -ItemType File -Path $loginFile | Out-Null }
    $acl = [System.Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'))
    $privateFile = [System.IO.FileInfo]::new($loginFile)
    if ($PSVersionTable.PSEdition -eq 'Desktop') { $privateFile.SetAccessControl($acl) }
    else { [System.IO.FileSystemAclExtensions]::SetAccessControl($privateFile,$acl) }
    $lines | Set-Content -LiteralPath $loginFile -Encoding UTF8
    Start-Sleep -Seconds 2
}
$status = Status
$status | ConvertTo-Json -Depth 8
if ($Action -eq 'Start' -and !$status.healthy) { throw 'Prototype is not fully ready; inspect the component status above.' }
