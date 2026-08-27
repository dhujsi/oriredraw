"""Close detector endpoints onto existing exact nodes or known paper edges.

The raw topology intentionally records where the line detector stopped.  A
detector terminal can sit a few pixels before an already evidenced exact
intersection or paper-boundary contact.  This module resolves that discrepancy
per finite segment endpoint; it never merges the observed point globally,
creates a point, creates a crease, or chooses a direction.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Mapping

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
    _crease_exact_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    _allow_crease_repair: bool = True,
) -> dict[str, Any]:
    """Rebind each finite segment endpoint to an existing exact graph point.

    A target is eligible only when it is an existing raw-evidence exact point,
    or the intersection of this exact crease with a boundary side already
    observed at the endpoint.  Its projected location must remain within both
    the paper-scale and local segment-evidence limits.  Near ties are rejected
    instead of guessed.
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
    unresolved_creases = (
        int(propagation.get("unresolved_crease_count", 0) or 0)
        if isinstance(propagation, Mapping)
        else 1
    )
    if guided_report.get("phase") != "complete_existing_creases" or unresolved_creases:
        return {
            "enabled": False,
            "mode": _CLOSED_TOPOLOGY_MODE,
            "reason": "exact_crease_graph_incomplete",
            "unresolved_crease_count": unresolved_creases,
        }

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
    exact_points: dict[str, ExactPoint] = {}
    projected_points: dict[str, tuple[float, float]] = {}
    observed_points: dict[str, tuple[float, float]] = {}
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
        if _RAW_SEGMENT_SOURCE not in set(entity.get("evidence_sources") or []):
            continue
        projected = _project_to_pixel(point, side_length, maximum)
        if math.dist(projected, observed) > max(0.0, max_target_residual_px) + 1e-9:
            continue
        exact_points[entity_id] = point
        projected_points[entity_id] = projected
        observed_points[entity_id] = observed

    exact_lines: dict[str, tuple[ExactPoint, int]] = {}
    for entity_id, entity in entities.items():
        if entity.get("kind") != "crease":
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
    crease_exact_overrides = {
        str(crease_id): copy.deepcopy(dict(override))
        for crease_id, override in (_crease_exact_overrides or {}).items()
        if isinstance(override, Mapping)
    }
    for crease_id, override in crease_exact_overrides.items():
        through = _exact_point(override.get("through_point_project"))
        try:
            direction = int(override.get("direction_index"))
        except (TypeError, ValueError):
            continue
        if through is not None and 0 <= direction < 8:
            exact_lines[crease_id] = through, direction

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
    ambiguity_margin = min(0.75, max(0.25, incidence_margin * 0.1))
    source_segments = [
        item for item in topology.get("segments", []) if isinstance(item, Mapping)
    ]
    retained_segments: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    collapsed_segments: list[dict[str, Any]] = []
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
            for side in boundary_sides or []:
                boundary_point = _line_boundary_intersection(
                    line,
                    str(side),
                    side_length,
                )
                if boundary_point is None:
                    continue
                target_px = _project_to_pixel(boundary_point, side_length, maximum)
                gap = math.dist(endpoint_px, target_px)
                if gap > min(gap_limit, max(0.0, max_target_residual_px)) + 1e-9:
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
                    "boundary_side": str(side),
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
                    "selected_exact_crease_known_paper_boundary_intersection"
                    if target_kind == "known_paper_boundary_intersection"
                    else "existing_exact_graph_node_on_selected_crease"
                ),
            }
            if selected.get("boundary_side"):
                binding["boundary_side"] = selected["boundary_side"]
                binding["resolved_project_coordinate"] = selected[
                    "target_project_coordinate"
                ]
            bindings.append(binding)
            closure_details[endpoint_name] = {"status": "rebound", **binding}

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
        "collapsed_segment_count": len(collapsed_segments),
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
        "unresolved_endpoint_occurrences": unresolved_occurrences,
        "crease_placement_repair_count": len(crease_exact_overrides),
        "crease_placement_repairs": list(crease_exact_overrides.values()),
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
            "crease_repair_keeps_observed_direction": True,
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
            "crease_exact_overrides": crease_exact_overrides,
        }
    )
    invariants = dict(closed.get("invariants") or {})
    invariants.update(closure["invariants"])
    closed["invariants"] = invariants

    if _allow_crease_repair and dangling_occurrences:
        replacement_targets: dict[str, set[str]] = defaultdict(set)
        for binding in bindings:
            if binding.get("target_kind") != "existing_exact_graph_point":
                continue
            observed_id = str(binding.get("observed_point_id") or "")
            target_id = str(binding.get("resolved_point_id") or "")
            if observed_id and target_id and observed_id != target_id:
                replacement_targets[observed_id].add(target_id)

        proposed_repairs: dict[str, dict[str, Any]] = {}
        segment_index = {
            str(segment.get("id") or ""): segment for segment in retained_segments
        }
        for occurrence in dangling_occurrences:
            observed_id = str(occurrence["point_id"])
            crease_id = str(occurrence["crease_entity_id"])
            if crease_id in proposed_repairs or crease_id not in exact_lines:
                continue
            segment = segment_index.get(str(occurrence["segment_id"]))
            endpoint_px = _observed_point(entities.get(observed_id, {}))
            crease_entity = entities.get(crease_id, {})
            observed_crease = crease_entity.get("observed_geometry")
            if (
                segment is None
                or endpoint_px is None
                or not isinstance(observed_crease, Mapping)
            ):
                continue
            try:
                angle = math.radians(float(observed_crease.get("angle_deg")))
                offset = float(observed_crease.get("line_offset_px"))
                match_tolerance = min(
                    3.2,
                    max(0.0, float(observed_crease.get("match_tolerance_px", 0.85))),
                )
            except (TypeError, ValueError):
                continue
            _, direction = exact_lines[crease_id]
            gap_limit = _endpoint_gap_limit(
                segment,
                maximum=maximum,
                incidence_margin=incidence_margin,
                line_residual_tolerance=line_residual_tolerance,
            )
            candidates: list[tuple[tuple[float, float, str], dict[str, Any]]] = []
            for target_id in sorted(replacement_targets.get(observed_id, ())):
                target = exact_points.get(target_id)
                target_px = projected_points.get(target_id)
                if target is None or target_px is None:
                    continue
                gap = math.dist(endpoint_px, target_px)
                if gap > gap_limit + 1e-9:
                    continue
                residual = abs(
                    -math.sin(angle) * target_px[0]
                    + math.cos(angle) * target_px[1]
                    - offset
                )
                if residual > match_tolerance + 1e-9:
                    continue
                original_through = exact_lines[crease_id][0]
                if _on_exact_line(target, original_through, direction):
                    continue
                repair = {
                    "crease_entity_id": crease_id,
                    "source": "dangling_endpoint_existing_node_incidence_repair",
                    "trigger_observed_point_id": observed_id,
                    "resolved_point_id": target_id,
                    "through_point_project": [
                        qsqrt2_to_mapping(target[0]),
                        qsqrt2_to_mapping(target[1]),
                    ],
                    "direction_index": direction,
                    "original_through_point_project": [
                        qsqrt2_to_mapping(original_through[0]),
                        qsqrt2_to_mapping(original_through[1]),
                    ],
                    "endpoint_gap_px": round(gap, 6),
                    "observed_line_residual_px": round(residual, 6),
                    "match_tolerance_px": round(match_tolerance, 6),
                    "generated_crease_count": 0,
                    "generated_direction_count": 0,
                }
                candidates.append(((residual, gap, target_id), repair))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                proposed_repairs[crease_id] = candidates[0][1]

        if proposed_repairs:
            repaired = build_finite_endpoint_closed_topology(
                guided_report,
                max_target_residual_px=max_target_residual_px,
                _crease_exact_overrides=proposed_repairs,
                _allow_crease_repair=False,
            )
            repaired_closure = repaired.get("endpoint_closure")
            if isinstance(repaired_closure, Mapping):
                repaired_unresolved = int(
                    repaired_closure.get("unresolved_endpoint_occurrence_count", 0) or 0
                )
                repaired_dangling = int(
                    repaired_closure.get("internal_dangling_endpoint_count", 0) or 0
                )
                if (
                    repaired_unresolved <= len(unresolved_occurrences)
                    and repaired_dangling < len(dangling_occurrences)
                ):
                    return repaired
    return closed


__all__ = ["build_finite_endpoint_closed_topology"]
