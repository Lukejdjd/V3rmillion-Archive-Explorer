param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,
    [string]$DataDirectory = "data",
    [switch]$RemoveArchives
)

$ErrorActionPreference = "Stop"
$base = $BaseUrl.TrimEnd("/")
$dataPath = [System.IO.Path]::GetFullPath($DataDirectory)
$downloadPath = Join-Path $dataPath "downloads"
New-Item -ItemType Directory -Force -Path $downloadPath | Out-Null

$manifestPath = Join-Path $downloadPath "SHA256SUMS"
Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile $manifestPath

$expected = @{}
foreach ($line in Get-Content -LiteralPath $manifestPath) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
        $expected[$Matches[2].Trim()] = $Matches[1].ToLowerInvariant()
    }
}

$zstd = Get-Command zstd -ErrorAction SilentlyContinue
if (-not $zstd) {
    throw "zstd was not found. Install it with: winget install Meta.Zstandard"
}

foreach ($name in @("users.db.zst", "threads.db.zst")) {
    if (-not $expected.ContainsKey($name)) {
        throw "SHA256SUMS does not contain $name"
    }

    $archivePath = Join-Path $downloadPath $name
    Write-Host "Downloading $name..."
    Invoke-WebRequest -Uri "$base/$name" -OutFile $archivePath

    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$name]) {
        throw "SHA-256 mismatch for $name"
    }

    $databaseName = $name.Substring(0, $name.Length - 4)
    $databasePath = Join-Path $dataPath $databaseName
    Write-Host "Decompressing $databaseName..."
    & $zstd.Source -d -f $archivePath -o $databasePath
    if ($LASTEXITCODE -ne 0) {
        throw "zstd failed for $name"
    }

    if ($RemoveArchives) {
        Remove-Item -LiteralPath $archivePath
    }
}

Write-Host "Databases are ready in $dataPath"
