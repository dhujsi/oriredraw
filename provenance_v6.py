from __future__ import annotations

from collections import Counter
from typing import Any, Hashable, Mapping

from construction_search import ConstructionGraph, SearchWeights, score_state
from provenance_v3 import _add_corner_candidates, _beam_search_continue, _copy_base_graph
from provenance_v4 import _add_segment_ratio_candidates, _guard_large_coefficients
from provenance_v5 import (
    _add_corner_symmetry_candidates,
    _add_geometry_reroot_candidates,
    _apply_quality_quarantine,
    build_provenance_report_v5,
)
from shadow_search import _operation_summary


_SAME_CORE_COORDINATE_PX = 0.22


def _target_id(metadata: Mapping[str, Any]) -> int | None:
    raw = metadata.get("target_trace_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _large_core_targets(
    details: Mapping[Hashable, Mapping[str, Any]],
) -> list[int]:
    targets: set[int] = set()
    for metadata in details.values():
        if not metadata.get("large_coefficient_guard"):
            continue
        target = _target_id(metadata)
        if target is not None:
            targets.add(target)
    return sorted(targets)


def _without_core_point_method(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: Mapping[Hashable, Mapping[str, Any]],
    target_id: int,
) -> tuple[ConstructionGraph, int]:
    """Build a branch that refuses the old core-point coordinate explanation.

    The observed ray itself is NOT removed.  Only operations that explain that
    observation by keeping the old large-coefficient core coordinate (or by
    merely renaming the same coordinate) are suppressed.  Material reroots,
    symmetry, ratio points and other genuinely different reference points stay
    available, so descendants may still use the re-explained ray normally.
    """

    output = ConstructionGraph()
    removed = 0
    try:
        legacy_offset = float(anchors[target_id]["line_offset_px"])
    except (KeyError, TypeError, ValueError):
        legacy_offset = None

    for operation in graph.operations.values():
        metadata = details.get(operation.id, {})
        if _target_id(metadata) != target_id:
            output.add_operation(operation)
            continue

        provenance = str(metadata.get("provenance") or "legacy")
        reject = provenance == "legacy" or bool(metadata.get("large_coefficient_guard"))
        if not reject and legacy_offset is not None:
            raw_candidate = metadata.get("candidate_offset_px")
            try:
                shift = abs(float(raw_candidate) - legacy_offset)
            except (TypeError, ValueError):
                shift = None
            if shift is not None and shift < _SAME_CORE_COORDINATE_PX:
                reject = True
        if reject:
            removed += 1
            continue
        output.add_operation(operation)
    return output, removed


def _summarize_state(
    selected,
    observations,
    anchors: Mapping[int, Mapping[str, Any]],
    details: Mapping[Hashable, Mapping[str, Any]],
    weights: SearchWeights,
) -> dict[str, Any]:
    operations = [_operation_summary(operation, details) for operation in selected.selected_operations]
    alternatives = [item for item in operations if item.get("provenance") != "legacy"]
    material: list[dict[str, Any]] = []
    offsets: dict[int, float] = {}
    for item in operations:
        if item.get("target_trace_id") is None or item.get("candidate_offset_px") is None:
            continue
        try:
            target = int(item["target_trace_id"])
            candidate = float(item["candidate_offset_px"])
        except (TypeError, ValueError):
            continue
        offsets[target] = candidate
        try:
            shift = abs(candidate - float(anchors[target]["line_offset_px"]))
        except (KeyError, TypeError, ValueError):
            shift = 0.0
        if item.get("provenance") != "legacy" and shift >= 0.12:
            material.append({**item, "geometry_shift_px": round(shift, 6)})
    counts = Counter(item.get("provenance") or item.get("kind") for item in alternatives)
    return {
        "selected_score": round(score_state(selected, observations, weights), 6),
        "unexplained_observations": len(observations - selected.explained_observations),
        "selected_operations": operations,
        "selected_alternatives": alternatives,
        "selected_material_alternatives": material,
        "selected_alternative_counts": dict(sorted(counts.items())),
        "selected_offsets_px": {str(key): round(value, 9) for key, value in sorted(offsets.items())},
        "route_changed": bool(alternatives),
        "material_geometry_changed": bool(material),
    }


def build_provenance_report_v6(
    result: Mapping[str, Any],
    quality_report: Mapping[str, Any] | None = None,
    geometry_report: Mapping[str, Any] | None = None,
    *,
    weights: SearchWeights = SearchWeights(camv_violation=4.5, unexplained=9.5),
    beam_width: int = 72,
) -> dict[str, Any]:
    normal = build_provenance_report_v5(
        result,
        quality_report=quality_report,
        geometry_report=geometry_report,
        weights=weights,
        beam_width=beam_width,
    )
    if not normal.get("enabled"):
        return normal

    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    try:
        analysis_size = max(2, int(result.get("stats", {}).get("analysis_size_used") or 512))
    except (TypeError, ValueError):
        analysis_size = 512
    maximum = float(analysis_size - 1)

    graph, observations, anchors, details = _copy_base_graph(trace, analysis_size)
    graph = _guard_large_coefficients(graph, details)
    _add_corner_candidates(graph, anchors, details, maximum)
    _add_segment_ratio_candidates(graph, anchors, details)
    _add_geometry_reroot_candidates(graph, anchors, details, geometry_report)
    _add_corner_symmetry_candidates(graph, anchors, details, maximum, quality_report)
    graph = _apply_quality_quarantine(graph, anchors, details, quality_report)

    core_targets = _large_core_targets(details)
    baseline_unexplained = int(normal.get("unexplained_observations", len(observations)))
    attempts: list[dict[str, Any]] = []
    viable: list[tuple[tuple[int, float], int, dict[str, Any]]] = []

    for target_id in core_targets[:8]:
        constrained, removed = _without_core_point_method(graph, anchors, details, target_id)
        selected = _beam_search_continue(
            constrained,
            observations,
            weights=weights,
            beam_width=max(beam_width, 80),
            max_rounds=max(20, min(300, len(trace) * 5 + 24)),
        )
        summary = _summarize_state(selected, observations, anchors, details, weights)
        target_alternatives = [
            item for item in summary["selected_material_alternatives"]
            if int(item.get("target_trace_id", -1)) == target_id
        ]
        attempt = {
            "target_trace_id": target_id,
            "suppressed_core_operations": removed,
            "selected_score": summary["selected_score"],
            "unexplained_observations": summary["unexplained_observations"],
            "material_replacement_found": bool(target_alternatives),
            "target_material_alternatives": target_alternatives,
        }
        attempts.append(attempt)
        if target_alternatives and summary["unexplained_observations"] <= baseline_unexplained:
            viable.append(
                (
                    (summary["unexplained_observations"], summary["selected_score"]),
                    target_id,
                    summary,
                )
            )

    output = dict(normal)
    output["mode"] = "provenance_v6"
    output["core_point_free_attempted"] = bool(core_targets)
    output["core_point_targets"] = core_targets
    output["core_point_free_attempts"] = attempts
    output["core_point_free_selected"] = False

    if viable:
        viable.sort(key=lambda item: item[0])
        _, target_id, summary = viable[0]
        for key, value in summary.items():
            output[key] = value
        output["core_point_free_selected"] = True
        output["core_point_free_selected_target"] = target_id
        output["core_point_free_baseline_score"] = normal.get("selected_score")
        output["core_point_free_baseline_unexplained"] = baseline_unexplained

    output["notes"] = list(normal.get("notes") or []) + [
        "Large-coefficient core points are explicitly retried in a branch that suppresses the old core-point coordinate method while keeping the raster observation required.",
        "A core-point-free branch may be kept as the shadow alternative even when its raster score is slightly worse, provided it does not explain fewer observations and materially relocates the suspect ray.",
    ]
    return output


__all__ = ["build_provenance_report_v6"]
