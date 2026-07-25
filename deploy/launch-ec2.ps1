# Launch the Sentinel Mesh EC2 instance.
#   .\deploy\launch-ec2.ps1
param(
    [string]$Profile  = "sentinel-discovery",
    [string]$Region   = "eu-west-1",
    [string]$Type     = "t3.medium",
    [string]$RepoUrl  = "https://github.com/raeescassoojee/fusion.git",
    [string]$Branch   = "main",
    [string]$KeyName  = "sentinel-key"
)

# NOTE: deliberately NOT "Stop". The AWS CLI writes to stderr on expected
# "does not exist" probes, and PowerShell would turn that into a fatal error.
# Every step checks $LASTEXITCODE explicitly instead.
$ErrorActionPreference = "Continue"
$env:AWS_PAGER = ""

function Invoke-Aws {
    param([string[]]$Arguments, [switch]$AllowFailure)
    $output = & aws @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        Write-Host "AWS command failed: aws $($Arguments -join ' ')" -ForegroundColor Red
        Write-Host ($output | Out-String) -ForegroundColor Red
        exit 1
    }
    if ($code -ne 0) { return $null }
    return ($output | Out-String).Trim()
}

Write-Host "Looking up Ubuntu 24.04 AMI..." -ForegroundColor DarkGray
$ami = Invoke-Aws @("ssm","get-parameter",
    "--name","/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
    "--region",$Region,"--profile",$Profile,"--query","Parameter.Value","--output","text")
Write-Host "AMI: $ami"

# ---------- key pair ----------
$existing = Invoke-Aws @("ec2","describe-key-pairs","--key-names",$KeyName,
    "--region",$Region,"--profile",$Profile,"--output","text") -AllowFailure

if (-not $existing) {
    Write-Host "Creating key pair $KeyName..." -ForegroundColor Yellow
    $pem = Invoke-Aws @("ec2","create-key-pair","--key-name",$KeyName,
        "--region",$Region,"--profile",$Profile,"--query","KeyMaterial","--output","text")
    Set-Content -Path "$KeyName.pem" -Value $pem -Encoding ascii
    Write-Host "Saved $KeyName.pem - do NOT commit this file." -ForegroundColor Yellow
} else {
    Write-Host "Key pair $KeyName already exists."
}

# ---------- security group ----------
$sgId = Invoke-Aws @("ec2","describe-security-groups","--group-names","sentinel-sg",
    "--region",$Region,"--profile",$Profile,
    "--query","SecurityGroups[0].GroupId","--output","text") -AllowFailure

if (-not $sgId -or $sgId -eq "None") {
    Write-Host "Creating security group..." -ForegroundColor Yellow
    $sgId = Invoke-Aws @("ec2","create-security-group","--group-name","sentinel-sg",
        "--description","Sentinel Mesh demo","--region",$Region,"--profile",$Profile,
        "--query","GroupId","--output","text")

    $myIp = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
    Invoke-Aws @("ec2","authorize-security-group-ingress","--group-id",$sgId,
        "--protocol","tcp","--port","22","--cidr","$myIp/32",
        "--region",$Region,"--profile",$Profile) -AllowFailure | Out-Null
    Invoke-Aws @("ec2","authorize-security-group-ingress","--group-id",$sgId,
        "--protocol","tcp","--port","80","--cidr","0.0.0.0/0",
        "--region",$Region,"--profile",$Profile) -AllowFailure | Out-Null
}
Write-Host "Security group: $sgId"

# ---------- user data ----------
$bootstrapPath = Join-Path $PSScriptRoot "bootstrap.sh"
if (-not (Test-Path $bootstrapPath)) {
    Write-Host "bootstrap.sh not found next to this script." -ForegroundColor Red
    exit 1
}
$bootstrap = (Get-Content $bootstrapPath -Raw) -replace "`r`n", "`n"
$bootstrap = $bootstrap.Replace(
    'REPO_URL="${REPO_URL:-https://github.com/raeescassoojee/fusion.git}"',
    "REPO_URL=`"$RepoUrl`"")
$bootstrap = $bootstrap.Replace('BRANCH="${BRANCH:-main}"', "BRANCH=`"$Branch`"")

if ($bootstrap -match "CHANGE-ME-LONG-RANDOM") {
    Write-Host "WARNING: SENTINEL_PLATE_SALT is still the placeholder." -ForegroundColor Yellow
}

$udFile = Join-Path $env:TEMP "sentinel-userdata.sh"
Set-Content -Path $udFile -Value $bootstrap -Encoding ascii -NoNewline

# ---------- launch ----------
Write-Host "Launching $Type..." -ForegroundColor Cyan
$instanceId = Invoke-Aws @("ec2","run-instances",
    "--image-id",$ami,"--instance-type",$Type,"--key-name",$KeyName,
    "--security-group-ids",$sgId,"--region",$Region,"--profile",$Profile,
    "--block-device-mappings","DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}",
    "--user-data","file://$udFile",
    "--tag-specifications","ResourceType=instance,Tags=[{Key=Name,Value=sentinel-mesh},{Key=project,Value=sentinel}]",
    "--query","Instances[0].InstanceId","--output","text")

Write-Host "Instance: $instanceId - waiting for it to run..." -ForegroundColor DarkGray
Invoke-Aws @("ec2","wait","instance-running","--instance-ids",$instanceId,
    "--region",$Region,"--profile",$Profile) | Out-Null

$dns = Invoke-Aws @("ec2","describe-instances","--instance-ids",$instanceId,
    "--region",$Region,"--profile",$Profile,
    "--query","Reservations[0].Instances[0].PublicDnsName","--output","text")
$ip = Invoke-Aws @("ec2","describe-instances","--instance-ids",$instanceId,
    "--region",$Region,"--profile",$Profile,
    "--query","Reservations[0].Instances[0].PublicIpAddress","--output","text")

Write-Host ""
Write-Host "Instance ID : $instanceId" -ForegroundColor Green
Write-Host "Public DNS  : $dns"        -ForegroundColor Green
Write-Host "Public IP   : $ip"         -ForegroundColor Green
Write-Host ""
Write-Host "Bootstrap takes 5-10 minutes. Watch it with:"
Write-Host "  ssh -i $KeyName.pem ubuntu@$ip `"sudo tail -f /var/log/cloud-init-output.log`""
Write-Host ""
Write-Host "Then test:  http://$ip/health"
Write-Host "Next:       .\deploy\create-cloudfront.ps1 -OriginDns $dns"
