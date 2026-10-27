param(
    [string]$EnvFile = '.env',
    [string]$ProjectName = 'chefmate-next',
    [switch]$WithCatalog,
    [switch]$NoBuild,
    [string[]]$ComposeOverride = @()
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildCommitWasSet = Test-Path Env:CHEFMATE_BUILD_COMMIT
$originalBuildCommit = $env:CHEFMATE_BUILD_COMMIT
$injectedBuildCommit = $false
Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        Copy-Item -LiteralPath '.env.docker.example' -Destination $EnvFile
        throw ('已创建配置文件 ' + $EnvFile + '。请填写 CHEFMATE_ADMIN_PASSWORD 与 CHEFMATE_APP_DB_PASSWORD 后重新运行。')
    }
    $fileCommitOverride = Select-String -LiteralPath $EnvFile -CaseSensitive -Quiet -Pattern '^\s*(?:export\s+)?CHEFMATE_BUILD_COMMIT\s*[:=]'
    if (-not $NoBuild -and -not $buildCommitWasSet -and -not $fileCommitOverride) {
        $gitCommand = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($gitCommand) {
            try {
                $candidateCommit = & $gitCommand.Source rev-parse HEAD 2>$null
                if ($LASTEXITCODE -eq 0 -and $candidateCommit -cmatch '^[0-9a-f]{40}$') {
                    $sourceStatus = & $gitCommand.Source status --porcelain 2>$null
                    if ($LASTEXITCODE -eq 0 -and -not $sourceStatus) {
                        $env:CHEFMATE_BUILD_COMMIT = $candidateCommit
                        $injectedBuildCommit = $true
                    }
                }
            } catch { $candidateCommit = $null } # Optional metadata cannot prevent archive-based startup.
        }
    }
    $composeArgs = @('compose', '--env-file', $EnvFile, '-p', $ProjectName, '-f', 'compose.yaml')
    if ($WithCatalog) { $composeArgs += @('-f', 'compose.catalog.yaml') }
    foreach ($overridePath in $ComposeOverride) { $composeArgs += @('-f', $overridePath) }
    & docker @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw ('Docker Compose 配置无效，请检查 ' + $EnvFile + '。') }
    $startArgs = $composeArgs + @('up', '-d', '--wait', '--wait-timeout', '180')
    if (-not $NoBuild) { $startArgs += '--build' }
    & docker @startArgs
    if ($LASTEXITCODE -ne 0) { throw 'ChefMate 尚未健康启动；使用 docker compose logs --tail 100 检查原因。' }
    & docker @composeArgs ps
    if ($LASTEXITCODE -ne 0) { throw 'ChefMate 健康启动后读取容器状态失败，请检查 Docker Desktop。' }
    $published = @(& docker @composeArgs port app 18765)
    if ($LASTEXITCODE -eq 0 -and $published.Count -eq 1) {
        $localAddress = $published[0].Replace('0.0.0.0:', '127.0.0.1:').Replace('[::]:', '[::1]:')
        Write-Host ('ChefMate 已启动，本机 HTTP 地址：http://' + $localAddress)
    } else { Write-Host 'ChefMate 已启动，端口绑定见上方容器状态；公开地址以反向代理配置为准。' }
} finally {
    if ($injectedBuildCommit) {
        if ($buildCommitWasSet) { $env:CHEFMATE_BUILD_COMMIT = $originalBuildCommit }
        else { Remove-Item Env:CHEFMATE_BUILD_COMMIT -ErrorAction SilentlyContinue }
    }
    Pop-Location
}
