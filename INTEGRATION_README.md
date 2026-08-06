# Fusion backend integration

This folder combines the three non-frontend workstreams:

- **Camera AI (`abbasi`)**: processes clips into event-v1 JSON.
- **Claims (`cassoojee`)**: produces Risk Pulse hotspots, SAPS context and route inputs.
- **Operations (`abed`)**: stores camera events and claims, creates evidence links and alerts, reconstructs incidents, and optimises patrol routes.

## The connection

```text
camera clip
  -> sentinel_camera_ai event-v1 JSON
  -> POST /api/events/camera-ai
  -> event adapter
  -> evidence comparison + alerting

Discovery claims workbook
  -> services/claims/data/curated/hotspots.json
  -> operations claims bridge
  -> hotspot context + patrol optimisation

new claim
  -> POST /api/claims
  -> incident time machine
  -> relevant stored camera events
```

## One-command backend test on Windows

From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\run_full_backend_test.ps1
```

This runs:

1. Camera AI unit tests.
2. Operations unit tests.
3. Claims bridge loading from the merged `services/claims` folder.
4. Two real synthetic camera-event files through the API adapter.
5. Repeat-evidence comparison and alert evaluation.
6. A matching claim through the Incident Time Machine.
7. Gauteng patrol optimisation and fuel comparison.
8. SQLite storage verification.

## Launch the temporary dashboard

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = ".\services\operations\src;.\src"
python -m uvicorn sentinel_ops.main:app --reload
```

Open:

- `http://127.0.0.1:8000/dashboard`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/overview?metro=Gauteng`
- `http://127.0.0.1:8000/api/claims/map`

## Important data note

The camera events in the test are synthetic/consented demo outputs. The claims hotspots come from the supplied Discovery workbook pipeline. The SAPS figures in the curated files are historical corroboration, not a live feed.
