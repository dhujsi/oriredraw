"""Deterministic exact-geometry propagation on the unified construction graph.

The frontier follows recorded point/crease incidence only.  It deliberately
does not connect a nearby detector endpoint to a proved point merely because
their supporting lines are close: that shortcut can turn a detached observed
stroke into a constructed crease.  Missing incidence must first be justified
by an explicit construction point or source-topology evidence.
"""

from __future__ import annotations

from collections import Counter, deque
from itertools import combinations
import math
import time
from typing import Any, Hashable, Iterable, Mapping

from construction_search import ConstructionGraph, GeometryEntity
from exact_qsqrt2 import Qsqrt2
from qsqrt2_coordinates import (
    qsqrt2_canonical_coefficients,
    qsqrt2_complexity,
    qsqrt2_from_mapping,
    qsqrt2_to_mapping,
)


ExactPoint = tuple[Qsqrt2, Qsqrt2]


_FIT_ONLY_POINT_SOURCES = {
    "guided_internal_topology_point",
    "guided_automatic_topology_point",
    "guided_internal_topology_point_trial",
}
_ENDPOINT_BRIDGE_SOURCE = "existing_canonical_crease_endpoint_from_exact_point"


def _exact_point_from_mapping(raw: Any) -> ExactPoint | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return qsqrt2_from_mapping(raw[0]), qsqrt2_from_mapping(raw[1])
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _entity_exact_point(entity: GeometryEntity) -> ExactPoint | None:
    return _exact_point_from_mapping(entity.exact_geometry.get("project_coordinate"))


def _entity_exact_line(entity: GeometryEntity) -> tuple[ExactPoint, int] | None:
    raw_direction = entity.exact_geometry.get("direction_index")
    point = _exact_point_from_mapping(entity.exact_geometry.get("through_point_project"))
    try:
        direction_index = int(raw_direction)
    except (TypeError, ValueError):
        return None
    if point is None or not 0 <= direction_index < 8:
        return None
    return point, direction_index


def _direction_vector(direction_index: int) -> ExactPoint:
    one = Qsqrt2(1)
    zero = Qsqrt2()
    diagonal = Qsqrt2(1, 1)
    vectors = (
        (one, zero),
        (diagonal, one),
        (one, one),
        (one, diagonal),
        (zero, one),
        (-one, diagonal),
        (-one, one),
        (-diagonal, one),
    )
    if 0 <= direction_index <= 7:
        return vectors[direction_index]
    raise ValueError(f"invalid 22.5-degree direction index: {direction_index}")


def _cross(first: ExactPoint, second: ExactPoint) -> Qsqrt2:
    return first[0] * second[1] - first[1] * second[0]


def _line_intersection(
    first: tuple[ExactPoint, int],
    second: tuple[ExactPoint, int],
) -> ExactPoint | None:
    first_point, first_index = first
    second_point, second_index = second
    first_direction = _direction_vector(first_index)
    second_direction = _direction_vector(second_index)
    denominator = _cross(first_direction, second_direction)
    if denominator == Qsqrt2():
        return None
    delta = (
        second_point[0] - first_point[0],
        second_point[1] - first_point[1],
    )
    parameter = _cross(delta, second_direction) / denominator
    return (
        first_point[0] + parameter * first_direction[0],
        first_point[1] + parameter * first_direction[1],
    )


def _line_boundary_intersection(
    line: tuple[ExactPoint, int],
    side: str,
    side_length: Qsqrt2,
) -> ExactPoint | None:
    point, direction_index = line
    direction = _direction_vector(direction_index)
    if side in {"top", "bottom"}:
        if direction[1] == Qsqrt2():
            return None
        target = Qsqrt2() if side == "top" else side_length
        parameter = (target - point[1]) / direction[1]
    elif side in {"left", "right"}:
        if direction[0] == Qsqrt2():
            return None
        target = Qsqrt2() if side == "left" else side_length
        parameter = (target - point[0]) / direction[0]
    else:
        return None
    candidate = (
        point[0] + parameter * direction[0],
        point[1] + parameter * direction[1],
    )
    epsilon = 1e-9
    if not (
        -epsilon <= float(candidate[0]) <= float(side_length) + epsilon
        and -epsilon <= float(candidate[1]) <= float(side_length) + epsilon
    ):
        return None
    return candidate


def _project_to_pixel(
    point: ExactPoint,
    side_length: Qsqrt2,
    maximum: float,
) -> tuple[float, float]:
    return (
        float(point[0] / side_length) * maximum,
        float(point[1] / side_length) * maximum,
    )


def _is_paper_boundary_tangent(
    point: ExactPoint,
    side_length: Qsqrt2,
    direction_index: int,
) -> bool:
    zero = Qsqrt2()
    on_horizontal_boundary = point[1] in {zero, side_length}
    on_vertical_boundary = point[0] in {zero, side_length}
    return (
        on_horizontal_boundary and direction_index == 0
    ) or (
        on_vertical_boundary and direction_index == 4
    )


def _on_boundary_exact(point: ExactPoint, side_length: Qsqrt2) -> bool:
    zero = Qsqrt2()
    return point[0] in {zero, side_length} or point[1] in {zero, side_length}


def _point_key(point: ExactPoint) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return (
        qsqrt2_canonical_coefficients(point[0]),
        qsqrt2_canonical_coefficients(point[1]),
    )


def _point_complexity(point: ExactPoint) -> int:
    return qsqrt2_complexity(point[0]) + qsqrt2_complexity(point[1])


def _observed_point(entity: GeometryEntity) -> tuple[float, float] | None:
    raw = entity.observed_geometry.get("point_px")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None


def _point_tolerance(graph: ConstructionGraph, point_id: Hashable) -> float:
    tolerances = []
    for entity in graph.incident_entities(point_id):
        if entity.kind != "crease":
            continue
        try:
            tolerances.append(float(entity.observed_geometry.get("match_tolerance_px", 0.85)))
        except (TypeError, ValueError):
            continue
    return min(3.2, max([0.85, *tolerances]))


def _observed_boundary_sides(
    point: tuple[float, float],
    maximum: float,
    tolerance: float,
) -> tuple[str, ...]:
    distances = {
        "top": abs(point[1]),
        "right": abs(maximum - point[0]),
        "bottom": abs(maximum - point[1]),
        "left": abs(point[0]),
    }
    return tuple(
        side
        for side, distance in sorted(distances.items(), key=lambda item: (item[1], item[0]))
        if distance <= max(1.0, tolerance)
    )


def _crease_observed_residual(entity: GeometryEntity, point_px: tuple[float, float]) -> float:
    try:
        angle = math.radians(float(entity.observed_geometry["angle_deg"]))
        offset = float(entity.observed_geometry["line_offset_px"])
    except (KeyError, TypeError, ValueError):
        return math.inf
    normal = (-math.sin(angle), math.cos(angle))
    return abs(normal[0] * point_px[0] + normal[1] * point_px[1] - offset)


def _evidence_intervals(entity: GeometryEntity) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for raw in entity.observed_geometry.get("evidence_intervals_px", []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        try:
            first, second = float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(first) or not math.isfinite(second):
            continue
        intervals.append((min(first, second), max(first, second)))
    return sorted(intervals)


def _exact_line_key(point: ExactPoint, direction_index: int) -> tuple[Any, ...]:
    direction = _direction_vector(direction_index)
    normal = (-direction[1], direction[0])
    offset = normal[0] * point[0] + normal[1] * point[1]
    return direction_index, qsqrt2_canonical_coefficients(offset)


def _endpoint_bridge_candidates(
    graph: ConstructionGraph,
    side_length: Qsqrt2,
    maximum: float,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Find unique exact lines at short, observed detector endpoint gaps.

    This is deliberately the inverse of all-direction ray enumeration: an
    already observed finite canonical crease must nominate the endpoint gap,
    and a proved point may only close that gap when every admissible point
    yields the same exact line.
    """

    rejections: Counter[str] = Counter()
    exact_points = [
        entity
        for entity in graph.geometry_entities.values()
        if entity.kind == "point"
        and _entity_exact_point(entity) is not None
        and str(entity.exact_geometry.get("source") or "")
        not in _FIT_ONLY_POINT_SOURCES
    ]
    accepted: list[dict[str, Any]] = []
    for crease in graph.geometry_entities.values():
        if crease.kind != "crease" or _entity_exact_line(crease) is not None:
            continue
        intervals = _evidence_intervals(crease)
        if not intervals:
            continue
        try:
            direction_index = int(crease.observed_geometry.get("direction_index"))
        except (TypeError, ValueError):
            rejections["endpoint_bridge_noncanonical_direction"] += 1
            continue
        if not 0 <= direction_index < 8:
            rejections["endpoint_bridge_noncanonical_direction"] += 1
            continue
        angle = math.radians(direction_index * 22.5)
        direction_px = (math.cos(angle), math.sin(angle))
        normal_px = (-direction_px[1], direction_px[0])
        try:
            observed_offset = float(crease.observed_geometry["line_offset_px"])
            residual_limit = min(
                3.2,
                max(
                    0.85,
                    float(crease.observed_geometry.get("match_tolerance_px", 0.85)),
                ),
            )
        except (KeyError, TypeError, ValueError):
            rejections["endpoint_bridge_missing_line_evidence"] += 1
            continue

        by_exact_line: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for point_entity in exact_points:
            if point_entity.id in graph.incidence.get(crease.id, set()):
                continue
            exact_point = _entity_exact_point(point_entity)
            assert exact_point is not None
            if _is_paper_boundary_tangent(
                exact_point,
                side_length,
                direction_index,
            ):
                continue
            point_px = _project_to_pixel(exact_point, side_length, maximum)
            residual = abs(
                normal_px[0] * point_px[0]
                + normal_px[1] * point_px[1]
                - observed_offset
            )
            if residual > residual_limit + 1e-9:
                continue
            parameter = (
                direction_px[0] * point_px[0]
                + direction_px[1] * point_px[1]
            )
            # A topology endpoint bridge must extend a detected finite run.
            # A proved point in the middle of a run is an intersection-repair
            # problem and is intentionally not promoted by this rule.
            if any(first - 1e-9 <= parameter <= second + 1e-9 for first, second in intervals):
                continue
            gap = min(
                abs(parameter - endpoint)
                for interval in intervals
                for endpoint in interval
            )
            endpoint_gap_limit = max(
                3.2,
                maximum
                * (0.025 if _on_boundary_exact(exact_point, side_length) else 0.012),
            )
            if gap > endpoint_gap_limit + 1e-9:
                continue
            candidate = {
                "crease_id": crease.id,
                "point_id": point_entity.id,
                "exact_point": exact_point,
                "direction_index": direction_index,
                "line_residual_px": residual,
                "endpoint_gap_px": gap,
                "endpoint_gap_limit_px": endpoint_gap_limit,
                "point_generation": int(
                    point_entity.exact_geometry.get("exact_generation", 0) or 0
                ),
            }
            by_exact_line.setdefault(
                _exact_line_key(exact_point, direction_index), []
            ).append(candidate)

        if not by_exact_line:
            continue
        if len(by_exact_line) != 1:
            rejections["ambiguous_endpoint_bridge_exact_line"] += 1
            continue
        candidates = next(iter(by_exact_line.values()))
        selected = min(
            candidates,
            key=lambda item: (
                float(item["endpoint_gap_px"]),
                float(item["line_residual_px"]),
                repr(item["point_id"]),
            ),
        )
        selected["incidence_point_ids"] = sorted(
            {item["point_id"] for item in candidates}, key=repr
        )
        accepted.append(selected)
    accepted.sort(key=lambda item: repr(item["crease_id"]))
    return accepted, rejections


def _candidate_exact_point(
    graph: ConstructionGraph,
    point_entity: GeometryEntity,
    side_length: Qsqrt2,
    maximum: float,
    *,
    consistency_tolerance_px: float,
) -> tuple[dict[str, Any] | None, str]:
    observed = _observed_point(point_entity)
    if observed is None:
        return None, "missing_observed_point"
    tolerance = _point_tolerance(graph, point_entity.id)
    exact_incident = [
        entity
        for entity in graph.incident_entities(point_entity.id)
        if entity.kind == "crease" and _entity_exact_line(entity) is not None
    ]
    raw_candidates: list[dict[str, Any]] = []
    for first, second in combinations(exact_incident, 2):
        coordinate = _line_intersection(
            _entity_exact_line(first),
            _entity_exact_line(second),
        )
        if coordinate is None:
            continue
        raw_candidates.append(
            {
                "coordinate": coordinate,
                "source": "existing_crease_intersection",
                "parents": [str(first.id), str(second.id)],
            }
        )
    for crease in exact_incident:
        line = _entity_exact_line(crease)
        assert line is not None
        for side in _observed_boundary_sides(observed, maximum, tolerance):
            coordinate = _line_boundary_intersection(line, side, side_length)
            if coordinate is None:
                continue
            raw_candidates.append(
                {
                    "coordinate": coordinate,
                    "source": "existing_crease_paper_boundary_intersection",
                    "parents": [str(crease.id), f"paper_boundary:{side}"],
                }
            )
    if not raw_candidates:
        return None, "insufficient_exact_incidence"

    deduplicated: dict[Any, dict[str, Any]] = {}
    for candidate in raw_candidates:
        coordinate = candidate["coordinate"]
        pixel = _project_to_pixel(coordinate, side_length, maximum)
        residual = math.hypot(pixel[0] - observed[0], pixel[1] - observed[1])
        if residual > tolerance:
            continue
        candidate["pixel"] = pixel
        candidate["residual_px"] = residual
        candidate["complexity"] = _point_complexity(coordinate)
        key = _point_key(coordinate)
        old = deduplicated.get(key)
        if old is None or (
            candidate["residual_px"], candidate["complexity"], candidate["parents"]
        ) < (old["residual_px"], old["complexity"], old["parents"]):
            deduplicated[key] = candidate
    candidates = sorted(
        deduplicated.values(),
        key=lambda item: (item["residual_px"], item["complexity"], item["parents"]),
    )
    if not candidates:
        return None, "observed_residual_pruned"
    best = candidates[0]
    for competitor in candidates[1:]:
        disagreement = math.hypot(
            competitor["pixel"][0] - best["pixel"][0],
            competitor["pixel"][1] - best["pixel"][1],
        )
        if disagreement > consistency_tolerance_px:
            return None, "conflicting_exact_candidates"
    return best, "accepted"


def _side_length_from_graph(graph: ConstructionGraph) -> Qsqrt2 | None:
    values: dict[tuple[int, int, int], Qsqrt2] = {}
    for entity in graph.geometry_entities.values():
        raw = entity.exact_geometry.get("side_length")
        if not isinstance(raw, Mapping):
            continue
        try:
            value = qsqrt2_from_mapping(raw)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        values[qsqrt2_canonical_coefficients(value)] = value
    return next(iter(values.values())) if len(values) == 1 else None


def propagate_exact_geometry(
    graph: ConstructionGraph,
    *,
    maximum: float,
    consistency_tolerance_px: float = 0.75,
) -> dict[str, Any]:
    """Propagate exactness over existing, source-observed incidence."""

    started = time.perf_counter()
    if maximum <= 0:
        return {
            "enabled": False,
            "mode": "node_incidence_exact_frontier_v3",
            "reason": "invalid_paper_size",
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    side_length = _side_length_from_graph(graph)
    if side_length is None or float(side_length) <= 0:
        return {
            "enabled": False,
            "mode": "node_incidence_exact_frontier_v3",
            "reason": "missing_or_conflicting_side_length",
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    initial_exact_points = sum(
        entity.kind == "point" and _entity_exact_point(entity) is not None
        for entity in graph.geometry_entities.values()
    )
    initial_exact_creases = sum(
        entity.kind == "crease" and _entity_exact_line(entity) is not None
        for entity in graph.geometry_entities.values()
    )
    frontier: deque[tuple[str, Hashable]] = deque()
    for entity in graph.geometry_entities.values():
        if entity.kind == "point" and _entity_exact_point(entity) is not None:
            frontier.append(("point", entity.id))
        elif entity.kind == "crease" and _entity_exact_line(entity) is not None:
            frontier.append(("crease", entity.id))

    processed_points: set[Hashable] = set()
    processed_creases: set[Hashable] = set()
    rejection_counts: Counter[str] = Counter()
    pruned_tangent_creases: set[Hashable] = set()
    events: list[dict[str, Any]] = []
    frontier_pops = 0
    maximum_frontier_pops = max(32, len(graph.geometry_entities) * 4)
    while frontier and frontier_pops < maximum_frontier_pops:
        kind, entity_id = frontier.popleft()
        frontier_pops += 1
        entity = graph.geometry_entity(entity_id)
        if kind == "point":
            if entity_id in processed_points:
                continue
            exact_point = _entity_exact_point(entity)
            if exact_point is None:
                continue
            processed_points.add(entity_id)
            point_px = _project_to_pixel(exact_point, side_length, maximum)
            point_generation = int(entity.exact_geometry.get("exact_generation", 0) or 0)
            for crease in graph.incident_entities(entity_id):
                if crease.kind != "crease" or _entity_exact_line(crease) is not None:
                    continue
                direction_index = crease.observed_geometry.get("direction_index")
                try:
                    direction_index = int(direction_index)
                except (TypeError, ValueError):
                    rejection_counts["non_legal_observed_direction"] += 1
                    continue
                if _is_paper_boundary_tangent(
                    exact_point,
                    side_length,
                    direction_index,
                ):
                    rejection_counts["paper_boundary_tangent_pruned"] += 1
                    pruned_tangent_creases.add(crease.id)
                    continue
                residual = _crease_observed_residual(crease, point_px)
                try:
                    tolerance = float(crease.observed_geometry.get("match_tolerance_px", 0.85))
                except (TypeError, ValueError):
                    tolerance = 0.85
                if residual > tolerance:
                    rejection_counts["crease_observed_residual_pruned"] += 1
                    continue
                graph.exactify_geometry(
                    crease.id,
                    {
                        "source": "existing_incident_crease_from_exact_point",
                        "source_point_id": str(entity_id),
                        "direction_index": direction_index,
                        "direction_deg": round(direction_index * 22.5, 6),
                        "through_point_id": str(entity_id),
                        "through_point_project": [
                            qsqrt2_to_mapping(exact_point[0]),
                            qsqrt2_to_mapping(exact_point[1]),
                        ],
                        "side_length": qsqrt2_to_mapping(side_length),
                        "exact_generation": point_generation + 1,
                        "observed_residual_px": round(residual, 6),
                    },
                )
                events.append(
                    {
                        "kind": "exactify_existing_crease",
                        "entity_id": str(crease.id),
                        "source_point_id": str(entity_id),
                        "direction_index": direction_index,
                        "residual_px": round(residual, 6),
                    }
                )
                frontier.append(("crease", crease.id))
        elif kind == "crease":
            if entity_id in processed_creases or _entity_exact_line(entity) is None:
                continue
            processed_creases.add(entity_id)
            crease_generation = int(entity.exact_geometry.get("exact_generation", 0) or 0)
            for point in graph.incident_entities(entity_id):
                if point.kind != "point" or _entity_exact_point(point) is not None:
                    continue
                candidate, reason = _candidate_exact_point(
                    graph,
                    point,
                    side_length,
                    maximum,
                    consistency_tolerance_px=max(0.0, float(consistency_tolerance_px)),
                )
                if candidate is None:
                    rejection_counts[reason] += 1
                    continue
                coordinate = candidate["coordinate"]
                graph.exactify_geometry(
                    point.id,
                    {
                        "source": candidate["source"],
                        "parent_entity_ids": candidate["parents"],
                        "project_coordinate": [
                            qsqrt2_to_mapping(coordinate[0]),
                            qsqrt2_to_mapping(coordinate[1]),
                        ],
                        "side_length": qsqrt2_to_mapping(side_length),
                        "exact_generation": crease_generation + 1,
                        "observed_residual_px": round(float(candidate["residual_px"]), 6),
                    },
                )
                events.append(
                    {
                        "kind": "exactify_existing_point",
                        "entity_id": str(point.id),
                        "source": candidate["source"],
                        "parent_entity_ids": candidate["parents"],
                        "residual_px": round(float(candidate["residual_px"]), 6),
                    }
                )
                frontier.append(("point", point.id))

    final_exact_points = sum(
        entity.kind == "point" and _entity_exact_point(entity) is not None
        for entity in graph.geometry_entities.values()
    )
    final_exact_creases = sum(
        entity.kind == "crease" and _entity_exact_line(entity) is not None
        for entity in graph.geometry_entities.values()
    )
    observed_points = sum(entity.kind == "point" for entity in graph.geometry_entities.values())
    observed_creases = sum(entity.kind == "crease" for entity in graph.geometry_entities.values())
    unexactified_crease_ids = {
        entity.id
        for entity in graph.geometry_entities.values()
        if entity.kind == "crease" and _entity_exact_line(entity) is None
    }
    unexactified_creases = len(unexactified_crease_ids)
    pruned_tangent_count = sum(
        entity_id in pruned_tangent_creases
        and _entity_exact_line(graph.geometry_entity(entity_id)) is None
        for entity_id in pruned_tangent_creases
    )
    unresolved_crease_ids = sorted(
        unexactified_crease_ids - pruned_tangent_creases,
        key=repr,
    )
    unresolved_creases = len(unresolved_crease_ids)
    frontier_limit_reached = bool(frontier)
    return {
        "enabled": True,
        "mode": "node_incidence_exact_frontier_v3",
        "status": (
            "complete_existing_creases"
            if unresolved_creases == 0
            else "partial_existing_creases"
        ),
        "side_length": qsqrt2_to_mapping(side_length),
        "initial_exact_point_count": initial_exact_points,
        "initial_exact_crease_count": initial_exact_creases,
        "propagated_point_count": final_exact_points - initial_exact_points,
        "propagated_crease_count": final_exact_creases - initial_exact_creases,
        "final_exact_point_count": final_exact_points,
        "final_exact_crease_count": final_exact_creases,
        "unresolved_point_count": observed_points - final_exact_points,
        "unexactified_crease_count": unexactified_creases,
        "pruned_paper_boundary_tangent_count": pruned_tangent_count,
        "unresolved_crease_count": unresolved_creases,
        "unresolved_crease_entity_ids": [
            str(entity_id) for entity_id in unresolved_crease_ids
        ],
        "endpoint_bridge_round_count": 0,
        "endpoint_bridge_applied_count": 0,
        "endpoint_bridge_applied_crease_entity_ids": [],
        "endpoint_bridge_added_incidence_count": 0,
        "pruned_paper_boundary_tangent_entity_ids": [
            str(entity_id)
            for entity_id in sorted(pruned_tangent_creases, key=repr)
            if entity_id in unexactified_crease_ids
        ],
        "frontier_pop_count": frontier_pops,
        "frontier_limit": maximum_frontier_pops,
        "frontier_limit_reached": frontier_limit_reached,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "events": events,
        "invariants": {
            "enumerated_direction_count": 0,
            "created_point_count": 0,
            "created_crease_count": 0,
            "each_entity_exactified_at_most_once": True,
            "proximity_endpoint_bridge_enabled": False,
            "new_incidence_requires_explicit_topology_evidence": True,
            "raster_selects_no_free_direction": True,
        },
    }


__all__ = ["propagate_exact_geometry"]
