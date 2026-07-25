# Put CloudFront in front of the EC2 instance to get free valid HTTPS.
# HTTPS is REQUIRED - browsers block getUserMedia (camera) on plain HTTP.
#
#   .\deploy\create-cloudfront.ps1 -OriginDns ec2-x-x-x-x.eu-west-1.compute.amazonaws.com
param(
    [Parameter(Mandatory = $true)][string]$OriginDns,
    [string]$Profile = "sentinel-discovery"
)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

# CachingDisabled + AllViewer: no stale UI while your teammate iterates,
# and every header/cookie/query reaches the origin so WebSockets work.
$config = @{
    CallerReference = "sentinel-$(Get-Date -UFormat %s)"
    Comment         = "Sentinel Mesh demo"
    Enabled         = $true
    Origins         = @{
        Quantity = 1
        Items    = @(@{
            Id                 = "sentinel-ec2"
            DomainName         = $OriginDns
            CustomOriginConfig = @{
                HTTPPort             = 80
                HTTPSPort            = 443
                OriginProtocolPolicy = "http-only"
                OriginSslProtocols   = @{ Quantity = 1; Items = @("TLSv1.2") }
                OriginReadTimeout    = 60
            }
        })
    }
    DefaultCacheBehavior = @{
        TargetOriginId       = "sentinel-ec2"
        ViewerProtocolPolicy = "redirect-to-https"
        AllowedMethods       = @{
            Quantity = 7
            Items    = @("GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE")
            CachedMethods = @{ Quantity = 2; Items = @("GET","HEAD") }
        }
        Compress                 = $true
        CachePolicyId            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"  # CachingDisabled
        OriginRequestPolicyId    = "216adef6-5c7f-47e4-b989-5492eafa07d3"  # AllViewer
    }
} | ConvertTo-Json -Depth 12

$cfgFile = Join-Path $env:TEMP "sentinel-cf.json"
Set-Content -Path $cfgFile -Value $config -Encoding ascii

Write-Host "Creating CloudFront distribution..." -ForegroundColor Cyan
$result = aws cloudfront create-distribution --distribution-config "file://$cfgFile" `
    --profile $Profile --output json | ConvertFrom-Json

$id     = $result.Distribution.Id
$domain = $result.Distribution.DomainName

Write-Host ""
Write-Host "Distribution ID : $id" -ForegroundColor Green
Write-Host "HTTPS URL       : https://$domain" -ForegroundColor Green
Write-Host ""
Write-Host "Takes 5-15 minutes to deploy. Check status with:"
Write-Host "  aws cloudfront get-distribution --id $id --profile $Profile --query Distribution.Status --output text"
Write-Host ""
Write-Host "Dashboard : https://$domain/"
Write-Host "Chat      : https://$domain/chat/"
Write-Host "Feedback  : https://$domain/feedback/"
Write-Host ""
Write-Host "TEARDOWN AFTER JUDGING - this is Discovery's account." -ForegroundColor Yellow
