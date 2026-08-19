from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace
from typing import Any, Hashable, Mapping

from construction_search import ConstructionGraph, ConstructionOperation, SearchWeights, score_state
from provenance_v3 import (
    _add_corner_candidates,
    _beam_search_continue,
    _candidate_tolerance,
    _copy_base_graph,
)
from provenance_v4 import _add_segment_ratio_candidates, _guard_large_coefficients
from shadow_search import _generation, _line_geometry, _operation_summary


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


def _quality_penalties(report: Mapping[str, Any] | None) -> dict[int, float]:
    output: dict[int, float] = {}
    raw = (report or {}).get("suspect_trace_penalties")
    if not isinstance(raw, Mapping):
        return output
    for key, value in raw.items():
        try:
            output[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return output


def _apply_quality_quarantine(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    quality_report: Mapping[str, Any] | None,
) -> ConstructionGraph:
    """Make a bad geometry hypothesis expensive without deleting its evidence.

    The raster observation stays required.  Only the current geometric
    explanation is quarantined, so the beam is pushed to try a different point
    or provenance for the same observed ridge.
    """

    penalties = _quality_penalties(quality_report)
    if not penalties:
        return graph
    output = ConstructionGraph()
    for operation in graph.operations.values():
        metadata = details.setdefault(operation.id, {})
        target = metadata.get("target_trace_id")
        try:
            trace_id = int(target) if target is not None else None
        except (TypeError, ValueError):
            trace_id = None
        severity = penalties.get(trace_id, 0.0) if trace_id is not None else 0.0
        extra = 0
        if severity > 0.0 and trace_id in anchors:
            provenance = str(metadata.get("provenance") or "legacy")
            if provenance == "legacy":
                extra = max(1, min(7, int(math.ceil(severity / 1.35))))
                metadata["quality_quarantined_legacy"] = True
            else:
                candidate = metadata.get("candidate_offset_px")
                try:
                    legacy = float(anchors[trace_id]["line_offset_px"])
                    shift = abs(float(candidate) - legacy)
                except (KeyError, TypeError, ValueError):
                    shift = math.inf
                # A prettier provenance for the same bad coordinates does not
                # repair a geometry failure.  Require a material alternative.
                if shift < 0.22:
                    extra = max(1, min(6, int(math.ceil(severity / 1.7))))
                    metadata["quality_stagnation_penalty"] = True
                    metadata["quality_candidate_shift_px"] = round(shift, 6)
            if extra:
                metadata["quality_extra_parameters"] = extra
                metadata["quality_severity"] = round(severity, 6)
        output.add_operation(
            replace(
                operation,
                independent_parameters=operation.independent_parameters + extra,
            )
        )
    return output


def _proof_parent_ids(
    route: Mapping[str, Any],
    anchors: Mapping[int, Mapping[str, Any]],
    target_id: int,
) -> list[int]:
    target_generation = _generation(anchors.get(target_id, {}))
    candidates: list[int] = []
    for operation in list(route.get("proof_operations") or []):
        if not isinstance(operation, Mapping):
            continue
        for key in ("source_trace_ids", "reused_trace_ids"):
            raw = operation.get(key)
            if not isinstance(raw, (list, tuple)):
                continue
            for value in raw:
                try:
                    trace_id = int(value)
                except (TypeError, ValueError):
                    continue
                if trace_id == target_id or trace_id not in anchors:
                    continue
                if _generation(anchors[trace_id]) >= target_generation:
                    continue
                if trace_id not in candidates:
                    candidates.append(trace_id)
        for key in ("axis_trace_id", "source_trace_id"):
            if operation.get(key) is None:
                continue
            try:
                trace_id = int(operation[key])
            except (TypeError, ValueError):
                continue
            if trace_id == target_id or trace_id not in anchors:
                continue
            if _generation(anchors[trace_id]) >= target_generation:
                continue
            if trace_id not in candidates:
                candidates.append(trace_id)
    return candidates


def _add_geometry_reroot_candidates(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    geometry_report: Mapping[str, Any] | None,
) -> None:
    for route in list((geometry_report or {}).get("suspicious_seed_routes") or []):
        if not isinstance(route, Mapping) or not route.get("replacement_found"):
            continue
        try:
            target_id = int(route["trace_id"])
            candidate_offset = float(route["selected_offset_px"])
            shift = abs(float(route.get("selected_root_shift_px", 0.0) or 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if target_id not in anchors or shift < 0.12:
            continue
        parent_ids = _proof_parent_ids(route, anchors, target_id)
        parents = tuple(("ray", trace_id) for trace_id in parent_ids)
        residual = abs(candidate_offset - _observed_offset(anchors[target_id]))
        proof_steps = max(1, len(list(route.get("proof_operations") or [])))
        # One graph operation summarizes a short exact proof.  Charge enough
        # proxy complexity that it competes fairly with an explicit multi-step
        # route without turning it into a free magic seed.
        proxy_parameters = max(0, int(math.ceil(max(0, proof_steps - 1) / 1.5)))
        operation_id = ("geometry_reroot_v5", target_id, round(candidate_offset, 6))
        if operation_id in graph.operations:
            continue
        generation = max((_generation(anchors[item]) for item in parent_ids), default=-1) + 1
        graph.add_operation(
            ConstructionOperation(
                id=operation_id,
                kind="geometry_reroot",
                parents=parents,
                outputs=(("ray", target_id),),
                explains=frozenset({("required_ray", target_id)}),
                residual=residual,
                generation=max(0, generation),
                independent_parameters=proxy_parameters,
            )
        )
        details[operation_id] = {
            "provenance": "geometry_reroot",
            "target_trace_id": target_id,
            "candidate_offset_px": round(candidate_offset, 9),
            "selected_root_shift_px": round(shift, 6),
            "proof_operations": list(route.get("proof_operations") or []),
            "source_trace_ids": parent_ids,
            "route_score_improvement": route.get("score_improvement"),
            "route_residual_improvement_px": route.get("residual_improvement_px"),
        }


def _paper_corners(maximum: float) -> list[tuple[str, tuple[float, float]]]:
    return [
        ("top_left", (0.0, 0.0)),
        ("top_right", (maximum, 0.0)),
        ("bottom_right", (maximum, maximum)),
        ("bottom_left", (0.0, maximum)),
    ]


def _reflect(point: tuple[float, float], line) -> tuple[float, float]:
    _, normal, offset = line
    signed = normal[0] * point[0] + normal[1] * point[1] - offset
    return (
        point[0] - 2.0 * signed * normal[0],
        point[1] - 2.0 * signed * normal[1],
    )


def _add_corner_symmetry_candidates(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    maximum: float,
    quality_report: Mapping[str, Any] | None,
) -> None:
    penalties = _quality_penalties(quality_report)
    for axis_id, axis_anchor in anchors.items():
        if penalties.get(axis_id, 0.0) >= 3.0:
            continue
        axis = _line_geometry(axis_anchor)
        if axis is None:
            continue
        for corner_name, corner in _paper_corners(maximum):
            point = _reflect(corner, axis)
            if not (-1e-7 <= point[0] <= maximum + 1e-7 and -1e-7 <= point[1] <= maximum + 1e-7):
                continue
            if math.hypot(point[0] - corner[0], point[1] - corner[1]) < 0.5:
                continue
            for target_id, target in anchors.items():
                if target_id == axis_id:
                    continue
                offset = _candidate_offset(target, point)
                if offset is None:
                    continue
                residual = abs(offset - _observed_offset(target))
                if residual > _candidate_tolerance(target):
                    continue
                operation_id = ("paper_corner_symmetry_ray", corner_name, axis_id, target_id)
                if operation_id in graph.operations:
                    continue
                graph.add_operation(
                    ConstructionOperation(
                        id=operation_id,
                        kind="paper_corner_symmetry_ray",
                        parents=(("ray", axis_id),),
                        outputs=(("ray", target_id),),
                        explains=frozenset({("required_ray", target_id)}),
                        residual=residual,
                        generation=max(0, _generation(axis_anchor) + 1),
                    )
                )
                details[operation_id] = {
                    "provenance": "paper_corner_symmetry_ray",
                    "target_trace_id": target_id,
                    "axis_trace_id": axis_id,
                    "source_corner": corner_name,
                    "anchor_point_px": [round(point[0], 6), round(point[1], 6)],
                    "candidate_offset_px": round(float(offset), 9),
                }


def build_provenance_report_v5(
    result: Mapping[str, Any],
    quality_report: Mapping[str, Any] | None = None,
    geometry_report: Mapping[str, Any] | None = None,
    *,
    weights: SearchWeights = SearchWeights(
        camv_violation=4.5,
        unexplained=9.5,
    ),
    beam_width: int = 72,
) -> dict[str, Any]:
    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    if not trace:
        return {"enabled": False, "mode": "provenance_v5", "reason": "no_playback_trace"}
    try:
        analysis_size = int(result.get("stats", {}).get("analysis_size_used") or 512)
    except (TypeError, ValueError):
        analysis_size = 512
    analysis_size = max(2, analysis_size)
    maximum = float(analysis_size - 1)

    graph, observations, anchors, details = _copy_base_graph(trace, analysis_size)
    graph = _guard_large_coefficients(graph, details)
    _add_corner_candidates(graph, anchors, details, maximum)
    _add_segment_ratio_candidates(graph, anchors, details)
    _add_geometry_reroot_candidates(graph, anchors, details, geometry_report)
    _add_corner_symmetry_candidates(graph, anchors, details, maximum, quality_report)
    graph = _apply_quality_quarantine(graph, anchors, details, quality_report)

    selected = _beam_search_continue(
        graph,
        observations,
        weights=weights,
        beam_width=beam_width,
        max_rounds=max(18, min(260, len(trace) * 4 + 20)),
    )
    selected_operations = [_operation_summary(operation, details) for operation in selected.selected_operations]
    alternatives = [item for item in selected_operations if item.get("provenance") != "legacy"]
    counts = Counter(item.get("provenance") or item.get("kind") for item in alternatives)
    selected_offsets = {
        int(item["target_trace_id"]): float(item["candidate_offset_px"])
        for item in selected_operations
        if item.get("target_trace_id") is not None and item.get("candidate_offset_px") is not None
    }
    material = []
    for item in alternatives:
        try:
            target_id = int(item["target_trace_id"])
            shift = abs(float(item["candidate_offset_px"]) - float(anchors[target_id]["line_offset_px"]))
        except (KeyError, TypeError, ValueError):
            shift = 0.0
        if shift >= 0.12:
            material.append({**item, "geometry_shift_px": round(shift, 6)})

    return {
        "enabled": True,
        "mode": "provenance_v5",
        "candidate_operations": len(graph.operations),
        "required_observations": len(observations),
        "selected_score": round(score_state(selected, observations, weights), 6),
        "unexplained_observations": len(observations - selected.explained_observations),
        "route_changed": bool(alternatives),
        "material_geometry_changed": bool(material),
        "selected_operations": selected_operations,
        "selected_alternatives": alternatives,
        "selected_material_alternatives": material,
        "selected_alternative_counts": dict(sorted(counts.items())),
        "selected_offsets_px": {str(key): round(value, 9) for key, value in sorted(selected_offsets.items())},
        "quality_quarantine": dict((quality_report or {}).get("suspect_trace_penalties") or {}),
        "notes": [
            "Raster observations remain required when their current geometry hypothesis is quarantined.",
            "A same-coordinate provenance rewrite does not count as repairing a suspect geometry.",
            "Paper-corner symmetry is generated generically for all corners and reliable axes; top-left is not hard-coded as a special case.",
            "Local reroot proofs from geometry search compete in the same global provenance beam instead of being discarded by a separate subsystem.",
            "cAMV is a strong structural prior but clean isolated violations are not a hard veto.",
        ],
    }


__all__ = ["build_provenance_report_v5"]
