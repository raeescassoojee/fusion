# Launch the Sentinel Mesh EC2 instance.
# Run from the repo root:  .\deploy\launch-ec2.ps1
param(
    [string]$Profile  = "sentinel-discovery",
    [string]$Region   = "eu-west-1",
    [string]$Type     = "t3.medium",
    [string]$RepoUrl  = "https://github.com/raeescassoojee/fusion.git",
    [string]$Branch   = "main",
    [string]$KeyName  = "sentinel-key"
)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

Write-Host "Looking up Ubuntu 24.04 AMI..." -ForegroundColor DarkGray
$ami = (aws ssm get-parameter `
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id `
    --region $Region --profile $Profile --query Parameter.Value --output text).Trim()
Write-Host "AMI: $ami"

# --- key pair (for SSH troubleshooting) ---
$existingKey = aws ec2 describe-key-pairs --key-names $KeyName --region $Region --profile $Profile 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating key pair $KeyName..." -ForegroundColor Yellow
    aws ec2 create-key-pair --key-name $KeyName --region $Region --profile $Profile `
        --query KeyMaterial --output text | Set-Content -Encoding ascii "$KeyName.pem"
    Write-Host "Saved $KeyName.pem - do NOT commit this file." -ForegroundColor Yellow
}

# --- security group ---
$sgId = aws ec2 describe-security-groups --group-names sentinel-sg `
    --region $Region --profile $Profile --query "SecurityGroups[0].GroupId" --output text 2>$null
if ($LASTEXITCODE -ne 0 -or -not $sgId -or $sgId -eq "None") {
    Write-Host "Creating security group..." -ForegroundColor Yellow
    $sgId = (aws ec2 create-security-group --group-name sentinel-sg `
        --description "Sentinel Mesh demo" --region $Region --profile $Profile `
        --query GroupId --output text).Trim()

    $myIp = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
    aws ec2 authorize-security-group-ingress --group-id $sgId --protocol tcp --port 22 `
        --cidr "$myIp/32" --region $Region --profile $Profile | Out-Null
    aws ec2 authorize-security-group-ingress --group-id $sgId --protocol tcp --port 80 `
        --cidr 0.0.0.0/0 --region $Region --profile $Profile | Out-Null
}
Write-Host "Security group: $sgId"

# --- user data ---
$bootstrap = Get-Content "$PSScriptRoot\bootstrap.sh" -Raw
$bootstrap = $bootstrap -replace "`r`n", "`n"
$bootstrap = $bootstrap.Replace(
    'REPO_URL="${REPO_URL:-https://github.com/raeescassoojee/fusion.git}"',
    "REPO_URL=`"$RepoUrl`"")
$bootstrap = $bootstrap.Replace('BRANCH="${BRANCH:-main}"', "BRANCH=`"$Branch`"")
$udFile = Join-Path $env:TEMP "sentinel-userdata.sh"
Set-Content -Path $udFile -Value $bootstrap -Encoding ascii -NoNewline

Write-Host "Launching $Type..." -ForegroundColor Cyan
$instanceId = (aws ec2 run-instances `
    --image-id $ami --instance-type $Type --key-name $KeyName `
    --security-group-ids $sgId --region $Region --profile $Profile `
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}" `
    --user-data "file://$udFile" `
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=sentinel-mesh},{Key=project,Value=sentinel}]" `
    --query "Instances[0].InstanceId" --output text).Trim()

Write-Host "Instance: $instanceId - waiting for it to run..." -ForegroundColor DarkGray
aws ec2 wait instance-running --instance-ids $instanceId --region $Region --profile $Profile

$dns = (aws ec2 describe-instances --instance-ids $instanceId --region $Region --profile $Profile `
    --query "Reservations[0].Instances[0].PublicDnsName" --output text).Trim()
$ip = (aws ec2 describe-instances --instance-ids $instanceId --region $Region --profile $Profile `
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text).Trim()

Write-Host ""
Write-Host "Instance ID : $instanceId" -ForegroundColor Green
Write-Host "Public DNS  : $dns"       -ForegroundColor Green
Write-Host "Public IP   : $ip"        -ForegroundColor Green
Write-Host ""
Write-Host "Bootstrap takes 5-10 minutes. Watch it with:"
Write-Host "  ssh -i $KeyName.pem ubuntu@$ip 'sudo tail -f /var/log/cloud-init-output.log'"
Write-Host ""
Write-Host "Then test:  http://$ip/health"
Write-Host "Next:       .\deploy\create-cloudfront.ps1 -OriginDns $dns"
