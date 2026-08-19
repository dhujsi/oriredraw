from __future__ import annotations

import re
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
_INTEGER_TOKEN = re.compile(r"(?<![\w.])[+-]?\d+(?![\w.])")


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
        coefficients.extend(int(token) for token in _INTEGER_TOKEN.findall(value))
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


def _operation_summary(operation: ConstructionOperation) -> dict[str, Any]:
    coefficients = [int(value) for value in operation.algebraic_coefficients]
    return {
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


def build_candidate_graph(
    playback_trace: list[Mapping[str, Any]],
) -> tuple[ConstructionGraph, frozenset[Hashable], dict[Hashable, Mapping[str, Any]]]:
    """Translate the legacy exact-ray trace into a candidate construction DAG.

    This first shadow adapter intentionally consumes the already-serialized
    legacy provenance. It does not invent symmetry or alternative parents yet.
    That makes it safe to compare the new global scorer with the current route
    before v2 starts proposing new geometry.
    """

    graph = ConstructionGraph()
    indexed = [(_trace_id(anchor, index), anchor) for index, anchor in enumerate(playback_trace)]
    valid_ids = {trace_id for trace_id, _ in indexed}
    anchors_by_node: dict[Hashable, Mapping[str, Any]] = {}
    observations: set[Hashable] = set()

    for trace_id, anchor in indexed:
        node = ("ray", trace_id)
        anchors_by_node[node] = anchor
        parents = tuple(("ray", value) for value in _parent_ids(anchor, valid_ids))
        forms_output = bool(anchor.get("forms_output"))
        explains = frozenset({("output_ray", trace_id)}) if forms_output else frozenset()
        if forms_output:
            observations.update(explains)
        kind = _source_kind(anchor)
        coefficients = _algebraic_coefficients(anchor) if kind == "algebraic_seed" else ()
        graph.add_operation(
            ConstructionOperation(
                id=("legacy", trace_id),
                kind=kind,
                parents=parents,
                outputs=(node,),
                explains=explains,
                residual=_snap_residual(anchor),
                generation=max(0, _generation(anchor)),
                independent_parameters=1 if kind == "algebraic_seed" else 0,
                algebraic_coefficients=coefficients,
            )
        )

    return graph, frozenset(observations), anchors_by_node


def _legacy_state(
    graph: ConstructionGraph,
    camv_violations: float,
) -> tuple[SearchState, list[ConstructionOperation]]:
    """Replay every legacy operation whose parents are available."""

    state = SearchState(known_nodes=frozenset(), camv_violations=camv_violations)
    remaining = list(graph.operations.values())
    applied: list[ConstructionOperation] = []
    while remaining:
        progressed = False
        next_remaining: list[ConstructionOperation] = []
        for operation in sorted(remaining, key=lambda item: (item.generation, str(item.id))):
            if set(operation.parents).issubset(state.known_nodes):
                state = state.apply(operation)
                applied.append(operation)
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

    graph, observations, anchors_by_node = build_candidate_graph(trace)
    stats = result.get("stats") or {}
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
        max_rounds=max(4, min(96, len(graph.operations) + 4)),
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
                **_operation_summary(operation),
                "source": str(anchor.get("source") or ""),
                "expression": anchor.get("coordinate_expression") or anchor.get("expression"),
            }
        )

    provenance_counts = [len(values) for values in graph.provenance.values()]
    selected_operations = [
        _operation_summary(operation)
        for operation in selected.selected_operations
    ]
    return {
        "enabled": True,
        "mode": "shadow",
        "output_unchanged": True,
        "candidate_rays": len(graph.operations),
        "output_observations": len(observations),
        "alternative_provenance_nodes": sum(count > 1 for count in provenance_counts),
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
        },
        "route_changed": selected_ids != legacy_ids,
        "high_complexity_algebraic_candidates": high_complexity,
        "notes": [
            "This pass ranks the legacy provenance snapshot only; it does not change exported CP geometry.",
            "Symmetry and other alternative provenance will be added as extra operations in later passes.",
        ],
    }
