"""Generate exact canonical ray candidates from construction-proved points.

This stage is deliberately geometry-only.  It neither reads raster angles nor
adds a crease to the proof topology.  Image evidence is allowed to rank these
candidates in a later stage, but can never choose a new direction or source
point here.
"""

from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter
from typing import Any, Mapping

from constrained_angle_candidates import (
    _nearest_contact,
    _raw_observations,
    _ray_evidence,
)
from exact_qsqrt2 import Qsqrt2
from qsqrt2_coordinates import qsqrt2_from_mapping, qsqrt2_to_mapping
from transactional_angle_repair import (
    _audit,
    _materialize,
    _normalise_segments,
    _split_segments_at_point,
    _unexpected_candidate_intersection,
)


ExactPoint = tuple[Qsqrt2, Qsqrt2]
_MODE = "proved_node_canonical_ray_candidates_v1"
_APPLICATION_MODE = "proved_canonical_ray_application_v1"
_MAX_APPLICATION_ROUNDS = 8
_MAX_APPLIED_RAYS = 256


def _exact_point(raw: Any) -> ExactPoint | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return qsqrt2_from_mapping(raw[0]), qsqrt2_from_mapping(raw[1])
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _direction_vector(direction_index: int) -> ExactPoint:
    one = Qsqrt2(1)
    zero = Qsqrt2()
    diagonal = Qsqrt2(1, 1)
    first_half = (
        (one, zero),
        (diagonal, one),
        (one, one),
        (one, diagonal),
        (zero, one),
        (-one, diagonal),
        (-one, one),
        (-diagonal, one),
    )
    if not 0 <= direction_index < 16:
        raise ValueError(f"invalid directed 22.5-degree index: {direction_index}")
    if direction_index < 8:
        return first_half[direction_index]
    opposite = first_half[direction_index - 8]
    return -opposite[0], -opposite[1]


def _point_key(point: ExactPoint) -> tuple[float, float]:
    return round(float(point[0]), 12), round(float(point[1]), 12)


def _boundary_exit(
    point: ExactPoint,
    direction: ExactPoint,
    side_length: Qsqrt2,
) -> tuple[ExactPoint, list[str], Qsqrt2] | None:
    zero = Qsqrt2()
    x, y = point
    dx, dy = direction

    # A boundary point may emit only into the paper.  Tangent and outward rays
    # are not internal crease candidates.
    if x == zero and dx <= zero:
        return None
    if x == side_length and dx >= zero:
        return None
    if y == zero and dy <= zero:
        return None
    if y == side_length and dy >= zero:
        return None

    hits: list[tuple[Qsqrt2, str]] = []
    if dx > zero:
        hits.append(((side_length - x) / dx, "right"))
    elif dx < zero:
        hits.append(((zero - x) / dx, "left"))
    if dy > zero:
        hits.append(((side_length - y) / dy, "bottom"))
    elif dy < zero:
        hits.append(((zero - y) / dy, "top"))
    positive = [(distance, side) for distance, side in hits if distance > zero]
    if not positive:
        return None
    distance = min((item[0] for item in positive), key=float)
    sides = sorted(
        side for candidate_distance, side in positive if candidate_distance == distance
    )
    endpoint = x + distance * dx, y + distance * dy
    return endpoint, sides, distance


def _directed_index(first: ExactPoint, second: ExactPoint) -> int | None:
    dx = float(second[0] - first[0])
    dy = float(second[1] - first[1])
    if math.hypot(dx, dy) <= 1e-12:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    index = int(round(angle / 22.5)) % 16
    expected = index * 22.5
    error = abs((angle - expected + 180.0) % 360.0 - 180.0)
    return index if error <= 1e-7 else None


def _candidate_id(point_id: str, direction_index: int) -> str:
    digest = hashlib.sha1(
        f"{point_id}|canonical-22.5|{direction_index}".encode("utf-8")
    ).hexdigest()[:12]
    return f"proof-ray-{digest}"


def _cp_point(raw: Any, side_length: Qsqrt2) -> tuple[float, float] | None:
    point = _exact_point(raw)
    if point is None or side_length <= Qsqrt2():
        return None
    return (
        float(point[0] / side_length) * 400.0 - 200.0,
        float(point[1] / side_length) * 400.0 - 200.0,
    )


def _float_point(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        point = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(value) for value in point) else None


def _float_point_key(point: tuple[float, float]) -> tuple[float, float]:
    return round(point[0], 6), round(point[1], 6)


def _segment_geometry_key(
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    return tuple(sorted((_float_point_key(first), _float_point_key(second))))


def _segment_geometries(
    segments: list[dict[str, Any]],
) -> set[tuple[tuple[float, float], tuple[float, float]]]:
    output: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for segment in segments:
        start = _float_point(segment.get("start_cp"))
        end = _float_point(segment.get("end_cp"))
        if start is not None and end is not None:
            output.add(_segment_geometry_key(start, end))
    return output


def _on_paper_boundary(point: tuple[float, float], tolerance: float = 1e-5) -> bool:
    return any(
        abs(value - boundary) <= tolerance
        for value in point
        for boundary in (-200.0, 200.0)
    )


def _point_on_segment(
    point: tuple[float, float],
    segment: Mapping[str, Any],
    tolerance: float = 1e-5,
) -> bool:
    start = _float_point(segment.get("start_cp"))
    end = _float_point(segment.get("end_cp"))
    if start is None or end is None:
        return False
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= tolerance * tolerance:
        return False
    parameter = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / length_squared
    if not -tolerance <= parameter <= 1.0 + tolerance:
        return False
    projected = start[0] + parameter * dx, start[1] + parameter * dy
    return math.dist(point, projected) <= tolerance


def _point_attached_to_topology(
    point: tuple[float, float],
    segments: list[dict[str, Any]],
) -> bool:
    return _on_paper_boundary(point) or any(
        _point_on_segment(point, segment) for segment in segments
    )


def _prune_internal_dangling_segments(
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove finite leaves whose free endpoint is not on the paper boundary."""

    working = copy.deepcopy(segments)
    pruned_ids: list[str] = []
    while working:
        degrees: Counter[tuple[float, float]] = Counter()
        endpoints: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {}
        for segment in working:
            start = _float_point(segment.get("start_cp"))
            end = _float_point(segment.get("end_cp"))
            if start is None or end is None:
                continue
            start_key, end_key = _float_point_key(start), _float_point_key(end)
            endpoints[str(segment.get("id") or "")] = start_key, end_key
            degrees[start_key] += 1
            degrees[end_key] += 1
        dangling = {
            point
            for point, degree in degrees.items()
            if degree == 1 and not _on_paper_boundary(point)
        }
        if not dangling:
            break
        retained: list[dict[str, Any]] = []
        removed_this_round = 0
        for segment in working:
            segment_id = str(segment.get("id") or "")
            points = endpoints.get(segment_id)
            if points is not None and (points[0] in dangling or points[1] in dangling):
                pruned_ids.append(segment_id)
                removed_this_round += 1
            else:
                retained.append(segment)
        working = retained
        if not removed_this_round:
            break
    return working, sorted(set(pruned_ids))


def _line_type_from_evidence(evidence: Mapping[str, Any]) -> tuple[int, str] | None:
    channels = {str(item) for item in evidence.get("source_channels", []) if str(item)}
    if "red" in channels and "blue" in channels:
        return None
    if "blue" in channels:
        return 3, "source_image_color_evidence"
    if "red" in channels:
        return 2, "source_image_color_evidence"
    # Black, gray and otherwise neutral strokes have geometry evidence but no
    # reliable MV colour.  The existing product rule keeps them as mountain
    # folds in the unverified draft; this choice does not create geometry.
    return 2, "source_image_neutral_default_mountain"


def _replace_camv_diagnostic(
    base_contract: Mapping[str, Any],
    segments: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
) -> dict[str, Any]:
    contract = copy.deepcopy(dict(base_contract))
    materialized = _materialize(segments)
    audit = _audit(materialized)
    blockers = [
        copy.deepcopy(dict(item))
        for item in contract.get("blockers", [])
        if isinstance(item, Mapping)
        and str(item.get("code") or "") != "camv_foldability_violations"
    ]
    violation_count = int(audit.get("violation_count", 0) or 0)
    if violation_count:
        blockers.append(
            {
                "code": "camv_foldability_violations",
                "count": violation_count,
                "rule_counts": dict(audit.get("rule_counts") or {}),
                "violations": list(audit.get("violations") or []),
            }
        )
    blocker_counts: Counter[str] = Counter()
    for blocker in blockers:
        try:
            count = int(blocker.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        blocker_counts[str(blocker.get("code") or "unknown")] += max(1, count)
    gate_results = copy.deepcopy(dict(contract.get("gate_results") or {}))
    gate_results["flat_foldability"] = {
        "passed": violation_count == 0,
        "blocker_codes": [] if violation_count == 0 else ["camv_foldability_violations"],
    }
    invariants = copy.deepcopy(dict(contract.get("invariants") or {}))
    previous_generated = int(invariants.get("generated_internal_segment_count", 0) or 0)
    invariants.update(
        {
            "generated_internal_segment_count": previous_generated + len(accepted),
            "generated_direction_count": 0,
            "image_supported_canonical_segment_count": len(accepted),
            "all_generated_segments_start_at_proved_points": True,
            "all_generated_directions_are_exact_22_5_multiples": True,
            "all_generated_segments_end_at_first_exact_contact": True,
            "raster_created_direction_count": 0,
        }
    )
    checks_passed = bool(contract.get("enabled", False)) and not blockers
    contract.update(
        {
            "status": "ready" if checks_passed else "unverified_canonical_rays_applied",
            "output_ready": checks_passed,
            "checks_passed": checks_passed,
            "cp_available": bool(materialized["cp"]),
            "cp": materialized["cp"],
            "candidate_internal_segment_count": len(segments),
            "typed_candidate_internal_segment_count": len(segments),
            "draft_internal_segment_count": len(segments),
            "boundary_segment_count": materialized["boundary_segment_count"],
            "boundary_segment_counts_by_side": materialized[
                "boundary_segment_counts_by_side"
            ],
            "checked_boundary_segment_count": materialized["boundary_segment_count"],
            "checked_boundary_segment_counts_by_side": materialized[
                "boundary_segment_counts_by_side"
            ],
            "candidate_segments": segments,
            "gate_results": gate_results,
            "blocker_count": len(blockers),
            "blocker_counts": dict(sorted(blocker_counts.items())),
            "blockers": blockers,
            "soft_diagnostics": {
                "camv": audit,
                "camv_blocks_output": False,
                "camv_blocks_verification": True,
            },
            "canonical_ray_application": {
                "applied": bool(accepted),
                "accepted_candidate_ids": [item["candidate_id"] for item in accepted],
                "accepted_segment_ids": [item["segment_id"] for item in accepted],
            },
            "invariants": invariants,
        }
    )
    return contract


def build_proved_node_canonical_ray_candidates(
    geometry_graph: Mapping[str, Any] | None,
    topology: Mapping[str, Any] | None,
    construction_proof: Mapping[str, Any] | None,
    side_length_mapping: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return unapplied 22.5-degree rays rooted only at proved exact points."""

    if not isinstance(geometry_graph, Mapping):
        return {"enabled": False, "mode": _MODE, "reason": "missing_geometry_graph"}
    if not isinstance(topology, Mapping) or not topology.get("enabled", False):
        return {"enabled": False, "mode": _MODE, "reason": "missing_topology"}
    if not isinstance(construction_proof, Mapping) or not construction_proof.get(
        "enabled", False
    ):
        return {
            "enabled": False,
            "mode": _MODE,
            "reason": "missing_construction_proof",
        }
    try:
        side_length = (
            qsqrt2_from_mapping(side_length_mapping)
            if isinstance(side_length_mapping, Mapping)
            else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        side_length = None
    if side_length is None or side_length <= Qsqrt2():
        return {"enabled": False, "mode": _MODE, "reason": "missing_side_length"}

    entities = {
        str(item.get("id")): item
        for item in geometry_graph.get("entities", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    exact_points = {
        entity_id: point
        for entity_id, entity in entities.items()
        if entity.get("kind") == "point"
        and (point := _exact_point(
            (entity.get("exact_geometry") or {}).get("project_coordinate")
            if isinstance(entity.get("exact_geometry"), Mapping)
            else None
        ))
        is not None
    }
    proved_point_ids = {
        str(item)
        for item in construction_proof.get("proved_point_ids", [])
        if str(item) in exact_points
    }
    proved_segment_ids = {
        str(item) for item in construction_proof.get("proved_segment_ids", [])
    }
    occupied: dict[str, set[int]] = {point_id: set() for point_id in proved_point_ids}
    segments = [
        item for item in topology.get("segments", []) if isinstance(item, Mapping)
    ]
    for segment in segments:
        if str(segment.get("id") or "") not in proved_segment_ids:
            continue
        start_id = str(segment.get("start_point_id") or "")
        end_id = str(segment.get("end_point_id") or "")
        start = _exact_point(segment.get("start_exact_project_coordinate"))
        if start is None:
            start = exact_points.get(start_id)
        end = _exact_point(segment.get("end_exact_project_coordinate"))
        if end is None:
            end = exact_points.get(end_id)
        if start is None or end is None:
            continue
        if start_id in occupied:
            index = _directed_index(start, end)
            if index is not None:
                occupied[start_id].add(index)
        if end_id in occupied:
            index = _directed_index(end, start)
            if index is not None:
                occupied[end_id].add(index)

    entity_proof = {
        str(item.get("id")): item
        for item in construction_proof.get("entity_records", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    candidates: list[dict[str, Any]] = []
    boundary_rejected = 0
    for point_id in sorted(proved_point_ids):
        source = exact_points[point_id]
        for direction_index in range(16):
            if direction_index in occupied[point_id]:
                continue
            direction = _direction_vector(direction_index)
            exit_data = _boundary_exit(source, direction, side_length)
            if exit_data is None:
                boundary_rejected += 1
                continue
            endpoint, boundary_sides, distance = exit_data
            proof_record = entity_proof.get(point_id, {})
            candidates.append(
                {
                    "id": _candidate_id(point_id, direction_index),
                    "kind": "canonical_22_5_ray",
                    "status": "unapplied_candidate",
                    "source_point_id": point_id,
                    "source_point_project": [
                        qsqrt2_to_mapping(source[0]),
                        qsqrt2_to_mapping(source[1]),
                    ],
                    "source_point_proof_kind": proof_record.get("proof_kind"),
                    "parent_entity_ids": [point_id],
                    "directed_direction_index": direction_index,
                    "line_orientation_index": direction_index % 8,
                    "direction_deg": round(direction_index * 22.5, 6),
                    "direction_vector_project": [
                        qsqrt2_to_mapping(direction[0]),
                        qsqrt2_to_mapping(direction[1]),
                    ],
                    "search_endpoint_project": [
                        qsqrt2_to_mapping(endpoint[0]),
                        qsqrt2_to_mapping(endpoint[1]),
                    ],
                    "search_endpoint_boundary_sides": boundary_sides,
                    "maximum_parameter": qsqrt2_to_mapping(distance),
                    "generation_rule": "canonical_22_5_ray_from_proved_point",
                    "image_evidence_status": "not_evaluated",
                }
            )

    return {
        "enabled": True,
        "mode": _MODE,
        "status": "candidates_available" if candidates else "no_new_canonical_rays",
        "source_proved_point_count": len(proved_point_ids),
        "candidate_count": len(candidates),
        "occupied_proved_ray_count": sum(len(items) for items in occupied.values()),
        "boundary_outward_or_tangent_rejection_count": boundary_rejected,
        "candidates": candidates,
        "invariants": {
            "all_candidates_start_at_construction_proved_points": True,
            "all_directions_are_exact_22_5_degree_multiples": True,
            "raster_angles_choose_no_candidate_direction": True,
            "candidates_are_not_applied_to_topology": True,
            "search_intervals_end_at_known_paper_boundary": True,
            "existing_proved_rays_are_not_duplicated": True,
        },
    }


def apply_image_supported_canonical_rays(
    candidate_report: Mapping[str, Any] | None,
    raw_report: Mapping[str, Any] | None,
    base_contract: Mapping[str, Any] | None,
    side_length_mapping: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply proved 22.5-degree rays with continuous image support.

    A candidate direction and source have already been fixed by construction.
    Raster data may only accept or reject that finite ray.  The ray ends at the
    first exact segment or paper-boundary contact, never at a fitted image point.
    """

    candidate_report = candidate_report if isinstance(candidate_report, Mapping) else {}
    raw_report = raw_report if isinstance(raw_report, Mapping) else {}
    base_contract = base_contract if isinstance(base_contract, Mapping) else {}
    if not candidate_report.get("enabled", False):
        return {
            "enabled": False,
            "mode": _APPLICATION_MODE,
            "reason": "canonical_candidate_layer_disabled",
        }, None
    try:
        side_length = (
            qsqrt2_from_mapping(side_length_mapping)
            if isinstance(side_length_mapping, Mapping)
            else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        side_length = None
    if side_length is None or side_length <= Qsqrt2():
        return {
            "enabled": False,
            "mode": _APPLICATION_MODE,
            "reason": "missing_side_length",
        }, None
    try:
        maximum_px = float(raw_report.get("maximum_coordinate_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        maximum_px = 0.0
    if maximum_px <= 0.0:
        return {
            "enabled": False,
            "mode": _APPLICATION_MODE,
            "reason": "missing_raw_image_scale",
        }, None
    working, topology_errors = _normalise_segments(base_contract.get("candidate_segments"))
    if topology_errors or not working:
        return {
            "enabled": False,
            "mode": _APPLICATION_MODE,
            "reason": "invalid_base_candidate_topology",
            "topology_errors": topology_errors,
        }, None

    observations = _raw_observations(raw_report)
    if not observations:
        return {
            "enabled": False,
            "mode": _APPLICATION_MODE,
            "reason": "missing_finite_image_observations",
        }, None

    rejection_counts: Counter[str] = Counter()
    remaining: dict[
        tuple[tuple[float, float], int],
        tuple[dict[str, Any], tuple[float, float]],
    ] = {}
    for raw_candidate in candidate_report.get("candidates", []):
        if not isinstance(raw_candidate, Mapping):
            rejection_counts["invalid_candidate_record"] += 1
            continue
        candidate = dict(raw_candidate)
        source_point_id = str(candidate.get("source_point_id") or "")
        if (
            candidate.get("kind") != "canonical_22_5_ray"
            or candidate.get("status") != "unapplied_candidate"
            or candidate.get("generation_rule")
            != "canonical_22_5_ray_from_proved_point"
            or not source_point_id
            or list(candidate.get("parent_entity_ids") or []) != [source_point_id]
        ):
            rejection_counts["candidate_not_from_proved_point_generator"] += 1
            continue
        start = _cp_point(candidate.get("source_point_project"), side_length)
        try:
            direction_index = int(candidate.get("directed_direction_index"))
        except (TypeError, ValueError):
            direction_index = -1
        if start is None or not 0 <= direction_index < 16:
            rejection_counts["invalid_exact_candidate_geometry"] += 1
            continue
        key = _float_point_key(start), direction_index
        if key in remaining:
            rejection_counts["duplicate_exact_source_direction"] += 1
            continue
        remaining[key] = candidate, start

    initial_unique_candidate_count = len(remaining)
    accepted: list[dict[str, Any]] = []
    application_rounds = 0
    while remaining and application_rounds < _MAX_APPLICATION_ROUNDS:
        application_rounds += 1
        geometries = _segment_geometries(working)
        options: list[
            tuple[float, float, float, tuple[tuple[float, float], int]]
        ] = []
        for key, (candidate, start) in remaining.items():
            if not _point_attached_to_topology(start, working):
                continue
            angle = float(candidate["direction_deg"])
            contact = _nearest_contact(start, angle, working)
            if contact is None:
                continue
            end = _float_point(contact.get("end_cp"))
            if end is None or _segment_geometry_key(start, end) in geometries:
                continue
            if _unexpected_candidate_intersection(start, end, working) is not None:
                continue
            evidence = _ray_evidence(
                start,
                end,
                angle,
                observations,
                maximum_px=maximum_px,
                angle_tolerance_deg=2.0,
                distance_tolerance_px=3.2,
            )
            if evidence is None or _line_type_from_evidence(evidence) is None:
                continue
            options.append(
                (
                    -float(evidence["visible_coverage"]),
                    float(evidence["unsupported_length_px"]),
                    math.dist(start, end),
                    key,
                )
            )
        if not options:
            break

        accepted_this_round = 0
        for *_, key in sorted(options):
            if key not in remaining or len(accepted) >= _MAX_APPLIED_RAYS:
                continue
            candidate, start = remaining[key]
            if not _point_attached_to_topology(start, working):
                continue
            angle = float(candidate["direction_deg"])
            contact = _nearest_contact(start, angle, working)
            if contact is None:
                continue
            end = _float_point(contact.get("end_cp"))
            if end is None:
                continue
            if _segment_geometry_key(start, end) in _segment_geometries(working):
                continue
            if _unexpected_candidate_intersection(start, end, working) is not None:
                continue
            evidence = _ray_evidence(
                start,
                end,
                angle,
                observations,
                maximum_px=maximum_px,
                angle_tolerance_deg=2.0,
                distance_tolerance_px=3.2,
            )
            line_type = _line_type_from_evidence(evidence) if evidence else None
            if evidence is None or line_type is None:
                continue

            remaining.pop(key)
            token = hashlib.sha1(str(candidate["id"]).encode("utf-8")).hexdigest()[:12]
            working, start_splits = _split_segments_at_point(
                working, start, token=f"{token}:start"
            )
            working, end_splits = _split_segments_at_point(
                working, end, token=f"{token}:end"
            )
            segment_id = f"proved-canonical-ray:{token}"
            working.append(
                {
                    "id": segment_id,
                    "source": "proved_canonical_22_5_image_supported",
                    "transactional_root_segment_id": segment_id,
                    "source_candidate_id": str(candidate["id"]),
                    "start_point_id": str(candidate.get("source_point_id") or ""),
                    "end_point_id": f"proved-exact-contact:{token}",
                    "start_cp": list(start),
                    "end_cp": list(end),
                    "orientation": int(candidate["line_orientation_index"]),
                    "direction_angle_deg": angle,
                    "direction_family": "canonical_22_5",
                    "line_type": line_type[0],
                    "line_type_source": line_type[1],
                    "visible_coverage": evidence["visible_coverage"],
                    "unsupported_length_px": evidence["unsupported_length_px"],
                    "image_evidence": copy.deepcopy(evidence),
                    "construction_sources": [
                        "proved_point",
                        "canonical_22_5_direction",
                    ],
                    "parent_entity_ids": list(candidate.get("parent_entity_ids", [])),
                    "target": copy.deepcopy(contact),
                }
            )
            accepted.append(
                {
                    "candidate_id": str(candidate["id"]),
                    "segment_id": segment_id,
                    "source_point_id": str(candidate.get("source_point_id") or ""),
                    "start_cp": list(start),
                    "end_cp": list(end),
                    "directed_direction_index": int(
                        candidate["directed_direction_index"]
                    ),
                    "direction_deg": angle,
                    "line_type": line_type[0],
                    "line_type_source": line_type[1],
                    "image_evidence": copy.deepcopy(evidence),
                    "target": copy.deepcopy(contact),
                    "split_segment_count": start_splits + end_splits,
                }
            )
            accepted_this_round += 1
        if len(accepted) >= _MAX_APPLIED_RAYS:
            rejection_counts["application_limit_reached"] += len(remaining)
            break
        if not accepted_this_round:
            break

    # Classify only the final stalled frontier.  A candidate that failed before
    # a newly applied ray created an earlier exact contact was not a rejection.
    geometries = _segment_geometries(working)
    for candidate, start in remaining.values():
        if not _point_attached_to_topology(start, working):
            rejection_counts["source_not_attached_to_current_output_topology"] += 1
            continue
        angle = float(candidate["direction_deg"])
        contact = _nearest_contact(start, angle, working)
        if contact is None:
            rejection_counts["no_first_exact_contact"] += 1
            continue
        end = _float_point(contact.get("end_cp"))
        if end is None:
            rejection_counts["invalid_exact_contact"] += 1
            continue
        if _segment_geometry_key(start, end) in geometries:
            rejection_counts["already_represented_geometry"] += 1
            continue
        if _unexpected_candidate_intersection(start, end, working) is not None:
            rejection_counts["overlaps_existing_exact_ray"] += 1
            continue
        evidence = _ray_evidence(
            start,
            end,
            angle,
            observations,
            maximum_px=maximum_px,
            angle_tolerance_deg=2.0,
            distance_tolerance_px=3.2,
        )
        if evidence is None:
            rejection_counts["insufficient_continuous_image_evidence"] += 1
        elif _line_type_from_evidence(evidence) is None:
            rejection_counts["conflicting_red_blue_evidence"] += 1
        else:
            rejection_counts["not_reached_before_round_limit"] += 1

    normalized, topology_errors = _normalise_segments(working)
    if topology_errors:
        return {
            "enabled": False,
            "mode": _APPLICATION_MODE,
            "reason": "generated_candidate_topology_invalid",
            "topology_errors": topology_errors,
        }, None
    normalized, dangling_pruned_segment_ids = _prune_internal_dangling_segments(
        normalized
    )
    retained_root_ids = {
        str(segment.get("transactional_root_segment_id") or segment.get("id") or "")
        for segment in normalized
    }
    attempted_accepted_count = len(accepted)
    accepted = [
        item for item in accepted if str(item["segment_id"]) in retained_root_ids
    ]
    pruned_generated_count = attempted_accepted_count - len(accepted)
    if pruned_generated_count:
        rejection_counts["generated_internal_dangling_segment_pruned"] += (
            pruned_generated_count
        )
    before_audit = _audit(_materialize(
        _normalise_segments(base_contract.get("candidate_segments"))[0]
    ))
    after_audit = _audit(_materialize(normalized))
    report = {
        "enabled": True,
        "mode": _APPLICATION_MODE,
        "status": "applied" if accepted else "no_supported_canonical_ray",
        "initial_candidate_count": int(candidate_report.get("candidate_count", 0) or 0),
        "initial_unique_source_direction_count": initial_unique_candidate_count,
        "application_round_count": application_rounds,
        "attempted_application_count": attempted_accepted_count,
        "accepted_candidate_count": len(accepted),
        "accepted_candidates": accepted,
        "base_segment_count": len(base_contract.get("candidate_segments", [])),
        "effective_segment_count": len(normalized),
        "internal_dangling_pruned_segment_count": len(
            dangling_pruned_segment_ids
        ),
        "internal_dangling_pruned_segment_ids": dangling_pruned_segment_ids,
        "camv_violation_count_before": int(before_audit.get("violation_count", 0) or 0),
        "camv_violation_count_after": int(after_audit.get("violation_count", 0) or 0),
        "camv_rule_counts_before": dict(before_audit.get("rule_counts") or {}),
        "camv_rule_counts_after": dict(after_audit.get("rule_counts") or {}),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "invariants": {
            "human_selects_only_one_initial_boundary_relation": True,
            "candidate_source_must_be_construction_proved": True,
            "candidate_direction_is_fixed_before_image_evaluation": True,
            "image_evidence_can_only_accept_or_reject": True,
            "candidate_ends_at_first_exact_contact": True,
            "fitted_image_point_can_become_endpoint": False,
            "noncanonical_direction_count": 0,
            "every_output_segment_endpoint_is_boundary_or_shared": True,
        },
    }
    return report, (
        _replace_camv_diagnostic(base_contract, normalized, accepted)
        if accepted
        else None
    )


__all__ = [
    "apply_image_supported_canonical_rays",
    "build_proved_node_canonical_ray_candidates",
]
