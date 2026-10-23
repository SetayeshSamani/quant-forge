$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    # Cooperative shutdown only; never kill unrelated processes or delete data.
    & python -B -m wallex.infra.demo --stop
    if($LASTEXITCODE -ne 0){throw 'Managed demo stop failed; no force kill performed.'}
} finally {Pop-Location}
