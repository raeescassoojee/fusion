from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sentinel_ops.models import Hotspot, Location, PatrolRequest, PeakWindow
from sentinel_ops.routing import optimise_patrol


OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = OPERATIONS_ROOT / "fixtures"


def _candidate_curated_dirs() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("SENTINEL_CLAIMS_CURATED_DIR")
    if configured:
        candidates.append(Path(configured))

    candidates.extend(
        [
            Path.cwd() / "services" / "claims" / "data" / "curated",
            OPERATIONS_ROOT.parent / "claims" / "data" / "curated",
            OPERATIONS_ROOT.parents[1] / "services" / "claims" / "data" / "curated",
        ]
    )

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def find_claims_file(filename: str) -> tuple[Path, bool]:
    for directory in _candidate_curated_dirs():
        candidate = directory / filename
        if candidate.exists():
            return candidate, True

    fallback = FIXTURES / f"cassoojee_{filename}"
    if fallback.exists():
        return fallback, False
    raise FileNotFoundError(
        f"Could not find {filename}. Merge the cassoojee branch or set "
        "SENTINEL_CLAIMS_CURATED_DIR."
    )


def _read_json(filename: str) -> tuple[Any, Path, bool]:
    path, live = find_claims_file(filename)
    return json.loads(path.read_text(encoding="utf-8")), path, live


def _to_hotspot(row: dict[str, Any], live: bool) -> Hotspot:
    peak = row.get("peak_window") or {}
    confidence = row.get("confidence") or {}
    saps_typed = row.get("saps_typed") or {}
    saps = row.get("saps") or {}

    operational_priority = row.get("blended_risk_v2")
    if operational_priority is None:
        operational_priority = row.get("blended_risk_score")
    if operational_priority is None:
        operational_priority = row.get("risk_score", 0)

    saps_score = saps_typed.get("saps_typed_score")
    if saps_score is None:
        saps_score = saps.get("saps_score")

    saps_year = saps_typed.get("year") or saps.get("theft_year")
    corroborated = saps_typed.get("corroborated")
    if corroborated is None:
        corroborated = saps.get("corroborated")

    return Hotspot(
        hotspot_id=row["hotspot_id"],
        name=row["name"],
        location=Location(
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
        ),
        risk_score=float(row.get("risk_score", operational_priority)),
        claims_risk_score=float(row.get("risk_score", operational_priority)),
        operational_priority=float(operational_priority),
        main_peril=row.get("main_peril"),
        peak_window=PeakWindow(
            days=[peak["day"]] if peak.get("day") else [],
            start=peak.get("start", "00:00"),
            end=peak.get("end", "23:59"),
        ),
        metro=row.get("metro"),
        confidence_score=confidence.get("score"),
        confidence_band=confidence.get("band"),
        claim_count=row.get("claim_count"),
        total_claim_value=row.get("total_claim_value"),
        primary_driver=row.get("primary_driver"),
        saps_context_score=float(saps_score) if saps_score is not None else None,
        saps_year=saps_year,
        saps_corroborated=corroborated,
        source="cassoojee-live" if live else "cassoojee-snapshot",
    )


def load_claims_hotspots(metro: str | None = None) -> tuple[list[Hotspot], dict[str, Any]]:
    rows, path, live = _read_json("hotspots.json")
    hotspots = [_to_hotspot(row, live) for row in rows]
    if metro:
        hotspots = [
            hotspot
            for hotspot in hotspots
            if (hotspot.metro or "").casefold() == metro.casefold()
        ]
    hotspots.sort(
        key=lambda item: item.operational_priority or item.risk_score,
        reverse=True,
    )
    metadata = {
        "path": str(path),
        "live_branch_output": live,
        "source": "cassoojee-live" if live else "cassoojee-snapshot",
        "note": (
            "SAPS values in the supplied claims output are historical corroboration, "
            "not a live SAPS incident feed."
        ),
    }
    return hotspots, metadata


def load_route_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    data, path, live = _read_json("route_inputs.json")
    return data, {
        "path": str(path),
        "live_branch_output": live,
        "source": "cassoojee-live" if live else "cassoojee-snapshot",
    }


def list_metros() -> list[str]:
    hotspots, _ = load_claims_hotspots()
    return sorted({hotspot.metro for hotspot in hotspots if hotspot.metro})


def build_metro_patrol(
    metro: str,
    *,
    fuel_l_per_100km: float = 10.0,
    max_stops: int | None = None,
):
    hotspots, metadata = load_claims_hotspots(metro)
    if not hotspots:
        raise ValueError(f"No hotspots found for metro: {metro}")

    route_inputs, _ = load_route_inputs()
    metro_input = (route_inputs.get("metros") or {}).get(metro, {})
    baseline = [stop["hotspot_id"] for stop in metro_input.get("stops", [])]
    depot_id = metro_input.get("depot")
    by_id = {hotspot.hotspot_id: hotspot for hotspot in hotspots}
    depot = by_id.get(depot_id) or hotspots[0]

    requested_stops = max_stops or len(hotspots)
    request = PatrolRequest(
        start=depot.location,
        hotspots=hotspots,
        baseline_order=baseline,
        max_stops=min(requested_stops, len(hotspots)),
        fuel_l_per_100km=fuel_l_per_100km,
        return_to_start=True,
    )
    comparison = optimise_patrol(request)
    return comparison, hotspots, depot, metadata


def claims_summary(hotspots: list[Hotspot]) -> dict[str, Any]:
    total_value = sum(hotspot.total_claim_value or 0 for hotspot in hotspots)
    total_claims = sum(hotspot.claim_count or 0 for hotspot in hotspots)
    top = hotspots[0] if hotspots else None
    return {
        "hotspot_count": len(hotspots),
        "claim_count": total_claims,
        "total_claim_value": round(total_value, 2),
        "top_hotspot": top.name if top else None,
        "top_operational_priority": (
            round(top.operational_priority or top.risk_score, 1) if top else None
        ),
    }
