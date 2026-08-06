# Abed + Abbasi Finals Merge

## Merge policy

- **Backend authority:** Abbasi (`AhmadBranch` / `sentinel-merged-abbasi.zip`)
- **Frontend authority:** Abed (`abed-work` / `sentinel-merged-abed.zip`)
- **Shared ancestor:** Git commit `22518f9` (`Sentinel Mesh finals build`)
- **Method:** three-way merge from the shared ancestor, followed by contract and integration tests

## What was retained

### From Abbasi

- Fast face tracking, face-first recognition and distant known-profile retrieval
- Delayed, opt-in height measurement that cannot create a new identity
- P1 trust-gated evidence and escalation
- P2 multi-frame OCR voting and per-character confidence
- P3 Evidence Passport and measured latency API/UI
- Claims and SAPS cleaning, audited datasets, training scripts and model artifacts
- All backend APIs, persistence, reset flow and automated tests

### From Abed

- Finals claims workspace redesign
- Security response desk redesign and contextual patrol presentation
- Mapbox maps, directions and 3D member-map frontend
- Updated member, claims and security layouts
- Dented-vehicle demo footage
- Consolidated role navigation for the live presentation

## Resolved overlap

The only textual conflict in the three-way dashboard merge was the plate-results renderer. The final version keeps Abbasi's multi-frame OCR vote, frame count, confidence and per-character confidence, while also keeping Abed's clear “OCR not applicable” state for non-vehicle claims.

The Mapbox public browser token supplied for the finals demo is configured in the dashboard metadata. If the token is rotated, replace the value in `services/operations/static/dashboard.html` under `mapbox-access-token`.

Abed's security frontend referenced three routes that were not in the authoritative Abbasi backend. Thin compatibility routes were added:

- `POST /api/security/dispatch/test-alert` uses the real local Member incident pipeline and never invents an identity.
- `GET /api/security/whatsapp/status` truthfully reports that external delivery is not configured.
- `POST /api/security/notifications/{id}/send-whatsapp` refuses delivery unless a real provider integration exists, leaving the reviewed notification safely queued.

## Finals regression repair

The merged finals UI was rechecked against the live backend after the first merge. Four integration regressions were repaired:

- Per-house camera, event, coverage and intruder layers are assigned to Mapbox Standard's `top` slot, so all three saved houses remain visible above roads and 3D buildings.
- The claims queue states its provenance explicitly: 15,712 supplied workbook rows, random claim generation disabled, and two consented local MP4 clips available.
- Opening the prepared real vehicle claim renders both videos immediately while the 16-second frame/OCR pass continues in the background; completed results then replace the prepared state.
- The security workspace now keeps the measured existing-vs-optimised patrol proof in the same response desk. Region selection and refresh recalculate the route, and the technical demo consoles are the final section.

## Dependency repair

The training scripts already used scikit-learn and joblib, but those packages were absent from the install manifest. Both are now declared in `requirements.txt` and the root `pyproject.toml`, so a fresh Windows setup installs everything needed for the 80/20 training workflows.

## Verification completed

- Root camera/training tests: **20 passed**
- Operations/API/UI tests: **45 passed**
- Full backend integration: **passed**
- Dashboard JavaScript parse: **passed**
- Python source compile: **passed**
- HTTP smoke test: health, dashboard, performance, claims queue provenance, both real claim videos, Mapbox UI, Evidence Passport and security compatibility contracts passed
- Real claims OCR smoke test: two MP4 files processed, 18 frame checks stored, both registrations reconciled, and vehicle mismatch continuity returned
- Test-alert flow: live sighting to Member incident to security dispatch passed

The test suite reports only upstream FastAPI/Starlette deprecation warnings; there are no test failures.

## Run on Windows

From the extracted `sentinel-merged` folder:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1 -Fresh
```

Use `-Fresh` only when no previous server process is holding `.venv` files. For normal restarts use:

```powershell
.\start.ps1
```

Open `http://127.0.0.1:8000/dashboard`.
