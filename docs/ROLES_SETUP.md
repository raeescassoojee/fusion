# Roles — setup

Three files, dropped in by hand.

| File | Goes to |
|---|---|
| `dashboard.html` | `services\operations\static\dashboard.html` (replace) |
| `roles_api.py` | `services\operations\src\sentinel_ops\roles_api.py` (new) |
| the claims workbook | `services\claims\data\raw\claims_workbook.xlsx` (new folder) |

In `main.py`, beside the other router imports:

```python
from sentinel_ops.roles_api import router as roles_router
...
app.include_router(roles_router)
```

```powershell
pip install pandas openpyxl
```

The workbook filename doesn't matter — the loader takes the first `.xlsx` under
`services\claims\data\raw` (then `services\claims\data`, then `data`, then the repo
root) that has a `CLAIM_AMOUNT` column.

On boot you should see:

```
[roles] claims workbook ready: 15,712 rows from ...\claims_workbook.xlsx
```

That warm-up matters. Parsing 15k rows takes several seconds, and lazily it would
land on whoever opens the claims browser first — in a demo that pause reads as a
hang.

---

## What each role sees

Switch with the **Member / Fraud / Security** control in the nav. Tabs change, and a
scope bar under the nav states the boundary in plain language — useful when a judge
asks who can see what.

### Member
`Overview · My property · Live AI · Cameras`

Registers a doorbell camera, sees their own suburb's pattern. Consent is a hard
gate: `consent_acknowledged: false` returns 422, not a warning. A camera on a shared
street records people who never agreed to anything, so the household has to confirm
before the device is accepted.

New cameras inherit the Adaptive Edge mode of the geofence they land in — register
one in Bryanston and it comes back HEIGHTENED at risk 66.

Members never see other households, individual claims, or anyone else's evidence.

### Fraud & claims
`Overview · Claims · Live AI · Evidence · Movement · Rewind · Patterns · Cameras`

All 15,712 claims, filterable by peril, item type, amount and free text. Click one
for a report.

The report is **computed, not generated**. Every figure traces to rows an
investigator can pull, which matters when a decision has to be defended. Findings are
graded INFO / WATCH / FLAG:

- amount against the suburb's own distribution
- amount against every claim of the same peril and item type nationally
- small-sample warning where the suburb has under 10 claims
- midnight-exact timestamps (962 in the book — usually unknown time at capture, not
  anything suspicious, and the report says so)
- claim clusters within 72 hours in the same suburb
- vehicle make frequency
- whether any participating camera could supply evidence

Then a priority and a recommendation. The disclaimer is on the page: this says where
a human should look, not whether a claim is fraudulent.

### Security partner
`Overview · Briefing · Patrol · Cameras`

Shift briefing per metro, day or night. Priority band, dominant peril, peak hours for
that shift, busiest days, and a recommended presence level per area. Coverage gaps
are called out separately — high-risk areas where no camera can supply evidence.

Partners can nominate their own patrol locations. A site inside a geofence inherits
its priority; beyond 3 km it carries no risk weight, because the claims data has
nothing to say about it.

Claim amounts, member identities and raw evidence are excluded from this role by
which endpoints it calls, not by hiding buttons.

---

## Movement trails

The Strava idea, built as **evidence trails**.

Each pattern with sightings on two or more cameras becomes a trail: its cameras
ordered in time, joined into a path. Segments used by more than one pattern
accumulate weight, and that aggregate is the corridor heat — thicker lines where
evidence recurs, bigger nodes where cameras see the most.

Selecting a trail draws it in cyan over the heat with numbered stops.

**It will be empty until patterns exist.** Confirm one under Patterns first — two
events of the same person or vehicle from nearby cameras, minutes apart.

Worth being precise about in the pitch: this shows where *evidence* recurs, not where
people go. It is built only from patterns the system already surfaced, and none of
them carries an identity.

---

## Endpoints

| Endpoint | Role |
|---|---|
| `GET /api/roles` | any — what each role may see |
| `POST /api/member/cameras` | member — register, consent-gated |
| `GET /api/member/summary?suburb=` | member — own area only |
| `GET /api/fraud/claims` | fraud — browse and filter |
| `GET /api/fraud/claims/{id}/report` | fraud — analytical report |
| `GET /api/fraud/movement` | fraud — trails, corridors, nodes |
| `GET /api/security/briefing` | security — shift briefing |
| `POST /api/security/sites` | security — nominate a location |

---

## Honest limits

**Role scope is not authentication.** Switching roles in the UI changes what the
front end requests; it does not stop anyone calling the endpoints directly. Real
enforcement needs Cognito with per-role authorisers on each route. Say that if
you're asked — claiming otherwise is the kind of thing a technical judge will
check.

**The report has no language model in it.** That's deliberate: an investigator can
trace every number. If you want narrative summaries, that layer slots in on top of
the structured findings — but the findings themselves should stay computed.

**Nominated security sites are in-memory.** They reset on restart. Fine for a demo,
needs a table for anything real.
