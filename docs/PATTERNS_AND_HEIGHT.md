# Evidence patterns on AWS + height estimation

Four new files. Drop the three modules into `services/operations/src/sentinel_ops/`,
keep `provision_aws.py` at the repo root.

| File | Purpose |
|---|---|
| `height.py` | Calibrated single-view height estimation |
| `patterns.py` | Signature store, matching, pattern clustering (DynamoDB or local) |
| `patterns_api.py` | FastAPI router exposing both |
| `provision_aws.py` | Creates the DynamoDB tables |

Wire it up in `main.py`:

```python
from sentinel_ops.patterns_api import router as patterns_router
app.include_router(patterns_router)
```

Runs with no AWS at all — it falls back to a local SQLite mirror with the same
schema, so a blocked venue network can't kill the demo. Force it with
`SENTINEL_FORCE_LOCAL=1`.

---

## 1. The framing decision — read this before the pitch

You asked for tables describing thieves. I built the registry around **anonymous
evidence patterns** instead, and you should present it that way.

Three reasons, in order of how much they'll cost you if ignored:

1. **POPIA s26.** Information about alleged criminal behaviour is *special personal
   information*. Processing it needs the Information Regulator's authorisation or a
   narrow exemption. A "known thieves" table with descriptions is squarely inside
   that. An unnamed cluster of observed cues is not.
2. **Your own handbook already committed to this.** It says *"do not build a public
   identity watchlist from ordinary members"* and *"similarity is a retrieval clue,
   not an identity verdict."* A judge who read your submission will notice if the
   build contradicts the document.
3. **A wrong match on a named person is a defamation problem.** A wrong match on a
   pattern is a dismissed candidate that improves your false-positive rate.

So: a `Pattern` holds cue observations, camera IDs, geofences, timestamps and a
`saps_reference` pointing *outward*. It has no name field, no ID number, no face
gallery, and no "suspect" flag. Identity attribution stays with SAPS under their
authority. You keep every bit of the detective capability and lose the liability.

The line for the pitch: **"We don't identify people. We surface a recurring pattern
of evidence and hand a human the reasons to judge it."**

---

## 2. The tables

```
sentinel-signatures    PK signature_id
  GSI bucket-index     bucket (kind#geofence#day) + observed_at
  GSI plate-index      plate_token + observed_at
  GSI pattern-index    pattern_id + observed_at
  TTL on `ttl`         signatures self-delete after SENTINEL_SIGNATURE_TTL_DAYS (30)

sentinel-patterns      PK pattern_id
  GSI status-index     status + last_seen
  PITR on              this is the audit record

sentinel-reviews       PK review_id
  GSI pattern-index    pattern_id + created_at
  PITR on
```

```powershell
pip install boto3
python provision_aws.py --region af-south-1
python provision_aws.py --region af-south-1 --teardown   # after judging
```

On-demand billing, so an idle demo costs nothing. Prefer `af-south-1` (Cape Town)
for data residency — say that out loud in the pitch, it's a real POPIA point.

Set a real plate salt before anything but a demo:

```powershell
$env:SENTINEL_PLATE_SALT = "<a secret, not the default>"
```

Plates are stored as a salted SHA-256 token plus a masked partial (`BX42****`). The
raw plate is never an index key.

**Deliberate design choices worth mentioning:**

- **TTL on signatures, PITR on patterns/reviews.** Observations expire automatically;
  decisions and their reasons are permanent. "We forgot to delete it" can't happen,
  and "we can't prove who approved it" can't either.
- **No vector database.** Candidate narrowing uses a `kind#geofence#day` bucket key
  plus exact plate-token lookup, then scores the survivors. OpenSearch k-NN is the
  upgrade path once you have face embeddings at volume — not needed for the pilot,
  and one less service to explain.
- **Retention is a decision.** `retention_reason` and `legal_hold` exist for the
  exceptions; the default is expiry.

---

## 3. Matching

`score_pair()` scores each cue separately. Weights sum to 100 for a perfect match:

| Appearance | | Vehicle | |
|---|---|---|---|
| upper colour | 16 | plate token exact | 40 |
| lower colour | 11 | vehicle colour | 15 |
| headwear | 14 | body type | 13 |
| carried item | 14 | distinctive marks | 17 |
| **height band overlap** | **27** | journey plausible | 15 |
| journey plausible | 18 | | |

Then three guards, and these are the interesting part:

- **Journey plausibility is a hard gate, not a score.** Over 120 km/h between two
  cameras and the score is zeroed regardless of how well the cues agree. Tested:
  identical cues 68 km apart in 15 minutes → rejected.
- **Camera trust caps everything.** `confidence = 0.55 + 0.45 × (0.7·trust + 0.3·quality)`.
  A blurry camera cannot produce strong evidence however neatly the cues line up.
- **One cue is not a pattern.** Fewer than two agreeing cues and the score is cut
  by 45%.

Thresholds: 45 to be *offered* as a candidate, 70 to open a pattern. Nothing is ever
auto-confirmed.

Measured behaviour:

| Scenario | Result |
|---|---|
| Same person, 2 cameras, 390 m / 18 min | 92.9 `POSSIBLE_SAME_APPEARANCE` ✓ |
| Same vehicle, same pair | 77.1 `POSSIBLE_SAME_VEHICLE` ✓ |
| Identical cues, 68 km in 15 min | rejected on journey ✓ |
| Unrelated person, same geofence | below floor, no link ✓ |
| Partial cues on a trust-58 camera | below floor ✓ |
| Review with a blank reason | 422, refused ✓ |

Dismissals are kept and feed `false_positive_rate` in `/api/patterns`. Show that
number to judges — a registry that only remembers its hits is lying to you.

---

## 4. Height estimation

Two calibration modes, both single-view, both from your handbook's reference
(Li et al., calibrated human-height estimation).

**INTRINSIC** — you know the mount. Camera height, downward tilt, horizontal FOV.
The foot ray is intersected with the ground plane to get depth, then the head ray is
measured against that depth.

```powershell
curl -X PUT .../api/cameras/CAM-BRY-01/calibration -d '{
  "camera_id":"CAM-BRY-01","image_width":1280,"image_height":720,
  "mode":"INTRINSIC","mount_height_m":2.8,"tilt_deg":18,"horizontal_fov_deg":78}'
```

**REFERENCE** — you don't. Mark one object of known height standing on the ground
(a door is ~2.04 m) plus the horizon row; the cross-ratio does the rest. Measured
within ~3 cm with no mount data at all.

Accuracy, measured against forward-projected ground truth:

| Test | Result |
|---|---|
| Pure geometry, 1.55–1.95 m at 6/10/16 m | 0.00 cm error |
| With detector noise (σ 4 px feet, 2.5 px head), 10 frames | true 1.78 → band **1.73–1.83 m**, point 1.78 |
| REFERENCE mode off a 2.04 m door | within 3 cm |

**It returns a band, never a number.** A single pixel of error at the top of frame is
worth centimetres, so the band is produced by re-running the geometry at the corners
of the pixel uncertainty and widening if frames disagree.

**It refuses when it should**, and this is the part to demo:

- subject clipped by the frame edge → the real head or feet are outside the image
- fewer than 3 usable frames
- subject under 60 px tall
- calibration score below 70 → *this is the Camera Trust link*: a drifted camera
  loses height estimation entirely, exactly as the handbook specifies
- result outside 0.6–2.6 m

Height then flows straight into matching as a 27-point cue, and appears in the
pattern description: *"Navy upper · Black lower · red cap · backpack · 1.73–1.83 m"*.

One honesty note for the pitch: a 10 cm band is **corroborating, not identifying**.
It's strong for excluding a candidate and weak for confirming one. Say that before a
judge says it for you.

---

## 5. Endpoints

| Endpoint | Purpose |
|---|---|
| `PUT /api/cameras/{id}/calibration` | Store camera geometry |
| `POST /api/height/estimate` | Height band from tracked person boxes (409 + reason when refused) |
| `POST /api/patterns/ingest` | Event → signatures → matches → pattern |
| `GET /api/patterns?status=` | Registry with stats and false-positive rate |
| `GET /api/patterns/{id}` | One pattern plus its full review history |
| `POST /api/patterns/{id}/review` | Confirm or dismiss; written reason mandatory |
| `POST /api/patterns/compare` | Score two signatures without storing anything |

---

## 6. Next

The frontend doesn't show any of this yet. Two additions when you're ready:

1. **Calibration panel** on the camera detail — mount height, tilt, FOV, or a
   click-to-mark reference object. Wire the live person boxes into
   `/api/height/estimate` and show the band on the detection viewer.
2. **Pattern registry tab** — candidate patterns with their cue descriptions, a
   confirm/dismiss form with the mandatory reason, and the false-positive counter.

On faces specifically: if you use Rekognition collections, index **consented
references only** (the handbook already says this). Do not build a collection from
observed faces — that's the watchlist, and it's the one thing that turns this from a
defensible system into an indefensible one.
