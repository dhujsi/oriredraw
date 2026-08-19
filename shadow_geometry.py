from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Mapping

from construction_search import SearchWeights


_HIGH_COEFFICIENT = 10
_INTEGER_TOKEN = re.compile(r"[+-]?\d+")

Point = tuple[float, float]
LineGeometry = tuple[tuple[float, float], tuple[float, float], float]


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(anchor.get("trace_id", fallback))
    except (TypeError, ValueError):
        return fallback


def _generation(anchor: Mapping[str, Any]) -> int:
    try:
        return int(anchor.get("generation", -1))
    except (TypeError, ValueError):
        return -1


def _parent_ids(anchor: Mapping[str, Any], valid_ids: set[int]) -> tuple[int, ...]:
    raw = anchor.get("trace_parent_ids")
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[int] = []
    for value in raw:
        try:
            parent = int(value)
        except (TypeError, ValueError):
            continue
        if parent in valid_ids and parent not in result:
            result.append(parent)
    return tuple(result)


def _anchor_point(anchor: Mapping[str, Any]) -> Point | None:
    raw = anchor.get("anchor_point_px")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None


def _orientation(anchor: Mapping[str, Any]) -> int | None:
    try:
        return int(round(float(anchor["angle"]) / 22.5)) % 8
    except (KeyError, TypeError, ValueError):
        return None


def _line_geometry(orientation: int, offset: float) -> LineGeometry:
    angle = orientation * math.pi / 8.0
    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    return direction, normal, float(offset)


def _ray_offset(orientation: int, point: Point) -> float:
    _, normal, _ = _line_geometry(orientation, 0.0)
    return normal[0] * point[0] + normal[1] * point[1]


def _observed_offset(anchor: Mapping[str, Any]) -> float:
    try:
        return float(anchor.get("observed_offset_px", anchor["line_offset_px"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _legacy_residual(anchor: Mapping[str, Any]) -> float:
    try:
        if anchor.get("legacy_image_residual_px") is not None:
            return max(0.0, float(anchor["legacy_image_residual_px"]))
        return abs(float(anchor["line_offset_px"]) - _observed_offset(anchor))
    except (KeyError, TypeError, ValueError):
        try:
            return max(0.0, float(anchor.get("snap_error_px", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0


def _coefficients(anchor: Mapping[str, Any]) -> tuple[int, ...]:
    coordinates = anchor.get("coordinate_expression")
    if isinstance(coordinates, (list, tuple)) and coordinates:
        texts = [str(value) for value in coordinates]
    else:
        expression = anchor.get("expression")
        texts = [str(expression)] if expression not in (None, "") else []
    if not any("√2" in text for text in texts):
        return ()
    values: list[int] = []
    for text in texts:
        values.extend(
            int(token)
            for token in _INTEGER_TOKEN.findall(text.replace("√2", "R"))
        )
    return tuple(values)


def _inside(point: Point, maximum: float, margin: float = 2.0) -> bool:
    return (
        -margin <= point[0] <= maximum + margin
        and -margin <= point[1] <= maximum + margin
    )


def _distance(first: Point, second: Point) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _legal_connector(first: Point, second: Point) -> bool:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    if math.hypot(dx, dy) < 1.0:
        return False
    angle = math.atan2(dy, dx)
    step = math.pi / 8.0
    nearest = round(angle / step) * step
    error = abs(math.atan2(math.sin(angle - nearest), math.cos(angle - nearest)))
    return error <= 1e-5


def _reflect(point: Point, axis: LineGeometry) -> Point:
    _, normal, offset = axis
    signed = normal[0] * point[0] + normal[1] * point[1] - offset
    return (
        point[0] - 2.0 * signed * normal[0],
        point[1] - 2.0 * signed * normal[1],
    )


def _intersection(first: LineGeometry, second: LineGeometry) -> Point | None:
    _, n1, o1 = first
    _, n2, o2 = second
    determinant = n1[0] * n2[1] - n1[1] * n2[0]
    if abs(determinant) < 1e-9:
        return None
    return (
        (o1 * n2[1] - n1[1] * o2) / determinant,
        (n1[0] * o2 - o1 * n2[0]) / determinant,
    )


def _boundary_contact(
    geometry: LineGeometry,
    legacy_anchor: Point,
    maximum: float,
) -> Point | None:
    direction, normal, offset = geometry
    choices = (
        (abs(legacy_anchor[0]), 0, 0.0),
        (abs(legacy_anchor[0] - maximum), 0, maximum),
        (abs(legacy_anchor[1]), 1, 0.0),
        (abs(legacy_anchor[1] - maximum), 1, maximum),
    )
    _, coordinate, boundary = min(choices, key=lambda item: item[0])
    base = (normal[0] * offset, normal[1] * offset)
    component = direction[coordinate]
    if abs(component) < 1e-9:
        return None
    parameter = (boundary - base[coordinate]) / component
    return (
        base[0] + parameter * direction[0],
        base[1] + parameter * direction[1],
    )


def _affected_descendants(
    anchors: Mapping[int, Mapping[str, Any]], root_id: int
) -> set[int]:
    valid = set(anchors)
    children: dict[int, list[int]] = defaultdict(list)
    for trace_id, anchor in anchors.items():
        for parent in _parent_ids(anchor, valid):
            children[parent].append(trace_id)
    affected = {root_id}
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child in children.get(current, ()):
            if child not in affected:
                affected.add(child)
                stack.append(child)
    return affected


def _root_tolerance(anchor: Mapping[str, Any]) -> float:
    try:
        snap = max(0.0, float(anchor.get("snap_error_px", 0.0) or 0.0))
    except (TypeError, ValueError):
        snap = 0.0
    return min(3.0, max(1.25, snap + 0.75))


def _candidate_root_proofs(
    anchors: Mapping[int, Mapping[str, Any]],
    root_id: int,
    affected: set[int],
    maximum: float,
    weights: SearchWeights,
) -> list[dict[str, Any]]:
    root = anchors[root_id]
    orientation = _orientation(root)
    if orientation is None:
        return []
    observed = _observed_offset(root)
    tolerance = _root_tolerance(root)
    unaffected = sorted(set(anchors) - affected)

    points: dict[tuple[float, float], tuple[Point, int]] = {}
    axes: list[tuple[int, LineGeometry]] = []
    for trace_id in unaffected:
        anchor = anchors[trace_id]
        point = _anchor_point(anchor)
        if point is not None:
            key = (round(point[0], 6), round(point[1], 6))
            points.setdefault(key, (point, trace_id))
        axis_orientation = _orientation(anchor)
        if axis_orientation is not None and anchor.get("line_offset_px") is not None:
            axes.append(
                (
                    trace_id,
                    _line_geometry(axis_orientation, float(anchor["line_offset_px"])),
                )
            )

    candidates: dict[float, dict[str, Any]] = {}

    def admit(point: Point, steps: list[dict[str, Any]]) -> None:
        if not _inside(point, maximum):
            return
        offset = _ray_offset(orientation, point)
        residual = abs(offset - observed)
        if residual > tolerance:
            return
        step_cost = weights.step * len(steps) + weights.residual * residual
        key = round(offset, 6)
        item = {
            "offset_px": offset,
            "image_residual_px": residual,
            "proof_cost": step_cost,
            "proof_operations": steps,
        }
        previous = candidates.get(key)
        if previous is None or (step_cost, residual) < (
            previous["proof_cost"], previous["image_residual_px"]
        ):
            candidates[key] = item

    point_items = list(points.values())
    for point, source_id in point_items:
        admit(
            point,
            [
                {
                    "kind": "direct_point",
                    "source_trace_id": source_id,
                    "point_px": [round(point[0], 6), round(point[1], 6)],
                }
            ],
        )
        for axis_id, axis in axes:
            reflected = _reflect(point, axis)
            if _distance(point, reflected) < 0.25:
                continue
            admit(
                reflected,
                [
                    {
                        "kind": "symmetry_point",
                        "source_trace_id": source_id,
                        "axis_trace_id": axis_id,
                        "source_point_px": [round(point[0], 6), round(point[1], 6)],
                        "reflected_point_px": [
                            round(reflected[0], 6), round(reflected[1], 6)
                        ],
                    },
                    {"kind": "ray_from_point", "target_trace_id": root_id},
                ],
            )

    for first_index, (first, first_id) in enumerate(point_items):
        for second, second_id in point_items[first_index + 1 :]:
            if not _legal_connector(first, second):
                continue
            midpoint = (
                (first[0] + second[0]) / 2.0,
                (first[1] + second[1]) / 2.0,
            )
            if not _inside(midpoint, maximum):
                continue
            midpoint_step = {
                "kind": "midpoint_point",
                "source_trace_ids": [first_id, second_id],
                "source_points_px": [
                    [round(first[0], 6), round(first[1], 6)],
                    [round(second[0], 6), round(second[1], 6)],
                ],
                "midpoint_px": [round(midpoint[0], 6), round(midpoint[1], 6)],
            }
            admit(
                midpoint,
                [midpoint_step, {"kind": "ray_from_point", "target_trace_id": root_id}],
            )
            for axis_id, axis in axes:
                reflected = _reflect(midpoint, axis)
                if _distance(midpoint, reflected) < 0.25:
                    continue
                admit(
                    reflected,
                    [
                        midpoint_step,
                        {
                            "kind": "symmetry_point",
                            "axis_trace_id": axis_id,
                            "source_point_px": [
                                round(midpoint[0], 6), round(midpoint[1], 6)
                            ],
                            "reflected_point_px": [
                                round(reflected[0], 6), round(reflected[1], 6)
                            ],
                        },
                        {"kind": "ray_from_point", "target_trace_id": root_id},
                    ],
                )

    return sorted(
        candidates.values(),
        key=lambda item: (item["proof_cost"], item["image_residual_px"]),
    )[:24]


def _propagate_offsets(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    root_id: int,
    root_offset: float,
    maximum: float,
) -> tuple[dict[int, float], list[int]]:
    valid = set(anchors)
    offsets = {
        trace_id: float(anchor["line_offset_px"])
        for trace_id, anchor in anchors.items()
        if trace_id not in affected and anchor.get("line_offset_px") is not None
    }
    offsets[root_id] = float(root_offset)
    unresolved: list[int] = []
    for trace_id in sorted(
        affected - {root_id}, key=lambda value: (_generation(anchors[value]), value)
    ):
        anchor = anchors[trace_id]
        parents = _parent_ids(anchor, valid)
        point: Point | None = None
        if len(parents) >= 2 and parents[0] in offsets and parents[1] in offsets:
            first_orientation = _orientation(anchors[parents[0]])
            second_orientation = _orientation(anchors[parents[1]])
            if first_orientation is not None and second_orientation is not None:
                point = _intersection(
                    _line_geometry(first_orientation, offsets[parents[0]]),
                    _line_geometry(second_orientation, offsets[parents[1]]),
                )
        elif (
            len(parents) == 1
            and parents[0] in offsets
            and "纸边交点" in str(anchor.get("source") or "")
        ):
            parent_orientation = _orientation(anchors[parents[0]])
            legacy_point = _anchor_point(anchor)
            if parent_orientation is not None and legacy_point is not None:
                point = _boundary_contact(
                    _line_geometry(parent_orientation, offsets[parents[0]]),
                    legacy_point,
                    maximum,
                )
        orientation = _orientation(anchor)
        if point is None or orientation is None or not _inside(point, maximum):
            unresolved.append(trace_id)
            continue
        offsets[trace_id] = _ray_offset(orientation, point)
    return offsets, unresolved


def _algebraic_extra_cost(
    anchor: Mapping[str, Any], weights: SearchWeights
) -> float:
    coefficients = _coefficients(anchor)
    if not coefficients:
        return 0.0
    return (
        weights.independent_parameter
        + weights.algebraic_complexity * math.log1p(sum(abs(value) for value in coefficients))
    )


def _legacy_route_score(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    weights: SearchWeights,
) -> tuple[float, float]:
    residual = sum(_legacy_residual(anchors[trace_id]) for trace_id in affected)
    cost = sum(
        weights.step
        + weights.residual * _legacy_residual(anchors[trace_id])
        + _algebraic_extra_cost(anchors[trace_id], weights)
        for trace_id in affected
    )
    cost += weights.generation_depth * max(
        (_generation(anchors[trace_id]) for trace_id in affected), default=0
    )
    return cost, residual


def _replacement_route_score(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    offsets: Mapping[int, float],
    proof_cost: float,
    root_id: int,
    weights: SearchWeights,
) -> tuple[float, float]:
    residual = sum(
        abs(float(offsets[trace_id]) - _observed_offset(anchors[trace_id]))
        for trace_id in affected
    )
    cost = proof_cost
    for trace_id in affected - {root_id}:
        item_residual = abs(
            float(offsets[trace_id]) - _observed_offset(anchors[trace_id])
        )
        cost += weights.step + weights.residual * item_residual
    cost += weights.generation_depth * max(
        (_generation(anchors[trace_id]) for trace_id in affected), default=0
    )
    return cost, residual


def build_geometry_shadow_report(
    result: Mapping[str, Any],
    *,
    weights: SearchWeights = SearchWeights(),
) -> dict[str, Any]:
    trace = [
        item
        for item in list(result.get("playback_trace") or [])
        if isinstance(item, Mapping)
    ]
    if not trace:
        return {
            "enabled": False,
            "mode": "shadow_geometry_propagation",
            "output_unchanged": True,
            "reason": "no_playback_trace",
        }
    anchors = {
        _trace_id(anchor, index): anchor for index, anchor in enumerate(trace)
    }
    try:
        analysis_size = int(result.get("stats", {}).get("analysis_size_used") or 512)
    except (TypeError, ValueError):
        analysis_size = 512
    maximum = float(max(2, analysis_size) - 1)

    reports: list[dict[str, Any]] = []
    for root_id, anchor in anchors.items():
        coefficients = _coefficients(anchor)
        if not coefficients or max(abs(value) for value in coefficients) <= _HIGH_COEFFICIENT:
            continue
        affected = _affected_descendants(anchors, root_id)
        legacy_score, legacy_residual = _legacy_route_score(anchors, affected, weights)
        best: dict[str, Any] | None = None
        for proof in _candidate_root_proofs(
            anchors, root_id, affected, maximum, weights
        ):
            offsets, unresolved = _propagate_offsets(
                anchors,
                affected,
                root_id,
                float(proof["offset_px"]),
                maximum,
            )
            if unresolved:
                continue
            route_score, route_residual = _replacement_route_score(
                anchors,
                affected,
                offsets,
                float(proof["proof_cost"]),
                root_id,
                weights,
            )
            candidate = {
                **proof,
                "offsets": offsets,
                "route_score": route_score,
                "route_residual": route_residual,
            }
            if best is None or (route_score, route_residual) < (
                best["route_score"], best["route_residual"]
            ):
                best = candidate

        item: dict[str, Any] = {
            "trace_id": root_id,
            "expression": anchor.get("coordinate_expression") or anchor.get("expression"),
            "algebraic_coefficients": list(coefficients),
            "affected_ray_count": len(affected),
            "legacy_offset_px": round(float(anchor["line_offset_px"]), 6),
            "observed_offset_px": round(_observed_offset(anchor), 6),
            "legacy_route_score": round(legacy_score, 6),
            "legacy_image_residual_sum_px": round(legacy_residual, 6),
            "replacement_found": best is not None,
        }
        if best is not None:
            changed = sorted(
                (
                    {
                        "trace_id": trace_id,
                        "legacy_offset_px": round(float(anchors[trace_id]["line_offset_px"]), 6),
                        "candidate_offset_px": round(float(best["offsets"][trace_id]), 6),
                        "observed_offset_px": round(_observed_offset(anchors[trace_id]), 6),
                        "shift_px": round(
                            float(best["offsets"][trace_id])
                            - float(anchors[trace_id]["line_offset_px"]),
                            6,
                        ),
                    }
                    for trace_id in affected
                    if abs(
                        float(best["offsets"][trace_id])
                        - float(anchors[trace_id]["line_offset_px"])
                    ) > 1e-5
                ),
                key=lambda value: (-abs(value["shift_px"]), value["trace_id"]),
            )
            item.update(
                {
                    "selected_offset_px": round(float(best["offset_px"]), 6),
                    "selected_root_shift_px": round(
                        float(best["offset_px"]) - float(anchor["line_offset_px"]), 6
                    ),
                    "replacement_route_score": round(float(best["route_score"]), 6),
                    "replacement_image_residual_sum_px": round(
                        float(best["route_residual"]), 6
                    ),
                    "score_improvement": round(
                        legacy_score - float(best["route_score"]), 6
                    ),
                    "residual_improvement_px": round(
                        legacy_residual - float(best["route_residual"]), 6
                    ),
                    "route_improved": float(best["route_score"]) < legacy_score,
                    "proof_operations": best["proof_operations"],
                    "changed_ray_offsets": changed[:80],
                }
            )
        reports.append(item)

    improved = [item for item in reports if item.get("route_improved")]
    return {
        "enabled": True,
        "mode": "shadow_geometry_propagation",
        "output_unchanged": True,
        "suspicious_seed_routes": reports,
        "improved_suspicious_seed_routes": len(improved),
        "route_changed": bool(improved),
        "notes": [
            "Large-coefficient algebraic seeds are challenged by direct, midpoint, and symmetry proofs built only from unaffected existing constructions.",
            "A winning root geometry is propagated through its legacy descendants before scoring, so a local improvement cannot win by ignoring downstream damage.",
        ],
    }
