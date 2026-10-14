param([int]$Port = 18765)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
uv run --project $projectPath chefmate-playground --port $Port
