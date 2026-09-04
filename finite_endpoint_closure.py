"""Close detector endpoints onto construction-proved exact geometry.

The raw topology intentionally records where the line detector stopped.  A
detector terminal can sit a few pixels before an already evidenced exact
intersection or paper-boundary contact.  This module resolves that discrepancy
per finite segment endpoint; it never merges the observed point globally,
creates a graph point, creates or moves a crease, chooses a direction, or
infers a paper-boundary contact that was not observed. A segment with one
proved endpoint may close onto the exact intersection of proved creases; a
detached raster fragment with no proved endpoint is not promoted.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Mapping

from construction_proof_topology import build_construction_proof_topology
from exact_qsqrt2 import Qsqrt2
from qsqrt2_coordinates import (
    qsqrt2_canonical_coefficients,
    qsqrt2_from_mapping,
    qsqrt2_to_mapping,
)


ExactPoint = tuple[Qsqrt2, Qsqrt2]

_RAW_SEGMENT_SOURCE = "raw_image_finite_line_evidence"
_RAW_TOPOLOGY_MODE = "raw_finite_crease_topology_v1"
_CLOSED_TOPOLOGY_MODE = "finite_endpoint_closed_topology_v1"


def _exact_point(raw: Any) -> ExactPoint | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return qsqrt2_from_mapping(raw[0]), qsqrt2_from_mapping(raw[1])
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _point_key(point: ExactPoint) -> tuple[tuple[int, int, int], ...]:
    return (
        qsqrt2_canonical_coefficients(point[0]),
        qsqrt2_canonical_coefficients(point[1]),
    )


def _direction_vector(direction_index: int) -> ExactPoint:
    zero = Qsqrt2()
    one = Qsqrt2(1)
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
    if not 0 <= direction_index < len(vectors):
        raise ValueError(f"invalid 22.5-degree direction index: {direction_index}")
    return vectors[direction_index]


def _cross(first: ExactPoint, second: ExactPoint) -> Qsqrt2:
    return first[0] * second[1] - first[1] * second[0]


def _on_exact_line(point: ExactPoint, through: ExactPoint, direction_index: int) -> bool:
    delta = (point[0] - through[0], point[1] - through[1])
    return _cross(delta, _direction_vector(direction_index)) == Qsqrt2()


def _exact_line_intersection(
    first: tuple[ExactPoint, int],
    second: tuple[ExactPoint, int],
) -> ExactPoint | None:
    first_point, first_direction_index = first
    second_point, second_direction_index = second
    first_direction = _direction_vector(first_direction_index)
    second_direction = _direction_vector(second_direction_index)
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


def _interval_distance(intervals: Any, value: float) -> float:
    distances: list[float] = []
    for raw in intervals or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        try:
            first, second = sorted((float(raw[0]), float(raw[1])))
        except (TypeError, ValueError):
            continue
        distances.append(
            0.0
            if first - 1e-9 <= value <= second + 1e-9
            else min(abs(value - first), abs(value - second))
        )
    return min(distances, default=math.inf)


def _observed_crease_supports_point(
    entity: Mapping[str, Any],
    point_px: tuple[float, float],
    *,
    incidence_margin: float,
    line_residual_tolerance: float,
) -> bool:
    observed = entity.get("observed_geometry")
    if not isinstance(observed, Mapping):
        return False
    try:
        direction_index = int(observed.get("direction_index"))
        offset = float(observed.get("line_offset_px"))
    except (TypeError, ValueError):
        return False
    if not 0 <= direction_index < 8:
        return False
    angle = direction_index * math.pi / 8.0
    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    residual = abs(normal[0] * point_px[0] + normal[1] * point_px[1] - offset)
    if residual > line_residual_tolerance + 1e-9:
        return False
    parameter = direction[0] * point_px[0] + direction[1] * point_px[1]
    return (
        _interval_distance(observed.get("evidence_intervals_px"), parameter)
        <= incidence_margin + 1e-9
    )


def _observed_crease_terminal_near_point(
    entity: Mapping[str, Any],
    point_px: tuple[float, float],
    *,
    maximum_gap: float,
    line_residual_tolerance: float,
) -> bool:
    observed = entity.get("observed_geometry")
    if not isinstance(observed, Mapping):
        return False
    try:
        direction_index = int(observed.get("direction_index"))
        offset = float(observed.get("line_offset_px"))
    except (TypeError, ValueError):
        return False
    if not 0 <= direction_index < 8:
        return False
    angle = direction_index * math.pi / 8.0
    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    residual = abs(normal[0] * point_px[0] + normal[1] * point_px[1] - offset)
    if residual > line_residual_tolerance + 1e-9:
        return False
    parameter = direction[0] * point_px[0] + direction[1] * point_px[1]
    gap = _interval_distance(observed.get("evidence_intervals_px"), parameter)
    return gap <= maximum_gap + 1e-9


def _project_to_pixel(
    point: ExactPoint,
    side_length: Qsqrt2,
    maximum: float,
) -> tuple[float, float]:
    return (
        float(point[0] / side_length) * maximum,
        float(point[1] / side_length) * maximum,
    )


def _line_boundary_intersection(
    line: tuple[ExactPoint, int],
    side: str,
    side_length: Qsqrt2,
) -> ExactPoint | None:
    point, direction_index = line
    direction = _direction_vector(direction_index)
    zero = Qsqrt2()
    if side in {"top", "bottom"}:
        if direction[1] == zero:
            return None
        target = zero if side == "top" else side_length
        parameter = (target - point[1]) / direction[1]
    elif side in {"left", "right"}:
        if direction[0] == zero:
            return None
        target = zero if side == "left" else side_length
        parameter = (target - point[0]) / direction[0]
    else:
        return None
    candidate = (
        point[0] + parameter * direction[0],
        point[1] + parameter * direction[1],
    )
    epsilon = 1e-9
    if not all(
        -epsilon <= float(value) <= float(side_length) + epsilon
        for value in candidate
    ):
        return None
    return candidate


def _observed_point(entity: Mapping[str, Any]) -> tuple[float, float] | None:
    observed = entity.get("observed_geometry")
    raw = observed.get("point_px") if isinstance(observed, Mapping) else None
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        point = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(value) for value in point) else None


def _positive_number(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) and value > 0 else fallback


def _endpoint_gap_limit(
    segment: Mapping[str, Any],
    *,
    maximum: float,
    incidence_margin: float,
    line_residual_tolerance: float,
) -> float:
    """Return a local, scale-normalized cap for detector linehead closure."""

    length = _positive_number(segment.get("length_px"), 0.0)
    paper_cap = maximum * 0.05
    # The finite-interval allowance is axial while clustered endpoints can sit
    # off the exact line by the accepted line residual.  Combine those two
    # independent uncertainties geometrically instead of adding a free margin.
    finite_evidence_cap = math.hypot(
        incidence_margin * 2.0,
        line_residual_tolerance,
    )
    local_cap = max(finite_evidence_cap, length * 0.75)
    return max(0.0, min(paper_cap, local_cap))


def _same_endpoint_ray(
    endpoint_px: tuple[float, float],
    other_px: tuple[float, float] | None,
    candidate_px: tuple[float, float],
    *,
    tolerance: float,
) -> bool:
    if other_px is None:
        return True
    toward_endpoint = (
        endpoint_px[0] - other_px[0],
        endpoint_px[1] - other_px[1],
    )
    toward_candidate = (
        candidate_px[0] - other_px[0],
        candidate_px[1] - other_px[1],
    )
    length = math.hypot(*toward_endpoint)
    if length <= 1e-9:
        return True
    signed_distance = (
        toward_endpoint[0] * toward_candidate[0]
        + toward_endpoint[1] * toward_candidate[1]
    ) / length
    return signed_distance >= -max(0.0, tolerance)


def _on_boundary(point: ExactPoint, side_length: Qsqrt2) -> bool:
    zero = Qsqrt2()
    return point[0] in {zero, side_length} or point[1] in {zero, side_length}


def build_finite_endpoint_closed_topology(
    guided_report: Mapping[str, Any],
    *,
    max_target_residual_px: float = 3.2,
) -> dict[str, Any]:
    """Rebind finite segment endpoints to construction-proved exact locations.

    A non-boundary target normally requires direct observed incidence to this
    finite crease. A directly observed segment with two fuzzy terminals may
    instead use proved exact nodes that unambiguously bracket the stroke. A
    boundary target may use either an explicit side or a source terminal close
    enough to the exact crease/paper-edge intersection.
    A segment whose opposite endpoint is proved may use the nearby exact
    intersection of proved creases. Its projected location must remain within
    both the paper-scale and local segment-evidence limits. Near ties are
    rejected instead of guessed.
    """

    graph = guided_report.get("geometry_graph")
    topology = guided_report.get("raw_topology")
    propagation = guided_report.get("geometry_propagation")
    if not isinstance(graph, Mapping):
        return {
            "enabled": False,
            "mode": _CLOSED_TOPOLOGY_MODE,
            "reason": "missing_geometry_graph",
        }
    if (
        not isinstance(topology, Mapping)
        or not topology.get("enabled", False)
        or topology.get("mode") != _RAW_TOPOLOGY_MODE
    ):
        return {
            "enabled": False,
            "mode": _CLOSED_TOPOLOGY_MODE,
            "reason": "missing_raw_finite_topology",
        }
    if not isinstance(propagation, Mapping) or propagation.get("enabled") is False:
        return {
            "enabled": False,
            "mode": _CLOSED_TOPOLOGY_MODE,
            "reason": "exact_geometry_propagation_unavailable",
        }
    unresolved_creases = int(propagation.get("unresolved_crease_count", 0) or 0)

    raw_side_length = guided_report.get("global_side_length")
    try:
        side_length = (
            qsqrt2_from_mapping(raw_side_length)
            if isinstance(raw_side_length, Mapping)
            else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        side_length = None
    maximum = _positive_number(topology.get("maximum_coordinate_px"), 0.0)
    if side_length is None or float(side_length) <= 0 or maximum <= 0:
        return {
            "enabled": False,
            "mode": _CLOSED_TOPOLOGY_MODE,
            "reason": "missing_coordinate_scale",
        }

    entities = {
        str(item.get("id")): item
        for item in graph.get("entities", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    proof = guided_report.get("construction_proof_topology")
    if not isinstance(proof, Mapping) or not proof.get("enabled", False):
        proof = build_construction_proof_topology(graph, topology)
    proved_point_ids = {str(item) for item in proof.get("proved_point_ids", [])}
    proved_crease_ids = {str(item) for item in proof.get("proved_crease_ids", [])}
    exact_points: dict[str, ExactPoint] = {}
    projected_points: dict[str, tuple[float, float]] = {}
    observed_points: dict[str, tuple[float, float]] = {}
    point_incident_creases: dict[str, set[str]] = {}
    for entity_id, entity in entities.items():
        if entity.get("kind") != "point":
            continue
        exact = entity.get("exact_geometry")
        point = _exact_point(
            exact.get("project_coordinate") if isinstance(exact, Mapping) else None
        )
        observed = _observed_point(entity)
        if point is None or observed is None:
            continue
        if entity_id not in proved_point_ids:
            continue
        if _RAW_SEGMENT_SOURCE not in set(entity.get("evidence_sources") or []):
            continue
        projected = _project_to_pixel(point, side_length, maximum)
        if math.dist(projected, observed) > max(0.0, max_target_residual_px) + 1e-9:
            continue
        exact_points[entity_id] = point
        projected_points[entity_id] = projected
        observed_points[entity_id] = observed
        point_incident_creases[entity_id] = {
            str(item) for item in entity.get("incident_ids", [])
        }

    exact_lines: dict[str, tuple[ExactPoint, int]] = {}
    for entity_id, entity in entities.items():
        if entity.get("kind") != "crease":
            continue
        if entity_id not in proved_crease_ids:
            continue
        exact = entity.get("exact_geometry")
        if not isinstance(exact, Mapping):
            continue
        through = _exact_point(exact.get("through_point_project"))
        try:
            direction = int(exact.get("direction_index"))
        except (TypeError, ValueError):
            continue
        if through is not None and 0 <= direction < 8:
            exact_lines[entity_id] = through, direction
    tolerances = topology.get("tolerances")
    incidence_margin = _positive_number(
        tolerances.get("incidence_margin_px") if isinstance(tolerances, Mapping) else None,
        max(1.0, maximum * 0.007),
    )
    line_residual_tolerance = _positive_number(
        (
            tolerances.get("line_residual_tolerance_px")
            if isinstance(tolerances, Mapping)
            else None
        ),
        max(1.0, incidence_margin * 0.7),
    )
    boundary_margin = _positive_number(
        tolerances.get("boundary_margin_px") if isinstance(tolerances, Mapping) else None,
        max(1.0, incidence_margin * 1.125),
    )
    ambiguity_margin = min(0.75, max(0.25, incidence_margin * 0.1))
    source_segments = [
        item for item in topology.get("segments", []) if isinstance(item, Mapping)
    ]
    for source_segment in source_segments:
        crease_id = str(source_segment.get("crease_entity_id") or "")
        if not crease_id:
            continue
        for endpoint_name in ("start", "end"):
            point_id = str(source_segment.get(f"{endpoint_name}_point_id") or "")
            if point_id:
                point_incident_creases.setdefault(point_id, set()).add(crease_id)
    retained_segments: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    collapsed_segments: list[dict[str, Any]] = []
    suppressed_unanchored_segments: list[dict[str, Any]] = []
    derived_endpoint_binding_count = 0
    unresolved_occurrences: list[dict[str, Any]] = []
    changed_observed_ids: set[str] = set()
    boundary_resolved_observed_ids: set[str] = set()

    for source_segment in source_segments:
        segment = copy.deepcopy(dict(source_segment))
        segment_id = str(segment.get("id") or "")
        crease_id = str(segment.get("crease_entity_id") or "")
        original_ids = {
            "start": str(segment.get("start_point_id") or ""),
            "end": str(segment.get("end_point_id") or ""),
        }
        segment["observed_start_point_id"] = original_ids["start"]
        segment["observed_end_point_id"] = original_ids["end"]
        resolved_ids = dict(original_ids)
        closure_details: dict[str, Any] = {}
        line = exact_lines.get(crease_id)
        both_observed_endpoints_are_fuzzy = all(
            exact_points.get(original_ids[name]) is None
            for name in ("start", "end")
        )

        for endpoint_name, other_name in (("start", "end"), ("end", "start")):
            observed_id = original_ids[endpoint_name]
            point = exact_points.get(observed_id)
            if line is not None and point is not None and _on_exact_line(point, *line):
                closure_details[endpoint_name] = {
                    "status": "kept_existing_exact_endpoint",
                    "observed_point_id": observed_id,
                    "resolved_point_id": observed_id,
                }
                continue

            reason = (
                "missing_exact_endpoint"
                if point is None
                else "exact_endpoint_not_on_selected_crease"
            )
            endpoint_px = _observed_point(entities.get(observed_id, {}))
            other_px = _observed_point(entities.get(original_ids[other_name], {}))
            gap_limit = _endpoint_gap_limit(
                segment,
                maximum=maximum,
                incidence_margin=incidence_margin,
                line_residual_tolerance=line_residual_tolerance,
            )
            if line is None or endpoint_px is None or gap_limit <= 0:
                detail = {
                    "segment_id": segment_id,
                    "endpoint": endpoint_name,
                    "observed_point_id": observed_id,
                    "reason": "missing_exact_crease_or_endpoint_evidence",
                }
                unresolved_occurrences.append(detail)
                closure_details[endpoint_name] = {"status": "unresolved", **detail}
                continue

            through, direction = line
            unique_candidates: dict[
                tuple[tuple[int, int, int], ...], dict[str, Any]
            ] = {}
            for target_id, target_point in exact_points.items():
                has_direct_incidence = (
                    crease_id in point_incident_creases.get(target_id, set())
                )
                if not has_direct_incidence and not both_observed_endpoints_are_fuzzy:
                    continue
                if not _on_exact_line(target_point, through, direction):
                    continue
                target_px = projected_points[target_id]
                gap = math.dist(endpoint_px, target_px)
                if gap > gap_limit + 1e-9:
                    continue
                if not _same_endpoint_ray(
                    endpoint_px,
                    other_px,
                    target_px,
                    tolerance=incidence_margin,
                ):
                    continue
                target_key = _point_key(target_point)
                candidate = {
                    "target_point_id": target_id,
                    "target_kind": "existing_exact_graph_point",
                    "target_projected_point_px": [round(value, 6) for value in target_px],
                    "gap_px": round(gap, 6),
                    "direct_observed_crease_incidence": has_direct_incidence,
                    "_exact_point": target_point,
                }
                previous = unique_candidates.get(target_key)
                if previous is None or (
                    float(candidate["gap_px"]),
                    0,
                    target_id,
                ) < (
                    float(previous["gap_px"]),
                    0
                    if previous.get("target_kind") == "existing_exact_graph_point"
                    else 1,
                    str(previous["target_point_id"]),
                ):
                    unique_candidates[target_key] = candidate

            observed_endpoint = entities.get(observed_id, {}).get("observed_geometry")
            boundary_sides = (
                observed_endpoint.get("boundary_sides")
                if isinstance(observed_endpoint, Mapping)
                else None
            )
            explicit_boundary_sides = {str(side) for side in boundary_sides or []}
            inferred_boundary_limit = min(
                gap_limit,
                max(
                    boundary_margin,
                    incidence_margin + min(2.0, line_residual_tolerance),
                ),
            )
            endpoint_boundary_distances = {
                "top": abs(endpoint_px[1]),
                "right": abs(maximum - endpoint_px[0]),
                "bottom": abs(maximum - endpoint_px[1]),
                "left": abs(endpoint_px[0]),
            }
            inferred_boundary_sides = {
                side
                for side, distance in endpoint_boundary_distances.items()
                if not explicit_boundary_sides
                and distance <= inferred_boundary_limit + 1e-9
            }
            for side in sorted(explicit_boundary_sides | inferred_boundary_sides):
                inferred_boundary = side in inferred_boundary_sides
                boundary_point = _line_boundary_intersection(
                    line,
                    side,
                    side_length,
                )
                if boundary_point is None:
                    continue
                target_px = _project_to_pixel(boundary_point, side_length, maximum)
                gap = math.dist(endpoint_px, target_px)
                boundary_gap_limit = min(
                    gap_limit,
                    (
                        inferred_boundary_limit
                        if inferred_boundary
                        else max(0.0, max_target_residual_px)
                    ),
                )
                if gap > boundary_gap_limit + 1e-9:
                    continue
                if not _same_endpoint_ray(
                    endpoint_px,
                    other_px,
                    target_px,
                    tolerance=incidence_margin,
                ):
                    continue
                target_key = _point_key(boundary_point)
                candidate = {
                    "target_point_id": observed_id,
                    "target_kind": "known_paper_boundary_intersection",
                    "boundary_side": side,
                    "boundary_side_inferred": inferred_boundary,
                    "target_projected_point_px": [round(value, 6) for value in target_px],
                    "target_project_coordinate": [
                        qsqrt2_to_mapping(boundary_point[0]),
                        qsqrt2_to_mapping(boundary_point[1]),
                    ],
                    "gap_px": round(gap, 6),
                    "_exact_point": boundary_point,
                }
                previous = unique_candidates.get(target_key)
                if previous is None or (
                    float(candidate["gap_px"]),
                    1,
                    observed_id,
                ) < (
                    float(previous["gap_px"]),
                    0
                    if previous.get("target_kind") == "existing_exact_graph_point"
                    else 1,
                    str(previous["target_point_id"]),
                ):
                    unique_candidates[target_key] = candidate

            candidates = sorted(
                unique_candidates.values(),
                key=lambda item: (
                    float(item["gap_px"]),
                    0
                    if item.get("target_kind") == "existing_exact_graph_point"
                    else 1,
                    str(item["target_point_id"]),
                ),
            )
            if not candidates:
                detail = {
                    "segment_id": segment_id,
                    "endpoint": endpoint_name,
                    "observed_point_id": observed_id,
                    "reason": "no_existing_exact_node_within_gap_limit",
                    "endpoint_gap_limit_px": round(gap_limit, 6),
                }
                unresolved_occurrences.append(detail)
                closure_details[endpoint_name] = {"status": "unresolved", **detail}
                continue
            if (
                len(candidates) > 1
                and float(candidates[1]["gap_px"]) - float(candidates[0]["gap_px"])
                <= ambiguity_margin + 1e-9
            ):
                detail = {
                    "segment_id": segment_id,
                    "endpoint": endpoint_name,
                    "observed_point_id": observed_id,
                    "reason": "ambiguous_existing_exact_nodes",
                    "endpoint_gap_limit_px": round(gap_limit, 6),
                    "ambiguity_margin_px": round(ambiguity_margin, 6),
                    "candidates": [
                        {key: value for key, value in candidate.items() if key != "_exact_point"}
                        for candidate in candidates[:2]
                    ],
                }
                unresolved_occurrences.append(detail)
                closure_details[endpoint_name] = {"status": "unresolved", **detail}
                continue

            selected = candidates[0]
            target_id = str(selected["target_point_id"])
            resolved_ids[endpoint_name] = target_id
            changed_observed_ids.add(observed_id)
            target_kind = str(selected["target_kind"])
            if target_kind == "known_paper_boundary_intersection":
                segment[f"{endpoint_name}_exact_project_coordinate"] = selected[
                    "target_project_coordinate"
                ]
                boundary_resolved_observed_ids.add(observed_id)
            binding = {
                "segment_id": segment_id,
                "endpoint": endpoint_name,
                "reason": reason,
                "observed_point_id": observed_id,
                "resolved_point_id": target_id,
                "target_kind": target_kind,
                "observed_point_px": [round(value, 6) for value in endpoint_px],
                "resolved_projected_point_px": selected["target_projected_point_px"],
                "gap_px": selected["gap_px"],
                "endpoint_gap_limit_px": round(gap_limit, 6),
                "source": (
                    (
                        "selected_exact_crease_source_verified_near_paper_boundary"
                        if selected.get("boundary_side_inferred")
                        else "selected_exact_crease_known_paper_boundary_intersection"
                    )
                    if target_kind == "known_paper_boundary_intersection"
                    else (
                        "existing_exact_graph_node_on_selected_crease"
                        if selected.get("direct_observed_crease_incidence", True)
                        else "observed_segment_endpoint_near_proved_exact_node"
                    )
                ),
            }
            if selected.get("boundary_side"):
                binding["boundary_side"] = selected["boundary_side"]
                binding["boundary_side_inferred"] = bool(
                    selected.get("boundary_side_inferred")
                )
                binding["resolved_project_coordinate"] = selected[
                    "target_project_coordinate"
                ]
            bindings.append(binding)
            closure_details[endpoint_name] = {"status": "rebound", **binding}

        def resolved_exact(endpoint_name: str) -> ExactPoint | None:
            override = _exact_point(
                segment.get(f"{endpoint_name}_exact_project_coordinate")
            )
            return override or exact_points.get(resolved_ids[endpoint_name])

        unresolved_names = [
            name
            for name in ("start", "end")
            if resolved_exact(name) is None
        ]
        # A directly observed finite stroke can prove that a segment exists
        # even when both detector terminals are fuzzy.  In that case, and only
        # when each end has one unambiguous proved graph point on the same exact
        # crease in the outward direction, bind both ends to those bracketing
        # nodes instead of discarding the observed stroke.
        if len(unresolved_names) == 2 and line is not None:
            observed_by_name = {
                name: _observed_point(entities.get(original_ids[name], {}))
                for name in ("start", "end")
            }
            gap_limit = _endpoint_gap_limit(
                segment,
                maximum=maximum,
                incidence_margin=incidence_margin,
                line_residual_tolerance=line_residual_tolerance,
            )
            trusted_observed_segment = bool(
                segment.get("source") == _RAW_SEGMENT_SOURCE
                and float(segment.get("visible_coverage", 0.0) or 0.0) >= 0.80
                and float(segment.get("unsupported_length_px", math.inf) or 0.0)
                <= line_residual_tolerance + 1e-9
            )
            selected_by_name: dict[str, dict[str, Any]] = {}
            if trusted_observed_segment and all(observed_by_name.values()):
                for endpoint_name, other_name in (("start", "end"), ("end", "start")):
                    endpoint_px = observed_by_name[endpoint_name]
                    other_px = observed_by_name[other_name]
                    candidates: list[dict[str, Any]] = []
                    for target_id, target_point in exact_points.items():
                        if not _on_exact_line(target_point, *line):
                            continue
                        target_px = projected_points[target_id]
                        gap = math.dist(endpoint_px, target_px)
                        if gap > gap_limit + 1e-9 or not _same_endpoint_ray(
                            endpoint_px,
                            other_px,
                            target_px,
                            tolerance=incidence_margin,
                        ):
                            continue
                        candidates.append(
                            {
                                "target_point_id": target_id,
                                "target_projected_point_px": target_px,
                                "gap_px": gap,
                            }
                        )
                    candidates.sort(
                        key=lambda item: (
                            float(item["gap_px"]),
                            str(item["target_point_id"]),
                        )
                    )
                    if candidates and (
                        len(candidates) == 1
                        or float(candidates[1]["gap_px"])
                        - float(candidates[0]["gap_px"])
                        > ambiguity_margin + 1e-9
                    ):
                        selected_by_name[endpoint_name] = candidates[0]

            selected_ids = {
                str(item["target_point_id"])
                for item in selected_by_name.values()
            }
            if len(selected_by_name) == 2 and len(selected_ids) == 2:
                unresolved_occurrences = [
                    item
                    for item in unresolved_occurrences
                    if item.get("segment_id") != segment_id
                ]
                for endpoint_name in ("start", "end"):
                    selected = selected_by_name[endpoint_name]
                    observed_id = original_ids[endpoint_name]
                    target_id = str(selected["target_point_id"])
                    resolved_ids[endpoint_name] = target_id
                    changed_observed_ids.add(observed_id)
                    binding = {
                        "segment_id": segment_id,
                        "endpoint": endpoint_name,
                        "reason": "observed_segment_bracketed_by_proved_exact_nodes",
                        "observed_point_id": observed_id,
                        "resolved_point_id": target_id,
                        "target_kind": "existing_exact_graph_point",
                        "observed_point_px": [
                            round(value, 6)
                            for value in observed_by_name[endpoint_name]
                        ],
                        "resolved_projected_point_px": [
                            round(value, 6)
                            for value in selected["target_projected_point_px"]
                        ],
                        "gap_px": round(float(selected["gap_px"]), 6),
                        "endpoint_gap_limit_px": round(gap_limit, 6),
                        "source": "observed_segment_between_proved_exact_nodes",
                        "parent_entity_ids": [crease_id],
                    }
                    bindings.append(binding)
                    closure_details[endpoint_name] = {
                        "status": "rebound",
                        **binding,
                    }

        unresolved_names = [
            name
            for name in ("start", "end")
            if resolved_exact(name) is None
        ]
        # A raster segment may acquire one missing endpoint from the exact
        # intersection of already proved creases, but only after its opposite
        # endpoint is construction-proved.  This prevents a detached two-ended
        # raster fragment from becoming a crease merely because nearby exact
        # supporting lines happen to cross it.
        if len(unresolved_names) == 1:
            endpoint_name = unresolved_names[0]
            other_name = "end" if endpoint_name == "start" else "start"
            if resolved_exact(other_name) is not None and line is not None:
                observed_id = original_ids[endpoint_name]
                endpoint_px = _observed_point(entities.get(observed_id, {}))
                other_px = _observed_point(entities.get(original_ids[other_name], {}))
                gap_limit = _endpoint_gap_limit(
                    segment,
                    maximum=maximum,
                    incidence_margin=incidence_margin,
                    line_residual_tolerance=line_residual_tolerance,
                )
                grouped: dict[
                    tuple[tuple[int, int, int], ...], dict[str, Any]
                ] = {}
                if endpoint_px is not None:
                    for other_crease_id, other_line in exact_lines.items():
                        if other_crease_id == crease_id:
                            continue
                        candidate_point = _exact_line_intersection(line, other_line)
                        if candidate_point is None or not all(
                            -1e-9 <= float(value) <= float(side_length) + 1e-9
                            for value in candidate_point
                        ):
                            continue
                        candidate_px = _project_to_pixel(
                            candidate_point, side_length, maximum
                        )
                        gap = math.dist(endpoint_px, candidate_px)
                        if gap > gap_limit + 1e-9 or not _same_endpoint_ray(
                            endpoint_px,
                            other_px,
                            candidate_px,
                            tolerance=incidence_margin,
                        ):
                            continue
                        key = _point_key(candidate_point)
                        item = grouped.setdefault(
                            key,
                            {
                                "_exact_point": candidate_point,
                                "projected_point_px": candidate_px,
                                "gap_px": gap,
                                "parent_entity_ids": {crease_id},
                                "source_supported_parent_ids": set(),
                                "terminal_supported_parent_ids": set(),
                            },
                        )
                        item["parent_entity_ids"].add(other_crease_id)
                        if _observed_crease_supports_point(
                            entities.get(other_crease_id, {}),
                            candidate_px,
                            incidence_margin=incidence_margin,
                            line_residual_tolerance=line_residual_tolerance,
                        ):
                            item["source_supported_parent_ids"].add(other_crease_id)
                        if _observed_crease_terminal_near_point(
                            entities.get(other_crease_id, {}),
                            candidate_px,
                            maximum_gap=incidence_margin
                            + line_residual_tolerance,
                            line_residual_tolerance=line_residual_tolerance,
                        ):
                            item["terminal_supported_parent_ids"].add(
                                other_crease_id
                            )

                candidates: list[dict[str, Any]] = []
                for item in grouped.values():
                    parents = sorted(item["parent_entity_ids"])
                    source_supported = sorted(item["source_supported_parent_ids"])
                    terminal_supported = sorted(
                        item["terminal_supported_parent_ids"]
                    )
                    # Exact concurrence alone does not establish a finite
                    # junction. Accept direct finite incidence, or a concurrence
                    # where at least two other observed crease terminals stop
                    # close to the same exact point.
                    if not source_supported and not (
                        len(parents) >= 3 and len(terminal_supported) >= 2
                    ):
                        continue
                    candidates.append(
                        {
                            **item,
                            "parent_entity_ids": parents,
                            "source_supported_parent_ids": source_supported,
                            "terminal_supported_parent_ids": terminal_supported,
                        }
                    )
                candidates.sort(
                    key=lambda item: (
                        float(item["gap_px"]),
                        -len(item["parent_entity_ids"]),
                        item["parent_entity_ids"],
                    )
                )
                unambiguous = bool(candidates) and (
                    len(candidates) == 1
                    or float(candidates[1]["gap_px"])
                    - float(candidates[0]["gap_px"])
                    > ambiguity_margin + 1e-9
                )
                if unambiguous:
                    selected = candidates[0]
                    point = selected["_exact_point"]
                    segment[f"{endpoint_name}_exact_project_coordinate"] = [
                        qsqrt2_to_mapping(point[0]),
                        qsqrt2_to_mapping(point[1]),
                    ]
                    unresolved_occurrences = [
                        item
                        for item in unresolved_occurrences
                        if not (
                            item.get("segment_id") == segment_id
                            and item.get("endpoint") == endpoint_name
                        )
                    ]
                    binding = {
                        "segment_id": segment_id,
                        "endpoint": endpoint_name,
                        "reason": "missing_exact_endpoint",
                        "observed_point_id": observed_id,
                        "resolved_point_id": observed_id,
                        "target_kind": "proved_exact_crease_intersection",
                        "observed_point_px": [round(value, 6) for value in endpoint_px],
                        "resolved_projected_point_px": [
                            round(value, 6)
                            for value in selected["projected_point_px"]
                        ],
                        "gap_px": round(float(selected["gap_px"]), 6),
                        "endpoint_gap_limit_px": round(gap_limit, 6),
                        "source": "proved_exact_crease_intersection_near_observed_endpoint",
                        "parent_entity_ids": selected["parent_entity_ids"],
                        "source_supported_parent_entity_ids": selected[
                            "source_supported_parent_ids"
                        ],
                        "terminal_supported_parent_entity_ids": selected[
                            "terminal_supported_parent_ids"
                        ],
                    }
                    bindings.append(binding)
                    closure_details[endpoint_name] = {
                        "status": "rebound",
                        **binding,
                    }
                    derived_endpoint_binding_count += 1

        if all(resolved_exact(name) is None for name in ("start", "end")):
            unresolved_occurrences = [
                item
                for item in unresolved_occurrences
                if item.get("segment_id") != segment_id
            ]
            suppressed_unanchored_segments.append(
                {
                    "id": segment_id,
                    "reason": "no_construction_proved_endpoint",
                    "observed_start_point_id": original_ids["start"],
                    "observed_end_point_id": original_ids["end"],
                    "length_px": segment.get("length_px"),
                }
            )
            continue

        segment["start_point_id"] = resolved_ids["start"]
        segment["end_point_id"] = resolved_ids["end"]
        segment["endpoint_closure"] = closure_details
        start_exact = _exact_point(segment.get("start_exact_project_coordinate"))
        if start_exact is None:
            start_exact = exact_points.get(resolved_ids["start"])
        end_exact = _exact_point(segment.get("end_exact_project_coordinate"))
        if end_exact is None:
            end_exact = exact_points.get(resolved_ids["end"])
        if start_exact is not None and end_exact is not None and start_exact == end_exact:
            collapsed_segments.append(
                {
                    "id": segment_id,
                    "reason": "collapsed_detector_linehead",
                    "observed_start_point_id": original_ids["start"],
                    "observed_end_point_id": original_ids["end"],
                    "resolved_point_id": resolved_ids["start"],
                    "length_px": segment.get("length_px"),
                }
            )
            continue
        retained_segments.append(segment)

    retained_endpoint_ids = {
        str(segment.get(key) or "")
        for segment in retained_segments
        for key in ("start_point_id", "end_point_id")
    }
    absorbed_observed_ids = sorted(changed_observed_ids - retained_endpoint_ids)
    unresolved_point_ids = sorted(
        {
            str(item.get("observed_point_id") or "")
            for item in unresolved_occurrences
            if item.get("observed_point_id")
        }
    )
    endpoint_occurrences: dict[
        tuple[tuple[int, int, int], ...], list[dict[str, Any]]
    ] = defaultdict(list)
    for segment in retained_segments:
        for endpoint_name in ("start", "end"):
            point_id = str(segment.get(f"{endpoint_name}_point_id") or "")
            point = _exact_point(
                segment.get(f"{endpoint_name}_exact_project_coordinate")
            )
            if point is None:
                point = exact_points.get(point_id)
            if point is None:
                continue
            endpoint_occurrences[_point_key(point)].append(
                {
                    "segment_id": str(segment.get("id") or ""),
                    "endpoint": endpoint_name,
                    "point_id": point_id,
                    "crease_entity_id": str(segment.get("crease_entity_id") or ""),
                    "point": point,
                }
            )
    dangling_occurrences = [
        occurrences[0]
        for occurrences in endpoint_occurrences.values()
        if len(occurrences) == 1
        and not _on_boundary(occurrences[0]["point"], side_length)
    ]
    dangling_point_ids = sorted(
        {str(item["point_id"]) for item in dangling_occurrences}
    )
    closure = {
        "enabled": True,
        "mode": "per_finite_segment_existing_node_closure_v1",
        "status": "complete" if not unresolved_occurrences else "partial",
        "source_segment_count": len(source_segments),
        "retained_segment_count": len(retained_segments),
        "binding_count": len(bindings),
        "derived_endpoint_binding_count": derived_endpoint_binding_count,
        "collapsed_segment_count": len(collapsed_segments),
        "suppressed_unanchored_segment_count": len(
            suppressed_unanchored_segments
        ),
        "unresolved_endpoint_occurrence_count": len(unresolved_occurrences),
        "unresolved_endpoint_point_count": len(unresolved_point_ids),
        "unresolved_endpoint_point_ids": unresolved_point_ids,
        "internal_dangling_endpoint_count": len(dangling_occurrences),
        "internal_dangling_endpoint_point_ids": dangling_point_ids,
        "absorbed_observed_point_ids": absorbed_observed_ids,
        "resolved_boundary_observed_point_ids": sorted(
            boundary_resolved_observed_ids
        ),
        "bindings": bindings,
        "collapsed_segments": collapsed_segments,
        "suppressed_unanchored_segments": suppressed_unanchored_segments,
        "unresolved_endpoint_occurrences": unresolved_occurrences,
        "crease_placement_repair_count": 0,
        "crease_placement_repairs": [],
        "input_unresolved_crease_count": unresolved_creases,
        "tolerances": {
            "paper_scale_gap_fraction": 0.05,
            "local_segment_length_fraction": 0.75,
            "incidence_margin_multiplier": 2.0,
            "line_residual_tolerance_px": round(line_residual_tolerance, 6),
            "ambiguity_margin_px": round(ambiguity_margin, 6),
            "maximum_target_observation_residual_px": round(
                max(0.0, max_target_residual_px), 6
            ),
        },
        "invariants": {
            "global_observed_point_merges": 0,
            "generated_point_count": 0,
            "generated_crease_count": 0,
            "generated_direction_count": 0,
            "generated_segment_count": 0,
            "non_boundary_targets_are_existing_exact_graph_points": True,
            "boundary_targets_are_exact_crease_boundary_intersections": True,
            "targets_lie_on_selected_exact_crease": True,
            "closure_is_per_segment_endpoint": True,
            "derived_endpoint_requires_opposite_proved_endpoint": True,
            "derived_endpoint_requires_proved_parent_creases": True,
            "two_unproved_endpoints_require_a_bracketed_observed_segment": True,
            "exact_concurrence_requires_finite_incidence_or_multiple_near_terminals": True,
            "nonincident_target_requires_direct_observed_segment_bracketing": True,
            "inferred_boundary_target_requires_a_nearby_observed_terminal": True,
            "crease_placement_repair_allowed": False,
            "endpoint_targets_require_construction_proof": True,
            "crease_lines_require_construction_proof": True,
            "proved_subgraph_closure_runs_before_global_crease_completion": True,
        },
    }

    closed = copy.deepcopy(dict(topology))
    closed.update(
        {
            "enabled": True,
            "mode": _CLOSED_TOPOLOGY_MODE,
            "source_topology_mode": _RAW_TOPOLOGY_MODE,
            "source_segment_count": len(source_segments),
            "segment_count": len(retained_segments),
            "segments": retained_segments,
            "endpoint_closure": closure,
        }
    )
    invariants = dict(closed.get("invariants") or {})
    invariants.update(closure["invariants"])
    closed["invariants"] = invariants

    return closed


__all__ = ["build_finite_endpoint_closed_topology"]
