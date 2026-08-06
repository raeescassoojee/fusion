"""Evidence pattern registry — anonymous recurring-signature matching.

WHAT THIS STORES, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
It stores *signatures*: what a camera observed at a moment in time. Clothing
colours, carried items, a height band, vehicle colour/type/marks, a salted plate
token. It clusters signatures that plausibly recur into *patterns*.

A pattern is not a person. It has no name, no ID number, no face gallery entry and
no "suspect" flag. Under POPIA s26 information about alleged criminal behaviour is
special personal information; attributing a pattern to an identified individual is
SAPS's job under their legal authority, not this system's. A pattern therefore
carries at most a `saps_reference` — a pointer outward, never an identity inward.

Every relationship is POSSIBLE_*. Confirmation requires a human review that is
recorded with a reason. Dismissals are kept, because a registry that only remembers
its hits is a registry that is lying to you about its accuracy.

Backends: DynamoDB when boto3 and credentials are present, otherwise an equivalent
local SQLite store so the whole thing runs and demos without an AWS account.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

# --------------------------------------------------------------------------- config
TABLE_SIGNATURES = os.environ.get("SENTINEL_TABLE_SIGNATURES", "sentinel-signatures")
TABLE_PATTERNS = os.environ.get("SENTINEL_TABLE_PATTERNS", "sentinel-patterns")
TABLE_REVIEWS = os.environ.get("SENTINEL_TABLE_REVIEWS", "sentinel-reviews")
AWS_REGION = os.environ.get("AWS_REGION", "af-south-1")
LOCAL_DB = os.environ.get("SENTINEL_LOCAL_DB", "sentinel_patterns.sqlite3")

# Signatures are temporary by default. A retention decision is a deliberate act.
SIGNATURE_TTL_DAYS = int(os.environ.get("SENTINEL_SIGNATURE_TTL_DAYS", "30"))

# Nothing is auto-confirmed. This is the floor for even *offering* a candidate.
CANDIDATE_FLOOR = 45.0
STRONG_FLOOR = 70.0

# A person cannot travel faster than this between two cameras on foot or by car.
MAX_PLAUSIBLE_KMH = 120.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def haversine_km(a: dict, b: dict) -> float:
    R = 6371.0
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- models
@dataclass
class Signature:
    """One observation, from one camera, at one moment."""

    signature_id: str
    event_id: str
    camera_id: str
    observed_at: str
    kind: Literal["APPEARANCE", "VEHICLE"]
    cues: dict[str, Any]
    location: dict[str, float]
    geofence_id: str | None = None
    camera_trust: float = 50.0
    quality: float = 0.5
    expires_at: str | None = None
    pattern_id: str | None = None

    @staticmethod
    def bucket_for(kind: str, geofence_id: str | None, observed_at: str) -> str:
        """Retrieval bucket: cheap candidate narrowing without a vector database."""
        day = str(observed_at)[:10]
        return f"{kind}#{geofence_id or 'NOGEO'}#{day}"

    @property
    def bucket(self) -> str:
        return self.bucket_for(self.kind, self.geofence_id, self.observed_at)


@dataclass
class Pattern:
    """A cluster of signatures that plausibly recur. Never an identity."""

    pattern_id: str
    kind: str
    status: Literal["CANDIDATE", "CONFIRMED_BY_REVIEW", "DISMISSED"] = "CANDIDATE"
    signature_ids: list[str] = field(default_factory=list)
    camera_ids: list[str] = field(default_factory=list)
    geofence_ids: list[str] = field(default_factory=list)
    cue_profile: dict[str, Any] = field(default_factory=dict)
    first_seen: str | None = None
    last_seen: str | None = None
    occurrence_count: int = 0
    best_score: float = 0.0
    claim_refs: list[str] = field(default_factory=list)
    saps_reference: str | None = None
    review_count: int = 0

    def describe(self) -> str:
        """Plain description of what was seen — observations only, never inference."""
        c = self.cue_profile
        if self.kind == "VEHICLE":
            bits = [c.get("colour"), c.get("body_type")]
            if c.get("marks"):
                bits.append(c["marks"])
            if c.get("plate_partial"):
                bits.append(f"plate {c['plate_partial']}")
            return " · ".join(b for b in bits if b) or "vehicle, few distinguishing cues"
        bits = []
        if c.get("upper_colour"):
            bits.append(f"{c['upper_colour']} upper")
        if c.get("lower_colour"):
            bits.append(f"{c['lower_colour']} lower")
        if c.get("headwear"):
            bits.append(c["headwear"])
        if c.get("carried"):
            bits.append(c["carried"])
        if c.get("height_low_m") and c.get("height_high_m"):
            bits.append(f"{c['height_low_m']:.2f}-{c['height_high_m']:.2f} m")
        return " · ".join(bits) or "person, few distinguishing cues"


@dataclass
class MatchResult:
    signature_id: str
    candidate_signature_id: str
    score: float
    relationship: str
    components: dict[str, float]
    reasons: list[str]
    journey_km: float
    journey_plausible: bool
    human_review_required: bool = True


# --------------------------------------------------------------------------- scoring
def _overlap(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float:
    """Fraction of overlap between two height bands. Bands, not points, on purpose."""
    if not a or not b or None in a or None in b:
        return 0.0
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return 0.0
    return (hi - lo) / max(a[1] - a[0], b[1] - b[0], 1e-6)


def score_pair(a: Signature, b: Signature) -> MatchResult:
    """Multi-cue comparison. No single cue can carry the result on its own."""
    comps: dict[str, float] = {}
    reasons: list[str] = []
    ac, bc = a.cues, b.cues

    if a.kind != b.kind:
        raise ValueError("cannot compare signatures of different kinds")

    if a.kind == "VEHICLE":
        pa, pb = (ac.get("plate_token") or ""), (bc.get("plate_token") or "")
        ta, tb = (ac.get("plate_partial") or ""), (bc.get("plate_partial") or "")
        if pa and pb and pa == pb:
            comps["plate"] = 40.0
            reasons.append("Salted plate token matches exactly")
        elif ta and tb:
            same = sum(1 for x, y in zip(ta, tb) if x == y and x != "*")
            denom = max(len(ta), len(tb), 1)
            comps["plate"] = round(36.0 * same / denom, 1)
            reasons.append(f"Partial plate agreement on {same}/{denom} characters")
        else:
            comps["plate"] = 0.0
            reasons.append("One or both plates unavailable — vehicle rests on weaker cues")

        comps["colour"] = 15.0 if ac.get("colour") and ac.get("colour") == bc.get("colour") else 0.0
        if comps["colour"]:
            reasons.append(f"Vehicle colour agrees ({ac['colour']})")
        comps["body"] = 13.0 if ac.get("body_type") and ac.get("body_type") == bc.get("body_type") else 0.0
        if comps["body"]:
            reasons.append(f"Body type agrees ({ac['body_type']})")
        comps["marks"] = 17.0 if ac.get("marks") and ac.get("marks") == bc.get("marks") else 0.0
        if comps["marks"]:
            reasons.append(f"Distinctive feature agrees ({ac['marks']})")
    else:
        comps["upper"] = 16.0 if ac.get("upper_colour") and ac.get("upper_colour") == bc.get("upper_colour") else 0.0
        if comps["upper"]:
            reasons.append(f"Upper clothing colour agrees ({ac['upper_colour']})")
        comps["lower"] = 11.0 if ac.get("lower_colour") and ac.get("lower_colour") == bc.get("lower_colour") else 0.0
        if comps["lower"]:
            reasons.append(f"Lower clothing colour agrees ({ac['lower_colour']})")
        comps["headwear"] = 14.0 if ac.get("headwear") and ac.get("headwear") == bc.get("headwear") else 0.0
        if comps["headwear"]:
            reasons.append(f"Headwear agrees ({ac['headwear']})")
        comps["carried"] = 14.0 if ac.get("carried") and ac.get("carried") == bc.get("carried") else 0.0
        if comps["carried"]:
            reasons.append(f"Carried item agrees ({ac['carried']})")

        band_a = (ac.get("height_low_m"), ac.get("height_high_m"))
        band_b = (bc.get("height_low_m"), bc.get("height_high_m"))
        ov = _overlap(band_a, band_b)
        comps["height"] = round(27.0 * ov, 1)
        if ov > 0.5:
            reasons.append(
                f"Height bands overlap {ov * 100:.0f}% "
                f"({band_a[0]:.2f}-{band_a[1]:.2f} m vs {band_b[0]:.2f}-{band_b[1]:.2f} m)"
            )
        elif band_a[0] and band_b[0]:
            reasons.append("Height bands do not overlap — argues against a match")

    # Journey plausibility. This is a hard gate, not a scoring term.
    km = haversine_km(a.location, b.location)
    minutes = abs(
        (datetime.fromisoformat(b.observed_at) - datetime.fromisoformat(a.observed_at)).total_seconds()
    ) / 60.0
    if minutes <= 0.01:
        plausible = km <= 0.05
        speed = float("inf") if km > 0.05 else 0.0
    else:
        speed = km / (minutes / 60.0)
        plausible = speed <= MAX_PLAUSIBLE_KMH
    comps["journey"] = (15.0 if a.kind == "VEHICLE" else 18.0) if plausible else 0.0
    reasons.append(
        f"Journey plausible — {km:.2f} km in {minutes:.0f} min ({speed:.0f} km/h)"
        if plausible
        else f"Journey IMPLAUSIBLE — {km:.2f} km in {minutes:.0f} min ({speed:.0f} km/h); candidate rejected"
    )

    raw = sum(comps.values())  # a perfect match on every cue sums to 100
    trust = min(a.camera_trust, b.camera_trust)
    quality = min(a.quality, b.quality)
    # Weak cameras cannot produce strong evidence, however well the cues line up.
    # Trust dominates; frame quality trims. One multiplier, so they cannot compound
    # into a penalty that makes the strong threshold unreachable.
    confidence = 0.55 + 0.45 * (0.7 * (trust / 100.0) + 0.3 * quality)
    score = raw * confidence
    if not plausible:
        score = 0.0
    score = round(max(0.0, min(100.0, score)), 1)

    distinct = sum(1 for k, v in comps.items() if k != "journey" and v > 0)
    if distinct < 2 and score > 0:
        score = round(score * 0.55, 1)
        reasons.append("Only one cue agrees — score reduced; a single cue is not a pattern")

    if score >= STRONG_FLOOR:
        rel = "POSSIBLE_SAME_VEHICLE" if a.kind == "VEHICLE" else "POSSIBLE_SAME_APPEARANCE"
    elif score >= CANDIDATE_FLOOR:
        rel = "WEAK_CANDIDATE"
    else:
        rel = "NO_LINK"

    reasons.append(f"Camera trust floor {trust:.0f}/100 applied to the raw cue total")
    return MatchResult(
        signature_id=a.signature_id,
        candidate_signature_id=b.signature_id,
        score=score,
        relationship=rel,
        components={k: round(v, 1) for k, v in comps.items()},
        reasons=reasons,
        journey_km=round(km, 3),
        journey_plausible=plausible,
    )


# --------------------------------------------------------------------------- storage
class LocalStore:
    """SQLite mirror of the DynamoDB schema so the system runs without AWS."""

    def __init__(self, path: str = LOCAL_DB):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signatures(
              signature_id TEXT PRIMARY KEY, bucket TEXT, kind TEXT,
              plate_token TEXT, body TEXT, observed_at TEXT);
            CREATE INDEX IF NOT EXISTS ix_sig_bucket ON signatures(bucket);
            CREATE INDEX IF NOT EXISTS ix_sig_plate ON signatures(plate_token);
            CREATE TABLE IF NOT EXISTS patterns(
              pattern_id TEXT PRIMARY KEY, status TEXT, kind TEXT, body TEXT, last_seen TEXT);
            CREATE INDEX IF NOT EXISTS ix_pat_status ON patterns(status);
            CREATE TABLE IF NOT EXISTS reviews(
              review_id TEXT PRIMARY KEY, pattern_id TEXT, body TEXT, created_at TEXT);
            """
        )
        self.conn.commit()

    def put_signature(self, s: Signature) -> None:
        self.conn.execute(
            "REPLACE INTO signatures VALUES(?,?,?,?,?,?)",
            (s.signature_id, s.bucket, s.kind, s.cues.get("plate_token"),
             json.dumps(asdict(s)), s.observed_at),
        )
        self.conn.commit()

    def signatures_in_buckets(self, buckets: list[str]) -> list[Signature]:
        if not buckets:
            return []
        q = ",".join("?" * len(buckets))
        rows = self.conn.execute(
            f"SELECT body FROM signatures WHERE bucket IN ({q})", buckets
        ).fetchall()
        return [Signature(**json.loads(r["body"])) for r in rows]

    def signatures_by_plate(self, token: str) -> list[Signature]:
        rows = self.conn.execute(
            "SELECT body FROM signatures WHERE plate_token=?", (token,)
        ).fetchall()
        return [Signature(**json.loads(r["body"])) for r in rows]

    def put_pattern(self, p: Pattern) -> None:
        self.conn.execute(
            "REPLACE INTO patterns VALUES(?,?,?,?,?)",
            (p.pattern_id, p.status, p.kind, json.dumps(asdict(p)), p.last_seen),
        )
        self.conn.commit()

    def get_pattern(self, pid: str) -> Pattern | None:
        row = self.conn.execute("SELECT body FROM patterns WHERE pattern_id=?", (pid,)).fetchone()
        return Pattern(**json.loads(row["body"])) if row else None

    def list_patterns(self, status: str | None = None) -> list[Pattern]:
        if status:
            rows = self.conn.execute(
                "SELECT body FROM patterns WHERE status=? ORDER BY last_seen DESC", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT body FROM patterns ORDER BY last_seen DESC").fetchall()
        return [Pattern(**json.loads(r["body"])) for r in rows]

    def put_review(self, review: dict) -> None:
        self.conn.execute(
            "REPLACE INTO reviews VALUES(?,?,?,?)",
            (review["review_id"], review["pattern_id"], json.dumps(review), review["created_at"]),
        )
        self.conn.commit()

    def list_reviews(self, pattern_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT body FROM reviews WHERE pattern_id=? ORDER BY created_at", (pattern_id,)
        ).fetchall()
        return [json.loads(r["body"]) for r in rows]


class DynamoStore:
    """DynamoDB backend. Same interface as LocalStore."""

    def __init__(self, region: str = AWS_REGION):
        import boto3  # imported lazily so the module works without AWS installed

        self.ddb = boto3.resource("dynamodb", region_name=region)
        self.sig = self.ddb.Table(TABLE_SIGNATURES)
        self.pat = self.ddb.Table(TABLE_PATTERNS)
        self.rev = self.ddb.Table(TABLE_REVIEWS)

    @staticmethod
    def _clean(d: dict) -> dict:
        """DynamoDB rejects float; store numbers as strings where precision matters."""
        from decimal import Decimal

        def conv(v):
            if isinstance(v, float):
                return Decimal(str(round(v, 6)))
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items() if x is not None}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v

        return {k: conv(v) for k, v in d.items() if v is not None}

    def put_signature(self, s: Signature) -> None:
        item = self._clean(asdict(s))
        item["bucket"] = s.bucket
        if s.expires_at:
            item["ttl"] = int(datetime.fromisoformat(s.expires_at).timestamp())
        self.sig.put_item(Item=item)

    def signatures_in_buckets(self, buckets: list[str]) -> list[Signature]:
        from boto3.dynamodb.conditions import Key

        out: list[Signature] = []
        for b in buckets:
            resp = self.sig.query(IndexName="bucket-index", KeyConditionExpression=Key("bucket").eq(b))
            out.extend(_sig_from_item(i) for i in resp.get("Items", []))
        return out

    def signatures_by_plate(self, token: str) -> list[Signature]:
        from boto3.dynamodb.conditions import Key

        resp = self.sig.query(
            IndexName="plate-index",
            KeyConditionExpression=Key("plate_token").eq(token),
        )
        return [_sig_from_item(i) for i in resp.get("Items", [])]

    def put_pattern(self, p: Pattern) -> None:
        self.pat.put_item(Item=self._clean(asdict(p)))

    def get_pattern(self, pid: str) -> Pattern | None:
        item = self.pat.get_item(Key={"pattern_id": pid}).get("Item")
        return _pat_from_item(item) if item else None

    def list_patterns(self, status: str | None = None) -> list[Pattern]:
        from boto3.dynamodb.conditions import Key

        if status:
            resp = self.pat.query(
                IndexName="status-index", KeyConditionExpression=Key("status").eq(status),
                ScanIndexForward=False,
            )
        else:
            resp = self.pat.scan()
        return [_pat_from_item(i) for i in resp.get("Items", [])]

    def put_review(self, review: dict) -> None:
        self.rev.put_item(Item=self._clean(review))

    def list_reviews(self, pattern_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        resp = self.rev.query(
            IndexName="pattern-index", KeyConditionExpression=Key("pattern_id").eq(pattern_id)
        )
        return resp.get("Items", [])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _sig_from_item(item: dict) -> Signature:
    item = dict(item)
    item.pop("bucket", None)
    item.pop("ttl", None)
    item["camera_trust"] = _num(item.get("camera_trust", 50))
    item["quality"] = _num(item.get("quality", 0.5))
    item["location"] = {k: _num(v) for k, v in (item.get("location") or {}).items()}
    item["cues"] = {k: _num(v) if k.endswith("_m") else v for k, v in (item.get("cues") or {}).items()}
    return Signature(**item)


def _pat_from_item(item: dict) -> Pattern:
    item = dict(item)
    item["best_score"] = _num(item.get("best_score", 0))
    item["occurrence_count"] = int(_num(item.get("occurrence_count", 0)))
    item["review_count"] = int(_num(item.get("review_count", 0)))
    prof = item.get("cue_profile") or {}
    item["cue_profile"] = {k: _num(v) if k.endswith("_m") else v for k, v in prof.items()}
    return Pattern(**item)


def get_store(prefer_aws: bool = True):
    """DynamoDB when it is genuinely reachable, otherwise the local mirror."""
    if prefer_aws and os.environ.get("SENTINEL_FORCE_LOCAL") != "1":
        try:
            store = DynamoStore()
            store.sig.table_status  # a real call; fails fast without creds or tables
            return store
        except Exception:
            pass
    return LocalStore()


# --------------------------------------------------------------------------- registry
class PatternRegistry:
    """Ingest signatures, retrieve candidates, cluster recurrences, record reviews."""

    def __init__(self, store=None):
        self.store = store or get_store()

    # ---- ingest ----
    def signature_from_event(self, event: dict, height: dict | None = None,
                             geofence_id: str | None = None) -> list[Signature]:
        """Turn one camera event into the signatures it can honestly support."""
        out: list[Signature] = []
        observed = event.get("timestamp") or _iso(_now())
        loc = event.get("location") or {}
        trust = float(event.get("camera_trust_score", 50))
        qm = event.get("quality_metrics") or {}
        quality = (sum(float(v) for v in qm.values()) / len(qm) / 100.0) if qm else 0.5
        expires = _iso(_now() + timedelta(days=SIGNATURE_TTL_DAYS))

        app = event.get("appearance") or {}
        if app.get("person_box") or (app.get("upper_colour") not in (None, "Unknown")):
            cues: dict[str, Any] = {
                "upper_colour": _none_if_unknown(app.get("upper_colour")),
                "lower_colour": _none_if_unknown(app.get("lower_colour")),
                "headwear": app.get("cap"),
                "carried": "backpack" if app.get("backpack") else None,
            }
            if height and height.get("height_low_m"):
                cues["height_low_m"] = float(height["height_low_m"])
                cues["height_high_m"] = float(height["height_high_m"])
                cues["height_quality"] = float(height.get("quality", 0))
            out.append(Signature(
                signature_id="SIG-" + uuid.uuid4().hex[:12].upper(),
                event_id=event["event_id"], camera_id=event["camera_id"],
                observed_at=observed, kind="APPEARANCE",
                cues={k: v for k, v in cues.items() if v is not None},
                location=loc, geofence_id=geofence_id,
                camera_trust=trust, quality=quality, expires_at=expires,
            ))

        veh = event.get("vehicle") or {}
        plate = event.get("plate") or {}
        if (veh.get("type") not in (None, "Unknown")) or plate.get("text"):
            token = None
            partial = None
            if plate.get("text"):
                import hashlib
                salt = os.environ.get("SENTINEL_PLATE_SALT", "pilot-salt")
                clean = str(plate["text"]).replace(" ", "").upper()
                token = "sha256:" + hashlib.sha256((salt + clean).encode()).hexdigest()[:24]
                partial = clean[:4] + "*" * max(0, len(clean) - 4)
            cues = {
                "colour": _none_if_unknown(veh.get("colour")),
                "body_type": _none_if_unknown(veh.get("type")),
                "marks": veh.get("marks"),
                "plate_token": token,
                "plate_partial": partial,
            }
            out.append(Signature(
                signature_id="SIG-" + uuid.uuid4().hex[:12].upper(),
                event_id=event["event_id"], camera_id=event["camera_id"],
                observed_at=observed, kind="VEHICLE",
                cues={k: v for k, v in cues.items() if v is not None},
                location=loc, geofence_id=geofence_id,
                camera_trust=trust, quality=quality, expires_at=expires,
            ))
        return out

    # ---- retrieve ----
    def candidates_for(self, sig: Signature, days_back: int = 30,
                       nearby_geofences: Iterable[str] = ()) -> list[Signature]:
        """Narrow before scoring. Plate token first (cheap and precise), then buckets."""
        seen: dict[str, Signature] = {}
        token = sig.cues.get("plate_token")
        if token:
            for s in self.store.signatures_by_plate(token):
                if s.signature_id != sig.signature_id:
                    seen[s.signature_id] = s

        day = datetime.fromisoformat(sig.observed_at)
        geos = {sig.geofence_id, *nearby_geofences}
        buckets = [
            Signature.bucket_for(sig.kind, g, _iso(day - timedelta(days=d)))
            for d in range(days_back)
            for g in geos
        ]
        for s in self.store.signatures_in_buckets(buckets):
            if s.signature_id != sig.signature_id:
                seen[s.signature_id] = s
        return list(seen.values())

    def match(self, sig: Signature, days_back: int = 30,
              nearby_geofences: Iterable[str] = (), top_k: int = 5) -> list[MatchResult]:
        """Score candidates and return the best few. Never a yes/no."""
        results = []
        for cand in self.candidates_for(sig, days_back, nearby_geofences):
            try:
                r = score_pair(sig, cand)
            except ValueError:
                continue
            if r.score >= CANDIDATE_FLOOR:
                results.append(r)
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # ---- cluster ----
    def register(self, sig: Signature, matches: list[MatchResult]) -> Pattern | None:
        """Attach a signature to an existing pattern, or open a new candidate pattern."""
        self.store.put_signature(sig)
        strong = [m for m in matches if m.score >= STRONG_FLOOR]
        if not strong:
            return None

        best = strong[0]
        existing = None
        matched_sig = None
        for cand_sig in self.candidates_for(sig):
            if cand_sig.signature_id == best.candidate_signature_id:
                matched_sig = cand_sig
                if cand_sig.pattern_id:
                    existing = self.store.get_pattern(cand_sig.pattern_id)
                break

        if existing is None:
            # A new pattern spans BOTH sightings, so it must carry both cameras and
            # both geofences. Recording only the newer one leaves the pattern looking
            # single-camera, which silently breaks movement reconstruction.
            cams = [sig.camera_id]
            geos = [g for g in [sig.geofence_id] if g]
            if matched_sig:
                if matched_sig.camera_id not in cams:
                    cams.insert(0, matched_sig.camera_id)
                if matched_sig.geofence_id and matched_sig.geofence_id not in geos:
                    geos.insert(0, matched_sig.geofence_id)
            pattern = Pattern(
                pattern_id="PAT-" + uuid.uuid4().hex[:10].upper(),
                kind=sig.kind,
                signature_ids=[best.candidate_signature_id, sig.signature_id],
                camera_ids=cams,
                geofence_ids=geos,
                cue_profile=dict(sig.cues),
                first_seen=min(matched_sig.observed_at, sig.observed_at) if matched_sig else sig.observed_at,
                last_seen=max(matched_sig.observed_at, sig.observed_at) if matched_sig else sig.observed_at,
                occurrence_count=2,
                best_score=best.score,
            )
        else:
            pattern = existing
            if sig.signature_id not in pattern.signature_ids:
                pattern.signature_ids.append(sig.signature_id)
                pattern.occurrence_count = len(pattern.signature_ids)
            if sig.camera_id not in pattern.camera_ids:
                pattern.camera_ids.append(sig.camera_id)
            if sig.geofence_id and sig.geofence_id not in pattern.geofence_ids:
                pattern.geofence_ids.append(sig.geofence_id)
            pattern.last_seen = max(pattern.last_seen or "", sig.observed_at)
            pattern.best_score = max(pattern.best_score, best.score)
            # Keep only cues that still hold across every sighting.
            pattern.cue_profile = {
                k: v for k, v in pattern.cue_profile.items()
                if k.endswith("_m") or sig.cues.get(k) == v
            }

        # Stamp BOTH signatures with the pattern id. Without this the earlier sighting
        # stays unlinked, so a third sighting that matches it opens yet another pattern
        # and the cluster fragments into pairs instead of accumulating.
        sig.pattern_id = pattern.pattern_id
        self.store.put_signature(sig)
        if matched_sig and matched_sig.pattern_id != pattern.pattern_id:
            matched_sig.pattern_id = pattern.pattern_id
            self.store.put_signature(matched_sig)
        self.store.put_pattern(pattern)
        return pattern

    # ---- review ----
    def review(self, pattern_id: str, decision: Literal["CONFIRMED_BY_REVIEW", "DISMISSED"],
               reason: str, reviewer: str, saps_reference: str | None = None,
               claim_ref: str | None = None) -> Pattern:
        """Record a human decision. Required before a pattern means anything."""
        if not reason.strip():
            raise ValueError("a written reason is required for every review decision")
        pattern = self.store.get_pattern(pattern_id)
        if pattern is None:
            raise KeyError(f"unknown pattern {pattern_id}")
        pattern.status = decision
        pattern.review_count += 1
        if saps_reference:
            pattern.saps_reference = saps_reference
        if claim_ref and claim_ref not in pattern.claim_refs:
            pattern.claim_refs.append(claim_ref)
        self.store.put_pattern(pattern)
        self.store.put_review({
            "review_id": "REV-" + uuid.uuid4().hex[:10].upper(),
            "pattern_id": pattern_id,
            "decision": decision,
            "reason": reason,
            "reviewer": reviewer,
            "saps_reference": saps_reference,
            "created_at": _iso(_now()),
        })
        return pattern

    def stats(self) -> dict:
        pats = self.store.list_patterns()
        confirmed = [p for p in pats if p.status == "CONFIRMED_BY_REVIEW"]
        dismissed = [p for p in pats if p.status == "DISMISSED"]
        reviewed = len(confirmed) + len(dismissed)
        return {
            "patterns": len(pats),
            "candidates": len([p for p in pats if p.status == "CANDIDATE"]),
            "confirmed": len(confirmed),
            "dismissed": len(dismissed),
            "false_positive_rate": round(100 * len(dismissed) / reviewed, 1) if reviewed else None,
            "backend": type(self.store).__name__,
        }


def _none_if_unknown(v):
    return None if v in (None, "", "Unknown", "unknown") else v
