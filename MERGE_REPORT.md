# Team merge report

## Base used

The role-separated, Tesseract-fixed live-camera build was used as the base because it
contained the newest UI and the safest demo fallback.

## Integrated from the additional team build

- `provision_aws.py`: DynamoDB plus private S3 provisioning.
- `aws_status.py` and `scripts/aws_readiness_check.py`.
- Environment-driven S3 publishing.
- Upload-size limits, ingest error reporting and safe media paths.
- Demo seed/reset endpoints.
- Local pilot route scoping to avoid metro-wide patrol routes.
- DynamoDB/S3 setup commands and AWS runbook.

## Conflict decisions

- Kept the newest role workspaces instead of the older mixed dashboard.
- Kept the newer evidence-pattern clustering fix that links both original sightings.
- Kept the Windows Tesseract discovery/install fix.
- Kept the newer live face-tracking controls.
- Added AWS features around those components rather than replacing them.

## AWS behaviour

With AWS configured:

- Uploaded evidence frames and event JSON are copied to private S3.
- Pattern signatures, clusters and human reviews use DynamoDB.
- The dashboard displays AWS readiness.

Without AWS:

- The same product continues with local media, SQLite operations storage and the
  local pattern registry.
- AWS errors are shown but do not delete or invalidate local demo results.

## Validation performed in the merge environment

- Python compilation: passed.
- Camera AI: 16 tests passed.
- Operations: 7 tests passed.
- Full backend integration: passed.
- Real MP4 upload through the camera pipeline: 2 events produced and ingested.
- Dashboard JavaScript syntax: passed.
- HTTP smoke tests: health, roles, claims, overview, demo reset/seed, AWS status,
  patrol and dashboard all returned successful responses.

## Known limitations

- Real AWS resources could not be provisioned from the isolated merge environment;
  the provisioning code and mocked AWS sink tests passed, but the team must run
  `configure_aws.ps1` in its own AWS account.
- Browser face tracking depends on browser camera permission and CDN-loaded models.
- Route distance remains a POC calculation until connected to a road-routing API.
- The role selector is display-level demo RBAC, not production authentication.
