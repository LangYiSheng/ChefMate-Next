param([int]$Port = 18765)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$frontendPath = Join-Path $projectPath 'frontend'
Push-Location $frontendPath
try {
    npm ci --no-fund
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
} finally {
    Pop-Location
}
uv run --project $projectPath chefmate-playground --port $Port
