# Build Cloudflare Pages output into dist/ (Windows PowerShell).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Test-Path dist) { Remove-Item -Recurse -Force dist }
New-Item -ItemType Directory -Path dist | Out-Null
Copy-Item -Recurse static dist/static
Copy-Item -Recurse stylesheets dist/stylesheets
Copy-Item deploy/pages-index.html dist/index.html
Copy-Item deploy/pages-headers dist/_headers
Copy-Item deploy/pages-redirects dist/_redirects
Copy-Item static/robots.txt dist/robots.txt
Write-Host "Built dist/ for Cloudflare Pages"
