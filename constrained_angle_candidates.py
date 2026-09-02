"""Constrained missing-ray candidates for the guided finite topology.

Image strokes are evidence, never a source of a free direction. Candidate rays
must first be derived from existing exact rays by a named construction rule.
Exact 22.5-degree candidates outrank non-canonical angle bisectors, and every
candidate must be supported from its source vertex to its first exact contact.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Mapping


Point = tuple[float, float]
_FOLDING_TYPES = {2, 3}
_GEOMETRY_CAMV_RULES = {"number_of_folds", "kawasaki_angles"}


def _point(raw: Any) -> Point | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        result = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(value) for value in result) else None


def _point_key(point: Point) -> tuple[float, float]:
    return round(point[0], 7), round(point[1], 7)


def _angle(angle_deg: float) -> float:
    return float(angle_deg) % 360.0


def _direction(angle_deg: float) -> Point:
    radians = math.radians(angle_deg)
    return math.cos(radians), math.sin(radians)


def _undirected_angle_error(first_deg: float, second_deg: float) -> float:
    return abs((first_deg - second_deg + 90.0) % 180.0 - 90.0)


def _canonical_direction_index(angle_deg: float, tolerance_deg: float = 1e-6) -> int | None:
    normalized = angle_deg % 180.0
    index = int(round(normalized / 22.5)) % 8
    return index if _undirected_angle_error(normalized, index * 22.5) <= tolerance_deg else None


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _nearest_contact(
    start: Point,
    angle_deg: float,
    segments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    direction = _direction(angle_deg)
    contacts: list[tuple[float, int, str, dict[str, Any]]] = []
    for axis, target, side in (
        (0, -200.0, "left"),
        (0, 200.0, "right"),
        (1, -200.0, "top"),
        (1, 200.0, "bottom"),
    ):
        if abs(direction[axis]) <= 1e-10:
            continue
        parameter = (target - start[axis]) / direction[axis]
        point = (
            start[0] + parameter * direction[0],
            start[1] + parameter * direction[1],
        )
        if (
            parameter > 1e-6
            and -200.000001 <= point[0] <= 200.000001
            and -200.000001 <= point[1] <= 200.000001
        ):
            contacts.append(
                (
                    parameter,
                    1,
                    side,
                    {
                        "target_kind": "paper_boundary",
                        "target_side": side,
                        "end_cp": [round(point[0], 12), round(point[1], 12)],
                    },
                )
            )

    for segment in segments:
        first = _point(segment.get("start_cp"))
        second = _point(segment.get("end_cp"))
        if first is None or second is None:
            continue
        edge = second[0] - first[0], second[1] - first[1]
        determinant = _cross(direction, edge)
        if abs(determinant) <= 1e-10:
            continue
        offset = first[0] - start[0], first[1] - start[1]
        parameter = _cross(offset, edge) / determinant
        edge_parameter = _cross(offset, direction) / determinant
        if parameter <= 1e-6 or not -1e-8 <= edge_parameter <= 1.0 + 1e-8:
            continue
        point = (
            start[0] + parameter * direction[0],
            start[1] + parameter * direction[1],
        )
        segment_id = str(segment.get("id") or "")
        contacts.append(
            (
                parameter,
                0,
                segment_id,
                {
                    "target_kind": "existing_exact_segment",
                    "target_segment_id": segment_id,
                    "target_segment_parameter": round(edge_parameter, 12),
                    "end_cp": [round(point[0], 12), round(point[1], 12)],
                },
            )
        )
    return min(contacts, key=lambda item: item[:3])[3] if contacts else None


def _raw_observations(raw_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in raw_report.get("lines", []):
        if not isinstance(line, Mapping):
            continue
        try:
            angle_deg = float(line.get("orientation_deg"))
        except (TypeError, ValueError):
            continue
        channels = [str(item) for item in line.get("source_channels", [])]
        for index, visible in enumerate(line.get("visible_segments_px", [])):
            if not isinstance(visible, Mapping):
                continue
            start = _point(visible.get("start"))
            end = _point(visible.get("end"))
            if start is None or end is None:
                continue
            observations.append(
                {
                    "id": f"{line.get('id')}:{index}",
                    "source": "raw_image_finite_line_evidence",
                    "angle_deg": angle_deg,
                    "start_px": start,
                    "end_px": end,
                    "channels": channels,
                    "canonical_22_5": True,
                }
            )
    for raw in raw_report.get("noncanonical_angle_observations", []):
        if not isinstance(raw, Mapping):
            continue
        start = _point(raw.get("start_px"))
        end = _point(raw.get("end_px"))
        try:
            angle_deg = float(raw.get("observed_angle_deg"))
        except (TypeError, ValueError):
            continue
        if start is None or end is None or not math.isfinite(angle_deg):
            continue
        observations.append(
            {
                "id": str(raw.get("id") or ""),
                "source": "raw_image_noncanonical_finite_stroke_observation",
                "angle_deg": angle_deg,
                "start_px": start,
                "end_px": end,
                "channels": [str(raw.get("channel") or "")],
                "canonical_22_5": False,
            }
        )
    return observations


def _merge_intervals(intervals: Iterable[tuple[float, float]], gap: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for first, second in sorted((min(a, b), max(a, b)) for a, b in intervals):
        if not merged or first - merged[-1][1] > gap:
            merged.append([first, second])
        else:
            merged[-1][1] = max(merged[-1][1], second)
    return merged


def _ray_evidence(
    start_cp: Point,
    end_cp: Point,
    angle_deg: float,
    observations: list[dict[str, Any]],
    *,
    maximum_px: float,
    angle_tolerance_deg: float,
    distance_tolerance_px: float,
) -> dict[str, Any] | None:
    if maximum_px <= 0:
        return None
    scale = 400.0 / maximum_px
    start_px = ((start_cp[0] + 200.0) / scale, (start_cp[1] + 200.0) / scale)
    target_length_cp = math.dist(start_cp, end_cp)
    target_length_px = target_length_cp / scale
    if target_length_px < 3.0:
        return None
    direction = _direction(angle_deg)
    normal = -direction[1], direction[0]
    intervals: list[tuple[float, float]] = []
    matched: list[dict[str, Any]] = []
    channels: set[str] = set()
    for observation in observations:
        if _undirected_angle_error(float(observation["angle_deg"]), angle_deg) > angle_tolerance_deg:
            continue
        start = observation["start_px"]
        end = observation["end_px"]
        relative_start = start[0] - start_px[0], start[1] - start_px[1]
        relative_end = end[0] - start_px[0], end[1] - start_px[1]
        residual = max(abs(relative_start[0] * normal[0] + relative_start[1] * normal[1]),
                       abs(relative_end[0] * normal[0] + relative_end[1] * normal[1]))
        if residual > distance_tolerance_px:
            continue
        first = relative_start[0] * direction[0] + relative_start[1] * direction[1]
        second = relative_end[0] * direction[0] + relative_end[1] * direction[1]
        lower, upper = sorted((first, second))
        if upper <= 0.0 or lower >= target_length_px:
            continue
        intervals.append((max(0.0, lower), min(target_length_px, upper)))
        matched.append(
            {
                "id": observation["id"],
                "source": observation["source"],
                "angle_error_deg": round(
                    _undirected_angle_error(float(observation["angle_deg"]), angle_deg),
                    6,
                ),
                "line_residual_px": round(residual, 6),
            }
        )
        channels.update(item for item in observation["channels"] if item)
    merged = _merge_intervals(intervals, distance_tolerance_px)
    supported = sum(second - first for first, second in merged)
    coverage = min(1.0, supported / target_length_px)
    unsupported = max(0.0, target_length_px - supported)
    if coverage < 0.8 or unsupported > distance_tolerance_px:
        return None
    image_line_type = (
        2 if channels == {"red"} else 3 if channels == {"blue"} else None
    )
    return {
        "matched_observation_ids": sorted({item["id"] for item in matched}),
        "matched_observations": matched,
        "source_channels": sorted(channels),
        "image_line_type": image_line_type,
        "visible_coverage": round(coverage, 6),
        "unsupported_length_px": round(unsupported, 6),
        "distance_tolerance_px": round(distance_tolerance_px, 6),
        "angle_tolerance_deg": round(angle_tolerance_deg, 6),
    }


def _vertices(segments: list[dict[str, Any]]) -> dict[tuple[float, float], dict[str, Any]]:
    vertices: dict[tuple[float, float], dict[str, Any]] = {}
    for segment in segments:
        first = _point(segment.get("start_cp"))
        second = _point(segment.get("end_cp"))
        if first is None or second is None or math.dist(first, second) <= 1e-8:
            continue
        for endpoint, other, point_id in (
            (first, second, segment.get("start_point_id")),
            (second, first, segment.get("end_point_id")),
        ):
            key = _point_key(endpoint)
            vertex = vertices.setdefault(
                key,
                {"point": endpoint, "point_ids": set(), "rays": []},
            )
            if point_id is not None:
                vertex["point_ids"].add(str(point_id))
            angle_deg = _angle(
                math.degrees(math.atan2(other[1] - endpoint[1], other[0] - endpoint[0]))
            )
            if any(
                abs((angle_deg - float(ray["angle_deg"]) + 180.0) % 360.0 - 180.0)
                <= 1e-6
                for ray in vertex["rays"]
            ):
                continue
            vertex["rays"].append(
                {
                    "angle_deg": angle_deg,
                    "segment_id": str(segment.get("id") or ""),
                    "line_type": segment.get("line_type"),
                    "line_type_source": segment.get("line_type_source"),
                }
            )
    for vertex in vertices.values():
        vertex["rays"].sort(key=lambda item: float(item["angle_deg"]))
    return vertices


def _kawasaki_directions(rays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(rays)
    if total < 3 or total % 2 == 0:
        return []
    angles = [float(item["angle_deg"]) for item in rays]
    sectors = [
        (angles[(index + 1) % total] - angles[index]) % 360.0
        for index in range(total)
    ]
    output: list[dict[str, Any]] = []
    for index, first_angle in enumerate(angles):
        alternating = sum(
            (1.0 if offset % 2 == 0 else -1.0)
            * sectors[(index + offset) % total]
            for offset in range(total)
        )
        half = alternating / 2.0
        if not 1e-7 < half < sectors[index] - 1e-7:
            continue
        output.append(
            {
                "kind": "kawasaki_single_missing_ray",
                "angle_deg": _angle(first_angle + half),
                "parent_segment_ids": sorted(
                    {str(item["segment_id"]) for item in rays}
                ),
                "construction_expression": (
                    f"Kawasaki alternating sector sum / 2 = {half:.12g}°"
                ),
            }
        )
    return output


def _angle_bisector_directions(rays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # This layer proposes exactly one missing ray. Adding one ray can repair
    # cAMV parity only when the current crease degree is odd. In particular, a
    # non-collinear degree-two vertex is not evidence for a missing bisector;
    # it must be repaired at its actual topology/provenance source.
    if len(rays) < 3 or len(rays) % 2 == 0:
        return []
    output: list[dict[str, Any]] = []
    for index, first in enumerate(rays):
        second = rays[(index + 1) % len(rays)]
        sector = (float(second["angle_deg"]) - float(first["angle_deg"])) % 360.0
        if sector <= 2.0:
            continue
        output.append(
            {
                "kind": "existing_sector_angle_bisector",
                "angle_deg": _angle(float(first["angle_deg"]) + sector / 2.0),
                "parent_segment_ids": sorted(
                    {str(first["segment_id"]), str(second["segment_id"])}
                ),
                "construction_expression": (
                    f"bisect sector {float(first['angle_deg']):.12g}°"
                    f" → {float(second['angle_deg']):.12g}°"
                ),
            }
        )
    return output


def _maekawa_line_type_options(rays: list[dict[str, Any]]) -> list[int]:
    trusted_sources = {
        "explicit_segment_assignment",
        "source_image_color_evidence",
        "user_confirmed",
    }
    if any(
        int(ray.get("line_type") or 0) not in _FOLDING_TYPES
        or str(ray.get("line_type_source") or "") not in trusted_sources
        for ray in rays
    ):
        return [2, 3]
    mountains = sum(int(ray["line_type"]) == 2 for ray in rays)
    valleys = sum(int(ray["line_type"]) == 3 for ray in rays)
    return [
        line_type
        for line_type in (2, 3)
        if abs(
            mountains + int(line_type == 2)
            - valleys
            - int(line_type == 3)
        )
        == 2
    ]


def build_constrained_angle_candidates(
    raw_report: Mapping[str, Any] | None,
    cp_contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return theorem-derived, image-supported missing rays without applying them."""

    raw_report = raw_report if isinstance(raw_report, Mapping) else {}
    cp_contract = cp_contract if isinstance(cp_contract, Mapping) else {}
    gate_results = cp_contract.get("gate_results")
    gate_results = gate_results if isinstance(gate_results, Mapping) else {}
    failed_prerequisites = sorted(
        name
        for name, value in gate_results.items()
        if name != "flat_foldability"
        and isinstance(value, Mapping)
        and value.get("passed") is not True
    )
    if failed_prerequisites:
        return {
            "enabled": False,
            "mode": "constrained_angle_candidates_v1",
            "reason": "strict_22_5_base_not_complete",
            "failed_prerequisite_gates": failed_prerequisites,
            "candidates": [],
        }

    segments = [
        dict(item)
        for item in cp_contract.get("candidate_segments", [])
        if isinstance(item, Mapping)
    ]
    camv = (cp_contract.get("soft_diagnostics") or {}).get("camv")
    violations = camv.get("violations", []) if isinstance(camv, Mapping) else []
    violation_rules: dict[tuple[float, float], set[str]] = {}
    for violation in violations:
        if not isinstance(violation, Mapping):
            continue
        point = _point(violation.get("point"))
        rule = str(violation.get("rule") or "")
        if point is None or rule not in _GEOMETRY_CAMV_RULES:
            continue
        violation_rules.setdefault(_point_key(point), set()).add(rule)
    if not violation_rules:
        return {
            "enabled": True,
            "mode": "constrained_angle_candidates_v1",
            "reason": "no_geometric_camv_violation",
            "candidates": [],
            "candidate_count": 0,
        }

    try:
        maximum_px = float(raw_report.get("maximum_coordinate_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        maximum_px = 0.0
    observations = _raw_observations(raw_report)
    vertices = _vertices(segments)
    rejected: Counter[str] = Counter()
    point_candidates: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for key, rules in sorted(violation_rules.items()):
        vertex = vertices.get(key)
        if vertex is None:
            rejected["violation_not_at_exact_segment_vertex"] += 1
            continue
        start = vertex["point"]
        rays = vertex["rays"]
        if "number_of_folds" not in rules or len(rays) % 2 == 0:
            rejected["not_a_single_missing_ray_parity_case"] += 1
            continue
        theoretical = [
            *_kawasaki_directions(rays),
            *_angle_bisector_directions(rays),
        ]
        deduplicated: dict[float, dict[str, Any]] = {}
        for item in theoretical:
            angle_deg = float(item["angle_deg"])
            angle_key = round(angle_deg, 7)
            old = deduplicated.get(angle_key)
            rank = 0 if item["kind"] == "kawasaki_single_missing_ray" else 1
            if old is None or rank < (0 if old["kind"] == "kawasaki_single_missing_ray" else 1):
                deduplicated[angle_key] = item
        admitted_here: list[dict[str, Any]] = []
        for item in deduplicated.values():
            contact = _nearest_contact(start, float(item["angle_deg"]), segments)
            if contact is None:
                rejected["no_first_exact_contact"] += 1
                continue
            end = _point(contact.get("end_cp"))
            assert end is not None
            evidence = _ray_evidence(
                start,
                end,
                float(item["angle_deg"]),
                observations,
                maximum_px=maximum_px,
                angle_tolerance_deg=2.0,
                distance_tolerance_px=3.2,
            )
            if evidence is None:
                rejected["insufficient_source_image_evidence"] += 1
                continue
            line_type_options = _maekawa_line_type_options(rays)
            if not line_type_options:
                rejected["maekawa_has_no_single_line_solution"] += 1
                continue
            image_line_type = evidence.get("image_line_type")
            if image_line_type is not None and image_line_type not in line_type_options:
                rejected["source_colour_conflicts_with_maekawa"] += 1
                continue
            proposed_line_type = (
                image_line_type
                if image_line_type in _FOLDING_TYPES
                else line_type_options[0]
                if len(line_type_options) == 1
                else None
            )
            canonical_index = _canonical_direction_index(float(item["angle_deg"]))
            family = "canonical_22_5" if canonical_index is not None else "derived_noncanonical"
            priority = (
                0
                if family == "canonical_22_5"
                and item["kind"] == "kawasaki_single_missing_ray"
                else 1
                if family == "canonical_22_5"
                else 2
                if item["kind"] == "kawasaki_single_missing_ray"
                else 3
            )
            candidate = {
                "id": f"{item['kind']}:{key[0]:.7g}:{key[1]:.7g}:{float(item['angle_deg']):.7g}",
                "kind": item["kind"],
                "priority": priority,
                "direction_family": family,
                "direction_index": canonical_index,
                "direction_angle_deg": round(float(item["angle_deg"]), 12),
                "start_cp": [round(start[0], 12), round(start[1], 12)],
                "end_cp": list(contact["end_cp"]),
                "source_point_ids": sorted(vertex["point_ids"]),
                "parent_segment_ids": list(item["parent_segment_ids"]),
                "trigger_camv_rules": sorted(rules),
                "construction_source": item["kind"],
                "construction_expression": item["construction_expression"],
                "target": {key: value for key, value in contact.items() if key != "end_cp"},
                "image_evidence": evidence,
                "maekawa_line_type_options": line_type_options,
                "proposed_line_type": proposed_line_type,
                "applied": False,
                "requires_transactional_camv_recheck": True,
            }
            admitted_here.append(candidate)
        if admitted_here:
            best_priority = min(int(item["priority"]) for item in admitted_here)
            selected = [item for item in admitted_here if int(item["priority"]) == best_priority]
            rejected["lower_priority_fallback_suppressed"] += len(admitted_here) - len(selected)
            point_candidates[key] = selected

    candidates = sorted(
        (item for values in point_candidates.values() for item in values),
        key=lambda item: (
            int(item["priority"]),
            -float(item["image_evidence"]["visible_coverage"]),
            item["id"],
        ),
    )
    return {
        "enabled": True,
        "mode": "constrained_angle_candidates_v1",
        "reason": None if candidates else "no_theorem_candidate_with_full_image_evidence",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "rejection_counts": dict(sorted(rejected.items())),
        "observation_count": len(observations),
        "geometric_camv_vertex_count": len(violation_rules),
        "invariants": {
            "free_image_fitted_directions": 0,
            "canonical_22_5_candidates_precede_angle_bisectors": True,
            "angle_bisectors_require_two_existing_parent_rays": True,
            "kawasaki_candidates_require_odd_existing_ray_count": True,
            "candidate_requires_source_image_evidence_to_first_exact_contact": True,
            "degree_two_noncollinear_vertices_do_not_generate_bisectors": True,
            "candidates_are_not_applied_without_transactional_camv_recheck": True,
        },
    }


__all__ = ["build_constrained_angle_candidates"]
