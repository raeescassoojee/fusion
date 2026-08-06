from __future__ import annotations

from sentinel_ops.geo import haversine_km
from sentinel_ops.models import (
    Hotspot,
    Location,
    PatrolComparison,
    PatrolRequest,
    RouteMetrics,
)


def _distance(
    start: Location,
    by_id: dict[str, Hotspot],
    order: list[str],
    return_to_start: bool,
) -> float:
    total, current = 0.0, start
    for hotspot_id in order:
        target = by_id[hotspot_id].location
        total += haversine_km(current, target)
        current = target
    if order and return_to_start:
        total += haversine_km(current, start)
    return total


def _metrics(request: PatrolRequest, order: list[str]) -> RouteMetrics:
    by_id = {hotspot.hotspot_id: hotspot for hotspot in request.hotspots}
    distance = _distance(request.start, by_id, order, request.return_to_start)
    risk = sum(
        (
            by_id[hotspot_id].operational_priority
            if by_id[hotspot_id].operational_priority is not None
            else by_id[hotspot_id].risk_score
        )
        for hotspot_id in order
    )
    total_risk = sum(
        (
            hotspot.operational_priority
            if hotspot.operational_priority is not None
            else hotspot.risk_score
        )
        for hotspot in request.hotspots
    )
    # "Peak risk" is the operationally high-priority band used elsewhere in the
    # security workspace. If a small fixture has no score above 60, use its
    # single highest-priority hotspot so the metric remains meaningful.
    priorities = {
        hotspot.hotspot_id: (
            hotspot.operational_priority
            if hotspot.operational_priority is not None
            else hotspot.risk_score
        )
        for hotspot in request.hotspots
    }
    peak_ids = {hotspot_id for hotspot_id, score in priorities.items() if score >= 60}
    if not peak_ids and priorities:
        peak_ids = {max(priorities, key=priorities.get)}
    peak_total = sum(priorities[hotspot_id] for hotspot_id in peak_ids)
    covered_peak_ids = peak_ids.intersection(order)
    peak_covered = sum(priorities[hotspot_id] for hotspot_id in covered_peak_ids)
    return RouteMetrics(
        ordered_hotspot_ids=order,
        distance_km=round(distance, 3),
        estimated_fuel_litres=round(
            distance * request.fuel_l_per_100km / 100,
            3,
        ),
        risk_covered=round(risk, 1),
        coverage_percent=round(100 * risk / total_risk, 1) if total_risk else 0,
        protected_risk_per_km=round(risk / distance, 3) if distance else 0,
        peak_risk_covered=round(peak_covered, 1),
        peak_risk_coverage_percent=(
            round(100 * peak_covered / peak_total, 1) if peak_total else 0
        ),
        peak_risk_stops=len(covered_peak_ids),
    )


def _greedy_selection(request: PatrolRequest) -> list[str]:
    remaining = {hotspot.hotspot_id: hotspot for hotspot in request.hotspots}
    current = request.start
    order: list[str] = []
    while remaining and len(order) < request.max_stops:
        best_id = max(
            remaining,
            key=lambda hotspot_id: (
                (
                    remaining[hotspot_id].operational_priority
                    if remaining[hotspot_id].operational_priority is not None
                    else remaining[hotspot_id].risk_score
                )
                / max(
                    0.2,
                    haversine_km(current, remaining[hotspot_id].location),
                )
            ),
        )
        order.append(best_id)
        current = remaining.pop(best_id).location
    return order


def _two_opt(request: PatrolRequest, route: list[str]) -> list[str]:
    if len(route) < 4:
        return route

    by_id = {hotspot.hotspot_id: hotspot for hotspot in request.hotspots}
    best = route[:]
    best_distance = _distance(
        request.start,
        by_id,
        best,
        request.return_to_start,
    )
    improved = True
    while improved:
        improved = False
        for start_index in range(0, len(best) - 2):
            for end_index in range(start_index + 2, len(best) + 1):
                candidate = (
                    best[:start_index]
                    + list(reversed(best[start_index:end_index]))
                    + best[end_index:]
                )
                candidate_distance = _distance(
                    request.start,
                    by_id,
                    candidate,
                    request.return_to_start,
                )
                if candidate_distance + 1e-9 < best_distance:
                    best = candidate
                    best_distance = candidate_distance
                    improved = True
    return best


def optimise_patrol(request: PatrolRequest) -> PatrolComparison:
    valid = {hotspot.hotspot_id for hotspot in request.hotspots}
    baseline = [
        hotspot_id
        for hotspot_id in request.baseline_order
        if hotspot_id in valid
    ][: request.max_stops]
    if not baseline:
        baseline = [
            hotspot.hotspot_id
            for hotspot in request.hotspots[: request.max_stops]
        ]

    optimised = _two_opt(request, _greedy_selection(request))
    base_metrics = _metrics(request, baseline)
    optimised_metrics = _metrics(request, optimised)
    improvement = (
        100
        * (
            optimised_metrics.protected_risk_per_km
            - base_metrics.protected_risk_per_km
        )
        / base_metrics.protected_risk_per_km
        if base_metrics.protected_risk_per_km
        else 0
    )
    return PatrolComparison(
        baseline=base_metrics,
        optimised=optimised_metrics,
        distance_saved_km=round(
            base_metrics.distance_km - optimised_metrics.distance_km,
            3,
        ),
        fuel_saved_litres=round(
            base_metrics.estimated_fuel_litres
            - optimised_metrics.estimated_fuel_litres,
            3,
        ),
        protected_risk_per_km_improvement_percent=round(improvement, 1),
        coverage_change_points=round(
            optimised_metrics.coverage_percent - base_metrics.coverage_percent,
            1,
        ),
        peak_risk_coverage_change_points=round(
            optimised_metrics.peak_risk_coverage_percent
            - base_metrics.peak_risk_coverage_percent,
            1,
        ),
        peak_risk_retained=(
            optimised_metrics.peak_risk_coverage_percent
            >= base_metrics.peak_risk_coverage_percent
        ),
    )
