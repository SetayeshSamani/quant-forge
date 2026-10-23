param([int]$Port=0,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$repoRoot=$PSScriptRoot
Push-Location $repoRoot
try {
    # Python reads .env in process memory; keys are never exported to npm or args.
    $describeArgs=@('-B','-m','wallex.infra.demo','--describe')
    if($Port){$describeArgs+=@('--port',"$Port")}
    $infoJson=& python @describeArgs
    if($LASTEXITCODE -ne 0){throw 'Configuration validation failed; inspect sanitized Python output.'}
    $info=$infoJson | ConvertFrom-Json
    if($info.running){Write-Output "Demo already running at $($info.url) | NO_TRADE";return}
    if(-not (Test-Path -LiteralPath 'frontend/node_modules')){
        & npm --prefix frontend ci
        if($LASTEXITCODE -ne 0){throw 'npm ci failed'}
    }
    & npm --prefix frontend run build
    if($LASTEXITCODE -ne 0){throw 'Frontend production build failed'}
    $logDir=Join-Path $info.data_dir 'demo-control'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $serveArgs=@('-B','-m','wallex.infra.demo','--serve')
    if($Port){$serveArgs+=@('--port',"$Port")}
    $stamp=[Guid]::NewGuid().ToString('N')
    $child=Start-Process -FilePath $info.python -ArgumentList $serveArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "$stamp.out.log") -RedirectStandardError (Join-Path $logDir "$stamp.err.log") -PassThru
    $ready=$false
    for($attempt=0;$attempt -lt 60;$attempt++){
        if($child.HasExited){throw 'Backend startup failed; sanitized logs are in DATA_DIR/demo-control.'}
        try{$health=Invoke-RestMethod "$($info.url)/api/health" -TimeoutSec 1;$controlPath=Join-Path $logDir "control.json";if(Test-Path -LiteralPath $controlPath){$control=Get-Content -LiteralPath $controlPath -Raw | ConvertFrom-Json;if($health.demo_instance -eq $control.instance){$ready=$true;break}}}catch{}
        Start-Sleep -Milliseconds 500
    }
    if(-not $ready){throw 'Backend health timeout; no other process was stopped.'}
    Write-Output "QuantForge: $($info.url) | mode=$($info.mode) | NO_TRADE / LIVE DISABLED"
    if(-not $NoBrowser){Start-Process $info.url}
} finally {Pop-Location}
