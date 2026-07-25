# Start Sentinel Mesh with the optional AWS integration enabled.
# Default port 8001 lets an older fallback build remain on port 8000.

param(
    [int]$Port = 8001,
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\aws.local.ps1")) {
    Write-Host "aws.local.ps1 is missing." -ForegroundColor Red
    Write-Host "Run .\configure_aws.ps1 first." -ForegroundColor Yellow
    exit 1
}

. .\aws.local.ps1
$env:SENTINEL_AUTO_PUBLISH_AWS = "1"
Remove-Item Env:SENTINEL_FORCE_LOCAL -ErrorAction SilentlyContinue

Write-Host "AWS profile: $env:AWS_PROFILE" -ForegroundColor DarkGray
Write-Host "AWS region:  $env:AWS_REGION" -ForegroundColor DarkGray
Write-Host "S3 bucket:   $env:SENTINEL_EVIDENCE_BUCKET" -ForegroundColor DarkGray

& .\start.ps1 -Port $Port -Fresh:$Fresh
