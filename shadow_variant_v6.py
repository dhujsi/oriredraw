from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

import numpy as np

import shadow_variant as legacy_variant
from isolated_ratio import infer_isolated_segment_ratio_segments
from selected_geometry_v4 import resolve_selected_geometry_v4
from shadow_variant_v3 import _build_playback_trace
from shadow_variant_v4 import _split_segments
from shadow_variant_v5 import build_shadow_candidate_variant_v5


_NODE_GROUP_RADIUS_PX = 4.5
_NODE_CLUSTER_RADIUS_PX = 0.45
_ENDPOINT_INTERSECTION_RADIUS_PX = 3.4


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    return legacy_variant._trace_id(anchor, fallback)


def _orientation(anchor: Mapping[str, Any]) -> int | None:
    return legacy_variant._orientation(anchor)


def _project_to_line(point: np.ndarray, line) -> np.ndarray:
    _, normal, offset = line
    delta = float(offset) - float(normal @ point)
    return point + normal * delta


def _distance(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.linalg.norm(first - second))


def _inside(point: np.ndarray, maximum: float, margin: float = 2.0) -> bool:
    return bool(np.all(point >= -margin) and np.all(point <= maximum + margin))


def _boundary_side(point: np.ndarray, maximum: float) -> str | None:
    values = [
        (abs(float(point[0])), "left"),
        (abs(float(point[0]) - maximum), "right"),
        (abs(float(point[1])), "top"),
        (abs(float(point[1]) - maximum), "bottom"),
    ]
    distance, side = min(values, key=lambda item: item[0])
    return side if distance <= 0.6 else None


def _operation_by_target(report: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    output: dict[int, Mapping[str, Any]] = {}
    for operation in list(report.get("selected_operations") or []):
        if not isinstance(operation, Mapping) or operation.get("target_trace_id") is None:
            continue
        try:
            output[int(operation["target_trace_id"])] = operation
        except (TypeError, ValueError):
            continue
    return output


def _prepare_shifted_segments(
    result: Mapping[str, Any],
    report: Mapping[str, Any],
    maximum: float,
):
    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    anchors = {_trace_id(anchor, index): anchor for index, anchor in enumerate(trace)}
    offsets, points, unresolved = resolve_selected_geometry_v4(result, report)
    rows = legacy_variant._parse_cp(str(result.get("cp") or ""), maximum)
    unmatched = legacy_variant._match_rays(rows, anchors)

    segments: list[dict[str, Any]] = []
    for row in rows:
        if int(row["line_type"]) == 1:
            continue
        trace_id = row.get("trace_id")
        old_start = row["start"].copy()
        old_end = row["end"].copy()
        start = old_start.copy()
        end = old_end.copy()
        orientation = None
        line = None
        if trace_id is not None and trace_id in anchors:
            orientation = _orientation(anchors[trace_id])
            if orientation is not None and anchors[trace_id].get("line_offset_px") is not None:
                candidate_offset = float(offsets.get(trace_id, anchors[trace_id]["line_offset_px"]))
                line = legacy_variant._line_geometry(orientation, candidate_offset)
                start = _project_to_line(old_start, line)
                end = _project_to_line(old_end, line)
        segments.append(
            {
                "line_type": int(row["line_type"]),
                "trace_id": trace_id,
                "orientation": orientation,
                "line": line,
                "old_start": old_start,
                "old_end": old_end,
                "start": start,
                "end": end,
            }
        )
    return segments, anchors, offsets, points, unresolved, unmatched


def _cluster_intersections(candidates):
    clusters: list[dict[str, Any]] = []
    for point, first_index, second_index in candidates:
        match = None
        for cluster in clusters:
            if _distance(point, cluster["point"]) <= _NODE_CLUSTER_RADIUS_PX:
                match = cluster
                break
        if match is None:
            clusters.append(
                {
                    "points": [point],
                    "point": point.copy(),
                    "segment_indices": {first_index, second_index},
                }
            )
        else:
            match["points"].append(point)
            match["point"] = np.mean(np.vstack(match["points"]), axis=0)
            match["segment_indices"].update((first_index, second_index))
    return clusters


def _free_old_nodes(segments, maximum: float) -> int:
    """Relax old multi-ray node incidence instead of least-squares forcing it.

    Each old CP vertex is only a neighbourhood hint. Shifted rays may form one
    new vertex, several nearby vertices, or a dangling endpoint. Pairwise exact
    intersections are clustered locally; no old high-degree node is required to
    survive as a single common point.
    """

    groups: dict[tuple[float, float], list[tuple[int, str]]] = defaultdict(list)
    for index, segment in enumerate(segments):
        groups[legacy_variant._node_key(segment["old_start"])].append((index, "start"))
        groups[legacy_variant._node_key(segment["old_end"])].append((index, "end"))

    split_nodes = 0
    for members in groups.values():
        if not members:
            continue
        first_segment, first_end = members[0]
        old_point = segments[first_segment]["old_start" if first_end == "start" else "old_end"].copy()
        side = _boundary_side(old_point, maximum)

        if side is not None:
            for segment_index, endpoint_name in members:
                segment = segments[segment_index]
                line = segment.get("line")
                if line is None:
                    continue
                contact = legacy_variant._boundary_contact(line, old_point, maximum)
                if contact is not None and _inside(contact, maximum, margin=0.5):
                    segment[endpoint_name] = contact
            continue

        candidates: list[tuple[np.ndarray, int, int]] = []
        for first_pos, (first_index, first_name) in enumerate(members):
            first_segment = segments[first_index]
            first_line = first_segment.get("line")
            if first_line is None:
                continue
            first_projected = first_segment[first_name]
            for second_index, second_name in members[first_pos + 1 :]:
                second_segment = segments[second_index]
                second_line = second_segment.get("line")
                if second_line is None:
                    continue
                if first_segment.get("trace_id") == second_segment.get("trace_id"):
                    continue
                point = legacy_variant._intersection(first_line, second_line)
                if point is None or not _inside(point, maximum):
                    continue
                if _distance(point, old_point) > _NODE_GROUP_RADIUS_PX:
                    continue
                second_projected = second_segment[second_name]
                if (
                    _distance(point, first_projected) > _ENDPOINT_INTERSECTION_RADIUS_PX
                    or _distance(point, second_projected) > _ENDPOINT_INTERSECTION_RADIUS_PX
                ):
                    continue
                candidates.append((point, first_index, second_index))

        clusters = _cluster_intersections(candidates)
        if len(clusters) > 1:
            split_nodes += 1

        for segment_index, endpoint_name in members:
            segment = segments[segment_index]
            projected = segment[endpoint_name]
            options = [
                cluster
                for cluster in clusters
                if segment_index in cluster["segment_indices"]
                and _distance(projected, cluster["point"]) <= _ENDPOINT_INTERSECTION_RADIUS_PX
            ]
            if not options:
                continue
            options.sort(
                key=lambda cluster: (
                    -len(cluster["segment_indices"]),
                    _distance(projected, cluster["point"]),
                )
            )
            segment[endpoint_name] = options[0]["point"].copy()
    return split_nodes


def _internal_tuples(segments):
    result = []
    seen = set()
    for segment in segments:
        start = np.asarray(segment["start"], dtype=float)
        end = np.asarray(segment["end"], dtype=float)
        if _distance(start, end) <= 0.04:
            continue
        first = (round(float(start[0]), 7), round(float(start[1]), 7))
        second = (round(float(end[0]), 7), round(float(end[1]), 7))
        key = (int(segment["line_type"]),) + tuple(sorted((first, second)))
        if key in seen:
            continue
        seen.add(key)
        result.append((int(segment["line_type"]), start, end))
    return result


def _append_ratio_trace(playback_trace, isolated):
    trace = [dict(item) for item in playback_trace if isinstance(item, Mapping)]
    maximum_id = max((int(item.get("trace_id", -1)) for item in trace), default=-1)
    generation = max((int(item.get("generation", 0) or 0) for item in trace), default=0) + 1
    for index, item in enumerate(isolated, start=1):
        start = np.asarray(item["start"], dtype=float)
        end = np.asarray(item["end"], dtype=float)
        midpoint = (start + end) * 0.5
        angle = int(item["orientation"]) * 22.5
        radians = math.radians(angle)
        normal = np.array([-math.sin(radians), math.cos(radians)], dtype=float)
        derivation = str(item.get("derivation") or "segment_trisection")
        ratio = str(item.get("ratio") or "")
        trace.append(
            {
                "trace_id": maximum_id + index,
                "angle": angle,
                "line_offset_px": float(normal @ midpoint),
                "anchor_point_px": [float(midpoint[0]), float(midpoint[1])],
                "generation": generation,
                "trace_parent_ids": [],
                "formed_segments_px": (
                    [{"start": [float(start[0]), float(start[1])], "end": [float(end[0]), float(end[1])]}]
                    if int(item["line_type"]) in {2, 3}
                    else []
                ),
                "forms_output": int(item["line_type"]) in {2, 3},
                "last_used_generation": generation,
                "source": (
                    f"线段中点后对半段三等分取 {ratio} 点，再过点按图像证据取线"
                    if "half" in derivation
                    else f"线段三等分取 {ratio} 点，再过点按图像证据取线"
                ),
                "selected_provenance": "isolated_segment_ratio",
                "construction_ratio": ratio,
                "isolated_inference": True,
                "evidence_score": float(item.get("evidence_score", 0.0) or 0.0),
            }
        )
    return trace


def _build_coreless_candidate(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    coreless_report: Mapping[str, Any],
) -> dict[str, Any] | None:
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None

    segments, anchors, offsets, points, unresolved, unmatched = _prepare_shifted_segments(
        result,
        coreless_report,
        maximum,
    )
    if int(coreless_report.get("unexplained_observations", 99) or 99) > 2:
        return None

    split_nodes = _free_old_nodes(segments, maximum)
    internal = _internal_tuples(segments)
    if not internal:
        return None
    # Split genuine crossings after old-node constraints have been released.
    internal = _split_segments(internal, [])
    boundaries = legacy_variant._boundaries(internal, maximum)
    cp_text = legacy_variant._to_cp(internal + boundaries, maximum)

    isolated = infer_isolated_segment_ratio_segments(
        image_bytes,
        settings_mapping,
        result,
        cp_text,
    )
    if isolated:
        internal = _split_segments(internal, isolated)
        boundaries = legacy_variant._boundaries(internal, maximum)
        cp_text = legacy_variant._to_cp(internal + boundaries, maximum)

    playback_trace = _build_playback_trace(
        result,
        coreless_report,
        cp_text,
        maximum,
        offsets,
    )
    operations = _operation_by_target(coreless_report)
    for index, anchor in enumerate(playback_trace):
        trace_id = _trace_id(anchor, index)
        if trace_id in points:
            anchor["anchor_point_px"] = [float(points[trace_id][0]), float(points[trace_id][1])]
        operation = operations.get(trace_id)
        if operation is None:
            continue
        provenance = str(operation.get("provenance") or "")
        if provenance == "coreless_reference_ray":
            kind = str(operation.get("reference_kind") or "reference")
            corner = operation.get("source_corner")
            if kind == "paper_corner_symmetry" and corner:
                names = {
                    "top_left": "左上角",
                    "top_right": "右上角",
                    "bottom_right": "右下角",
                    "bottom_left": "左下角",
                }
                anchor["source"] = f"去核心取点：{names.get(str(corner), str(corner))}经可靠轴对称"
            elif kind == "stable_segment_trisection":
                anchor["source"] = f"去核心取点：可靠线段 {operation.get('ratio') or ''} 分点"
            elif kind == "stable_midpoint":
                anchor["source"] = "去核心取点：外部可靠点中点"
            elif kind == "stable_intersection":
                anchor["source"] = "去核心取点：外部可靠折痕交点"
            else:
                anchor["source"] = f"去核心取点：{kind}"
            anchor["selected_provenance"] = "coreless_reference_ray"
    playback_trace = _append_ratio_trace(playback_trace, isolated)

    import cv2
    from foldability import GeometrySegment, audit_camv_structure
    from reconstructor import Settings, _decode_image, _png_data_uri, prepare_paper_square

    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square, _, _ = prepare_paper_square(image, settings.analysis_size, settings.paper_corners)
    overlay_lines = square.copy()
    reconstruction = np.full_like(square, 255)
    all_segments = internal + boundaries
    for line_type, start, end in all_segments:
        start_xy = tuple(np.rint(start).astype(int))
        end_xy = tuple(np.rint(end).astype(int))
        if line_type == 1:
            color, width = (20, 20, 20), 2
        elif line_type == 2:
            color, width = (20, 20, 235), 1
        elif line_type == 3:
            color, width = (235, 45, 35), 1
        else:
            color, width = (70, 70, 70), 1
        cv2.line(overlay_lines, start_xy, end_xy, color, width, cv2.LINE_AA)
        cv2.line(reconstruction, start_xy, end_xy, color, width, cv2.LINE_AA)
    overlay = cv2.addWeighted(square, 0.50, overlay_lines, 0.50, 0)

    geometry = [
        GeometrySegment(int(line_type), (float(start[0]), float(start[1])), (float(end[0]), float(end[1])))
        for line_type, start, end in all_segments
    ]
    structure = audit_camv_structure(geometry, include_mv=False)
    mv_enabled = str(result.get("stats", {}).get("mv_input_mode") or "") == "color"
    full = audit_camv_structure(geometry, folding_types={2, 3}, include_mv=mv_enabled)
    stats = dict(result.get("stats") or {})
    stats.update(
        {
            "internal_segments": len(internal),
            "boundary_segments": len(boundaries),
            "total_cp_segments": len(all_segments),
            "camv_structural_completeness_score": structure["structural_completeness_score"],
            "camv_structure_violation_count": structure["violation_count"],
            "camv_structure": structure,
            "camv_full": full,
            "shadow_candidate_provenance_mode": "coreless_reference_v6",
            "shadow_candidate_coreless_root": int(coreless_report.get("coreless_root_trace_id", -1)),
            "shadow_candidate_coreless_reference_rays": int(coreless_report.get("coreless_reference_ray_count", 0) or 0),
            "shadow_candidate_free_topology_split_nodes": int(split_nodes),
            "shadow_candidate_unresolved_geometry": len(unresolved),
            "shadow_candidate_unmatched_cp_rows": int(unmatched),
            "shadow_candidate_isolated_ratio_segments": len(isolated),
        }
    )
    warnings = [
        "这是显式去核心构造 A/B：大系数核心点取线法被暂时禁用，图像 observation 保留并从核心依赖区之外重新找参考点。",
        "该候选不会把旧核心节点做最小二乘强制共点；旧高阶节点允许拆成多个附近交点或保持待解端点。",
        "cAMV 仍作为强结构先验参与质量比较，但不是绝对硬门槛。",
    ]
    if isolated:
        warnings.append(f"另外由有限线段比例点 + 原图证据补出 {len(isolated)} 条候选线。")
    if unresolved:
        warnings.append(f"有 {len(unresolved)} 条选中几何未能完整传播，保留旧位置作为局部待解。")

    constructions = []
    for item in list(coreless_report.get("selected_alternatives") or []):
        if not isinstance(item, Mapping):
            continue
        provenance = str(item.get("provenance") or item.get("kind") or "construction")
        if provenance == "coreless_reference_ray":
            constructions.append(
                {
                    "label": "去核心参考点取线",
                    "expression": (
                        f"target={item.get('target_trace_id')} · ref={item.get('reference_kind')}"
                        + (f" · ratio={item.get('ratio')}" if item.get("ratio") else "")
                        + (f" · corner={item.get('source_corner')}" if item.get("source_corner") else "")
                    ),
                    "support": max(0.0, 1.0 - float(item.get("residual_px", 0.0) or 0.0) / 3.2),
                }
            )
    for item in isolated:
        constructions.append(
            {
                "label": "线段比例点补线",
                "expression": f"ratio={item['ratio']} · evidence={item['evidence_score']:.3f}",
                "support": float(item.get("evidence_score", 0.0) or 0.0),
            }
        )

    return {
        "id": "construction-v2-coreless",
        "label": "构造搜索：去核心参考点",
        "cp": cp_text,
        "stats": stats,
        "warnings": warnings,
        "constructions": constructions,
        "playback_trace": playback_trace,
        "overlay_data_uri": _png_data_uri(overlay),
        "reconstruction_data_uri": _png_data_uri(reconstruction),
    }


def build_shadow_candidate_variant_v6(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any] | None:
    # The deliberate coreless branch is an alternative in its own right. It is
    # tried first and is not required to beat the ordinary core-point route.
    coreless = report.get("coreless_selected_report")
    if isinstance(coreless, Mapping) and coreless.get("root_abandons_core_seed"):
        variant = _build_coreless_candidate(
            image_bytes,
            settings_mapping,
            result,
            coreless,
        )
        if variant is not None:
            return variant

    # If no usable coreless route exists, retain the ordinary quality-aware
    # candidate behaviour as a fallback.
    variant = build_shadow_candidate_variant_v5(
        image_bytes,
        settings_mapping,
        result,
        report,
    )
    if variant is not None:
        variant.setdefault("stats", {})["shadow_candidate_provenance_mode"] = "quality_aware_v6_fallback"
    return variant


__all__ = ["build_shadow_candidate_variant_v6"]
