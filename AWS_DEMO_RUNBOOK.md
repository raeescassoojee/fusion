# AWS demo runbook

## Recommended one-day deployment

For the judging demo, keep the OpenCV/Tesseract camera pipeline on the team laptop and use AWS for **private evidence storage and the anonymous pattern registry**. This avoids spending the day debugging a large computer-vision container while still showing a real cloud integration.

1. Use Region `af-south-1`.
2. Configure credentials with IAM Identity Center / SSO.
3. Create a private S3 bucket and the three DynamoDB tables.
4. Keep the FastAPI dashboard on localhost for the main demo.
5. Publish one processed camera event to S3 and show `/api/aws/status` returning ready.
6. Keep the local SQLite fallback enabled so a venue-network failure cannot break the pitch.

## Important

- Live webcam access works on `localhost` or HTTPS. It will not work from an insecure raw HTTP cloud URL.
- Do not make the evidence bucket public.
- Use a real `SENTINEL_PLATE_SALT` secret.
- The browser AI models use public CDNs. Pre-load them before judging or use the server upload demo as the reliable fallback.
- The current full video pipeline is better suited to App Runner/ECS/EC2 than Lambda because it includes OpenCV, FFmpeg and Tesseract.

## Commands

```powershell
python -m pip install -e ".[aws]"
aws configure sso
$env:AWS_PROFILE = "sentinel-dev"
$env:AWS_REGION = "af-south-1"
$env:SENTINEL_EVIDENCE_BUCKET = "YOUR-GLOBALLY-UNIQUE-BUCKET"
$env:SENTINEL_PLATE_SALT = "A-LONG-RANDOM-SECRET"

python provision_aws.py --region af-south-1 --bucket $env:SENTINEL_EVIDENCE_BUCKET
python scripts/aws_readiness_check.py
```

Open `http://127.0.0.1:8000/api/aws/status` after starting the API.
