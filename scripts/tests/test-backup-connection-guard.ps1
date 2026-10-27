# Standalone regression; Docker is stubbed and no files/containers are created.
$ErrorActionPreference='Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$testId = [Guid]::NewGuid().ToString('N')
$global:ChefMateGuardStopCalls=0
$global:ChefMateGuardReadinessCalls=0
$global:ChefMateGuardEnvironment=@()
function docker {
    if ($args[0] -eq 'inspect') {
        if ($args[2] -eq '{{json .Config.Env}}') { ConvertTo-Json -InputObject @($global:ChefMateGuardEnvironment) -Compress }
        else { 'sha256:fixture|fixture:local' }
    } elseif ($args -contains 'stop') { $global:ChefMateGuardStopCalls++;throw 'Should never stop in this regression' }
    elseif ($args -contains 'pg_isready') { $global:ChefMateGuardReadinessCalls++;$global:LASTEXITCODE=1;return }
    elseif (($args -contains 'ps') -and ($args -contains '--all')) { 'fixture-app' }
    elseif ($args -contains 'ps') { 'fixture-db' }
    $global:LASTEXITCODE=0
}
$defaults=@('CHEFMATE_APP_DATABASE_URL=','CHEFMATE_APP_DB_HOST=app-db','CHEFMATE_APP_DB_USER=chefmate','CHEFMATE_APP_DB_NAME=chefmate_app','CHEFMATE_CATALOG_DB_HOST=db','CHEFMATE_CATALOG_DB_USER=chefmate','CHEFMATE_CATALOG_DB_NAME=chefmate','CHEFMATE_APP_DB_PASSWORD=PRIVATE_MARKER_NOT_FOR_OUTPUT','OPENAI_API_KEY=PRIVATE_KEY_NOT_FOR_OUTPUT')
$cases=@(
    @{name='explicit_app_dsn';extra='CHEFMATE_APP_DATABASE_URL=postgresql://fixture:PRIVATE_SECRET@external.invalid/db';external=$true},
    @{name='explicit_catalog_dsn';extra='CHEFMATE_DATABASE_URL=postgresql://fixture:PRIVATE_SECRET@external.invalid/db';external=$true},
    @{name='external_app_host';extra='CHEFMATE_APP_DB_HOST=external.invalid';external=$true},
    @{name='custom_catalog_port';extra='CHEFMATE_CATALOG_DB_PORT=5544';external=$true},
    @{name='custom_app_name';extra='CHEFMATE_APP_DB_NAME=another';external=$true},
    @{name='wrong_case_host';extra='chefmate_app_db_host=app-db';removeHost=$true;external=$true},
    @{name='managed_defaults';extra='';external=$false}
)
foreach($case in $cases) {
    $global:ChefMateGuardStopCalls=0;$global:ChefMateGuardReadinessCalls=0
    $caseDefaults=if($case.removeHost){@($defaults | Where-Object { $_ -cnotlike 'CHEFMATE_APP_DB_HOST=*' })}else{@($defaults)}
    $global:ChefMateGuardEnvironment=@($caseDefaults)+@($case.extra)
    $target=Join-Path ([System.IO.Path]::GetTempPath()) ('chefmate-guard-'+$testId+'-'+$case.name)
    if(Test-Path -LiteralPath $target){throw 'Regression target already exists; refuse overwrite'}
    $message=''
    try { & (Join-Path $repoRoot 'scripts/backup-docker.ps1') -EnvFile (Join-Path $repoRoot '.env.docker.example') -ProjectName ('guard-'+$testId+'-'+$case.name) -OutputDirectory $target }
    catch { $message=$_.Exception.Message }
    if(-not $message -or $message -match 'PRIVATE_|postgresql://|external.invalid'){throw 'Missing/generic diagnostic guard failed'}
    if($global:ChefMateGuardStopCalls -ne 0 -or (Test-Path -LiteralPath $target)){throw 'Guard stopped App or created backup files'}
    if($case.external -and $message -notmatch '外部或自定义数据库连接'){throw ('Wrong external guard failure: '+$message)}
    if($case.external -and $global:ChefMateGuardReadinessCalls -ne 0){throw 'External connection proceeded past early guard'}
    if(-not $case.external -and $global:ChefMateGuardReadinessCalls -ne 1){throw ('Managed connection failed admission: '+$message+' readiness='+$global:ChefMateGuardReadinessCalls)}
    Write-Host ('PASS '+$case.name+' generic rejection before stop/files; expected admission='+(-not $case.external))
}

Remove-Variable ChefMateGuardEnvironment,ChefMateGuardReadinessCalls,ChefMateGuardStopCalls -Scope Global
