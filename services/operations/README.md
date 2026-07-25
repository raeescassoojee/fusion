# Sentinel Mesh — Operations Service (`abed` branch)

This service is ready to live beside the claims pipeline:

```text
fusion/
└── services/
    ├── claims/       # cassoojee branch
    └── operations/   # abed branch (this folder)
```

It now connects the team workstreams in one flow:

```text
cassoojee hotspots.json
→ operational patrol priority
→ repeat-evidence comparison
→ human-review alert
→ incident rewind
→ distance/fuel/risk route comparison
→ dashboard/API for the frontend
```

## What is implemented

- Automatic adapter for `services/claims/data/curated/hotspots.json`
- Automatic adapter for `route_inputs.json`
- Live branch output preferred; bundled snapshot used before branches are merged
- Metro-only routing to avoid Cape Town/Gauteng cross-routing
- Claims risk and `blended_risk_v2` kept as separate explainable values
- Multi-signal evidence comparison
- Human-review alerting
- Incident Time Machine
- Fuel-aware patrol optimisation with two-opt route improvement
- SQLite ingestion for current camera events, claims and generated alerts
- Interactive graphics dashboard
- Existing cassoojee Folium hotspot map exposed through the API
- CORS enabled for a separately hosted hackathon frontend

## Data truth

- The claims hotspot output is real output from the supplied Discovery workbook pipeline.
- The SAPS figures currently present in that output are **historical corroboration**, not a live SAPS incident feed.
- The bundled camera events and claim used in `/api/overview` are synthetic demo fixtures.
- When Person 3 posts current camera events to `POST /api/events`, they are saved and compared against prior events.
- Vumacam, Vision Tactical and similar operational feeds require formal partner access. The API accepts a neutral event contract but does not pretend those private feeds are already connected.

## Run on Windows PowerShell

From `fusionGradhack\services\operations`:

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m uvicorn sentinel_ops.main:app --reload
```

Open:

- Dashboard: `http://127.0.0.1:8000/dashboard`
- API docs: `http://127.0.0.1:8000/docs`
- Integrated output: `http://127.0.0.1:8000/api/overview?metro=Gauteng`
- Cassoojee hotspot map: `http://127.0.0.1:8000/api/claims/map`

## Automatic claims integration

The service searches for claims output in this order:

1. `SENTINEL_CLAIMS_CURATED_DIR` environment variable
2. `fusion/services/claims/data/curated`
3. A bundled cassoojee snapshot for independent branch development

After the branches merge, no manual field conversion is required.

## Current-data ingestion

### Post a new camera event

```http
POST /api/events
```

The service will:

1. Store the event in SQLite.
2. Compare it against prior events.
3. Return candidate repeat-evidence links.
4. Check current claims hotspots.
5. Create and store a human-review alert when justified.

### Post a new claim

```http
POST /api/claims
```

The service stores the claim and reconstructs a timeline from stored camera events around its time and location.

SQLite is written to `data/sentinel_ops.db` and is excluded from Git.

## Frontend endpoints

| Graphic or function | Endpoint |
|---|---|
| Metro list | `GET /api/claims/metros` |
| Hotspots | `GET /api/claims/hotspots?metro=Gauteng` |
| Existing claims map | `GET /api/claims/map` |
| Full dashboard payload | `GET /api/overview?metro=Gauteng` |
| Metro route comparison | `GET /api/routes/metro/Gauteng` |
| New camera event | `POST /api/events` |
| Stored camera events | `GET /api/events` |
| New claim and rewind | `POST /api/claims` |
| Stored alerts | `GET /api/alerts` |
| Direct evidence comparison | `POST /api/evidence/compare` |

## Tests

```powershell
pytest
```

## Product boundary

Similarity is a retrieval clue, not an identity or guilt verdict. Alerts remain subject to human review. Use only consented or synthetic biometric media for the hackathon.
