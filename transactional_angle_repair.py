"""Transactional application of constrained cAMV missing-ray candidates.

Every candidate is applied to a deep-copied finite segment graph.  A trial is
kept only when it removes its source ``number_of_folds`` violation, strictly
reduces the complete cAMV violation set, and creates no new violation.  No CP
contract is promoted unless the final copied graph passes the complete audit.
"""

from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter
from typing import Any, Mapping

from foldability import GeometrySegment, audit_camv_structure
from guided_cp_output import _draft_boundary_rows, _serialize_cp


Point = tuple[float, float]
_FOLDING_TYPES = {2, 3}
_POINT_TOLERANCE = 1e-5
_MAX_CANDIDATES = 128
_MAX_TRIALS = 512
_REQUIRED_BASE_GATES = {
    "exact_crease_closure",
    "exact_endpoint_closure",
    "observed_source_provenance",
    "direction_consistency",
    "residual_tolerance",
    "finite_planar_geometry",
    "segment_line_types",
    "paper_boundary_closure",
}


def _point(raw: Any) -> Point | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        point = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(value) for value in point) else None


def _point_key(point: Point) -> tuple[float, float]:
    return round(point[0], 6), round(point[1], 6)


def _inside_paper(point: Point) -> bool:
    return all(-200.0 - _POINT_TOLERANCE <= value <= 200.0 + _POINT_TOLERANCE for value in point)


def _on_boundary(point: Point) -> bool:
    return any(abs(value - edge) <= _POINT_TOLERANCE for value in point for edge in (-200.0, 200.0))


def _angle_error(first_deg: float, second_deg: float) -> float:
    return abs((first_deg - second_deg + 180.0) % 360.0 - 180.0)


def _undirected_angle_error(first_deg: float, second_deg: float) -> float:
    return abs((first_deg - second_deg + 90.0) % 180.0 - 90.0)


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _subtract(first: Point, second: Point) -> Point:
    return first[0] - second[0], first[1] - second[1]


def _segment_points(segment: Mapping[str, Any]) -> tuple[Point, Point] | None:
    start = _point(segment.get("start_cp"))
    end = _point(segment.get("end_cp"))
    return (start, end) if start is not None and end is not None else None


def _segment_parameter(point: Point, start: Point, end: Point) -> float | None:
    delta = _subtract(end, start)
    length_squared = delta[0] ** 2 + delta[1] ** 2
    if length_squared <= _POINT_TOLERANCE**2:
        return None
    relative = _subtract(point, start)
    parameter = (relative[0] * delta[0] + relative[1] * delta[1]) / length_squared
    projected = (
        start[0] + parameter * delta[0],
        start[1] + parameter * delta[1],
    )
    if math.dist(point, projected) > _POINT_TOLERANCE:
        return None
    return parameter


def _root_segment_id(segment: Mapping[str, Any]) -> str:
    return str(
        segment.get("transactional_root_segment_id")
        or segment.get("id")
        or ""
    )


def _geometry_key(start: Point, end: Point) -> tuple[tuple[float, float], ...]:
    return tuple(sorted((_point_key(start), _point_key(end))))


def _normalise_segments(
    raw_segments: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_geometry: dict[tuple[tuple[float, float], ...], str] = {}
    for index, raw in enumerate(raw_segments if isinstance(raw_segments, list) else []):
        if not isinstance(raw, Mapping):
            errors.append({"code": "invalid_segment_record", "index": index})
            continue
        segment = copy.deepcopy(dict(raw))
        segment_id = str(segment.get("id") or "")
        points = _segment_points(segment)
        try:
            line_type = int(segment.get("line_type"))
        except (TypeError, ValueError):
            line_type = 0
        if not segment_id or segment_id in seen_ids:
            errors.append({"code": "missing_or_duplicate_segment_id", "id": segment_id, "index": index})
            continue
        if points is None:
            errors.append({"code": "invalid_segment_coordinates", "id": segment_id})
            continue
        start, end = points
        if not _inside_paper(start) or not _inside_paper(end):
            errors.append({"code": "segment_outside_paper", "id": segment_id})
            continue
        if math.dist(start, end) <= _POINT_TOLERANCE:
            errors.append({"code": "zero_length_segment", "id": segment_id})
            continue
        if line_type not in _FOLDING_TYPES:
            errors.append({"code": "invalid_segment_line_type", "id": segment_id})
            continue
        geometry = _geometry_key(start, end)
        if geometry in seen_geometry:
            errors.append(
                {
                    "code": "duplicate_segment_geometry",
                    "id": segment_id,
                    "other_id": seen_geometry[geometry],
                }
            )
            continue
        segment["id"] = segment_id
        segment["line_type"] = line_type
        segment["start_cp"] = [start[0], start[1]]
        segment["end_cp"] = [end[0], end[1]]
        segment.setdefault("transactional_root_segment_id", segment_id)
        seen_ids.add(segment_id)
        seen_geometry[geometry] = segment_id
        segments.append(segment)
    return segments, errors


def _materialize(segments: list[dict[str, Any]]) -> dict[str, Any]:
    internal_rows: list[tuple[int, float, float, float, float]] = []
    endpoint_points: list[Point] = []
    for segment in segments:
        points = _segment_points(segment)
        assert points is not None
        start, end = points
        internal_rows.append((int(segment["line_type"]), *start, *end))
        endpoint_points.extend((start, end))
    boundary_rows, boundary_counts = _draft_boundary_rows(endpoint_points)
    rows = [*boundary_rows, *internal_rows]
    return {
        "cp": _serialize_cp(rows),
        "rows": rows,
        "internal_segment_count": len(internal_rows),
        "boundary_segment_count": len(boundary_rows),
        "boundary_segment_counts_by_side": boundary_counts,
    }


def _audit(materialized: Mapping[str, Any]) -> dict[str, Any]:
    return audit_camv_structure(
        [
            GeometrySegment(line_type, (x1, y1), (x2, y2), row=index)
            for index, (line_type, x1, y1, x2, y2) in enumerate(
                materialized.get("rows", [])
            )
        ],
        folding_types={2, 3},
        include_mv=True,
    )


def _violation_key(violation: Mapping[str, Any]) -> tuple[float, float, str] | None:
    point = _point(violation.get("point"))
    rule = str(violation.get("rule") or "")
    if point is None or not rule:
        return None
    return *_point_key(point), rule


def _violation_state(audit: Mapping[str, Any]) -> dict[tuple[float, float, str], dict[str, Any]]:
    state: dict[tuple[float, float, str], dict[str, Any]] = {}
    for raw in audit.get("violations", []):
        if not isinstance(raw, Mapping):
            continue
        key = _violation_key(raw)
        if key is None:
            continue
        state[key] = {
            str(name): value
            for name, value in raw.items()
            if name not in {"rows", "point"}
        }
    return state


def _candidate_token(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:12]


def _candidate_priority(candidate: Mapping[str, Any]) -> int:
    try:
        return int(candidate.get("priority", 10**9))
    except (TypeError, ValueError):
        return 10**9


def _segments_at_root(
    segments: list[dict[str, Any]], root_id: str
) -> list[dict[str, Any]]:
    return [
        segment
        for segment in segments
        if str(segment.get("id") or "") == root_id
        or _root_segment_id(segment) == root_id
    ]


def _incident_ray_angles(
    segments: list[dict[str, Any]],
    start: Point,
    *,
    root_id: str | None = None,
) -> list[float]:
    angles: list[float] = []
    for segment in segments:
        if root_id is not None and _root_segment_id(segment) != root_id:
            continue
        points = _segment_points(segment)
        assert points is not None
        first, second = points
        directions: list[Point] = []
        if math.dist(first, start) <= _POINT_TOLERANCE:
            directions.append(_subtract(second, start))
        if math.dist(second, start) <= _POINT_TOLERANCE:
            directions.append(_subtract(first, start))
        parameter = _segment_parameter(start, first, second)
        if (
            parameter is not None
            and _POINT_TOLERANCE < parameter < 1.0 - _POINT_TOLERANCE
        ):
            directions.extend((_subtract(first, start), _subtract(second, start)))
        for direction in directions:
            angle = math.degrees(math.atan2(direction[1], direction[0])) % 360.0
            if not any(_angle_error(angle, old) <= 1e-6 for old in angles):
                angles.append(angle)
    return sorted(angles)


def _is_verified_existing_sector_bisector(
    candidate: Mapping[str, Any],
    segments: list[dict[str, Any]],
    start: Point,
    candidate_angle: float,
) -> bool:
    all_angles = _incident_ray_angles(segments, start)
    derivations = candidate.get("angle_bisector_derivations")
    if not isinstance(derivations, list):
        return False
    kawasaki_parents = {
        str(item) for item in candidate.get("parent_segment_ids", []) if str(item)
    }
    for derivation in derivations:
        if not isinstance(derivation, Mapping):
            continue
        parent_ids = [
            str(item)
            for item in derivation.get("parent_segment_ids", [])
            if str(item)
        ]
        if len(set(parent_ids)) != 2 or not set(parent_ids) <= kawasaki_parents:
            continue
        first_angles = _incident_ray_angles(
            segments, start, root_id=parent_ids[0]
        )
        second_angles = _incident_ray_angles(
            segments, start, root_id=parent_ids[1]
        )
        for first in first_angles:
            for second in second_angles:
                for sector_start, sector_end in ((first, second), (second, first)):
                    sector = (sector_end - sector_start) % 360.0
                    if sector <= 1e-6:
                        continue
                    midpoint = (sector_start + sector / 2.0) % 360.0
                    if _angle_error(midpoint, candidate_angle) > 1e-5:
                        continue
                    if any(
                        1e-6
                        < (angle - sector_start) % 360.0
                        < sector - 1e-6
                        for angle in all_angles
                        if (
                            _angle_error(angle, sector_start) > 1e-6
                            and _angle_error(angle, sector_end) > 1e-6
                        )
                    ):
                        continue
                    return True
    return False


def _boundary_side_matches(point: Point, side: str) -> bool:
    if side == "left":
        return abs(point[0] + 200.0) <= _POINT_TOLERANCE
    if side == "right":
        return abs(point[0] - 200.0) <= _POINT_TOLERANCE
    if side == "top":
        return abs(point[1] + 200.0) <= _POINT_TOLERANCE
    if side == "bottom":
        return abs(point[1] - 200.0) <= _POINT_TOLERANCE
    return False


def _unexpected_candidate_intersection(
    start: Point,
    end: Point,
    segments: list[dict[str, Any]],
) -> str | None:
    candidate_delta = _subtract(end, start)
    length_squared = candidate_delta[0] ** 2 + candidate_delta[1] ** 2
    for segment in segments:
        points = _segment_points(segment)
        assert points is not None
        first, second = points
        edge = _subtract(second, first)
        denominator = _cross(candidate_delta, edge)
        offset = _subtract(first, start)
        if abs(denominator) <= 1e-10:
            if (
                abs(_cross(offset, candidate_delta))
                > _POINT_TOLERANCE * math.sqrt(length_squared)
            ):
                continue
            projections = [
                (_subtract(point, start)[0] * candidate_delta[0]
                 + _subtract(point, start)[1] * candidate_delta[1])
                / length_squared
                for point in (first, second)
            ]
            overlap_start = max(_POINT_TOLERANCE, min(projections))
            overlap_end = min(1.0 - _POINT_TOLERANCE, max(projections))
            if overlap_end > overlap_start:
                return str(segment.get("id") or "")
            continue
        candidate_parameter = _cross(offset, edge) / denominator
        edge_parameter = _cross(offset, candidate_delta) / denominator
        if (
            _POINT_TOLERANCE < candidate_parameter < 1.0 - _POINT_TOLERANCE
            and -_POINT_TOLERANCE <= edge_parameter <= 1.0 + _POINT_TOLERANCE
        ):
            return str(segment.get("id") or "")
    return None


def _validate_candidate(
    candidate: Mapping[str, Any],
    segments: list[dict[str, Any]],
    current_audit: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    candidate_id = str(candidate.get("id") or "")
    if not candidate_id:
        return "missing_candidate_id", {}
    if candidate.get("kind") != "kawasaki_single_missing_ray":
        return "missing_ray_not_derived_by_kawasaki", {}
    sources = {str(item) for item in candidate.get("construction_sources", [])}
    if "kawasaki_single_missing_ray" not in sources:
        return "missing_kawasaki_construction_provenance", {}
    if candidate.get("applied") is not False:
        return "candidate_was_not_read_only", {}
    if candidate.get("requires_transactional_camv_recheck") is not True:
        return "candidate_does_not_require_transactional_recheck", {}
    if "number_of_folds" not in {
        str(item) for item in candidate.get("trigger_camv_rules", [])
    }:
        return "candidate_not_triggered_by_number_of_folds", {}

    start = _point(candidate.get("start_cp"))
    end = _point(candidate.get("end_cp"))
    if start is None or end is None or not _inside_paper(start) or not _inside_paper(end):
        return "invalid_candidate_coordinates", {}
    if _on_boundary(start) or math.dist(start, end) <= _POINT_TOLERANCE:
        return "invalid_missing_ray_source_vertex", {}
    source_violation = (*_point_key(start), "number_of_folds")
    if source_violation not in _violation_state(current_audit):
        return "source_is_not_current_number_of_folds_violation", {}

    try:
        line_type = int(candidate.get("proposed_line_type"))
        stated_angle = float(candidate.get("direction_angle_deg")) % 360.0
    except (TypeError, ValueError):
        return "missing_candidate_direction_or_line_type", {}
    if line_type not in _FOLDING_TYPES:
        return "missing_candidate_line_type", {}
    actual_angle = math.degrees(
        math.atan2(end[1] - start[1], end[0] - start[0])
    ) % 360.0
    if _angle_error(actual_angle, stated_angle) > 1e-5:
        return "candidate_endpoint_direction_mismatch", {}

    family = str(candidate.get("direction_family") or "")
    direction_index = candidate.get("direction_index")
    if family == "canonical_22_5":
        try:
            direction_index = int(direction_index)
        except (TypeError, ValueError):
            return "missing_canonical_direction_index", {}
        if not 0 <= direction_index < 8 or _undirected_angle_error(
            actual_angle, direction_index * 22.5
        ) > 1e-5:
            return "invalid_canonical_direction", {}
    elif family == "derived_existing_sector_angle_bisector":
        if "existing_sector_angle_bisector" not in sources:
            return "noncanonical_requires_existing_sector_bisector", {}
        if not _is_verified_existing_sector_bisector(
            candidate, segments, start, actual_angle
        ):
            return "invalid_existing_sector_angle_bisector", {}
    else:
        return "untrusted_candidate_direction_family", {}

    evidence = candidate.get("image_evidence")
    if not isinstance(evidence, Mapping):
        return "missing_candidate_image_evidence", {}
    try:
        coverage = float(evidence.get("visible_coverage"))
        unsupported = float(evidence.get("unsupported_length_px"))
        distance_tolerance = float(evidence.get("distance_tolerance_px"))
        angle_tolerance = float(evidence.get("angle_tolerance_deg"))
    except (TypeError, ValueError):
        return "invalid_candidate_image_evidence", {}
    if (
        not 0.8 <= coverage <= 1.0 + 1e-9
        or not 0.0 <= unsupported <= 3.2 + 1e-9
        or not 0.0 <= distance_tolerance <= 3.2 + 1e-9
        or not 0.0 <= angle_tolerance <= 2.0 + 1e-9
        or not evidence.get("matched_observation_ids")
    ):
        return "insufficient_candidate_image_evidence", {}
    image_line_type = evidence.get("image_line_type")
    if image_line_type is not None:
        try:
            image_line_type = int(image_line_type)
        except (TypeError, ValueError):
            return "invalid_source_colour_line_type", {}
        if image_line_type not in _FOLDING_TYPES:
            return "invalid_source_colour_line_type", {}
        if image_line_type != line_type:
            return "source_colour_conflicts_with_candidate_line_type", {}
    try:
        maekawa_options = {
            int(item) for item in candidate.get("maekawa_line_type_options", [])
        }
    except (TypeError, ValueError):
        maekawa_options = set()
    if line_type not in maekawa_options:
        return "candidate_line_type_not_allowed_by_maekawa", {}

    parent_ids = {str(item) for item in candidate.get("parent_segment_ids", []) if str(item)}
    if len(parent_ids) < 3:
        return "missing_existing_parent_rays", {}
    for parent_id in parent_ids:
        parent_segments = _segments_at_root(segments, parent_id)
        if not parent_segments or not any(
            points is not None
            and (
                math.dist(points[0], start) <= _POINT_TOLERANCE
                or math.dist(points[1], start) <= _POINT_TOLERANCE
            )
            for points in (_segment_points(segment) for segment in parent_segments)
        ):
            return "parent_ray_not_incident_at_source", {"parent_segment_id": parent_id}

    target = candidate.get("target")
    if not isinstance(target, Mapping):
        return "missing_first_exact_contact", {}
    target_kind = str(target.get("target_kind") or "")
    if target_kind == "paper_boundary":
        if not _boundary_side_matches(end, str(target.get("target_side") or "")):
            return "candidate_boundary_contact_mismatch", {}
    elif target_kind == "existing_exact_segment":
        target_id = str(target.get("target_segment_id") or "")
        target_segments = _segments_at_root(segments, target_id)
        if not target_segments or not any(
            points is not None
            and _segment_parameter(end, points[0], points[1]) is not None
            and -_POINT_TOLERANCE
            <= float(_segment_parameter(end, points[0], points[1]))
            <= 1.0 + _POINT_TOLERANCE
            for points in (_segment_points(segment) for segment in target_segments)
        ):
            return "candidate_target_segment_mismatch", {"target_segment_id": target_id}
    else:
        return "untrusted_candidate_contact_kind", {}

    crossed_id = _unexpected_candidate_intersection(start, end, segments)
    if crossed_id is not None:
        return "candidate_has_earlier_exact_contact", {"segment_id": crossed_id}
    return None, {
        "candidate_id": candidate_id,
        "start": start,
        "end": end,
        "line_type": line_type,
        "source_violation": source_violation,
    }


def _split_segments_at_point(
    segments: list[dict[str, Any]],
    point: Point,
    *,
    token: str,
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    split_count = 0
    point_id = f"transaction-point:{token}:{point[0]:.9g}:{point[1]:.9g}"
    for segment in segments:
        points = _segment_points(segment)
        assert points is not None
        start, end = points
        parameter = _segment_parameter(point, start, end)
        if parameter is None or not _POINT_TOLERANCE < parameter < 1.0 - _POINT_TOLERANCE:
            output.append(segment)
            continue
        root_id = _root_segment_id(segment)
        base_id = str(segment.get("id") or "")
        first = copy.deepcopy(segment)
        second = copy.deepcopy(segment)
        first["id"] = f"{base_id}::split:{token}:a"
        second["id"] = f"{base_id}::split:{token}:b"
        first["transactional_root_segment_id"] = root_id
        second["transactional_root_segment_id"] = root_id
        first["end_cp"] = [point[0], point[1]]
        second["start_cp"] = [point[0], point[1]]
        first["end_point_id"] = point_id
        second["start_point_id"] = point_id
        split_detail = {
            "source_segment_id": base_id,
            "root_segment_id": root_id,
            "point_cp": [point[0], point[1]],
        }
        first["transactional_split"] = split_detail
        second["transactional_split"] = split_detail
        output.extend((first, second))
        split_count += 1
    return output, split_count


def _apply_candidate_copy(
    candidate: Mapping[str, Any],
    segments: list[dict[str, Any]],
    validated: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str, int]:
    candidate_id = str(validated["candidate_id"])
    token = _candidate_token(candidate_id)
    start = validated["start"]
    end = validated["end"]
    copied = copy.deepcopy(segments)
    copied, start_splits = _split_segments_at_point(copied, start, token=f"{token}:start")
    copied, end_splits = _split_segments_at_point(copied, end, token=f"{token}:end")
    evidence = candidate.get("image_evidence")
    assert isinstance(evidence, Mapping)
    image_line_type = evidence.get("image_line_type")
    line_type_source = (
        "source_image_color_evidence"
        if image_line_type in _FOLDING_TYPES
        else "camv_maekawa_single_line_solution"
    )
    segment_id = f"transactional-missing-ray:{token}"
    copied.append(
        {
            "id": segment_id,
            "source": "theorem_derived_image_supported_missing_ray",
            "transactional_root_segment_id": segment_id,
            "source_candidate_id": candidate_id,
            "start_point_id": (
                str(candidate.get("source_point_ids", [""])[0])
                if candidate.get("source_point_ids")
                else f"transaction-source:{token}"
            ),
            "end_point_id": f"transaction-contact:{token}",
            "start_cp": [start[0], start[1]],
            "end_cp": [end[0], end[1]],
            "orientation": candidate.get("direction_index"),
            "direction_angle_deg": candidate.get("direction_angle_deg"),
            "direction_family": candidate.get("direction_family"),
            "line_type": int(validated["line_type"]),
            "line_type_source": line_type_source,
            "visible_coverage": evidence.get("visible_coverage"),
            "unsupported_length_px": evidence.get("unsupported_length_px"),
            "construction_sources": list(candidate.get("construction_sources", [])),
            "parent_segment_ids": list(candidate.get("parent_segment_ids", [])),
            "target": copy.deepcopy(candidate.get("target")),
            "transactionally_validated": False,
        }
    )
    return copied, segment_id, start_splits + end_splits


def _trial_candidate(
    candidate: Mapping[str, Any],
    segments: list[dict[str, Any]],
    current_audit: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = str(candidate.get("id") or "")
    rejection, detail = _validate_candidate(candidate, segments, current_audit)
    if rejection is not None:
        return {"candidate_id": candidate_id, "accepted": False, "reason": rejection, **detail}
    trial_segments, added_segment_id, split_count = _apply_candidate_copy(
        candidate, segments, detail
    )
    normalized, errors = _normalise_segments(trial_segments)
    if errors:
        return {
            "candidate_id": candidate_id,
            "accepted": False,
            "reason": "invalid_trial_topology",
            "topology_errors": errors,
        }
    materialized = _materialize(normalized)
    trial_audit = _audit(materialized)
    old_state = _violation_state(current_audit)
    new_state = _violation_state(trial_audit)
    new_keys = sorted(set(new_state) - set(old_state))
    if new_keys:
        return {
            "candidate_id": candidate_id,
            "accepted": False,
            "reason": "introduces_new_camv_violation",
            "new_violation_keys": [list(item) for item in new_keys],
        }
    changed_unresolved = sorted(
        key
        for key in set(old_state) & set(new_state)
        if old_state[key] != new_state[key]
    )
    if changed_unresolved:
        return {
            "candidate_id": candidate_id,
            "accepted": False,
            "reason": "mutates_unresolved_camv_violation",
            "changed_violation_keys": [list(item) for item in changed_unresolved],
        }
    old_count = int(current_audit.get("violation_count", 0) or 0)
    new_count = int(trial_audit.get("violation_count", 0) or 0)
    if new_count >= old_count:
        return {
            "candidate_id": candidate_id,
            "accepted": False,
            "reason": "camv_violation_count_not_reduced",
            "before": old_count,
            "after": new_count,
        }
    if detail["source_violation"] in new_state:
        return {
            "candidate_id": candidate_id,
            "accepted": False,
            "reason": "source_number_of_folds_not_resolved",
        }
    for segment in normalized:
        if str(segment.get("id") or "") == added_segment_id:
            segment["transactionally_validated"] = True
            break
    return {
        "candidate_id": candidate_id,
        "accepted": True,
        "segments": normalized,
        "audit": trial_audit,
        "materialized": materialized,
        "added_segment_id": added_segment_id,
        "split_segment_count": split_count,
        "violation_count_before": old_count,
        "violation_count_after": new_count,
        "resolved_violation_keys": [
            list(item) for item in sorted(set(old_state) - set(new_state))
        ],
    }


def _effective_contract(
    base_contract: Mapping[str, Any],
    segments: list[dict[str, Any]],
    materialized: Mapping[str, Any],
    final_audit: Mapping[str, Any],
    accepted: list[dict[str, Any]],
) -> dict[str, Any]:
    contract = copy.deepcopy(dict(base_contract))
    assignments = {
        str(segment["id"]): {
            "line_type": int(segment["line_type"]),
            "source": str(segment.get("line_type_source") or ""),
        }
        for segment in segments
    }
    gate_results = copy.deepcopy(dict(contract.get("gate_results") or {}))
    gate_results["flat_foldability"] = {"passed": True, "blocker_codes": []}
    gate_results["transactional_angle_repair"] = {
        "passed": True,
        "blocker_codes": [],
    }
    base_internal_count = int(contract.get("candidate_internal_segment_count", 0) or 0)
    generated_count = len(accepted)
    split_count = sum(int(item.get("split_segment_count", 0) or 0) for item in accepted)
    invariants = copy.deepcopy(dict(contract.get("invariants") or {}))
    invariants.update(
        {
            "old_cp_reused": False,
            "generated_internal_segment_count": generated_count,
            "generated_direction_count": sum(
                str(item.get("direction_family") or "") != "canonical_22_5"
                for item in accepted
            ),
            "transactionally_validated_generated_segment_count": generated_count,
            "transactional_split_segment_count": split_count,
            "all_generated_segments_have_theorem_and_image_provenance": True,
            "all_generated_segments_pass_complete_camv": True,
        }
    )
    contract.update(
        {
            "status": "ready_transactionally_repaired",
            "output_ready": True,
            "checks_passed": True,
            "cp_available": True,
            "cp": materialized["cp"],
            "base_candidate_internal_segment_count": base_internal_count,
            "required_internal_segment_count": len(segments),
            "candidate_internal_segment_count": len(segments),
            "typed_candidate_internal_segment_count": len(segments),
            "draft_internal_segment_count": len(segments),
            "draft_observed_endpoint_fallback_count": 0,
            "draft_red_fallback_segment_count": 0,
            "draft_skipped_internal_segment_ids": [],
            "boundary_segment_count": materialized["boundary_segment_count"],
            "boundary_segment_counts_by_side": materialized[
                "boundary_segment_counts_by_side"
            ],
            "checked_boundary_segment_count": materialized[
                "boundary_segment_count"
            ],
            "checked_boundary_segment_counts_by_side": materialized[
                "boundary_segment_counts_by_side"
            ],
            "unresolved_endpoint_point_ids": [],
            "unassigned_segment_ids": [],
            "segment_line_type_assignments": assignments,
            "candidate_segments": segments,
            "gate_results": gate_results,
            "blocker_count": 0,
            "blocker_counts": {},
            "blockers": [],
            "soft_diagnostics": {
                "camv": copy.deepcopy(dict(final_audit)),
                "camv_blocks_output": False,
                "camv_blocks_verification": True,
            },
            "transactional_angle_repair": {
                "applied": True,
                "accepted_candidate_ids": [
                    str(item["candidate_id"]) for item in accepted
                ],
                "base_camv_violation_count": int(
                    (base_contract.get("soft_diagnostics") or {})
                    .get("camv", {})
                    .get("violation_count", 0)
                    or 0
                ),
                "final_camv_violation_count": 0,
            },
            "invariants": invariants,
        }
    )
    return contract


def _disabled(reason: str, **detail: Any) -> tuple[dict[str, Any], None]:
    return (
        {
            "enabled": False,
            "mode": "transactional_angle_repair_v1",
            "reason": reason,
            "output_promoted": False,
            "accepted_candidate_ids": [],
            **detail,
        },
        None,
    )


def build_transactional_angle_repair(
    cp_contract: Mapping[str, Any] | None,
    candidate_report: Mapping[str, Any] | None,
    *,
    max_trials: int = _MAX_TRIALS,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Try candidates on copied topology and optionally return a safe contract."""

    cp_contract = cp_contract if isinstance(cp_contract, Mapping) else {}
    candidate_report = candidate_report if isinstance(candidate_report, Mapping) else {}
    gates = cp_contract.get("gate_results")
    gates = gates if isinstance(gates, Mapping) else {}
    missing_prerequisites = sorted(_REQUIRED_BASE_GATES - set(gates))
    if missing_prerequisites:
        return _disabled(
            "missing_base_prerequisite_gate",
            missing_prerequisite_gates=missing_prerequisites,
        )
    failed_prerequisites = sorted(
        str(name)
        for name, value in gates.items()
        if name != "flat_foldability"
        and (not isinstance(value, Mapping) or value.get("passed") is not True)
    )
    if failed_prerequisites:
        return _disabled(
            "base_prerequisite_gate_failed",
            failed_prerequisite_gates=failed_prerequisites,
        )
    other_blockers = sorted(
        str(code)
        for code in (cp_contract.get("blocker_counts") or {})
        if str(code) != "camv_foldability_violations"
    )
    if other_blockers:
        return _disabled(
            "base_has_non_camv_blockers",
            non_camv_blockers=other_blockers,
        )
    if not isinstance((cp_contract.get("soft_diagnostics") or {}).get("camv"), Mapping):
        return _disabled("missing_base_camv_audit")
    if not candidate_report.get("enabled", False):
        return _disabled(
            "candidate_layer_disabled",
            candidate_reason=candidate_report.get("reason"),
        )

    candidates = [
        copy.deepcopy(dict(item))
        for item in candidate_report.get("candidates", [])
        if isinstance(item, Mapping)
    ]
    if len(candidates) > _MAX_CANDIDATES:
        return _disabled(
            "candidate_limit_exceeded",
            candidate_count=len(candidates),
            candidate_limit=_MAX_CANDIDATES,
        )
    base_segments, topology_errors = _normalise_segments(
        cp_contract.get("candidate_segments")
    )
    if topology_errors:
        return _disabled(
            "invalid_base_candidate_topology",
            topology_errors=topology_errors,
        )
    if not base_segments:
        return _disabled("no_base_candidate_segments")
    base_materialized = _materialize(base_segments)
    base_audit = _audit(base_materialized)
    recorded_audit = (cp_contract.get("soft_diagnostics") or {}).get("camv")
    assert isinstance(recorded_audit, Mapping)
    if _violation_state(base_audit) != _violation_state(recorded_audit):
        return _disabled(
            "reconstructed_base_camv_mismatch",
            recorded_violation_count=recorded_audit.get("violation_count"),
            reconstructed_violation_count=base_audit.get("violation_count"),
        )
    base_violation_count = int(base_audit.get("violation_count", 0) or 0)
    if base_violation_count == 0:
        return _disabled("base_already_passes_camv")
    if not candidates:
        return _disabled(
            "no_constrained_missing_ray_candidates",
            base_camv_violation_count=base_violation_count,
        )

    grouped: dict[tuple[float, float], list[dict[str, Any]]] = {}
    malformed_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        start = _point(candidate.get("start_cp"))
        if start is None:
            malformed_candidates.append(
                {
                    "candidate_id": str(candidate.get("id") or ""),
                    "reason": "invalid_candidate_coordinates",
                }
            )
            continue
        grouped.setdefault(_point_key(start), []).append(candidate)

    working_segments = copy.deepcopy(base_segments)
    working_audit = copy.deepcopy(base_audit)
    working_materialized = base_materialized
    pending = dict(grouped)
    accepted: list[dict[str, Any]] = []
    latest_results: dict[tuple[float, float], list[dict[str, Any]]] = {}
    ambiguous_points: dict[tuple[float, float], list[str]] = {}
    trial_count = 0
    trial_limit_reached = False

    while pending:
        progress = False
        ordered_points = sorted(
            pending,
            key=lambda key: (
                len(pending[key]),
                min(_candidate_priority(item) for item in pending[key]),
                key,
            ),
        )
        for point_key in ordered_points:
            if point_key not in pending:
                continue
            results: list[dict[str, Any]] = []
            for candidate in pending[point_key]:
                if trial_count >= max(1, int(max_trials)):
                    trial_limit_reached = True
                    break
                results.append(
                    _trial_candidate(candidate, working_segments, working_audit)
                )
                trial_count += 1
            latest_results[point_key] = results
            if trial_limit_reached:
                break
            valid = [item for item in results if item.get("accepted") is True]
            if not valid:
                continue
            best_rank = min(
                (
                    int(item["violation_count_after"]),
                    _candidate_priority(
                        next(
                            candidate
                            for candidate in pending[point_key]
                            if str(candidate.get("id") or "")
                            == item["candidate_id"]
                        )
                    ),
                )
                for item in valid
            )
            best = []
            for item in valid:
                candidate = next(
                    candidate
                    for candidate in pending[point_key]
                    if str(candidate.get("id") or "") == item["candidate_id"]
                )
                rank = (
                    int(item["violation_count_after"]),
                    _candidate_priority(candidate),
                )
                if rank == best_rank:
                    best.append((candidate, item))
            unique_geometry = {
                (
                    _geometry_key(
                        _point(candidate["start_cp"]),
                        _point(candidate["end_cp"]),
                    ),
                    int(candidate["proposed_line_type"]),
                )
                for candidate, _ in best
            }
            if len(unique_geometry) > 1:
                ambiguous_points[point_key] = sorted(
                    str(candidate.get("id") or "") for candidate, _ in best
                )
                continue
            candidate, winner = sorted(
                best, key=lambda pair: str(pair[0].get("id") or "")
            )[0]
            working_segments = winner.pop("segments")
            working_audit = winner.pop("audit")
            working_materialized = winner.pop("materialized")
            winner["direction_family"] = candidate.get("direction_family")
            accepted.append(winner)
            pending.pop(point_key)
            ambiguous_points.pop(point_key, None)
            progress = True
        if trial_limit_reached or not progress:
            break

    candidate_results: list[dict[str, Any]] = [*malformed_candidates]
    accepted_ids = {str(item["candidate_id"]) for item in accepted}
    candidate_results.extend(
        {
            key: value
            for key, value in item.items()
            if key not in {"segments", "audit", "materialized"}
        }
        for item in accepted
    )
    for point_key, point_candidates in pending.items():
        ambiguous_ids = set(ambiguous_points.get(point_key, []))
        latest_by_id = {
            str(item.get("candidate_id") or ""): item
            for item in latest_results.get(point_key, [])
        }
        for candidate in point_candidates:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id in accepted_ids:
                continue
            if candidate_id in ambiguous_ids:
                candidate_results.append(
                    {
                        "candidate_id": candidate_id,
                        "accepted": False,
                        "reason": "ambiguous_equal_rank_transaction",
                    }
                )
            elif candidate_id in latest_by_id:
                candidate_results.append(
                    {
                        key: value
                        for key, value in latest_by_id[candidate_id].items()
                        if key not in {"segments", "audit", "materialized"}
                    }
                )
            else:
                candidate_results.append(
                    {
                        "candidate_id": candidate_id,
                        "accepted": False,
                        "reason": "trial_limit_reached",
                    }
                )

    final_violation_count = int(working_audit.get("violation_count", 0) or 0)
    output_promoted = bool(
        accepted and final_violation_count == 0 and not trial_limit_reached
    )
    rejection_counts = Counter(
        str(item.get("reason") or "")
        for item in candidate_results
        if item.get("accepted") is not True
    )
    report = {
        "enabled": True,
        "mode": "transactional_angle_repair_v1",
        "reason": (
            None
            if output_promoted
            else "camv_not_fully_resolved_by_unambiguous_monotonic_transactions"
        ),
        "output_promoted": output_promoted,
        "base_camv_violation_count": base_violation_count,
        "final_trial_camv_violation_count": final_violation_count,
        "accepted_candidate_ids": [str(item["candidate_id"]) for item in accepted],
        "accepted_transactions": accepted,
        "candidate_results": sorted(
            candidate_results, key=lambda item: str(item.get("candidate_id") or "")
        ),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "ambiguous_source_points": [
            {
                "point_cp": list(point),
                "candidate_ids": candidate_ids,
            }
            for point, candidate_ids in sorted(ambiguous_points.items())
        ],
        "trial_count": trial_count,
        "trial_limit": max(1, int(max_trials)),
        "trial_limit_reached": trial_limit_reached,
        "final_camv": working_audit,
        "invariants": {
            "input_contract_mutated": False,
            "trials_use_deep_copied_topology": True,
            "accepted_trial_must_strictly_reduce_camv_violations": True,
            "accepted_trial_must_not_introduce_new_camv_violation": True,
            "accepted_trial_must_not_change_unresolved_camv_violation": True,
            "equal_rank_distinct_solutions_are_not_chosen_automatically": True,
            "cp_promoted_only_when_complete_camv_passes": True,
        },
    }
    effective = (
        _effective_contract(
            cp_contract,
            working_segments,
            working_materialized,
            working_audit,
            accepted,
        )
        if output_promoted
        else None
    )
    return report, effective


__all__ = ["build_transactional_angle_repair"]
