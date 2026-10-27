param(
    [string]$EnvFile = '.env',
    [string]$ProjectName = 'chefmate-next',
    [switch]$WithCatalog,
    [string[]]$ComposeOverride = @()
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $composeArgs = @('compose', '--env-file', $EnvFile, '-p', $ProjectName, '-f', 'compose.yaml')
    if ($WithCatalog) { $composeArgs += @('-f', 'compose.catalog.yaml') }
    foreach ($overridePath in $ComposeOverride) { $composeArgs += @('-f', $overridePath) }
    & docker @composeArgs down
    if ($LASTEXITCODE -ne 0) { throw '停止失败，请检查 Docker Desktop 与 Compose 状态。' }
    Write-Host 'ChefMate 容器已停止，账号、个人数据和菜谱卷均已保留。'
} finally {
    Pop-Location
}
