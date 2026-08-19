from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Hashable, Mapping

from construction_search import (
    ConstructionGraph,
    ConstructionOperation,
    SearchState,
    SearchWeights,
    beam_search,
    score_state,
)


_HIGH_COEFFICIENT = 10
_INTEGER_TOKEN = re.compile(r"[+-]?\d+")
_POINT_BUCKET_PX = 3.0


def _generation(anchor: Mapping[str, Any]) -> int:
    try:
        return int(anchor.get("generation", -1))
    except (TypeError, ValueError):
        return -1


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(anchor.get("trace_id", fallback))
    except (TypeError, ValueError):
        return fallback


def _source_kind(anchor: Mapping[str, Any]) -> str:
    source = str(anchor.get("source") or "")
    expression = str(anchor.get("expression") or "")
    coordinate_expression = anchor.get("coordinate_expression")
    coordinate_text = " ".join(
        str(value) for value in coordinate_expression
    ) if isinstance(coordinate_expression, (list, tuple)) else ""
    algebraic = "√2" in expression or "√2" in coordinate_text or "a+b√2" in source

    if "角点" in source:
        return "corner_seed"
    if "中点" in source or expression == "1/2":
        return "midpoint_seed"
    if "纸边交点" in source:
        return "boundary_contact"
    if "交点" in source:
        return "intersection"
    if algebraic:
        return "algebraic_seed"
    return "existing_candidate"


def _algebraic_coefficients(anchor: Mapping[str, Any]) -> tuple[int, ...]:
    values: list[str] = []
    coordinate_expression = anchor.get("coordinate_expression")
    if isinstance(coordinate_expression, (list, tuple)):
        values.extend(str(value) for value in coordinate_expression)
    expression = anchor.get("expression")
    if expression not in (None, ""):
        values.append(str(expression))
    if not any("√2" in value for value in values):
        return ()
    coefficients: list[int] = []
    for value in values:
        # Do not count the literal 2 in √2 as an algebraic coefficient.
        stripped = value.replace("√2", "R")
        coefficients.extend(int(token) for token in _INTEGER_TOKEN.findall(stripped))
    return tuple(coefficients)


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


def _snap_residual(anchor: Mapping[str, Any]) -> float:
    try:
        value = float(anchor.get("snap_error_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, value)


def _anchor_point(anchor: Mapping[str, Any]) -> tuple[float, float] | None:
    raw = anchor.get("anchor_point_px")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None


def _line_geometry(
    anchor: Mapping[str, Any],
) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    try:
        angle = math.radians(float(anchor["angle"]))
        offset = float(anchor["line_offset_px"])
    except (KeyError, TypeError, ValueError):
        return None
    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    return direction, normal, offset


def _point_tolerance(anchor: Mapping[str, Any]) -> float:
    # A shadow alternative may move the legacy exact anchor only by about the
    # amount already justified by the legacy raster snap. This prevents broad
    # symmetry matching while still allowing a better exact construction to
    # replace a slightly shifted high-coefficient algebraic seed.
    return min(_POINT_BUCKET_PX, max(0.75, _snap_residual(anchor) + 1.0))


def _line_residual(
    point: tuple[float, float],
    geometry: tuple[tuple[float, float], tuple[float, float], float],
) -> float:
    _, normal, offset = geometry
    return abs(normal[0] * point[0] + normal[1] * point[1] - offset)


def _reflect_point(
    point: tuple[float, float],
    axis: tuple[tuple[float, float], tuple[float, float], float],
) -> tuple[float, float]:
    _, normal, offset = axis
    signed = normal[0] * point[0] + normal[1] * point[1] - offset
    return (
        point[0] - 2.0 * signed * normal[0],
        point[1] - 2.0 * signed * normal[1],
    )


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _bucket(point: tuple[float, float]) -> tuple[int, int]:
    return (
        math.floor(point[0] / _POINT_BUCKET_PX),
        math.floor(point[1] / _POINT_BUCKET_PX),
    )


def _nearby_ids(
    point: tuple[float, float],
    buckets: Mapping[tuple[int, int], list[int]],
) -> list[int]:
    x, y = _bucket(point)
    result: list[int] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            result.extend(buckets.get((x + dx, y + dy), ()))
    return result


def _operation_summary(
    operation: ConstructionOperation,
    details: Mapping[Hashable, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    coefficients = [int(value) for value in operation.algebraic_coefficients]
    result = {
        "id": str(operation.id),
        "kind": operation.kind,
        "parents": [str(value) for value in operation.parents],
        "outputs": [str(value) for value in operation.outputs],
        "generation": int(operation.generation),
        "residual_px": round(float(operation.residual), 6),
        "algebraic_coefficients": coefficients,
        "high_coefficient": bool(
            coefficients and max(abs(value) for value in coefficients) > _HIGH_COEFFICIENT
        ),
    }
    if details and operation.id in details:
        result.update(dict(details[operation.id]))
    return result


def build_candidate_graph(
    playback_trace: list[Mapping[str, Any]],
    *,
    analysis_size: int | None = None,
) -> tuple[
    ConstructionGraph,
    frozenset[Hashable],
    dict[Hashable, Mapping[str, Any]],
    dict[Hashable, dict[str, Any]],
]:
    """Translate legacy provenance and add conservative alternative routes.

    The logical ray node stays the same for every provenance candidate. In
    shadow mode this lets downstream legacy dependencies remain usable while a
    direct-point or reflected-point explanation competes with the old seed.
    Geometry is still diagnostic only: exported CP coordinates remain v1.
    """

    graph = ConstructionGraph()
    indexed = [
        (_trace_id(anchor, index), anchor)
        for index, anchor in enumerate(playback_trace)
    ]
    anchors_by_id = {trace_id: anchor for trace_id, anchor in indexed}
    valid_ids = set(anchors_by_id)
    anchors_by_node: dict[Hashable, Mapping[str, Any]] = {}
    observations: set[Hashable] = set()
    operation_details: dict[Hashable, dict[str, Any]] = {}

    points = {
        trace_id: point
        for trace_id, anchor in indexed
        if (point := _anchor_point(anchor)) is not None
    }
    geometries = {
        trace_id: geometry
        for trace_id, anchor in indexed
        if (geometry := _line_geometry(anchor)) is not None
    }
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for trace_id, point in points.items():
        buckets[_bucket(point)].append(trace_id)

    # First preserve every legacy explanation verbatim.
    for trace_id, anchor in indexed:
        node = ("ray", trace_id)
        anchors_by_node[node] = anchor
        parents = tuple(("ray", value) for value in _parent_ids(anchor, valid_ids))
        observation = ("required_ray", trace_id)
        observations.add(observation)
        kind = _source_kind(anchor)
        coefficients = _algebraic_coefficients(anchor) if kind == "algebraic_seed" else ()
        operation_id = ("legacy", trace_id)
        graph.add_operation(
            ConstructionOperation(
                id=operation_id,
                kind=kind,
                parents=parents,
                outputs=(node,),
                explains=frozenset({observation}),
                residual=_snap_residual(anchor),
                generation=max(0, _generation(anchor)),
                independent_parameters=1 if kind == "algebraic_seed" else 0,
                algebraic_coefficients=coefficients,
            )
        )
        operation_details[operation_id] = {
            "target_trace_id": trace_id,
            "provenance": "legacy",
        }

    # Reuse an already constructed anchor point directly. The source ray node
    # acts as the proof that its anchor point already exists; the target ray is
    # then emitted from that point with one ordinary construction step.
    for source_id, source_point in points.items():
        for target_id in _nearby_ids(source_point, buckets):
            if target_id == source_id or target_id not in geometries:
                continue
            target_point = points[target_id]
            point_delta = _distance(source_point, target_point)
            tolerance = _point_tolerance(anchors_by_id[target_id])
            if point_delta > tolerance:
                continue
            line_delta = _line_residual(source_point, geometries[target_id])
            if line_delta > tolerance:
                continue
            operation_id = ("direct_point", source_id, target_id)
            if operation_id in graph.operations:
                continue
            graph.add_operation(
                ConstructionOperation(
                    id=operation_id,
                    kind="direct_point",
                    parents=(("ray", source_id),),
                    outputs=(("ray", target_id),),
                    explains=frozenset({("required_ray", target_id)}),
                    residual=max(point_delta, line_delta),
                    generation=max(0, _generation(anchors_by_id[source_id]) + 1),
                )
            )
            operation_details[operation_id] = {
                "source_trace_id": source_id,
                "target_trace_id": target_id,
                "provenance": "direct_point",
                "point_delta_px": round(point_delta, 6),
            }

    # Reflect an already constructed point across an already constructed ray.
    # Candidate lookup is spatially bucketed, so this is O(points * axes)
    # rather than O(points * axes * targets).
    maximum = float(analysis_size - 1) if analysis_size and analysis_size > 1 else None
    for source_id, source_point in points.items():
        for axis_id, axis_geometry in geometries.items():
            reflected = _reflect_point(source_point, axis_geometry)
            if _distance(source_point, reflected) < 0.25:
                continue
            if maximum is not None and not (
                -2.0 <= reflected[0] <= maximum + 2.0
                and -2.0 <= reflected[1] <= maximum + 2.0
            ):
                continue
            for target_id in _nearby_ids(reflected, buckets):
                if target_id in {source_id, axis_id} or target_id not in geometries:
                    continue
                target_point = points[target_id]
                tolerance = _point_tolerance(anchors_by_id[target_id])
                point_delta = _distance(reflected, target_point)
                if point_delta > tolerance:
                    continue
                line_delta = _line_residual(reflected, geometries[target_id])
                if line_delta > tolerance:
                    continue
                operation_id = ("symmetry_point", source_id, axis_id, target_id)
                if operation_id in graph.operations:
                    continue
                graph.add_operation(
                    ConstructionOperation(
                        id=operation_id,
                        kind="symmetry_point",
                        parents=(("ray", source_id), ("ray", axis_id)),
                        outputs=(("ray", target_id),),
                        explains=frozenset({("required_ray", target_id)}),
                        residual=max(point_delta, line_delta),
                        generation=max(
                            0,
                            max(
                                _generation(anchors_by_id[source_id]),
                                _generation(anchors_by_id[axis_id]),
                            )
                            + 1,
                        ),
                    )
                )
                operation_details[operation_id] = {
                    "source_trace_id": source_id,
                    "axis_trace_id": axis_id,
                    "target_trace_id": target_id,
                    "provenance": "symmetry_point",
                    "reflected_point_px": [
                        round(reflected[0], 6),
                        round(reflected[1], 6),
                    ],
                    "point_delta_px": round(point_delta, 6),
                }

    return graph, frozenset(observations), anchors_by_node, operation_details


def _legacy_state(
    graph: ConstructionGraph,
    camv_violations: float,
) -> tuple[SearchState, list[ConstructionOperation]]:
    """Replay only legacy operations, never the newly proposed alternatives."""

    state = SearchState(known_nodes=frozenset(), camv_violations=camv_violations)
    remaining = [
        operation
        for operation in graph.operations.values()
        if isinstance(operation.id, tuple)
        and operation.id
        and operation.id[0] == "legacy"
    ]
    while remaining:
        progressed = False
        next_remaining: list[ConstructionOperation] = []
        for operation in sorted(remaining, key=lambda item: (item.generation, str(item.id))):
            if set(operation.parents).issubset(state.known_nodes):
                state = state.apply(operation)
                progressed = True
            else:
                next_remaining.append(operation)
        if not progressed:
            break
        remaining = next_remaining
    return state, remaining


def build_shadow_report(
    result: Mapping[str, Any],
    *,
    weights: SearchWeights = SearchWeights(),
    beam_width: int = 32,
) -> dict[str, Any]:
    trace = [
        item for item in list(result.get("playback_trace") or [])
        if isinstance(item, Mapping)
    ]
    if not trace:
        return {
            "enabled": False,
            "mode": "shadow",
            "output_unchanged": True,
            "reason": "no_playback_trace",
        }

    stats = result.get("stats") or {}
    try:
        analysis_size = int(stats.get("analysis_size_used") or 0)
    except (TypeError, ValueError):
        analysis_size = 0
    graph, observations, anchors_by_node, operation_details = build_candidate_graph(
        trace,
        analysis_size=analysis_size or None,
    )
    try:
        camv_violations = float(stats.get("camv_structure_violation_count", 0) or 0)
    except (TypeError, ValueError):
        camv_violations = 0.0

    legacy_state, unresolved_legacy = _legacy_state(graph, camv_violations)
    initial = SearchState(
        known_nodes=frozenset(),
        camv_violations=camv_violations,
    )
    selected = beam_search(
        graph,
        initial,
        observations,
        weights=weights,
        beam_width=beam_width,
        max_rounds=max(4, min(96, len(trace) * 2 + 8)),
    )

    selected_ids = selected.operation_ids
    legacy_ids = legacy_state.operation_ids
    unexplained_v1 = observations - legacy_state.explained_observations
    unexplained_v2 = observations - selected.explained_observations

    high_complexity: list[dict[str, Any]] = []
    for operation in graph.operations.values():
        coefficients = operation.algebraic_coefficients
        if not coefficients or max(abs(value) for value in coefficients) <= _HIGH_COEFFICIENT:
            continue
        output = operation.outputs[0] if operation.outputs else None
        anchor = anchors_by_node.get(output, {})
        high_complexity.append(
            {
                **_operation_summary(operation, operation_details),
                "source": str(anchor.get("source") or ""),
                "expression": anchor.get("coordinate_expression") or anchor.get("expression"),
            }
        )

    provenance_counts = [len(values) for values in graph.provenance.values()]
    selected_operations = [
        _operation_summary(operation, operation_details)
        for operation in selected.selected_operations
    ]
    selected_alternatives = [
        item for item in selected_operations if item.get("provenance") != "legacy"
    ]
    alternative_counts = Counter(
        operation.kind
        for operation in graph.operations.values()
        if not (
            isinstance(operation.id, tuple)
            and operation.id
            and operation.id[0] == "legacy"
        )
    )
    return {
        "enabled": True,
        "mode": "shadow",
        "output_unchanged": True,
        "candidate_operations": len(graph.operations),
        "candidate_rays": len(trace),
        "required_observations": len(observations),
        "output_rays": sum(bool(item.get("forms_output")) for item in trace),
        "alternative_provenance_nodes": sum(count > 1 for count in provenance_counts),
        "alternative_candidates": dict(sorted(alternative_counts.items())),
        "camv_violation_count": camv_violations,
        "legacy": {
            "operation_count": len(legacy_state.selected_operations),
            "score": round(score_state(legacy_state, observations, weights), 6),
            "unexplained_observations": len(unexplained_v1),
            "unresolved_operations": len(unresolved_legacy),
        },
        "v2": {
            "operation_count": len(selected.selected_operations),
            "score": round(score_state(selected, observations, weights), 6),
            "unexplained_observations": len(unexplained_v2),
            "selected_operations": selected_operations,
            "selected_alternatives": selected_alternatives,
        },
        "route_changed": selected_ids != legacy_ids,
        "high_complexity_algebraic_candidates": high_complexity,
        "notes": [
            "Shadow mode can now compare legacy, direct-point, and symmetry-point provenance without changing exported CP geometry.",
            "Alternative geometry is admitted only near the legacy anchor; a later pass will propagate the winning exact geometry through downstream intersections.",
        ],
    }
