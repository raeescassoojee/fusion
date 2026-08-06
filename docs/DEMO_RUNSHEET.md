# Demo runsheet — 5 minutes

Rehearse this three times before you present. Not twice. Three.

---

## Before you walk up

```powershell
.\start.ps1
```

Then, in this order:

- [ ] Dashboard open, **already past the welcome page**, sitting on **Overview**
- [ ] Nav shows `vision AI ready` (models loaded) and a **green** status dot (API live)
- [ ] Second browser tab: `deliverables\site\pitch-site.html`, scrolled to top
- [ ] Webcam permission already granted — click **Start live camera** once, then **Stop**
- [ ] Calibrate `CAM-BRY-01` under **Cameras → Height calibration → Save**
- [ ] Reset state so counters start clean:
      ```powershell
      Remove-Item sentinel_patterns.sqlite3 -ErrorAction SilentlyContinue
      ```
- [ ] `media\camera_1_clip.mp4` located in a file dialog you can reach in one click
- [ ] Phone hotspot ready in case venue wifi drops the CDN

Know your one-liner cold:

> **"Discovery's claims data already knows where crime concentrates. We use it to
> decide where cameras pay attention — and every match a human sees comes with the
> reasons to doubt it."**

---

## The sequence

### 0:00–0:30 · The problem, on the map

**Overview.** Point at the map.

> "Five years of Discovery claims, geocoded and fused with SAPS context. Bryanston is
> our highest priority at 66 — but notice frequency and financial severity disagree.
> The suburb with the most incidents isn't the one losing the most money, so we score
> them separately."

Click a hotspot. Filter to **Vehicle Theft**. Switch metro to **Cape Town**, then back.

*Don't linger. This is setup.*

### 0:30–1:00 · Claims drive the cameras

**Cameras** tab.

> "Eight participating households. Look at the mode badges — Bryanston is HEIGHTENED,
> Fourways is NORMAL. Nobody set those by hand. They come from the claims risk of the
> geofence each camera sits in. That's the whole thesis: claims decide where attention
> goes, before anything happens."

### 1:00–2:15 · Live AI — the moment that lands

**Live AI** tab. **Start live camera.** Step into frame.

> "This is running in the browser right now. COCO-SSD for objects, BlazeFace for
> faces — real models, real inference, on this laptop."

Move around. Let the boxes track you. Point at the chips (object count, face count, fps).

> "It's also sampling appearance — the clothing colours you see it derive — and scoring
> frame quality, which becomes the camera's trust score. A blurry camera can't produce
> strong evidence, and the system knows that about itself."

Now the height beat. Stand still for a few seconds so it accumulates frames, then hit
**Estimate height**.

> "It just measured me. Not a guess — the foot ray hits the ground plane to give depth,
> then the head ray is measured at that depth. And it gives a **band**, never a number,
> because one pixel of error at the top of frame is worth centimetres."

**Then immediately undercut it** — this is what separates you:

> "A ten-centimetre band is corroborating, not identifying. It's strong for ruling a
> candidate out and weak for confirming one. We say that before you have to ask."

### 2:15–3:00 · The server pipeline

Drop `media\camera_1_clip.mp4` on the dropzone. Let it sample frames. Then
**Run server pipeline on file**.

> "Browser vision doesn't do plate OCR — we don't pretend it does. That runs server-side
> in Python: plate detection with OCR, YuNet faces, calibrated trust scoring."

Watch the log fill. Plate `AB12CDGP` comes back.

### 3:00–3:45 · Evidence — the differentiator

**Evidence** tab. Pick two events, **Compare**.

> "Ordinary systems match one face or one plate and call it a hit. We score plate,
> face, vehicle, appearance and height separately, then apply a journey check — if the
> two sightings would need someone to travel 68 km in 15 minutes, we throw it out no
> matter how well the cues agree."

Point at the relationship label.

> "And notice the label: POSSIBLE_SAME_VEHICLE. Never IS. This system does not decide
> who someone is."

### 3:45–4:30 · Patterns — the maturity beat

**Patterns** tab.

> "Recurring signatures cluster into patterns. Read what's in this record: navy upper,
> black lower, backpack, a height band. Now read what isn't in it — no name, no ID
> number, no face gallery. Under POPIA, criminal-behaviour information is special
> personal information. So we don't build a watchlist. We build a pattern, and it
> carries a SAPS case reference *outward*. Attribution to a person is SAPS's call
> under their authority, not ours."

Try to confirm with a blank reason. It refuses. Then fill it in and confirm.

> "Every decision needs a written reason. And we keep the dismissals — that's the
> false-positive rate on screen. A system that only remembers its hits is lying to you."

### 4:30–5:00 · The business case

**Patrol** tab.

> "Same hour, same depot. Other routing minimises distance. We maximise protected risk
> per kilometre — 116 km and 11.6 litres saved per shift, with more risk covered, not
> less."

Close:

> "Claims decide where cameras look. Vision turns footage into evidence. Evidence
> surfaces patterns. A human decides. And the outcome makes tomorrow's thresholds
> better. One loop — and it never once tells you who someone is."

---

## When something breaks

| Failure | Say this, do this |
|---|---|
| Webcam won't start | *"Let me use recorded footage instead"* → drop `camera_1_clip.mp4`. Same beats, no dead air. |
| `vision AI failed to load` | CDN blocked. Switch to hotspot, or skip to the server pipeline — that's Python, it doesn't need the CDN. |
| Map blank | It falls back to an SVG plot in ~4 seconds. Don't mention it. Keep talking. |
| Server down / red dot | Everything still runs on embedded data. *"We're on the offline fallback — the app is built to survive a dead venue network."* That's a feature, say it as one. |
| Height refuses | **Best case, not worst.** *"That's it refusing — I'm clipped by the frame edge, so it won't guess. A wrong height is worse than no height."* |
| Plate OCR empty | Tesseract missing. *"Plate OCR needs Tesseract on this machine; detection and everything else is unaffected."* |
| Nothing works at all | Second tab: the pitch site. Talk the story. Never freeze on a broken screen. |

---

## Questions you will get

**"Isn't this just surveillance?"**
> Routine video never leaves the camera — only event crops upload. Cameras in low-risk
> areas stay in light mode. Faces and plates are blurred by default, un-blurred only
> inside an authorised case, and every access is logged. We spend attention where the
> claims say it matters, rather than watching everything equally.

**"What if it matches the wrong person?"**
> It can't match a person — it has no identities. It surfaces a pattern with every cue
> scored separately and every reason visible, a human decides, and the dismissal is
> kept. Show them the false-positive rate.

**"Is the height accurate enough to identify someone?"**
> No, and we don't claim it. Ten-centimetre band. It's good for excluding a candidate,
> weak for confirming one. It's one cue of five, worth 27 points out of 100.

**"POPIA?"**
> Criminal-behaviour information is special personal information under s26, so we
> deliberately don't store it. Anonymous patterns, TTL on observations, permanent audit
> on decisions, `af-south-1` for residency, and a real legal review before any live
> deployment.

**"Hasn't Vumacam done this?"**
> Vumacam sees a plate. It doesn't know that address filed a R400k claim last month.
> We're the layer above the cameras that already exist — the claims data is the moat.

**"Does the AI actually run, or is it mocked?"**
> Open the browser console — TensorFlow.js is loaded. Or point the webcam at them and
> let it box their face live. This is the easiest question you'll get.

---

## Two rules

**Lead with the refusals.** Every team will show something working. Almost none will
show their system declining to answer. The refusal is your credibility.

**Never say "identify."** Say *surface*, *retrieve*, *flag for review*. One slip and
the whole POPIA position wobbles.
