# Sentinel Mesh sanity-check report

Date: 2026-07-25

## Automated verification

- Camera AI: **16 tests passed**.
- Operations API: **7 tests passed**.
- Python source compilation: passed.
- Full claims → camera event → evidence → alert → claim rewind → patrol flow: passed.
- Real upload endpoint tested with `media/camera_1_clip.mp4`: 2 events produced and stored.
- Generated evidence image fetched successfully through `/api/cameras/media/...`.
- Dashboard JavaScript syntax checked with Node.js.
- Core API GET endpoints returned HTTP 200 in a FastAPI TestClient smoke test.

## Current integrated demo result

- Gauteng risk-ranked 3-stop baseline: approximately **82.9 km**.
- Risk-and-distance optimised 3-stop route: approximately **47.3 km**.
- Estimated fuel: approximately **8.29 L → 4.73 L** at 10 L/100 km.
- Protected-risk-per-kilometre improvement: approximately **62%**.

These are demonstration outputs using straight-line/Haversine distances. Replace the distance matrix with Amazon Location or another road-routing provider before presenting the kilometres as real driven distance.

## Fixes applied during review

1. Added the missing `python-multipart` runtime dependency for footage uploads.
2. Fixed stored evidence URLs so Incident Time Machine links survive after temp files are deleted.
3. Rejected path traversal in the evidence media endpoint.
4. Added a configurable upload-size limit (150 MB by default).
5. Exposed ingestion failures instead of silently swallowing them.
6. Removed a Pydantic field-shadowing warning while preserving the API field name.
7. Added `POST /api/demo/seed` and `DELETE /api/demo/reset` for repeatable judging runs.
8. Added `GET /api/aws/status` and an AWS readiness checker.
9. Added secure optional S3 provisioning: public-access block, encryption, versioning and expiry.
10. Changed the default patrol demonstration to compare three stops rather than an unrealistic entire-metro shift.
11. Toned down UI wording so configured demo cameras are not presented as a real deployed network.

## Remaining limitations

- Browser detection depends on external model CDNs; preload before judging and retain the Python upload flow as the reliable fallback.
- Live webcam access needs localhost or HTTPS.
- AWS credentials and resources were not mutated during this review. Run the readiness checker in the team AWS account.
- The current Python vision fallback is suitable for controlled demo media, not a claim of South African CCTV accuracy.
- SAPS values in the curated claims files are historical corroboration, not a live operational feed.
- The local operations store uses SQLite. For a long-running cloud deployment, migrate events/claims/alerts to DynamoDB or a managed relational store.
