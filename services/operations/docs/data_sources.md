# External data and partnership plan

## Public sources

### SAPS crime statistics

Official source: https://www.saps.gov.za/services/crimestats.php

Use station/precinct-level counts for burglary at residential premises, robbery at residential premises, theft of motor vehicles, theft from motor vehicles and carjacking. This is broad context, not exact incident-location evidence.

### Statistics South Africa

Official ward-level small-area population product: https://www.statssa.gov.za/?p=18967

Use population or household exposure so a high-volume suburb is not automatically treated as high-risk simply because more people or policies are present.

## Partnership feeds

- Vumacam: camera/LPR events, camera health and authorised evidence references.
- Vision Tactical: patrol, dispatch and response outcomes.
- Tracking/recovery partners: vehicle-of-interest and recovery outcomes.

These are represented by a neutral JSON adapter. Do not scrape private feeds or claim access without an agreement and credentials.

## Neutral event contract

```json
{
  "event_id": "PARTNER-ID",
  "camera_id": "CAMERA-ID",
  "timestamp": "ISO-8601",
  "location": {"latitude": -26.1, "longitude": 28.05},
  "plate": {"text": "AB12CDGP", "confidence": 0.91},
  "vehicle": {"colour": "white", "type": "sedan"},
  "camera_trust_score": 82,
  "media_url": "authorised-time-limited-reference",
  "source": "partner-name"
}
```

## Privacy boundary

Similarity is a retrieval clue, not a guilt verdict. Use synthetic or consented media, blur unmatched faces, require human review, keep access logs and retain only necessary evidence.
