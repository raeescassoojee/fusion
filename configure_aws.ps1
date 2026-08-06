# Configure the optional Sentinel Mesh AWS demo integration.
# Creates private S3 evidence storage + DynamoDB pattern tables, then writes
# local environment settings to aws.local.ps1 (which is git-ignored).

param(
    [string]$Profile = "sentinel-dev",
    [string]$Region = "af-south-1",
    [string]$Bucket = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "  SENTINEL MESH · AWS SETUP" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Host "AWS CLI is not installed." -ForegroundColor Red
    Write-Host "Install it, reopen PowerShell, then rerun this script:" -ForegroundColor Yellow
    Write-Host "  winget install --id Amazon.AWSCLI -e" -ForegroundColor White
    exit 1
}

# Configure the profile only when it does not already exist.
$profiles = @(aws configure list-profiles 2>$null)
if ($profiles -notcontains $Profile) {
    Write-Host "Creating AWS SSO profile '$Profile'..." -ForegroundColor Yellow
    aws configure sso --profile $Profile
}

# Static-key profiles work immediately; SSO profiles may need a login refresh.
$identity = aws sts get-caller-identity --profile $Profile --region $Region --output json 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Refreshing AWS SSO login for '$Profile'..." -ForegroundColor DarkGray
    aws sso login --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw "AWS login failed" }
}

$account = (aws sts get-caller-identity --profile $Profile --region $Region --query Account --output text).Trim()
if (-not $account) { throw "Could not read AWS account ID" }
if (-not $Bucket) {
    $Bucket = "sentinel-mesh-evidence-$account-$Region".ToLower()
}

if (-not (Test-Path ".venv")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) { py -3.12 -m venv .venv }
    } else {
        python -m venv .venv
    }
}
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt

$secretBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($secretBytes)
$rng.Dispose()
$plateSalt = [Convert]::ToBase64String($secretBytes)

@"
# Generated locally by configure_aws.ps1. Do not commit this file.
`$env:AWS_PROFILE = "$Profile"
`$env:AWS_REGION = "$Region"
`$env:SENTINEL_EVIDENCE_BUCKET = "$Bucket"
`$env:SENTINEL_PLATE_SALT = "$plateSalt"
`$env:SENTINEL_AUTO_PUBLISH_AWS = "1"
Remove-Item Env:SENTINEL_FORCE_LOCAL -ErrorAction SilentlyContinue
"@ | Set-Content -Encoding UTF8 aws.local.ps1

. .\aws.local.ps1

Write-Host "Provisioning DynamoDB and private S3..." -ForegroundColor DarkGray
python provision_aws.py --region $Region --bucket $Bucket
if ($LASTEXITCODE -ne 0) { throw "AWS provisioning failed" }

Write-Host "Checking AWS readiness..." -ForegroundColor DarkGray
python scripts\aws_readiness_check.py
if ($LASTEXITCODE -ne 0) {
    Write-Warning "AWS resources are not fully ready yet. Wait 30 seconds and rerun: python scripts\aws_readiness_check.py"
}

Write-Host ""
Write-Host "AWS configuration saved locally." -ForegroundColor Green
Write-Host "Bucket: $Bucket" -ForegroundColor Green
Write-Host "Next: .\start_aws.ps1 -Port 8001" -ForegroundColor Cyan
