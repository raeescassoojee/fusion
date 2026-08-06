# Merge fix report — sentinel-merged-fixed

Base: `sentinel-merged-final__1_.zip`
Sources compared: `sentinel-merged-abbasi.zip` (backend/AI) and `sentinel-merged.zip` (frontend/teammate)

## Merge audit result

All 112 source files present. No file was lost. Provenance:

| Outcome | Count |
|---|---|
| Identical in both sources | 68 |
| Abbasi-only file | 13 |
| Abbasi version taken where they differed | 25 |
| Hand-merged | 6 |
| Teammate version taken | 0 |

Frontend/backend API contract verified: every `/api/...` call in `dashboard.html`
resolves to a registered FastAPI route. No broken contracts.

## Defects found and fixed

### 1. CRITICAL — `/api/security/dispatch/test-alert` returned a 500

`create_test_security_alert()` ran:

    SELECT origin_household FROM member_incidents WHERE incident_id=?

`origin_household` is not a column on `member_incidents`; it is derived in
`member_mesh._incident_payload()` by joining `member_cameras`. The query raised
`sqlite3.OperationalError: no such column` whenever an active member incident
existed — which is exactly the demo path the runbook recommends (detect a person
in My Property, then switch to Security).

Fixed by joining `member_cameras` on `origin_camera_id`, with a safe fallback.

### 2. MAJOR — the working WhatsApp Cloud API integration was lost

The teammate's `security_dispatch.py` (1777 lines) contained a real Meta WhatsApp
Cloud API sender. The merge took Abbasi's version (1352 lines), which replaced it
with a hard-coded "not configured" stub that always returned 503.

Four functions were silently dropped:

- `_whatsapp_configuration()`
- `_send_whatsapp_template()`
- `_persist_whatsapp_delivery()`
- `_auto_send_free_whatsapp()`

All four are restored, along with the `WHATSAPP_*` environment constants, the
`urllib` imports and optional `python-dotenv` loading. `/api/security/whatsapp/status`
now reports true configuration state, and `send-whatsapp` performs a real send when
credentials are present. Auto-send is wired back into dispatch creation.

The billing guard is preserved: automatic sending is permitted only for free service
text (`WHATSAPP_AUTO_SEND_FREE_ONLY`), never billable templates, with a daily cap.

With no credentials set the behaviour is unchanged and still truthful — notifications
stay in the local review queue and nothing is fabricated.

### 3. Lost regression tests restored

- `test_control_room_test_alert_creates_member_incident_and_dispatch`
- `test_test_alert_auto_sends_one_free_service_message`

Both were present in the teammate's build and absent from the merge. They have been
adapted to the merged backend's behaviour (Abbasi's `test-alert` requires an existing
incident or sighting rather than fabricating one — a deliberate design difference).

Suite now: **67 passing** (was 65).

## Verified working, no change needed

- `routing.py` — identical function set in both builds; Abbasi's version taken and is
  a superset (194 vs 159 lines). All routing endpoints present.
- Patrol comparison, `optimise_patrol`, `/api/routes/metro/{metro}` — all intact.
- Claims, rewind, evidence passport, trust policy, performance ledger — intact.

## Open decisions for the team

### A. Timezone assumption in `rewind.py` differs between builds

- Teammate: a naive timestamp is assumed **SAST (UTC+2)**
- Abbasi (current): a naive timestamp is assumed **UTC**, or the timezone of the first
  timezone-aware event

For a South African deployment the SAST assumption is arguably more correct. The
difference is only visible with genuinely mixed-awareness input; the demo data is
internally consistent, so both behave identically in practice. **Not changed** —
this needs a team decision, not a silent edit before finals.

### B. Household coordinates do not match the patrol road graph

- `member_mesh.py` demo households sit at roughly `-26.1812, 28.2900`
- `security_dispatch.py` `ROAD_NODES` are centred on Lakefield at roughly `-26.198, 28.310`

That is about 2.5 km apart, so patrol routes are computed against a road network that
does not line up with where the houses render. The teammate's build was internally
consistent (both on Killarney Avenue at `-26.1846, 28.2889`).

This is pre-existing in Abbasi's build, not introduced by the merge. **Not changed** —
fixing it means moving either the households or the road graph, which affects map
rendering and route previews and should not be done untested the night before finals.

## Also added

- `.github/workflows/ci.yml` at the repository root (GitHub Actions only reads the
  root `.github/workflows/`; the previous copy under `services/operations/` never ran)
- `.env.example` documenting the WhatsApp and AWS environment variables
- `python-dotenv` added to `services/operations` dependencies
