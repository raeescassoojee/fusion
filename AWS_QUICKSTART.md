# Sentinel Mesh AWS quick start

The product stays fully usable locally. AWS is an enhancement, not a dependency:

- **S3:** private evidence frames, crops and event JSON.
- **DynamoDB:** anonymous signatures, recurring evidence patterns and human reviews.
- **Local fallback:** SQLite and local media remain active when credentials or venue internet fail.

## First-time setup

Open PowerShell in the project folder:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\configure_aws.ps1
```

The script:

1. Checks the AWS CLI.
2. Creates or signs into the `sentinel-dev` SSO profile.
3. Selects `af-south-1`.
4. Creates a private, encrypted and versioned S3 bucket.
5. Adds a 30-day evidence-expiry lifecycle rule.
6. Creates the three DynamoDB tables.
7. Enables TTL for temporary signatures and recovery for review/audit records.
8. Generates a local random plate-token salt.
9. Writes non-committed settings to `aws.local.ps1`.
10. Runs the AWS readiness check.

Supply a different bucket name when needed:

```powershell
.\configure_aws.ps1 -Bucket "sentinel-mesh-teamname-unique"
```

## Start the AWS build

```powershell
.\start_aws.ps1 -Port 8001
```

The older fallback product can remain open on port 8000.

Open:

```text
http://127.0.0.1:8001/dashboard
```

The navigation bar reports one of:

- `AWS ready · S3 + 3 tables`
- `AWS connected · setup incomplete`
- `AWS local fallback`

## Prove that AWS is really connected

1. Open the Member workspace.
2. Open **Live AI**.
3. Upload a consented video or image.
4. Press **Run server pipeline on file**.
5. The processing log should say that event evidence was copied to private S3.
6. Send the event to the Pattern Registry. With the tables active, signatures and patterns use DynamoDB automatically.
7. Open the status JSON:

```text
http://127.0.0.1:8001/api/aws/status
```

You can also run:

```powershell
. .\aws.local.ps1
python scripts\aws_readiness_check.py
```

## Safe fallback

To force the entire demonstration to stay local:

```powershell
$env:SENTINEL_FORCE_LOCAL = "1"
Remove-Item Env:SENTINEL_EVIDENCE_BUCKET -ErrorAction SilentlyContinue
.\start.ps1 -Port 8002
```

AWS upload failures are returned in the camera-upload response but do not destroy the local event or the live demo.
