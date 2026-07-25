# Repair the CloudFront origin domain after a typo.
#   .\deploy\fix-cloudfront-origin.ps1 -Id E2LH4SDKB98382 -OriginDns ec2-18-201-10-96.eu-west-1.compute.amazonaws.com
param(
    [Parameter(Mandatory=$true)][string]$Id,
    [Parameter(Mandatory=$true)][string]$OriginDns,
    [string]$Profile = "sentinel-discovery"
)
$ErrorActionPreference = "Continue"
$env:AWS_PAGER = ""

Write-Host "Fetching current config for $Id..." -ForegroundColor DarkGray
$raw = & aws cloudfront get-distribution-config --id $Id --profile $Profile --output json 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host ($raw | Out-String) -ForegroundColor Red; exit 1 }

$wrapper = ($raw | Out-String) | ConvertFrom-Json
$etag    = $wrapper.ETag
$config  = $wrapper.DistributionConfig

$old = $config.Origins.Items[0].DomainName
Write-Host "Current origin : $old" -ForegroundColor Yellow
Write-Host "New origin     : $OriginDns" -ForegroundColor Green
$config.Origins.Items[0].DomainName = $OriginDns

$cfgFile = Join-Path $env:TEMP "cf-fixed.json"
$config | ConvertTo-Json -Depth 20 | Set-Content -Path $cfgFile -Encoding ascii

$result = & aws cloudfront update-distribution --id $Id `
    --distribution-config "file://$cfgFile" --if-match $etag `
    --profile $Profile --output json 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host ($result | Out-String) -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Origin updated. Redeploying (5-15 min)." -ForegroundColor Cyan
Write-Host "Check with:"
Write-Host "  aws cloudfront get-distribution --id $Id --profile $Profile --query Distribution.Status --output text"
