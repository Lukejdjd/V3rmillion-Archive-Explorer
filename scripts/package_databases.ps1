param(
    [string]$DataDirectory = (Join-Path $PSScriptRoot "..\data"),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\data\downloads")
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command zstd -ErrorAction SilentlyContinue)) {
    throw "zstd is required. Install it, then run this script again."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$databaseNames = @("users.db", "threads.db")

foreach ($name in $databaseNames) {
    $source = Join-Path $DataDirectory $name
    $target = Join-Path $OutputDirectory "$name.zst"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Missing database: $source"
    }

    Write-Host "Compressing $name with Zstandard level 3..."
    & zstd -3 -T0 -f -- $source -o $target
    if ($LASTEXITCODE -ne 0) { throw "zstd failed for $source" }
    & zstd -t -- $target
    if ($LASTEXITCODE -ne 0) { throw "Verification failed for $target" }
}

$manifest = Join-Path $OutputDirectory "SHA256SUMS"
$lines = foreach ($name in $databaseNames) {
    $archiveName = "$name.zst"
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $OutputDirectory $archiveName)).Hash.ToLowerInvariant()
    "$hash  $archiveName"
}
[System.IO.File]::WriteAllLines($manifest, $lines, [System.Text.UTF8Encoding]::new($false))

Write-Host "Created and verified both database packages in $OutputDirectory"
Get-Item -LiteralPath ($databaseNames | ForEach-Object { Join-Path $OutputDirectory "$_.zst" }) |
    Select-Object Name, Length

