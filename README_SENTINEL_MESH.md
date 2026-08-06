# Discovery Sentinel Mesh — complete build

Discovery GradHack 2026 · AI for Safer Communities

Claims decide where cameras pay attention. Computer vision turns footage into
evidence. Multi-signal matching surfaces recurring patterns for human review. A
claim rewinds the minutes that matter. Patrols cover the most risk per kilometre.

```
claims workbook ─┐
                 ├─► Risk Pulse ─► camera mode ─► camera AI ─► evidence graph
SAPS context ────┘                                    │             │
                                                      ▼             ▼
                                          pattern registry    reviewed alert
                                                      │             │
                                                      ▼             ▼
                                              incident rewind   patrol route
                                                      └──── outcome loop ────┘
```

---

## Run it

**`RUNBOOK.md` has the full walkthrough and a guided tour.** Fastest path:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1
```

Manual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:PYTHONPATH = ".\services\operations\src;.\src"
python -m uvicorn sentinel_ops.main:app --reload
```

| URL | What |
|---|---|
| `http://127.0.0.1:8000/dashboard` | The operations app |
| `http://127.0.0.1:8000/docs` | Every endpoint, interactive |
| `deliverables/site/pitch-site.html` | Standalone pitch site (open directly) |

The dashboard runs with the backend **or without it** — if the API is unreachable
it falls back to embedded payloads for both metros so a blocked venue network
cannot kill the demo. The status dot in the nav tells you which mode you're in.

---

## What's where

```
services/operations/          the API and the app
  src/sentinel_ops/
    main.py                   FastAPI app, all routers wired
    claims_bridge.py          reads the claims pipeline output
    evidence.py  alerts.py    multi-signal comparison and alert rules
    rewind.py    routing.py   incident time machine, patrol optimisation
    camera_upload.py    NEW   upload real footage, run the camera-AI pipeline
    height.py           NEW   calibrated height estimation
    patterns.py         NEW   evidence-pattern registry (DynamoDB or SQLite)
    patterns_api.py     NEW   endpoints for the above
  static/dashboard.html  NEW  the whole frontend, one file
services/claims/              risk pipeline (step1..step10, hotspots, routes)
src/sentinel_camera_ai/       the vision pipeline (faces, plates, vehicles, trust)
provision_aws.py        NEW   creates the DynamoDB tables
docs/
  PATTERNS_AND_HEIGHT.md      pattern registry + height design and accuracy
  CAMERA_INTAKE_SETUP.md      camera upload endpoint setup
deliverables/site/            pitch site
```

---

## Where the AI runs

**In the browser** — no backend needed. COCO-SSD object detection and BlazeFace
face detection run on a live webcam feed or uploaded video at frame rate, drawing
boxes live. Appearance colours are sampled from the person region; frame quality
becomes a trust score. Multi-cue comparison with a journey-plausibility gate runs
client-side too.

**On the server** — the full Python stack: licence-plate detection with OCR, YuNet
faces, vehicle colour and type, quality-weighted trust scoring, height estimation,
and the pattern registry.

The browser never claims to do plate OCR. The UI says so and routes that work to
the server pipeline. Knowing exactly which model runs where is a better answer than
overclaiming.

---

## The frontend

Six tabs, one view at a time:

| Tab | What it does |
|---|---|
| **Overview** | Leaflet map of hotspots and cameras, peril filter, priority table |
| **Cameras** | Participating households, Adaptive Edge mode from geofence risk |
| **Live AI** | Webcam feed with live detection · drop any video · run the server pipeline |
| **Evidence** | Every captured event, detection viewer with boxes, compare any two |
| **Rewind** | Incident Time Machine over a claim window |
| **Patrol** | Protected risk per kilometre, fuel saving, route comparison |

Animations live on the welcome page only; the console stays calm.

---

## New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/cameras` | Participating households and their modes |
| `POST /api/cameras/upload` | Real footage → camera-AI pipeline → event-v1 JSON |
| `GET /api/cameras/media/{batch}/{path}` | Evidence crops from an upload |
| `PUT /api/cameras/{id}/calibration` | Camera geometry for height estimation |
| `POST /api/height/estimate` | Height band from tracked person boxes |
| `POST /api/patterns/ingest` | Event → signatures → matches → pattern |
| `GET /api/patterns` | Registry, stats, false-positive rate |
| `POST /api/patterns/{id}/review` | Confirm or dismiss; written reason required |

---

## AWS

```powershell
python provision_aws.py --region af-south-1
python provision_aws.py --region af-south-1 --teardown   # after judging
```

Three DynamoDB tables, on-demand billed. Signatures carry a **TTL** and delete
themselves; patterns and reviews get **point-in-time recovery** because they are the
audit record. Prefer `af-south-1` for data residency.

Set a real plate salt before anything but a demo:

```powershell
$env:SENTINEL_PLATE_SALT = "<a secret, not the default>"
```

Without AWS the registry uses an identical local SQLite schema automatically.

---

## What the system will not do

- It does not identify people. Patterns are unnamed clusters of observed cues with a
  `saps_reference` pointing outward — never an identity inward.
- It does not infer race, gender, age, disability, emotion or criminal intent.
- It does not auto-dispatch. Every outcome needs a human and a written reason.
- It does not keep observations indefinitely. Signatures expire on a TTL; retention
  is a decision someone makes.
- It does not trust a bad camera. Trust gates what evidence may claim, and a drifted
  calibration disables height estimation entirely.

Alleged-criminal-behaviour information is special personal information under POPIA
s26. This build is a proof of concept and needs legal, privacy and operational
sign-off before any real-world processing.

---

## Verified

Run against this package with the backend live:

| Check | Result |
|---|---|
| Claims → hotspots → routes | Bryanston top priority, 7 hotspots, both metros |
| Real video upload | 2 events, plate `AB12CDGP` @ 0.91 OCR, both ingested |
| Evidence media served | HTTP 200 |
| Height, true 1.78 m | band **1.73–1.83 m**, point 1.78 |
| Height, subject clipped | refused, 409 with reason |
| Same person, 2 cameras, 390 m / 18 min | **92.9** `POSSIBLE_SAME_APPEARANCE` |
| Identical cues, 68 km in 15 min | rejected on journey plausibility |
| Review with blank reason | refused, 422 |

Data note: claims hotspots come from the supplied Discovery workbook pipeline. SAPS
figures are historical corroboration, not a live feed. All camera media is synthetic
or consented.
