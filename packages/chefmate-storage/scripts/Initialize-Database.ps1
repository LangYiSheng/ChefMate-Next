[CmdletBinding()]
param(
    [ValidatePattern('^[a-z][a-z0-9_]{0,62}$')]
    [string]$Schema = 'chefmate'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:CHEFMATE_DATABASE_URL)) {
    throw 'Set CHEFMATE_DATABASE_URL in this process before initializing the database.'
}

$storageCommand = Get-Command 'chefmate-storage' -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $storageCommand) {
    throw 'chefmate-storage was not found. Install the package and activate its Python environment first.'
}

& $storageCommand.Source '--schema' $Schema 'migrate'
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed (exit code $LASTEXITCODE)."
}
