param([Parameter(Mandatory = $true)][string]$BackupDirectory)

$ErrorActionPreference = 'Stop'
$backupPath = (Resolve-Path -LiteralPath $BackupDirectory).Path.TrimEnd([char[]]'\/')
$manifestPath = Join-Path $backupPath 'backup.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($manifest.schema_version -ne 2 -or $manifest.complete -ne $true -or -not $manifest.files) {
    throw '缺少受支持的完整备份清单；旧备份请按原验收记录核对，不能跳过完整性检查。'
}
if (-not (Test-Path -LiteralPath (Join-Path $backupPath 'runtime') -PathType Container)) {
    throw '缺少配套运行文件目录。'
}
$allItems = @((Get-Item -LiteralPath $backupPath -Force)) + @(Get-ChildItem -LiteralPath $backupPath -Recurse -Force)
if ($allItems | Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint }) {
    throw '备份不能包含符号链接或目录联接。'
}
$listed = @{}
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    if (-not $relative -or $relative.Contains('\') -or $relative.Contains(':') -or
        [System.IO.Path]::IsPathRooted($relative) -or
        ($relative.Split('/') | Where-Object { $_ -in @('', '.', '..') }) -or
        $entry.sha256 -notmatch '^[0-9a-f]{64}$' -or $entry.bytes -lt 0) {
        throw '备份清单包含无效路径或摘要。'
    }
    if ($listed.ContainsKey($relative)) { throw '备份清单含重复文件。' }
    $listed[$relative] = $true
    $filePath = [System.IO.Path]::GetFullPath((Join-Path $backupPath $relative))
    if (-not $filePath.StartsWith($backupPath + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw '备份清单路径超出备份目录。'
    }
    $file = Get-Item -LiteralPath $filePath -Force
    if ($file.PSIsContainer -or $file.Length -ne $entry.bytes -or
        (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw ('备份文件缺失或内容已变化：' + $relative)
    }
}
if (-not $listed.ContainsKey('app.dump') -or
    ($manifest.includes_catalog -and -not $listed.ContainsKey('catalog.dump'))) {
    throw '备份清单缺少数据库导出文件。'
}
$actual = @($allItems | Where-Object { -not $_.PSIsContainer -and $_.FullName -ne $manifestPath })
if ($actual.Count -ne $listed.Count) { throw '备份目录存在未列入清单的文件，或清单文件不完整。' }
Write-Host ('备份 SHA256 与长度核对通过，共 ' + $listed.Count + ' 个文件。')
