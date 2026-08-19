from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Hashable, Mapping

from construction_search import ConstructionGraph, ConstructionOperation, SearchWeights, score_state
from provenance_v3 import _add_corner_candidates, _beam_search_continue, _copy_base_graph
from shadow_search import _anchor_point, _generation, _line_geometry, _operation_summary, _trace_id


_HIGH_COEFFICIENT_LIMIT = 10
_POINT_ON_RAY_PX = 0.32
_SEGMENT_MIN_PX = 6.0


def _observed_offset(anchor: Mapping[str, Any]) -> float:
    try:
        return float(anchor.get("observed_offset_px", anchor["line_offset_px"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _candidate_offset(anchor: Mapping[str, Any], point: tuple[float, float]) -> float | None:
    geometry = _line_geometry(anchor)
    if geometry is None:
        return None
    _, normal, _ = geometry
    return normal[0] * point[0] + normal[1] * point[1]


def _line_residual(anchor: Mapping[str, Any], offset: float) -> float:
    return abs(float(offset) - _observed_offset(anchor))


def _target_tolerance(anchor: Mapping[str, Any]) -> float:
    try:
        snap = float(anchor.get("snap_error_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        snap = 0.0
    return min(3.0, max(0.85, snap + 1.15))


def _point_line_distance(point: tuple[float, float], geometry) -> float:
    _, normal, offset = geometry
    return abs(normal[0] * point[0] + normal[1] * point[1] - offset)


def _large_coefficient_extra(coefficients: tuple[int, ...]) -> int:
    if not coefficients:
        return 0
    maximum = max(abs(value) for value in coefficients)
    if maximum <= _HIGH_COEFFICIENT_LIMIT:
        return 0
    # Large radical coefficients remain a fallback, not a hard ban.  But a
    # value such as -34+24√2 is no longer almost free: it pays a guard cost that
    # grows with coefficient magnitude, so a short exact construction route can
    # beat it even when raster residuals are similar.
    return 2 + math.ceil((maximum - _HIGH_COEFFICIENT_LIMIT) / 8.0)


def _guard_large_coefficients(
    graph: ConstructionGraph,
    details: dict[Hashable, dict[str, Any]],
) -> ConstructionGraph:
    guarded = ConstructionGraph()
    for operation in graph.operations.values():
        extra = _large_coefficient_extra(operation.algebraic_coefficients)
        metadata = details.setdefault(operation.id, {})
        if extra:
            metadata["large_coefficient_guard"] = True
            metadata["large_coefficient_extra_parameters"] = extra
            metadata["large_coefficient_limit"] = _HIGH_COEFFICIENT_LIMIT
        guarded.add_operation(
            replace(
                operation,
                independent_parameters=operation.independent_parameters + extra,
            )
        )
    return guarded


def _points_on_existing_rays(
    anchors: Mapping[int, Mapping[str, Any]],
) -> dict[int, list[tuple[float, int, tuple[float, float]]]]:
    result: dict[int, list[tuple[float, int, tuple[float, float]]]] = defaultdict(list)
    points = {
        trace_id: point
        for trace_id, anchor in anchors.items()
        if (point := _anchor_point(anchor)) is not None
    }
    for ray_id, ray_anchor in anchors.items():
        geometry = _line_geometry(ray_anchor)
        if geometry is None:
            continue
        direction, _, _ = geometry
        for point_id, point in points.items():
            if _point_line_distance(point, geometry) > _POINT_ON_RAY_PX:
                continue
            parameter = direction[0] * point[0] + direction[1] * point[1]
            result[ray_id].append((parameter, point_id, point))
        result[ray_id].sort(key=lambda value: value[0])
    return result


def _segment_pairs(
    values: list[tuple[float, int, tuple[float, float]]],
) -> list[tuple[tuple[float, int, tuple[float, float]], tuple[float, int, tuple[float, float]]]]:
    # Adjacent constructed points define the most credible finite segment.  Add
    # one-neighbour skips as a small allowance for a missing intermediate point;
    # never take arbitrary all-pairs chords.
    pairs = []
    for index in range(len(values) - 1):
        pairs.append((values[index], values[index + 1]))
        if index + 2 < len(values):
            pairs.append((values[index], values[index + 2]))
    return pairs


def _point_node(source_ray: int, first_id: int, second_id: int, tag: str) -> tuple[Any, ...]:
    low, high = sorted((first_id, second_id))
    return ("segment_ratio_point", source_ray, low, high, tag)


def _add_point_operation(
    graph: ConstructionGraph,
    details: dict[Hashable, dict[str, Any]],
    *,
    operation_id: Hashable,
    kind: str,
    parents: tuple[Hashable, ...],
    output: Hashable,
    generation: int,
    point: tuple[float, float],
    metadata: Mapping[str, Any],
) -> None:
    if operation_id in graph.operations:
        return
    graph.add_operation(
        ConstructionOperation(
            id=operation_id,
            kind=kind,
            parents=parents,
            outputs=(output,),
            generation=generation,
        )
    )
    details[operation_id] = {
        "provenance": kind,
        "point_px": [round(point[0], 6), round(point[1], 6)],
        **dict(metadata),
    }


def _add_ray_from_ratio_point(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    *,
    point_node: Hashable,
    point: tuple[float, float],
    provenance: str,
    metadata: Mapping[str, Any],
    generation: int,
) -> None:
    for target_id, target in anchors.items():
        offset = _candidate_offset(target, point)
        if offset is None:
            continue
        residual = _line_residual(target, offset)
        if residual > _target_tolerance(target):
            continue
        operation_id = ("ray_from_segment_ratio", point_node, target_id)
        if operation_id in graph.operations:
            continue
        graph.add_operation(
            ConstructionOperation(
                id=operation_id,
                kind="ray_from_segment_ratio",
                parents=(point_node,),
                outputs=(("ray", target_id),),
                explains=frozenset({("required_ray", target_id)}),
                residual=residual,
                generation=generation,
            )
        )
        details[operation_id] = {
            "provenance": provenance,
            "target_trace_id": target_id,
            "candidate_offset_px": round(float(offset), 9),
            "anchor_point_px": [round(point[0], 6), round(point[1], 6)],
            **dict(metadata),
        }


def _add_segment_ratio_candidates(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
) -> None:
    for source_ray, values in _points_on_existing_rays(anchors).items():
        for first, second in _segment_pairs(values):
            _, first_id, a = first
            _, second_id, b = second
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < _SEGMENT_MIN_PX:
                continue
            parents = tuple(dict.fromkeys((("ray", source_ray), ("ray", first_id), ("ray", second_id))))
            base_generation = max(
                _generation(anchors.get(source_ray, {})),
                _generation(anchors.get(first_id, {})),
                _generation(anchors.get(second_id, {})),
                0,
            )
            common = {
                "source_ray_trace_id": source_ray,
                "segment_endpoint_trace_ids": [first_id, second_id],
                "segment_start_px": [round(a[0], 6), round(a[1], 6)],
                "segment_end_px": [round(b[0], 6), round(b[1], 6)],
            }

            ratio_points: dict[str, tuple[float, float]] = {}
            for numerator, denominator in ((1, 2), (1, 3), (2, 3)):
                t = numerator / denominator
                point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                tag = f"{numerator}/{denominator}"
                node = _point_node(source_ray, first_id, second_id, tag)
                _add_point_operation(
                    graph,
                    details,
                    operation_id=("segment_ratio_point", source_ray, first_id, second_id, numerator, denominator),
                    kind="segment_ratio_point",
                    parents=parents,
                    output=node,
                    generation=base_generation + 1,
                    point=point,
                    metadata={**common, "ratio": tag},
                )
                ratio_points[tag] = point
                _add_ray_from_ratio_point(
                    graph,
                    anchors,
                    details,
                    point_node=node,
                    point=point,
                    provenance="segment_ratio_ray",
                    metadata={**common, "ratio": tag},
                    generation=base_generation + 2,
                )

            # 1/6 and 5/6 are deliberately NOT primitive ratio operators.  They
            # are represented as midpoint -> trisect the corresponding half,
            # matching the intended construction semantics.
            midpoint_node = _point_node(source_ray, first_id, second_id, "1/2")
            midpoint = ratio_points["1/2"]
            for tag, endpoint_id, endpoint, t in (
                ("1/6", first_id, a, 1.0 / 3.0),
                ("5/6", second_id, b, 2.0 / 3.0),
            ):
                point = (
                    endpoint[0] + (midpoint[0] - endpoint[0]) * t,
                    endpoint[1] + (midpoint[1] - endpoint[1]) * t,
                ) if tag == "1/6" else (
                    midpoint[0] + (endpoint[0] - midpoint[0]) * t,
                    midpoint[1] + (endpoint[1] - midpoint[1]) * t,
                )
                node = _point_node(source_ray, first_id, second_id, tag)
                _add_point_operation(
                    graph,
                    details,
                    operation_id=("half_segment_trisection_point", source_ray, first_id, second_id, tag),
                    kind="half_segment_trisection_point",
                    parents=(midpoint_node, ("ray", endpoint_id)),
                    output=node,
                    generation=base_generation + 2,
                    point=point,
                    metadata={**common, "ratio": tag, "derived_as": "midpoint_then_trisection"},
                )
                _add_ray_from_ratio_point(
                    graph,
                    anchors,
                    details,
                    point_node=node,
                    point=point,
                    provenance="segment_ratio_ray",
                    metadata={**common, "ratio": tag, "derived_as": "midpoint_then_trisection"},
                    generation=base_generation + 3,
                )


def build_provenance_report_v4(
    result: Mapping[str, Any],
    *,
    weights: SearchWeights = SearchWeights(),
    beam_width: int = 56,
) -> dict[str, Any]:
    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    if not trace:
        return {"enabled": False, "mode": "provenance_v4", "reason": "no_playback_trace"}
    try:
        analysis_size = int(result.get("stats", {}).get("analysis_size_used") or 512)
    except (TypeError, ValueError):
        analysis_size = 512
    analysis_size = max(2, analysis_size)

    graph, observations, anchors, details = _copy_base_graph(trace, analysis_size)
    graph = _guard_large_coefficients(graph, details)
    _add_corner_candidates(graph, anchors, details, float(analysis_size - 1))
    _add_segment_ratio_candidates(graph, anchors, details)

    selected = _beam_search_continue(
        graph,
        observations,
        weights=weights,
        beam_width=beam_width,
        max_rounds=max(16, min(220, len(trace) * 4 + 16)),
    )
    selected_operations = [_operation_summary(operation, details) for operation in selected.selected_operations]
    alternatives = [item for item in selected_operations if item.get("provenance") != "legacy"]
    counts = Counter(item.get("provenance") or item.get("kind") for item in alternatives)
    selected_offsets = {
        int(item["target_trace_id"]): float(item["candidate_offset_px"])
        for item in selected_operations
        if item.get("target_trace_id") is not None and item.get("candidate_offset_px") is not None
    }

    guarded_seeds = [
        item
        for item in selected_operations
        if item.get("large_coefficient_guard")
    ]
    all_guarded = [
        _operation_summary(operation, details)
        for operation in graph.operations.values()
        if details.get(operation.id, {}).get("large_coefficient_guard")
    ]
    selected_score = score_state(selected, observations, weights)
    return {
        "enabled": True,
        "mode": "provenance_v4",
        "candidate_operations": len(graph.operations),
        "required_observations": len(observations),
        "selected_score": round(selected_score, 6),
        "unexplained_observations": len(observations - selected.explained_observations),
        "route_changed": bool(alternatives),
        "selected_operations": selected_operations,
        "selected_alternatives": alternatives,
        "selected_alternative_counts": dict(sorted(counts.items())),
        "selected_offsets_px": {str(key): round(value, 9) for key, value in sorted(selected_offsets.items())},
        "large_coefficient_limit": _HIGH_COEFFICIENT_LIMIT,
        "large_coefficient_guarded_candidates": all_guarded,
        "selected_large_coefficient_guarded_candidates": guarded_seeds,
        "notes": [
            "Large-coefficient Q(√2) legacy seeds pay an explicit magnitude-growing guard cost but remain available as fallback.",
            "Ratio construction is performed on constructed line segments, never by dividing the paper boundary.",
            "A 1/6 point is represented as midpoint plus trisection of the half-segment, not as a primitive sixth-division rule.",
        ],
    }


__all__ = ["build_provenance_report_v4"]
