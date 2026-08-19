from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from dataclasses import replace
from math import gcd
from typing import Any, Hashable, Mapping

from construction_search import (
    ConstructionGraph,
    ConstructionOperation,
    SearchState,
    SearchWeights,
    score_state,
)
from shadow_search import (
    _anchor_point,
    _generation,
    _line_geometry,
    _operation_summary,
    _parent_ids,
    _point_tolerance,
    _trace_id,
    build_candidate_graph,
)


_RATIO_DENOMINATOR_MAX = 6
_BOUNDARY_EPSILON_PX = 1.25


def _observed_offset(anchor: Mapping[str, Any]) -> float:
    try:
        return float(anchor.get("observed_offset_px", anchor["line_offset_px"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _legacy_offset(anchor: Mapping[str, Any]) -> float:
    try:
        return float(anchor["line_offset_px"])
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


def _candidate_tolerance(anchor: Mapping[str, Any]) -> float:
    # Keep the provenance search conservative. Raster ridge estimates are only
    # evidence; they never create a new orientation or a free floating point.
    return min(3.2, max(0.9, _point_tolerance(anchor) + 0.45))


def _paper_corners(maximum: float) -> list[tuple[str, tuple[float, float]]]:
    return [
        ("top_left", (0.0, 0.0)),
        ("top_right", (maximum, 0.0)),
        ("bottom_right", (maximum, maximum)),
        ("bottom_left", (0.0, maximum)),
    ]


def _boundary_source(
    trace_id: int,
    anchor: Mapping[str, Any],
    maximum: float,
) -> tuple[str, float, tuple[float, float]] | None:
    point = _anchor_point(anchor)
    if point is None:
        return None
    x, y = point
    options = [
        (abs(y), "top", x, (min(maximum, max(0.0, x)), 0.0)),
        (abs(x - maximum), "right", y, (maximum, min(maximum, max(0.0, y)))),
        (abs(y - maximum), "bottom", x, (min(maximum, max(0.0, x)), maximum)),
        (abs(x), "left", y, (0.0, min(maximum, max(0.0, y)))),
    ]
    distance, side, coordinate, snapped = min(options, key=lambda item: item[0])
    source = str(anchor.get("source") or "")
    if distance > _BOUNDARY_EPSILON_PX and "纸边交点" not in source:
        return None
    return side, float(coordinate), snapped


def _ratio_fractions() -> list[tuple[int, int]]:
    values: list[tuple[int, int]] = []
    for denominator in range(2, _RATIO_DENOMINATOR_MAX + 1):
        for numerator in range(1, denominator):
            if gcd(numerator, denominator) == 1:
                values.append((numerator, denominator))
    return values


def _beam_search_continue(
    graph: ConstructionGraph,
    observations: frozenset[Hashable],
    *,
    weights: SearchWeights,
    beam_width: int,
    max_rounds: int,
) -> SearchState:
    """Beam search that does not stop at the first complete legacy route.

    Complete states are retained as the incumbent while incomplete states keep
    expanding. This is required for routes that use one extra construction step
    (for example a ratio point or symmetry) but have lower total description
    cost than a magic independent seed.
    """

    initial = SearchState(known_nodes=frozenset())
    beam = [initial]
    best = initial
    best_score = score_state(best, observations, weights)

    for _ in range(max_rounds):
        candidates: list[SearchState] = []
        seen: set[tuple[frozenset[Hashable], frozenset[Hashable]]] = set()
        for state in beam:
            if observations.issubset(state.explained_observations):
                value = score_state(state, observations, weights)
                if value < best_score:
                    best, best_score = state, value
                continue
            for operation in graph.available_operations(state):
                child = state.apply(operation)
                fingerprint = (child.operation_ids, child.known_nodes)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                candidates.append(child)
        if not candidates:
            break
        candidates.sort(key=lambda state: score_state(state, observations, weights))
        if score_state(candidates[0], observations, weights) < best_score:
            best = candidates[0]
            best_score = score_state(best, observations, weights)

        # Preserve route-family diversity, especially independent-seed vs
        # corner/ratio/symmetry alternatives.
        next_beam: list[SearchState] = []
        counts: dict[tuple[str, ...], int] = {}
        for state in candidates:
            signature = tuple(sorted({op.kind for op in state.selected_operations}))
            if counts.get(signature, 0) >= 5:
                continue
            counts[signature] = counts.get(signature, 0) + 1
            next_beam.append(state)
            if len(next_beam) >= beam_width:
                break
        beam = next_beam

    for state in beam:
        if observations.issubset(state.explained_observations):
            value = score_state(state, observations, weights)
            if value < best_score:
                best, best_score = state, value
    return best


def _copy_base_graph(
    trace: list[Mapping[str, Any]],
    analysis_size: int,
) -> tuple[
    ConstructionGraph,
    frozenset[Hashable],
    dict[int, Mapping[str, Any]],
    dict[Hashable, dict[str, Any]],
]:
    base_graph, observations, _, details = build_candidate_graph(
        trace,
        analysis_size=analysis_size,
    )
    anchors = {_trace_id(anchor, i): anchor for i, anchor in enumerate(trace)}
    graph = ConstructionGraph()
    copied_details: dict[Hashable, dict[str, Any]] = {key: dict(value) for key, value in details.items()}

    for operation in base_graph.operations.values():
        target_id: int | None = None
        if operation.outputs and isinstance(operation.outputs[0], tuple) and len(operation.outputs[0]) == 2:
            try:
                target_id = int(operation.outputs[0][1])
            except (TypeError, ValueError):
                target_id = None
        anchor = anchors.get(target_id) if target_id is not None else None
        residual = operation.residual
        independent = operation.independent_parameters
        metadata = copied_details.setdefault(operation.id, {})

        if anchor is not None:
            candidate_offset: float | None = None
            provenance = str(metadata.get("provenance") or "")
            if provenance == "direct_point":
                try:
                    source_id = int(metadata["source_trace_id"])
                except (KeyError, TypeError, ValueError):
                    source_id = -1
                source_point = _anchor_point(anchors.get(source_id, {}))
                if source_point is not None:
                    candidate_offset = _candidate_offset(anchor, source_point)
                    metadata["anchor_point_px"] = [round(source_point[0], 6), round(source_point[1], 6)]
            elif provenance == "symmetry_point":
                reflected = metadata.get("reflected_point_px")
                if isinstance(reflected, (list, tuple)) and len(reflected) >= 2:
                    point = (float(reflected[0]), float(reflected[1]))
                    candidate_offset = _candidate_offset(anchor, point)
                    metadata["anchor_point_px"] = [round(point[0], 6), round(point[1], 6)]
            elif provenance == "legacy":
                candidate_offset = _legacy_offset(anchor)
                # A parentless non-corner/non-midpoint legacy ray is an
                # independent parameter, even if its coordinate happened to be
                # serialized without a large radical expression.
                if not operation.parents and operation.kind not in {"corner_seed", "midpoint_seed"}:
                    independent = max(1, independent)

            if candidate_offset is not None:
                metadata["candidate_offset_px"] = round(float(candidate_offset), 9)
                residual = _line_residual(anchor, candidate_offset)

        graph.add_operation(replace(operation, residual=max(0.0, residual), independent_parameters=independent))
    return graph, observations, anchors, copied_details


def _add_corner_candidates(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    maximum: float,
) -> None:
    for target_id, anchor in anchors.items():
        for corner_name, point in _paper_corners(maximum):
            offset = _candidate_offset(anchor, point)
            if offset is None:
                continue
            residual = _line_residual(anchor, offset)
            if residual > _candidate_tolerance(anchor):
                continue
            operation_id = ("paper_corner_ray", corner_name, target_id)
            graph.add_operation(
                ConstructionOperation(
                    id=operation_id,
                    kind="paper_corner_ray",
                    parents=(),
                    outputs=(("ray", target_id),),
                    explains=frozenset({("required_ray", target_id)}),
                    residual=residual,
                    generation=0,
                )
            )
            details[operation_id] = {
                "provenance": "paper_corner_ray",
                "target_trace_id": target_id,
                "corner": corner_name,
                "anchor_point_px": [round(point[0], 6), round(point[1], 6)],
                "candidate_offset_px": round(float(offset), 9),
            }


def _boundary_points(
    anchors: Mapping[int, Mapping[str, Any]],
    maximum: float,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    corner_map = {
        "top": [("top_left", 0.0, (0.0, 0.0)), ("top_right", maximum, (maximum, 0.0))],
        "right": [("top_right", 0.0, (maximum, 0.0)), ("bottom_right", maximum, (maximum, maximum))],
        "bottom": [("bottom_left", 0.0, (0.0, maximum)), ("bottom_right", maximum, (maximum, maximum))],
        "left": [("top_left", 0.0, (0.0, 0.0)), ("bottom_left", maximum, (0.0, maximum))],
    }
    for side, values in corner_map.items():
        for name, coordinate, point in values:
            result[side].append({"coordinate": coordinate, "point": point, "trace_id": None, "label": name})

    for trace_id, anchor in anchors.items():
        item = _boundary_source(trace_id, anchor, maximum)
        if item is None:
            continue
        side, coordinate, point = item
        result[side].append({"coordinate": coordinate, "point": point, "trace_id": trace_id, "label": f"trace_{trace_id}"})

    for side in result:
        dedup: list[dict[str, Any]] = []
        for item in sorted(result[side], key=lambda value: value["coordinate"]):
            if dedup and abs(item["coordinate"] - dedup[-1]["coordinate"]) < 0.5:
                # Prefer a real constructed boundary contact over an implicit
                # duplicate unless the existing point is a paper corner.
                if dedup[-1]["trace_id"] is None:
                    continue
                if item["trace_id"] is None:
                    dedup[-1] = item
                continue
            dedup.append(item)
        result[side] = dedup[:28]
    return result


def _pair_indices(count: int) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    if count <= 16:
        for first in range(count):
            for second in range(first + 1, count):
                pairs.add((first, second))
        return pairs
    for first in range(count):
        for second in range(first + 1, min(count, first + 5)):
            pairs.add((first, second))
    for index in range(1, count - 1):
        pairs.add((0, index))
        pairs.add((index, count - 1))
    return pairs


def _add_ratio_candidates(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    maximum: float,
) -> None:
    points_by_side = _boundary_points(anchors, maximum)
    fractions = _ratio_fractions()

    # Index observed rays by orientation and observed offset. A ratio point can
    # then find only nearby target rays instead of scanning every ray.
    by_orientation: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for trace_id, anchor in anchors.items():
        try:
            orientation = int(round(float(anchor["angle"]) / 22.5)) % 8
        except (KeyError, TypeError, ValueError):
            continue
        by_orientation[orientation].append((_observed_offset(anchor), trace_id))
    for values in by_orientation.values():
        values.sort()

    for side, sources in points_by_side.items():
        for first_index, second_index in _pair_indices(len(sources)):
            first = sources[first_index]
            second = sources[second_index]
            if abs(second["coordinate"] - first["coordinate"]) < 2.0:
                continue
            for numerator, denominator in fractions:
                t = numerator / denominator
                point = (
                    first["point"][0] + (second["point"][0] - first["point"][0]) * t,
                    first["point"][1] + (second["point"][1] - first["point"][1]) * t,
                )
                parents = tuple(
                    ("ray", trace_id)
                    for trace_id in (first["trace_id"], second["trace_id"])
                    if trace_id is not None
                )
                for orientation, targets in by_orientation.items():
                    angle = math.radians(orientation * 22.5)
                    normal = (-math.sin(angle), math.cos(angle))
                    candidate_offset = normal[0] * point[0] + normal[1] * point[1]
                    offsets = [value[0] for value in targets]
                    lower = bisect.bisect_left(offsets, candidate_offset - 3.2)
                    upper = bisect.bisect_right(offsets, candidate_offset + 3.2)
                    for _, target_id in targets[lower:upper]:
                        if ("ray", target_id) in parents:
                            continue
                        anchor = anchors[target_id]
                        residual = _line_residual(anchor, candidate_offset)
                        if residual > _candidate_tolerance(anchor):
                            continue
                        operation_id = (
                            "boundary_ratio_ray",
                            side,
                            first["label"],
                            second["label"],
                            numerator,
                            denominator,
                            target_id,
                        )
                        if operation_id in graph.operations:
                            continue
                        parent_generation = max(
                            (_generation(anchors[int(node[1])]) for node in parents),
                            default=-1,
                        )
                        graph.add_operation(
                            ConstructionOperation(
                                id=operation_id,
                                kind="boundary_ratio_ray",
                                parents=parents,
                                outputs=(("ray", target_id),),
                                explains=frozenset({("required_ray", target_id)}),
                                residual=residual,
                                generation=max(0, parent_generation + 1),
                                # Small denominator ratios are preferred softly;
                                # nothing is hard-coded specifically to thirds.
                                algebraic_coefficients=(numerator, denominator),
                            )
                        )
                        details[operation_id] = {
                            "provenance": "boundary_ratio_ray",
                            "target_trace_id": target_id,
                            "side": side,
                            "ratio": f"{numerator}/{denominator}",
                            "ratio_numerator": numerator,
                            "ratio_denominator": denominator,
                            "source_labels": [first["label"], second["label"]],
                            "source_trace_ids": [
                                value for value in (first["trace_id"], second["trace_id"]) if value is not None
                            ],
                            "anchor_point_px": [round(point[0], 6), round(point[1], 6)],
                            "candidate_offset_px": round(float(candidate_offset), 9),
                        }


def build_provenance_report_v3(
    result: Mapping[str, Any],
    *,
    weights: SearchWeights = SearchWeights(),
    beam_width: int = 48,
) -> dict[str, Any]:
    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    if not trace:
        return {"enabled": False, "mode": "provenance_v3", "reason": "no_playback_trace"}
    try:
        analysis_size = int(result.get("stats", {}).get("analysis_size_used") or 512)
    except (TypeError, ValueError):
        analysis_size = 512
    analysis_size = max(2, analysis_size)
    maximum = float(analysis_size - 1)

    graph, observations, anchors, details = _copy_base_graph(trace, analysis_size)
    _add_corner_candidates(graph, anchors, details, maximum)
    _add_ratio_candidates(graph, anchors, details, maximum)

    selected = _beam_search_continue(
        graph,
        observations,
        weights=weights,
        beam_width=beam_width,
        max_rounds=max(12, min(180, len(trace) * 3 + 12)),
    )
    selected_operations = [_operation_summary(operation, details) for operation in selected.selected_operations]
    alternatives = [item for item in selected_operations if item.get("provenance") != "legacy"]
    counts = Counter(item.get("provenance") or item.get("kind") for item in alternatives)
    selected_offsets = {
        int(item["target_trace_id"]): float(item["candidate_offset_px"])
        for item in selected_operations
        if item.get("target_trace_id") is not None and item.get("candidate_offset_px") is not None
    }

    legacy_graph, _, _, legacy_details = _copy_base_graph(trace, analysis_size)
    # Greedy topological replay of legacy operations gives a comparable baseline
    # score using the same raster residuals and independent-seed penalties.
    legacy_state = SearchState(known_nodes=frozenset())
    remaining = [op for op in legacy_graph.operations.values() if str(legacy_details.get(op.id, {}).get("provenance")) == "legacy"]
    while remaining:
        progressed = False
        later: list[ConstructionOperation] = []
        for operation in sorted(remaining, key=lambda op: (op.generation, str(op.id))):
            if set(operation.parents).issubset(legacy_state.known_nodes):
                legacy_state = legacy_state.apply(operation)
                progressed = True
            else:
                later.append(operation)
        if not progressed:
            break
        remaining = later

    selected_score = score_state(selected, observations, weights)
    legacy_score = score_state(legacy_state, observations, weights)
    return {
        "enabled": True,
        "mode": "provenance_v3",
        "candidate_operations": len(graph.operations),
        "required_observations": len(observations),
        "legacy_score": round(legacy_score, 6),
        "selected_score": round(selected_score, 6),
        "score_improvement": round(legacy_score - selected_score, 6),
        "unexplained_observations": len(observations - selected.explained_observations),
        "route_changed": bool(alternatives),
        "selected_operations": selected_operations,
        "selected_alternatives": alternatives,
        "selected_alternative_counts": dict(sorted(counts.items())),
        "selected_offsets_px": {str(key): round(value, 9) for key, value in sorted(selected_offsets.items())},
        "notes": [
            "Paper corners are explicit generation-0 construction seeds.",
            "Boundary intervals may be divided by simple rational ratios up to denominator 6; thirds are not hard-coded to any region.",
            "Parentless legacy rays are charged as independent parameters unless they are explicit corner/midpoint constructions.",
            "The search does not stop just because a complete legacy route appears first.",
        ],
    }


__all__ = ["build_provenance_report_v3"]
