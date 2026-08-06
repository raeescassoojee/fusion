# Sentinel Mesh — merged working product

This build combines the strongest parts of the team branches into one product:

- Claims ingestion, risk scoring and curated hotspots.
- Live phone/laptop camera demo with face detection and a green tracking box.
- Full server camera pipeline for face, plate, vehicle, appearance and camera quality.
- Anonymous repeat-evidence patterns and calibrated height bands.
- Human-review alerts and Incident Time Machine.
- Separate Member, Fraud & Claims, and Security Partner workspaces.
- Local patrol optimisation with a compact-pilot option.
- Optional AWS S3 evidence storage and DynamoDB pattern registry.
- Local SQLite/media fallback when AWS or venue internet is unavailable.

## Safest way to start now

Keep any older product running on port 8000 and start this build on port 8001:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1 -Port 8001
```

Open:

```text
http://127.0.0.1:8001/dashboard
```

## Add AWS

First-time setup:

```powershell
.\configure_aws.ps1
```

Then start:

```powershell
.\start_aws.ps1 -Port 8001
```

Read `AWS_QUICKSTART.md` for the exact AWS demonstration flow.

## Test everything

```powershell
.\scripts\run_full_backend_test.ps1
```

Expected core result:

- 16 camera-AI tests pass.
- Operations tests pass.
- Claims → camera → evidence → alert → rewind → route integration passes.

## Current page sequence

1. Workspace chooser
2. Discovery Member: My Property and Live AI
3. Fraud & Claims: Claims, Evidence, Movement, Rewind and Patterns
4. Security Partner: Briefing and Patrol

We can now refine these one page at a time without changing the working backend contract.
