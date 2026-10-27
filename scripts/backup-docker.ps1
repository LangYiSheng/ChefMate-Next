param(
    [string]$EnvFile = '.env',
    [string]$ProjectName = 'chefmate-next',
    [string]$OutputDirectory = '',
    [switch]$WithCatalog,
    [string[]]$ComposeOverride = @()
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupId = $stamp + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$backupMutex, $ownsMutex = $null, $false
Push-Location $repoRoot
try {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $projectDigest = [BitConverter]::ToString($hasher.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($ProjectName))).Replace('-', '').Substring(0, 24)
    } finally { $hasher.Dispose() }
    $backupMutex = [System.Threading.Mutex]::new($false, ('ChefMateNext_Backup_' + $projectDigest))
    try { $ownsMutex = $backupMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
        Write-Warning '上次备份进程异常退出；本次继续前请核对旧备份与服务状态。'
    }
    if (-not $ownsMutex) { throw '该 Compose 项目已有备份正在运行，未停止 App 或创建新备份。' }
    $composeArgs = @('compose', '--env-file', $EnvFile, '-p', $ProjectName, '-f', 'compose.yaml')
    if ($WithCatalog) { $composeArgs += @('-f', 'compose.catalog.yaml') }
    foreach ($overridePath in $ComposeOverride) { $composeArgs += @('-f', $overridePath) }
    & docker @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose 配置无效，未开始备份。' }
    if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot ('backups/chefmate-' + $backupId) }
    if (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path $repoRoot $OutputDirectory }
    $backupPath = [System.IO.Path]::GetFullPath($OutputDirectory)
    if (Test-Path -LiteralPath $backupPath) { throw '备份目录已存在，请使用新的空路径，避免覆盖已有备份。' }
    $appContainer = & docker @composeArgs ps --all -q app
    if ($LASTEXITCODE -ne 0 -or -not $appContainer) { throw '找不到 App 容器，未开始备份。' }
    if (@($appContainer).Count -ne 1) { throw '备份脚本要求单个 App 实例，未开始备份。' }
    $imageIdentity = & docker inspect --format '{{.Image}}|{{.Config.Image}}' $appContainer
    if ($LASTEXITCODE -ne 0 -or -not $imageIdentity) { throw '无法读取 App 镜像身份，未开始备份。' }
    $imageParts = $imageIdentity.Split('|', 2)
    # Use the running/stopped container's actual configuration, rather than a
    # possibly edited .env. Capture privately; never send environment values to
    # the output stream, backup manifest or exception diagnostics.
    $connectionFields = @{}
    $hasExplicitDatabase = $false
    $capturedEnvironment, $environmentEntries = $null, $null
    try {
        $capturedEnvironment = & docker inspect --format '{{json .Config.Env}}' $appContainer
        if ($LASTEXITCODE -ne 0) { throw 'inspect failed' }
        $environmentEntries = $capturedEnvironment | ConvertFrom-Json
        foreach ($environmentEntry in $environmentEntries) {
            $environmentParts = ([string]$environmentEntry).Split('=', 2)
            if ($environmentParts.Count -ne 2) { continue }
            if ($environmentParts[0] -in @('CHEFMATE_APP_DATABASE_URL', 'CHEFMATE_DATABASE_URL')) {
                if (-not [string]::IsNullOrWhiteSpace($environmentParts[1])) { $hasExplicitDatabase = $true }
            } elseif ($environmentParts[0] -cmatch '^CHEFMATE_(APP|CATALOG)_DB_(HOST|USER|NAME|PORT)$') {
                $connectionFields[$environmentParts[0]] = $environmentParts[1]
            }
        }
    } catch { throw '无法安全核对 App 实际数据库连接类型，未停止 App 或创建备份。' }
    finally { $capturedEnvironment, $environmentEntries, $environmentEntry, $environmentParts = $null, $null, $null, $null }
    $managedDefaults = @{
        CHEFMATE_APP_DB_HOST = 'app-db'; CHEFMATE_APP_DB_USER = 'chefmate'; CHEFMATE_APP_DB_NAME = 'chefmate_app'
        CHEFMATE_CATALOG_DB_HOST = 'db'; CHEFMATE_CATALOG_DB_USER = 'chefmate'; CHEFMATE_CATALOG_DB_NAME = 'chefmate'
    }
    $managedConnections = -not $hasExplicitDatabase
    foreach ($field in $managedDefaults.Keys) {
        if ($connectionFields[$field] -cne $managedDefaults[$field]) { $managedConnections = $false }
    }
    foreach ($portField in @('CHEFMATE_APP_DB_PORT', 'CHEFMATE_CATALOG_DB_PORT')) {
        if ($connectionFields[$portField] -and $connectionFields[$portField] -cne '5432') { $managedConnections = $false }
    }
    if (-not $managedConnections) {
        throw 'App 使用外部或自定义数据库连接。本脚本只备份默认受管双库，已在停止 App 和创建备份前拒绝；请按外部数据库备份流程成组保存实际数据库与运行文件。'
    }
    $databaseRunning = & docker @composeArgs ps --status running -q app-db
    if ($LASTEXITCODE -ne 0 -or -not $databaseRunning) { throw '应用 PostgreSQL 未运行，未停止 App 或创建备份。' }
    & docker @composeArgs exec -T app-db pg_isready -U chefmate -d chefmate_app
    if ($LASTEXITCODE -ne 0) { throw '应用 PostgreSQL 尚未就绪，未停止 App 或创建备份。' }
    $catalogRunning = & docker @composeArgs ps --status running -q db
    if ($LASTEXITCODE -ne 0 -or -not $catalogRunning) { throw '基础菜谱 PostgreSQL 未运行，未停止 App 或创建备份。' }
    & docker @composeArgs exec -T db pg_isready -U chefmate -d chefmate
    if ($LASTEXITCODE -ne 0) { throw '基础菜谱 PostgreSQL 尚未就绪，未停止 App 或创建备份。' }
    New-Item -ItemType Directory -Path $backupPath | Out-Null
    $appWasRunning = [bool](& docker @composeArgs ps --status running -q app)
    if ($LASTEXITCODE -ne 0) { throw '无法读取 App 运行状态，未停止服务。' }
    $backupFailed, $backupComplete = $false, $false
    $appDump, $catalogDump = $null, $null
    try {
        if ($appWasRunning) {
            & docker @composeArgs stop app
            if ($LASTEXITCODE -ne 0) { throw 'App 尚未正常停止，未开始数据导出。' }
        }
        $appDump = '/tmp/chefmate-app-' + $backupId + '.dump'
        & docker @composeArgs exec -T app-db pg_dump -U chefmate -d chefmate_app -Fc -f $appDump
        if ($LASTEXITCODE -ne 0) { throw '应用数据库导出失败；当前目录不是完整备份。' }
        & docker @composeArgs cp ('app-db:' + $appDump) (Join-Path $backupPath 'app.dump')
        if ($LASTEXITCODE -ne 0) { throw '应用数据库备份复制失败。' }
        & docker @composeArgs cp 'app:/var/lib/chefmate' (Join-Path $backupPath 'runtime')
        if ($LASTEXITCODE -ne 0) { throw '运行文件备份复制失败。' }
        $catalogDump = '/tmp/chefmate-catalog-' + $backupId + '.dump'
        & docker @composeArgs exec -T db pg_dump -U chefmate -d chefmate -Fc -f $catalogDump
        if ($LASTEXITCODE -ne 0) { throw '基础菜谱库导出失败。' }
        & docker @composeArgs cp ('db:' + $catalogDump) (Join-Path $backupPath 'catalog.dump')
        if ($LASTEXITCODE -ne 0) { throw '基础菜谱库备份复制失败。' }
        $backupItems = @(Get-ChildItem -LiteralPath $backupPath -Recurse -Force)
        if ($backupItems | Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint }) {
            throw '备份中存在符号链接或目录联接，未生成完成标记。'
        }
        $files = @($backupItems | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName | ForEach-Object {
            @{
                path = $_.FullName.Substring($backupPath.TrimEnd([char[]]'\/').Length + 1).Replace('\', '/')
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        })
        $sourceCommit, $sourceDirty = $null, $null
        $gitCommand = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($gitCommand) {
            $sourceCommit = & $gitCommand.Source rev-parse HEAD 2>$null
            if ($LASTEXITCODE -ne 0) { $sourceCommit = $null }
            else {
                $sourceStatus = & $gitCommand.Source status --porcelain 2>$null
                if ($LASTEXITCODE -eq 0) { $sourceDirty = [bool]$sourceStatus }
            }
        }
        @{
            schema_version = 2
            backup_id = $backupId
            created_at = (Get-Date).ToUniversalTime().ToString('o')
            project = $ProjectName
            source_commit = $sourceCommit
            source_dirty = $sourceDirty
            app_image_id = $imageParts[0]
            app_image_ref = $imageParts[1]
            includes_catalog = $true
            complete = $true
            files = $files
        } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupPath 'backup.pending.json') -Encoding utf8
        Move-Item -LiteralPath (Join-Path $backupPath 'backup.pending.json') -Destination (Join-Path $backupPath 'backup.json')
        $backupComplete = $true
        Write-Host ('备份已完成：' + $backupPath)
    } catch {
        $backupFailed = $true
        throw
    } finally {
        if ($backupComplete) {
            try {
                foreach ($temporary in @(@{service = 'app-db'; path = $appDump; kind = 'app'}, @{service = 'db'; path = $catalogDump; kind = 'catalog'})) {
                    if (-not $temporary.path) { continue }
                    $expectedTemporary = '/tmp/chefmate-' + $temporary.kind + '-' + $backupId + '.dump'
                    if ($temporary.path -cne $expectedTemporary -or $temporary.path -notmatch '^/tmp/chefmate-(app|catalog)-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.dump$') {
                        Write-Warning '临时导出路径核对失败，已保留该文件。'
                        continue
                    }
                    # Delete only the exact generated regular-file pathname in
                    # the database container; no shell or recursive removal.
                    & docker @composeArgs exec -T $temporary.service rm -- $temporary.path
                    if ($LASTEXITCODE -ne 0) { Write-Warning '备份已完成，但本次容器临时导出文件未清理。' }
                }
            } catch { Write-Warning '备份已完成，临时导出清理异常；继续恢复 App。' }
        }
        if ($appWasRunning) {
            # Compose v2 start has no --wait. Keep existing containers/config,
            # start dependencies as needed, then wait on their health checks.
            & docker @composeArgs up -d --no-build --no-recreate --wait --wait-timeout 180 app
            if ($LASTEXITCODE -ne 0) {
                if ($backupFailed) { Write-Warning '备份失败，App 也未能健康恢复；请检查 PostgreSQL 与 Docker Compose 状态。' }
                else { throw '备份文件已生成，但 App 未能健康恢复；请检查 PostgreSQL 与 Docker Compose 状态。' }
            }
        }
    }
} finally {
    if ($ownsMutex) { $backupMutex.ReleaseMutex() }
    if ($backupMutex) { $backupMutex.Dispose() }
    Pop-Location
}
