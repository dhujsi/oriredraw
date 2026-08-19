from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

import shadow_variant as legacy_variant


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    return legacy_variant._trace_id(anchor, fallback)


def _orientation(anchor: Mapping[str, Any]) -> int | None:
    return legacy_variant._orientation(anchor)


def _selected_offsets(report: Mapping[str, Any]) -> dict[int, float]:
    result: dict[int, float] = {}
    raw = report.get("selected_offsets_px") or {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            try:
                result[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return result


def _selected_operations(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in list(report.get("selected_operations") or []) if isinstance(item, Mapping)]


def _meaningful_alternatives(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        item
        for item in _selected_operations(report)
        if str(item.get("provenance") or "legacy") != "legacy"
    ]


def _changed_rays(
    anchors: Mapping[int, Mapping[str, Any]],
    offsets: Mapping[int, float],
) -> set[int]:
    result: set[int] = set()
    for trace_id, anchor in anchors.items():
        if trace_id not in offsets or anchor.get("line_offset_px") is None:
            continue
        try:
            old = float(anchor["line_offset_px"])
        except (TypeError, ValueError):
            continue
        if abs(float(offsets[trace_id]) - old) > 1e-5:
            result.add(trace_id)
    return result


def _candidate_rows_for_anchor(
    rows: list[dict[str, Any]],
    anchor: Mapping[str, Any],
    offset: float,
) -> list[dict[str, Any]]:
    orientation = _orientation(anchor)
    if orientation is None:
        return []
    direction, normal, _ = legacy_variant._line_geometry(orientation, offset)
    matched: list[dict[str, Any]] = []
    for row in rows:
        if row["line_type"] == 1:
            continue
        delta = row["end"] - row["start"]
        length = float(np.linalg.norm(delta))
        if length <= 1e-8:
            continue
        unit = delta / length
        if abs(float(unit @ direction)) < 0.999999:
            continue
        residual = max(
            abs(float(normal @ row["start"]) - offset),
            abs(float(normal @ row["end"]) - offset),
        )
        if residual <= 0.18:
            matched.append(row)
    return matched


def _source_label(operation: Mapping[str, Any], original: Mapping[str, Any]) -> str:
    provenance = str(operation.get("provenance") or "legacy")
    if provenance == "paper_corner_ray":
        corner = str(operation.get("corner") or "paper corner")
        names = {
            "top_left": "左上角",
            "top_right": "右上角",
            "bottom_right": "右下角",
            "bottom_left": "左下角",
        }
        return f"纸角起线：{names.get(corner, corner)}"
    if provenance == "boundary_ratio_ray":
        side = str(operation.get("side") or "")
        names = {"top": "上边", "right": "右边", "bottom": "下边", "left": "左边"}
        ratio = str(operation.get("ratio") or "")
        return f"纸边比例取线：{names.get(side, side)} {ratio}"
    if provenance == "symmetry_point":
        return "对称点取线"
    if provenance == "direct_point":
        return "复用已有点取线"
    return str(original.get("source") or "旧构造")


def _parent_trace_ids(operation: Mapping[str, Any], original: Mapping[str, Any]) -> list[int]:
    provenance = str(operation.get("provenance") or "legacy")
    if provenance == "legacy":
        raw = original.get("trace_parent_ids")
        return [int(value) for value in raw] if isinstance(raw, (list, tuple)) else []
    values: list[int] = []
    for key in ("source_trace_id", "axis_trace_id"):
        if operation.get(key) is not None:
            try:
                value = int(operation[key])
            except (TypeError, ValueError):
                continue
            if value not in values:
                values.append(value)
    raw = operation.get("source_trace_ids")
    if isinstance(raw, (list, tuple)):
        for item in raw:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value not in values:
                values.append(value)
    return values


def _build_playback_trace(
    result: Mapping[str, Any],
    report: Mapping[str, Any],
    cp_text: str,
    maximum: float,
    offsets: Mapping[int, float],
) -> list[dict[str, Any]]:
    original_trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    originals = {_trace_id(anchor, index): anchor for index, anchor in enumerate(original_trace)}
    rows = legacy_variant._parse_cp(cp_text, maximum)
    selected = _selected_operations(report)
    emitted: list[dict[str, Any]] = []
    seen: set[int] = set()

    for sequence, operation in enumerate(selected):
        if operation.get("target_trace_id") is None:
            continue
        try:
            trace_id = int(operation["target_trace_id"])
        except (TypeError, ValueError):
            continue
        if trace_id in seen or trace_id not in originals:
            continue
        seen.add(trace_id)
        original = originals[trace_id]
        anchor = dict(original)
        candidate_offset = float(offsets.get(trace_id, original.get("line_offset_px", 0.0)))
        anchor["line_offset_px"] = candidate_offset
        point = operation.get("anchor_point_px")
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            anchor["anchor_point_px"] = [float(point[0]), float(point[1])]
        anchor["source"] = _source_label(operation, original)
        anchor["trace_parent_ids"] = _parent_trace_ids(operation, original)
        try:
            anchor["generation"] = max(0, int(operation.get("generation", sequence)))
        except (TypeError, ValueError):
            anchor["generation"] = sequence
        matched = _candidate_rows_for_anchor(rows, anchor, candidate_offset)
        anchor["formed_segments_px"] = [
            {
                "start": [float(row["start"][0]), float(row["start"][1])],
                "end": [float(row["end"][0]), float(row["end"][1])],
            }
            for row in matched
        ]
        anchor["forms_output"] = bool(matched)
        anchor["selected_provenance"] = str(operation.get("provenance") or "legacy")
        if operation.get("ratio") is not None:
            anchor["construction_ratio"] = str(operation["ratio"])
        if operation.get("corner") is not None:
            anchor["construction_corner"] = str(operation["corner"])
        emitted.append(anchor)

    # A defensive fallback keeps any trace that the search somehow omitted.
    # Such a ray remains explicitly marked as legacy rather than disappearing.
    for trace_id, original in originals.items():
        if trace_id in seen:
            continue
        anchor = dict(original)
        anchor["selected_provenance"] = "legacy_fallback"
        emitted.append(anchor)

    generation_by_id = {_trace_id(anchor, index): int(anchor.get("generation", 0) or 0) for index, anchor in enumerate(emitted)}
    last_use = dict(generation_by_id)
    for index, anchor in enumerate(emitted):
        child_generation = int(anchor.get("generation", 0) or 0)
        for parent in anchor.get("trace_parent_ids") or []:
            try:
                parent_id = int(parent)
            except (TypeError, ValueError):
                continue
            last_use[parent_id] = max(last_use.get(parent_id, 0), child_generation)
    for index, anchor in enumerate(emitted):
        trace_id = _trace_id(anchor, index)
        anchor["last_used_generation"] = int(last_use.get(trace_id, anchor.get("generation", 0) or 0))

    emitted.sort(key=lambda anchor: (int(anchor.get("generation", 0) or 0), _trace_id(anchor, 0)))
    return emitted


def _construction_rows(operations: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    labels = {
        "paper_corner_ray": "纸角起线",
        "boundary_ratio_ray": "纸边比例取线",
        "direct_point": "复用已有点取线",
        "symmetry_point": "对称点取线",
    }
    rows: list[dict[str, Any]] = []
    for operation in operations:
        provenance = str(operation.get("provenance") or operation.get("kind") or "construction")
        if provenance == "legacy":
            continue
        details: list[str] = []
        if operation.get("corner") is not None:
            details.append(f"corner={operation['corner']}")
        if operation.get("side") is not None:
            details.append(f"side={operation['side']}")
        if operation.get("ratio") is not None:
            details.append(f"ratio={operation['ratio']}")
        if operation.get("target_trace_id") is not None:
            details.append(f"target={operation['target_trace_id']}")
        if operation.get("candidate_offset_px") is not None:
            details.append(f"offset={operation['candidate_offset_px']}")
        rows.append({
            "label": labels.get(provenance, provenance),
            "expression": " · ".join(details) or provenance,
            "support": 1.0,
        })
    return rows


def build_candidate_cp_v3(
    result: Mapping[str, Any],
    report: Mapping[str, Any],
):
    alternatives = _meaningful_alternatives(report)
    if not alternatives or int(report.get("unexplained_observations", 1) or 0) != 0:
        return None
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None

    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    anchors = {_trace_id(anchor, index): anchor for index, anchor in enumerate(trace)}
    offsets = _selected_offsets(report)
    for trace_id, anchor in anchors.items():
        if trace_id not in offsets and anchor.get("line_offset_px") is not None:
            offsets[trace_id] = float(anchor["line_offset_px"])
    changed_rays = _changed_rays(anchors, offsets)
    if not changed_rays:
        # Do not emit a fake A/B tab whose CP is byte-for-byte the same route.
        return None

    rows = legacy_variant._parse_cp(result.get("cp", ""), maximum)
    unmatched = legacy_variant._match_rays(rows, anchors)
    internal_count = sum(row["line_type"] != 1 for row in rows)
    if unmatched or internal_count == 0:
        return None
    nodes, topology_residual, changed_nodes = legacy_variant._rebuild_nodes(rows, anchors, offsets, maximum)
    if topology_residual > 0.05:
        return None

    internal = []
    seen = set()
    dropped = 0
    for row in rows:
        if row["line_type"] == 1:
            continue
        start = nodes[legacy_variant._node_key(row["start"])]
        end = nodes[legacy_variant._node_key(row["end"])]
        if float(np.linalg.norm(end - start)) <= 0.05:
            dropped += 1
            continue
        first = (round(float(start[0]), 7), round(float(start[1]), 7))
        second = (round(float(end[0]), 7), round(float(end[1]), 7))
        key = (int(row["line_type"]),) + tuple(sorted((first, second)))
        if key in seen:
            continue
        seen.add(key)
        internal.append((int(row["line_type"]), start.copy(), end.copy()))
    if dropped > max(4, math.ceil(internal_count * 0.12)):
        return None
    boundaries = legacy_variant._boundaries(internal, maximum)
    segments = internal + boundaries
    cp_text = legacy_variant._to_cp(segments, maximum)
    if not cp_text.strip():
        return None

    playback_trace = _build_playback_trace(result, report, cp_text, maximum, offsets)
    info = {
        "changed_rays": len(changed_rays),
        "changed_output_rays": sum(bool(anchors[trace_id].get("forms_output")) for trace_id in changed_rays),
        "changed_nodes": int(changed_nodes),
        "dropped_collapsed_segments": int(dropped),
        "topology_max_residual_px": float(topology_residual),
        "internal_segments": len(internal),
        "boundary_segments": len(boundaries),
        "alternatives": alternatives,
        "selected_operations": _selected_operations(report),
        "playback_trace": playback_trace,
    }
    return cp_text, info, segments


def build_shadow_candidate_variant_v3(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any] | None:
    built = build_candidate_cp_v3(result, report)
    if built is None:
        return None
    cp_text, info, segments = built

    import cv2
    from foldability import GeometrySegment, audit_camv_structure
    from reconstructor import Settings, _decode_image, _png_data_uri, prepare_paper_square

    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square, _, _ = prepare_paper_square(image, settings.analysis_size, settings.paper_corners)
    overlay_lines = square.copy()
    reconstruction = np.full_like(square, 255)
    for line_type, start, end in segments:
        start_xy = tuple(np.rint(start).astype(int))
        end_xy = tuple(np.rint(end).astype(int))
        if line_type == 1: color, width = (20, 20, 20), 2
        elif line_type == 2: color, width = (20, 20, 235), 1
        elif line_type == 3: color, width = (235, 45, 35), 1
        else: color, width = (40, 185, 25), 1
        cv2.line(overlay_lines, start_xy, end_xy, color, width, cv2.LINE_AA)
        cv2.line(reconstruction, start_xy, end_xy, color, width, cv2.LINE_AA)
    overlay = cv2.addWeighted(square, 0.50, overlay_lines, 0.50, 0)

    geometry = [
        GeometrySegment(int(line_type), (float(start[0]), float(start[1])), (float(end[0]), float(end[1])))
        for line_type, start, end in segments
    ]
    structure = audit_camv_structure(geometry, include_mv=False)
    mv_enabled = str(result.get("stats", {}).get("mv_input_mode") or "") == "color"
    full = audit_camv_structure(geometry, folding_types={2, 3}, include_mv=mv_enabled)
    stats = dict(result.get("stats") or {})
    stats.update({
        "internal_segments": info["internal_segments"],
        "boundary_segments": info["boundary_segments"],
        "total_cp_segments": info["internal_segments"] + info["boundary_segments"],
        "camv_structural_completeness_score": structure["structural_completeness_score"],
        "camv_structure_violation_count": structure["violation_count"],
        "camv_structure": structure,
        "camv_full": full,
        "shadow_candidate_changed_rays": info["changed_rays"],
        "shadow_candidate_changed_output_rays": info["changed_output_rays"],
        "shadow_candidate_changed_nodes": info["changed_nodes"],
        "shadow_candidate_collapsed_segments": info["dropped_collapsed_segments"],
        "shadow_candidate_topology_max_residual_px": round(info["topology_max_residual_px"], 9),
        "shadow_candidate_provenance_mode": "global_v3",
    })
    warnings = [
        "这是 construction-search v2 的全局 provenance 影子候选，仅用于 A/B；strict 仍是默认输出。",
        f"全局搜索选择了 {len(info['alternatives'])} 个替代构造，实际改变 {info['changed_rays']} 条射线、{info['changed_nodes']} 个拓扑节点。",
    ]
    return {
        "id": "construction-v2-shadow",
        "label": "构造搜索 v2（影子）",
        "cp": cp_text,
        "stats": stats,
        "warnings": warnings,
        "constructions": _construction_rows(info["alternatives"]),
        "playback_trace": info["playback_trace"],
        "overlay_data_uri": _png_data_uri(overlay),
        "reconstruction_data_uri": _png_data_uri(reconstruction),
    }


__all__ = ["build_candidate_cp_v3", "build_shadow_candidate_variant_v3"]
