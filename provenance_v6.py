from __future__ import annotations

import math
from collections import Counter
from typing import Any, Hashable, Mapping

from construction_search import ConstructionGraph, ConstructionOperation, SearchWeights, score_state
from provenance_v3 import _add_corner_candidates, _beam_search_continue, _copy_base_graph
from provenance_v4 import _add_segment_ratio_candidates, _guard_large_coefficients
from provenance_v5 import (
    _add_corner_symmetry_candidates,
    _apply_quality_quarantine,
    build_provenance_report_v5,
)
from shadow_search import (
    _anchor_point,
    _generation,
    _line_geometry,
    _operation_summary,
    _point_tolerance,
)
from shadow_variant import _affected_descendants


_MAX_REFERENCE_POINTS = 720
_MAX_REFERENCES_PER_TARGET = 12
_RELIABLE_TRACE_PENALTY_MAX = 3.0


def _target_id(metadata: Mapping[str, Any]) -> int | None:
    raw = metadata.get("target_trace_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _large_core_targets(details: Mapping[Hashable, Mapping[str, Any]]) -> list[int]:
    targets: set[int] = set()
    for metadata in details.values():
        if not metadata.get("large_coefficient_guard"):
            continue
        target = _target_id(metadata)
        if target is not None:
            targets.add(target)
    return sorted(targets)


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


def _inside(point: tuple[float, float], maximum: float, margin: float = 1e-6) -> bool:
    return (
        -margin <= point[0] <= maximum + margin
        and -margin <= point[1] <= maximum + margin
    )


def _intersection(first, second) -> tuple[float, float] | None:
    _, n1, o1 = first
    _, n2, o2 = second
    determinant = n1[0] * n2[1] - n1[1] * n2[0]
    if abs(determinant) <= 1e-10:
        return None
    x = (o1 * n2[1] - n1[1] * o2) / determinant
    y = (n1[0] * o2 - o1 * n2[0]) / determinant
    return float(x), float(y)


def _reflect(point: tuple[float, float], line) -> tuple[float, float]:
    _, normal, offset = line
    signed = normal[0] * point[0] + normal[1] * point[1] - offset
    return (
        point[0] - 2.0 * signed * normal[0],
        point[1] - 2.0 * signed * normal[1],
    )


def _line_distance(point: tuple[float, float], line) -> float:
    _, normal, offset = line
    return abs(normal[0] * point[0] + normal[1] * point[1] - offset)


def _candidate_offset(anchor: Mapping[str, Any], point: tuple[float, float]) -> float | None:
    geometry = _line_geometry(anchor)
    if geometry is None:
        return None
    _, normal, _ = geometry
    return normal[0] * point[0] + normal[1] * point[1]


def _observed_offset(anchor: Mapping[str, Any]) -> float:
    try:
        return float(anchor.get("observed_offset_px", anchor["line_offset_px"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _dedup_ids(values) -> tuple[int, ...]:
    result: list[int] = []
    for raw in values:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in result:
            result.append(value)
    return tuple(result)


def _add_reference_point(
    points: dict[tuple[float, float], dict[str, Any]],
    point: tuple[float, float] | None,
    *,
    maximum: float,
    cost: int,
    kind: str,
    source_trace_ids=(),
    **metadata,
) -> None:
    if point is None or not _inside(point, maximum):
        return
    point = (float(point[0]), float(point[1]))
    key = (round(point[0], 3), round(point[1], 3))
    item = {
        "point": point,
        "cost": int(cost),
        "kind": str(kind),
        "source_trace_ids": _dedup_ids(source_trace_ids),
        **metadata,
    }
    previous = points.get(key)
    if previous is None or (item["cost"], item["kind"]) < (previous["cost"], previous["kind"]):
        points[key] = item


def _paper_corners(maximum: float):
    return [
        ("top_left", (0.0, 0.0)),
        ("top_right", (maximum, 0.0)),
        ("bottom_right", (maximum, maximum)),
        ("bottom_left", (0.0, maximum)),
    ]


def _stable_reference_points(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    maximum: float,
    quality_report: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Build references without using any ray descended from the suspect core.

    The pool deliberately contains points, not a replacement core coordinate.
    A target crease may attach to any one of these references independently.
    This is the key difference from v5/v6-old rerooting.
    """

    penalties = _quality_penalties(quality_report)
    stable_ids = [
        trace_id
        for trace_id in sorted(anchors)
        if trace_id not in affected
        and penalties.get(trace_id, 0.0) < _RELIABLE_TRACE_PENALTY_MAX
        and _line_geometry(anchors[trace_id]) is not None
    ]
    points: dict[tuple[float, float], dict[str, Any]] = {}

    for corner_name, corner in _paper_corners(maximum):
        _add_reference_point(
            points,
            corner,
            maximum=maximum,
            cost=0,
            kind="paper_corner",
            source_corner=corner_name,
        )

    simple_points: list[dict[str, Any]] = []
    for trace_id in stable_ids:
        anchor_point = _anchor_point(anchors[trace_id])
        if anchor_point is None:
            continue
        _add_reference_point(
            points,
            anchor_point,
            maximum=maximum,
            cost=1,
            kind="stable_anchor",
            source_trace_ids=(trace_id,),
        )
        simple_points.append(
            {
                "point": anchor_point,
                "cost": 1,
                "kind": "stable_anchor",
                "source_trace_ids": (trace_id,),
            }
        )

    # Exact intersections of reliable rays are reusable points even when they
    # were not chosen as anchor points by the legacy trace.
    for index, first_id in enumerate(stable_ids):
        first = _line_geometry(anchors[first_id])
        if first is None:
            continue
        for second_id in stable_ids[index + 1 :]:
            second = _line_geometry(anchors[second_id])
            if second is None:
                continue
            point = _intersection(first, second)
            _add_reference_point(
                points,
                point,
                maximum=maximum,
                cost=2,
                kind="stable_intersection",
                source_trace_ids=(first_id, second_id),
            )

    # Explicitly include paper-corner symmetry as a point source.  This is
    # generic for all corners; a top-left route wins only when its geometry fits.
    for axis_id in stable_ids:
        axis = _line_geometry(anchors[axis_id])
        if axis is None:
            continue
        for corner_name, corner in _paper_corners(maximum):
            reflected = _reflect(corner, axis)
            if math.hypot(reflected[0] - corner[0], reflected[1] - corner[1]) < 0.25:
                continue
            _add_reference_point(
                points,
                reflected,
                maximum=maximum,
                cost=2,
                kind="paper_corner_symmetry",
                source_trace_ids=(axis_id,),
                source_corner=corner_name,
                axis_trace_id=axis_id,
            )

    # Midpoints between simple reliable points are allowed without assuming a
    # named region or a central hub.  This also covers the common case where a
    # line is naturally taken from a midpoint elsewhere in the CP.
    corner_items = [
        {
            "point": corner,
            "cost": 0,
            "kind": "paper_corner",
            "source_trace_ids": (),
            "source_corner": name,
        }
        for name, corner in _paper_corners(maximum)
    ]
    midpoint_sources = corner_items + simple_points
    for index, first in enumerate(midpoint_sources):
        ax, ay = first["point"]
        for second in midpoint_sources[index + 1 :]:
            bx, by = second["point"]
            distance = math.hypot(bx - ax, by - ay)
            if distance < 5.0 or distance > maximum * 0.92:
                continue
            point = ((ax + bx) / 2.0, (ay + by) / 2.0)
            _add_reference_point(
                points,
                point,
                maximum=maximum,
                cost=2,
                kind="stable_midpoint",
                source_trace_ids=(
                    *first.get("source_trace_ids", ()),
                    *second.get("source_trace_ids", ()),
                ),
                source_labels=(first.get("kind"), second.get("kind")),
            )

    # 1/3 and 2/3 points are generated only on finite intervals whose endpoints
    # already lie on the same reliable ray.  This is line-segment division, not
    # paper-edge division and not a square-specific rule.
    for ray_id in stable_ids:
        line = _line_geometry(anchors[ray_id])
        if line is None:
            continue
        on_line: list[tuple[float, dict[str, Any]]] = []
        direction = line[0]
        for item in midpoint_sources:
            point = item["point"]
            if _line_distance(point, line) > 0.42:
                continue
            parameter = direction[0] * point[0] + direction[1] * point[1]
            on_line.append((parameter, item))
        on_line.sort(key=lambda value: value[0])
        for first, second in zip(on_line, on_line[1:]):
            a = first[1]
            b = second[1]
            ax, ay = a["point"]
            bx, by = b["point"]
            if math.hypot(bx - ax, by - ay) < 6.0:
                continue
            for numerator in (1, 2):
                t = numerator / 3.0
                point = (ax + (bx - ax) * t, ay + (by - ay) * t)
                _add_reference_point(
                    points,
                    point,
                    maximum=maximum,
                    cost=3,
                    kind="stable_segment_trisection",
                    source_trace_ids=(
                        ray_id,
                        *a.get("source_trace_ids", ()),
                        *b.get("source_trace_ids", ()),
                    ),
                    ratio=f"{numerator}/3",
                    source_ray_trace_id=ray_id,
                )

    ranked = sorted(
        points.values(),
        key=lambda item: (item["cost"], item["kind"], item["point"]),
    )
    return ranked[:_MAX_REFERENCE_POINTS], stable_ids


def _add_coreless_reference_candidates(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    affected: set[int],
    reference_points: list[Mapping[str, Any]],
    root_id: int,
) -> int:
    added = 0
    for target_id in sorted(affected):
        target = anchors.get(target_id)
        if target is None or _line_geometry(target) is None:
            continue
        tolerance = min(3.2, max(0.85, _point_tolerance(target) + 0.35))
        options: list[tuple[float, int, str, Mapping[str, Any], float]] = []
        legacy_offset = float(target.get("line_offset_px", 0.0) or 0.0)
        for item in reference_points:
            point = item["point"]
            candidate_offset = _candidate_offset(target, point)
            if candidate_offset is None:
                continue
            residual = abs(candidate_offset - _observed_offset(target))
            if residual > tolerance:
                continue
            # Point complexity is primary, then raster residual.  We do not
            # require a line-position shift: abandoning the old *point method*
            # can legitimately recover the same crease from a simpler point.
            score = item["cost"] + residual * 3.5
            options.append((score, int(item["cost"]), str(item["kind"]), item, candidate_offset))
        options.sort(key=lambda value: (value[0], value[1], value[2]))
        for _, point_cost, point_kind, item, candidate_offset in options[:_MAX_REFERENCES_PER_TARGET]:
            parents = tuple(("ray", trace_id) for trace_id in item.get("source_trace_ids", ()))
            operation_id = (
                "coreless_reference_ray",
                root_id,
                target_id,
                point_kind,
                round(float(item["point"][0]), 3),
                round(float(item["point"][1]), 3),
            )
            if operation_id in graph.operations:
                continue
            generation = max(
                (_generation(anchors[trace_id]) for trace_id in item.get("source_trace_ids", ()) if trace_id in anchors),
                default=-1,
            ) + 1
            residual = abs(float(candidate_offset) - _observed_offset(target))
            graph.add_operation(
                ConstructionOperation(
                    id=operation_id,
                    kind="coreless_reference_ray",
                    parents=parents,
                    outputs=(("ray", target_id),),
                    explains=frozenset({("required_ray", target_id)}),
                    residual=residual,
                    generation=max(0, generation),
                    independent_parameters=max(0, int(math.ceil(max(0, point_cost - 1) / 2.0))),
                )
            )
            details[operation_id] = {
                "provenance": "coreless_reference_ray",
                "target_trace_id": target_id,
                "candidate_offset_px": round(float(candidate_offset), 9),
                "anchor_point_px": [round(float(item["point"][0]), 6), round(float(item["point"][1]), 6)],
                "reference_kind": point_kind,
                "reference_cost": point_cost,
                "source_trace_ids": list(item.get("source_trace_ids", ())),
                "source_corner": item.get("source_corner"),
                "axis_trace_id": item.get("axis_trace_id"),
                "ratio": item.get("ratio"),
                "source_ray_trace_id": item.get("source_ray_trace_id"),
                "abandons_core_seed": True,
                "coreless_root_trace_id": root_id,
                "legacy_line_shift_px": round(abs(float(candidate_offset) - legacy_offset), 6),
            }
            added += 1
    return added


def _metadata_source_ids(metadata: Mapping[str, Any]) -> set[int]:
    values: list[Any] = []
    for key in ("source_trace_id", "axis_trace_id", "source_ray_trace_id"):
        if metadata.get(key) is not None:
            values.append(metadata[key])
    for key in ("source_trace_ids", "segment_endpoint_trace_ids"):
        raw = metadata.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(raw)
    result: set[int] = set()
    for raw in values:
        try:
            result.add(int(raw))
        except (TypeError, ValueError):
            continue
    return result


def _operation_parent_ray_ids(operation: ConstructionOperation) -> set[int]:
    result: set[int] = set()
    for parent in operation.parents:
        if isinstance(parent, tuple) and len(parent) == 2 and parent[0] == "ray":
            try:
                result.add(int(parent[1]))
            except (TypeError, ValueError):
                pass
    return result


def _coreless_graph(
    graph: ConstructionGraph,
    details: Mapping[Hashable, Mapping[str, Any]],
    affected: set[int],
    root_id: int,
) -> tuple[ConstructionGraph, int]:
    """Remove the core-point method, not merely one coordinate value.

    - The large-coefficient root legacy operation is unavailable.
    - `geometry_reroot` is unavailable: shifting the same core point is exactly
      the behaviour this branch is meant to stop testing.
    - Alternatives whose reference point is itself inside the old core
      dependency region are suppressed because their serialized point may carry
      the same bad geometry forward.
    - Legacy *downstream relations* may remain. Once an upstream ray has been
      independently re-anchored, their intersections are recomputed later.
    """

    output = ConstructionGraph()
    removed = 0
    for operation in graph.operations.values():
        metadata = details.get(operation.id, {})
        target = _target_id(metadata)
        provenance = str(metadata.get("provenance") or "legacy")
        if target not in affected:
            output.add_operation(operation)
            continue

        if target == root_id and provenance == "legacy":
            removed += 1
            continue
        if provenance == "geometry_reroot":
            removed += 1
            continue
        if provenance == "legacy":
            output.add_operation(operation)
            continue
        if provenance == "coreless_reference_ray":
            output.add_operation(operation)
            continue

        sources = _metadata_source_ids(metadata) | _operation_parent_ray_ids(operation)
        if sources & affected:
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
    root_id: int,
    affected: set[int],
) -> dict[str, Any]:
    operations = [_operation_summary(operation, details) for operation in selected.selected_operations]
    alternatives = [item for item in operations if item.get("provenance") != "legacy"]
    offsets: dict[int, float] = {}
    material: list[dict[str, Any]] = []
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

    root_operation = next(
        (
            item for item in operations
            if item.get("target_trace_id") is not None
            and int(item.get("target_trace_id")) == root_id
        ),
        None,
    )
    coreless_inside = [
        item for item in alternatives
        if item.get("target_trace_id") is not None
        and int(item.get("target_trace_id")) in affected
        and item.get("provenance") == "coreless_reference_ray"
    ]
    counts = Counter(item.get("provenance") or item.get("kind") for item in alternatives)
    return {
        "enabled": True,
        "mode": "coreless_reference_search_v6",
        "coreless_root_trace_id": root_id,
        "affected_trace_ids": sorted(affected),
        "selected_score": round(score_state(selected, observations, weights), 6),
        "unexplained_observations": len(observations - selected.explained_observations),
        "selected_operations": operations,
        "selected_alternatives": alternatives,
        "selected_material_alternatives": material,
        "selected_alternative_counts": dict(sorted(counts.items())),
        "selected_offsets_px": {str(key): round(value, 9) for key, value in sorted(offsets.items())},
        "root_operation": root_operation,
        "root_abandons_core_seed": bool(
            root_operation and root_operation.get("provenance") == "coreless_reference_ray"
        ),
        "coreless_reference_ray_count": len(coreless_inside),
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
    # Keep the ordinary quality-aware search as one branch.  The coreless route
    # is intentionally a separate alternative; it must not disappear merely
    # because a core-point route has a slightly better raster score.
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
    # Deliberately DO NOT add `_add_geometry_reroot_candidates` here. A reroot
    # is still the core-point method with a different coordinate.
    _add_corner_symmetry_candidates(graph, anchors, details, maximum, quality_report)
    graph = _apply_quality_quarantine(graph, anchors, details, quality_report)

    core_targets = _large_core_targets(details)
    baseline_unexplained = int(normal.get("unexplained_observations", len(observations)))
    attempts: list[dict[str, Any]] = []
    viable: list[tuple[tuple[int, int, float], dict[str, Any]]] = []

    for root_id in core_targets[:8]:
        affected = _affected_descendants(anchors, root_id)
        references, stable_ids = _stable_reference_points(
            anchors,
            affected,
            maximum,
            quality_report,
        )
        working = ConstructionGraph()
        for operation in graph.operations.values():
            working.add_operation(operation)
        added = _add_coreless_reference_candidates(
            working,
            anchors,
            details,
            affected,
            references,
            root_id,
        )
        constrained, removed = _coreless_graph(working, details, affected, root_id)
        selected = _beam_search_continue(
            constrained,
            observations,
            weights=weights,
            beam_width=max(96, beam_width),
            max_rounds=max(24, min(360, len(trace) * 6 + 30)),
        )
        summary = _summarize_state(
            selected,
            observations,
            anchors,
            details,
            weights,
            root_id,
            affected,
        )
        attempt = {
            "target_trace_id": root_id,
            "affected_trace_count": len(affected),
            "stable_reference_trace_count": len(stable_ids),
            "reference_point_count": len(references),
            "coreless_candidate_operations_added": added,
            "suppressed_core_method_operations": removed,
            "unexplained_observations": summary["unexplained_observations"],
            "root_abandons_core_seed": summary["root_abandons_core_seed"],
            "coreless_reference_ray_count": summary["coreless_reference_ray_count"],
            "selected_score": summary["selected_score"],
            "root_operation": summary["root_operation"],
        }
        attempts.append(attempt)

        if not summary["root_abandons_core_seed"]:
            continue
        # A deliberate alternative may be slightly less complete than the
        # normal branch, but it should remain close enough to be diagnostically
        # useful rather than becoming a random sparse reconstruction.
        if summary["unexplained_observations"] > baseline_unexplained + 1:
            continue
        viable.append(
            (
                (
                    summary["unexplained_observations"],
                    -summary["coreless_reference_ray_count"],
                    summary["selected_score"],
                ),
                summary,
            )
        )

    output = dict(normal)
    output["mode"] = "provenance_v6"
    output["core_point_free_attempted"] = bool(core_targets)
    output["core_point_targets"] = core_targets
    output["core_point_free_attempts"] = attempts
    output["core_point_free_selected"] = False
    output["coreless_selected_report"] = None

    if viable:
        viable.sort(key=lambda item: item[0])
        coreless = viable[0][1]
        output["core_point_free_selected"] = True
        output["core_point_free_selected_target"] = coreless["coreless_root_trace_id"]
        output["coreless_selected_report"] = coreless

    output["notes"] = list(normal.get("notes") or []) + [
        "The coreless branch does not move a large-coefficient core point; it suppresses that construction method and independently re-anchors affected observed creases from references outside the old dependency region.",
        "Paper corners, reliable external anchor/intersection points, paper-corner symmetry, midpoints and finite-segment thirds are generic reference sources; no top-left, square or named-region special case is hard-coded.",
        "The ordinary quality-aware route and the coreless route are retained as separate alternatives so the latter cannot be hidden merely by a slightly better core-point raster fit.",
    ]
    return output


__all__ = ["build_provenance_report_v6"]
