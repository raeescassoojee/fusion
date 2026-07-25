# Live activity feed + Rekognition

All files go to `services\operations\src\sentinel_ops\`.

| File | Change |
|---|---|
| `activity.py` | **new** — ring buffer of datastore writes |
| `rekognition.py` | **new** — Amazon Rekognition DetectFaces |
| `main.py` | overwrite — adds `GET /api/activity` |
| `patterns.py` | overwrite — logs the 3 DynamoDB puts |
| `storage.py` | overwrite — logs local SQLite writes |
| `camera_upload.py` | overwrite — adds the Rekognition endpoint |

9 ops tests still pass after all six.

---

## GET /api/activity

```json
{
  "entries": [
    { "seq": 11, "at": "2026-07-25T19:46:15Z", "action": "PUT_ITEM",
      "backend": "dynamodb", "target": "sentinel-signatures",
      "detail": "signature written", "status": "ok", "latency_ms": 24.3 }
  ],
  "latest_seq": 13,
  "buffered": 6,
  "totals_by_backend": { "dynamodb": 4, "sqlite": 9 }
}
```

Poll with `?since=<last latest_seq>` to get only new rows.

`backend` is the field that matters on screen. With AWS configured, pattern
writes show `dynamodb` and the table names are the real ones a judge can find in
the console. Without AWS they show `sqlite` — the feed still works, it just
proves the fallback.

Verified live:

```
19:46:15  PUT_ITEM  sqlite  sentinel-events   event written
19:46:15  PUT_ITEM  sqlite  sentinel-alerts   alert written
after seed+upload -> latest_seq 13 | by backend: {'sqlite': 13}
```

### Frontend snippet

```js
let lastSeq = 0;
setInterval(async () => {
  const r = await fetch(`/api/activity?since=${lastSeq}`);
  const d = await r.json();
  lastSeq = d.latest_seq;
  for (const e of d.entries) {
    const row = document.createElement("div");
    row.className = `log-row ${e.backend}`;
    row.textContent =
      `${e.at.slice(11,19)}  ${e.action}  ${e.backend}  ${e.target}  ${e.detail}`
      + (e.latency_ms ? `  ${e.latency_ms}ms` : "");
    feed.prepend(row);
  }
  while (feed.children.length > 60) feed.lastChild.remove();
}, 1000);
```

Use `textContent`, not `innerHTML`. Colour-code `dynamodb` differently from
`sqlite` so the AWS rows stand out.

Put the feed beside the camera panel. When a clip is uploaded the viewer sees
detection finish and rows land in the same second.

---

## POST /api/vision/rekognition/{event_id}

Runs DetectFaces on the stored evidence crop.

```json
{ "available": true, "service": "amazon-rekognition", "operation": "DetectFaces",
  "face_count": 1, "top_confidence": 99.43, "request_id": "a1b2c3d4",
  "note": "Face presence and quality only. Not an identity match." }
```

Returns `{"available": false, "reason": ...}` when AWS is off. Never raises.

---

## Deployment

**You are not deployed.** Storage is on AWS; compute is on your laptop.

If judges watch your screen, deployment adds nothing they can see. Proper
ECS/App Runner deployment is 3+ hours and would put a working system at risk the
night before.

If a public URL is genuinely required, the cheapest honest option is one EC2
instance running all three processes behind nginx — roughly 90 minutes, and it
still needs redeploying on every change.

### Keeping it current while your teammate edits the UI

You do not need to redeploy anything. Both dashboards are static files served
from disk:

- Ops dashboard: `services/operations/static/dashboard.html`
- Chat: `services/chat/client.html`

Uvicorn runs with `--reload`, so Python changes restart automatically. HTML
changes need only a browser refresh (Ctrl+F5 to skip cache). Your teammate saves
the file, you refresh, you see it.

Git is the coordination tool, not a deploy pipeline:

```powershell
git add -A ; git commit -m "activity feed" ; git push
# teammate:
git pull
```

Work on a branch and merge when the tests pass.

---

## What fits in two hours

1. Copy these six files, verify the feed — **15 min**
2. Teammate renders the feed with the snippet — **30 min**
3. Rekognition in the UI, showing AWS corroborating the local result — **20 min**
4. Re-run tests, full flow twice with reset — **20 min**
5. Rehearse — **the rest**

That is a full two hours. Do not add a seventh thing.

## The line for judges

> *"Evidence is written to a private encrypted S3 bucket with 30-day expiry, and
> the evidence graph writes to DynamoDB — signatures carry a TTL so they
> self-delete, and the reviews table has point-in-time recovery as an audit
> record. The feed on the right is those writes, live. Compute runs locally so
> the demo is reproducible offline."*

Every clause is checkable in your AWS console.
