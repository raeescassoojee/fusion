# Run it and look at everything

## Start

Extract the zip, open PowerShell **in the extracted folder**, then:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1
```

That creates the venv, installs everything, sets `PYTHONPATH`, starts the server and
opens the dashboard. First run takes a few minutes for the installs; after that it's
seconds. Re-running is safe — it skips work already done.

If your folder path has a space in it (`New folder`), that's fine — the script
handles it. Just make sure you `cd` into the folder before running.

Manual equivalent, if you'd rather see each step:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = ".\services\operations\src;.\src"
python -m uvicorn sentinel_ops.main:app --reload
```

| | |
|---|---|
| Dashboard | http://127.0.0.1:8000/dashboard |
| API docs | http://127.0.0.1:8000/docs |
| Pitch site | `deliverables\site\pitch-site.html` — open directly, no server |

---

## The tour — roughly 15 minutes

### 1. The pitch site (2 min)

Open `deliverables\site\pitch-site.html` in a browser. This is the scrolling
showcase — hero animation, the five-stage loop, community watch concept, privacy
section. It's the "here's the idea" artefact, separate from the working product.

### 2. Welcome page (1 min)

Go to the dashboard. You land on the dark SENTINEL MESH page: grid draws itself,
the wordmark slides up, four USP cards stagger in. Scroll down for the five-stage
explainer. Click **Open operations**.

This is where all the animation lives, deliberately — the console stays calm.

### 3. Overview (2 min)

- Real Leaflet map with your hotspots sized by priority and cameras marked
- Click a hotspot on the map — the table selects it, and vice versa
- Filter by peril (Home Invasion / Vehicle Theft) and watch the map dim
- Switch the metro dropdown to **Cape Town** — everything re-derives from Sea Point
- The status dot top-right: green = live API, amber = embedded fallback

### 4. Cameras (2 min)

Eight participating households across both metros. Note the **mode** badge — that
comes from the claims risk of the geofence each camera sits in, resolved server-side.
Bryanston cameras are HEIGHTENED because its priority is 66; Fourways is NORMAL at
42. That's USP 01 made concrete.

Click a card, then **Attach live feed to this camera**.

### 5. Live AI (4 min) — the centrepiece

The nav shows `vision AI ready` once TensorFlow.js has loaded the models.

**Webcam:** click **Start live camera**, allow access. Boxes draw on you in real
time — person, face, and any vehicle in view, each with confidence. Watch the chips:
object count, face count, fps. The quality bars on the right move as lighting and
sharpness change; the trust score follows. Hit **Capture event** to freeze one.

**Upload:** drop any video file on the dropzone. It samples up to 12 frames, runs
detection on each, logs what it found per frame, and keeps the best as an event.

**Server pipeline:** with the backend running, click **Run server pipeline on file**.
That POSTs the actual file to `/api/cameras/upload`, which runs your real Python
stack — plate detection with OCR, YuNet faces, vehicle colour/type, trust scoring —
and pulls the results plus evidence frames back into the UI.

Try `media\camera_1_clip.mp4` from the repo. You should get 2 events and plate
`AB12CDGP`.

### 6. Evidence (3 min)

Every event lands here — recorded, uploaded, or captured from your webcam. Click one:
the detection viewer draws its boxes over the stored frame, with the signal cards
(face / plate / vehicle / appearance) and quality metrics beneath.

Now pick two events in the compare selectors and hit **Compare**. The graph scores
each cue separately, applies journey plausibility, and raises a priority-graded
alert. Try comparing two events from cameras far apart — watch it reject on journey.

### 7. Rewind and Patrol (2 min)

Rewind reconstructs a claim's incident window from stored events, ranked by
relevance. Patrol shows protected risk per kilometre with the fuel saving; change
the fuel figure or max stops and hit Recalculate.

---

## Roles — switch with the control in the nav

The **Member / Fraud / Security** switch changes which tabs exist and what data the
app requests. A scope bar under the nav states the boundary in plain language.

### Member  ·  `Overview · My property · Live AI · Cameras`
Register a doorbell camera and see your own suburb's pattern. Tick nothing and press
Add — it refuses. Consent is a hard gate, not a checkbox we log and ignore.
Register in **Bryanston** and it comes back HEIGHTENED at risk 66, inheriting the
geofence mode automatically.

### Fraud  ·  `Overview · Claims · Live AI · Evidence · Movement · Rewind · Patterns · Cameras`
All 15,712 claims. Sort by **Highest value**, click the top row, read the report.
Findings are graded INFO / WATCH / FLAG. Watch what it does with the biggest claim:
the suburb has only 3 records, so it flags the small sample and falls back to a
national comparison — which catches what the suburb percentile misses.

### Security  ·  `Overview · Briefing · Patrol · Cameras`
Shift briefing per metro, day or night. Priority bands, peak hours for that shift,
recommended presence, and coverage gaps called out separately. Nominate a patrol
location and it tells you which geofence it falls in — or that it falls in none.

---

## Height  ·  Cameras tab

1. Select a camera, **Height calibration** panel on the right
2. Leave the defaults (2.8 m mount, 18° tilt, 78° FOV) → **Save calibration**
3. **Live AI** → start the camera, stand still a few seconds → **Estimate height**

You get a band on a ruler, never a single number. Then press **Simulate drift** back
on the Cameras tab and estimate again — it refuses, because a camera that has drifted
off its calibrated pose cannot be trusted to measure anything. That refusal is worth
demonstrating.

---

## Patterns and Movement

**Patterns** clusters recurring signatures. Send two events from nearby cameras
minutes apart (Live AI → **Send to pattern registry**) and a pattern opens. Try
confirming with a blank reason first — it refuses.

**Movement** is empty until patterns exist with sightings on two or more cameras.
Once they do, each pattern becomes a trail and shared segments thicken into corridor
heat. Selecting a trail draws it in cyan with numbered stops.

---

## Height, Patterns and AWS

Height calibration and anonymous recurring-pattern review are now visible in the
dashboard where the role allows them. The same functions remain available in the
interactive API docs at `http://127.0.0.1:8000/docs`.

AWS is optional. Run `configure_aws.ps1` once, then `start_aws.ps1`. The navigation
bar reports whether private S3 evidence storage and the three DynamoDB tables are
ready. When AWS is unavailable, local storage remains active.

## If something doesn't work

**`No module named uvicorn`** — dependencies didn't install. Run `.\start.ps1 -Fresh`.

**`ExecutionPolicy` error** — run the `Set-ExecutionPolicy` line above first. It only
applies to that PowerShell window.

**Webcam does nothing** — the browser needs `localhost` or HTTPS. `127.0.0.1:8000`
qualifies. Check the browser's camera permission for the site.

**`vision AI failed to load`** — the TensorFlow models come from a CDN. Without
internet you lose live detection; everything else still works. Test this on the venue
network before you present.

**Map is blank** — OpenStreetMap tiles need internet too. It falls back to an SVG plot
automatically after a few seconds.

**Plate OCR returns nothing** — Tesseract isn't on PATH. Install Tesseract 5, or
ignore it; detection and everything else works without it.

**Port already in use** — `.\start.ps1 -Port 8080`.

---

## Reset between demos

```powershell
Remove-Item -Recurse -Force services\operations\uploads -ErrorAction SilentlyContinue
Remove-Item sentinel_patterns.sqlite3 -ErrorAction SilentlyContinue
```

Then restart. Both regenerate on their own.
