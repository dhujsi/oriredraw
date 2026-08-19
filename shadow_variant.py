from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

import numpy as np


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(anchor.get("trace_id", fallback))
    except (TypeError, ValueError):
        return fallback


def _orientation(anchor: Mapping[str, Any]) -> int | None:
    try:
        return int(round(float(anchor["angle"]) / 22.5)) % 8
    except (KeyError, TypeError, ValueError):
        return None


def _line_geometry(orientation: int, offset: float):
    angle = orientation * math.pi / 8.0
    direction = np.array([math.cos(angle), math.sin(angle)], dtype=float)
    normal = np.array([-direction[1], direction[0]], dtype=float)
    return direction, normal, float(offset)


def _cp_to_pixel(value: float, maximum: float) -> float:
    return (float(value) + 200.0) * maximum / 400.0


def _pixel_to_cp(value: float, maximum: float) -> float:
    return -200.0 + 400.0 * float(value) / maximum


def _cp_value(value: float) -> str:
    if abs(value) < 5e-10:
        value = 0.0
    if abs(value - 200.0) < 5e-9:
        value = 200.0
    if abs(value + 200.0) < 5e-9:
        value = -200.0
    return f"{value:.12g}"


def _parse_cp(cp_text: str, maximum: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in str(cp_text or "").splitlines():
        parts = raw.split()
        if len(parts) != 5:
            continue
        try:
            line_type = int(parts[0])
            x1, y1, x2, y2 = [float(value) for value in parts[1:]]
        except ValueError:
            continue
        rows.append(
            {
                "line_type": line_type,
                "start": np.array(
                    [_cp_to_pixel(x1, maximum), _cp_to_pixel(y1, maximum)], dtype=float
                ),
                "end": np.array(
                    [_cp_to_pixel(x2, maximum), _cp_to_pixel(y2, maximum)], dtype=float
                ),
            }
        )
    return rows


def refine_trace_offsets_from_cp(result: dict[str, Any]) -> int:
    """Rebind output rays to the higher-precision geometry preserved by .cp."""
    trace = [x for x in list(result.get("playback_trace") or []) if isinstance(x, dict)]
    if not trace:
        return 0
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return 0
    if maximum <= 0:
        return 0
    rows = [row for row in _parse_cp(result.get("cp", ""), maximum) if row["line_type"] != 1]
    count = 0
    for anchor in trace:
        if not anchor.get("forms_output"):
            continue
        orientation = _orientation(anchor)
        if orientation is None:
            continue
        try:
            old_offset = float(anchor["line_offset_px"])
        except (KeyError, TypeError, ValueError):
            continue
        direction, normal, _ = _line_geometry(orientation, old_offset)
        candidates: list[tuple[float, float]] = []
        for row in rows:
            delta = row["end"] - row["start"]
            length = float(np.linalg.norm(delta))
            if length <= 1e-8:
                continue
            if abs(float((delta / length) @ direction)) < 0.999999:
                continue
            offset = float((normal @ row["start"] + normal @ row["end"]) / 2.0)
            if abs(offset - old_offset) <= 0.15:
                candidates.append((offset, length))
        if not candidates:
            continue
        total = sum(length for _, length in candidates)
        refined = sum(offset * length for offset, length in candidates) / total
        if abs(refined - old_offset) > 0.15:
            continue
        anchor["shadow_serialized_offset_px"] = old_offset
        anchor["line_offset_px"] = float(refined)
        count += 1
    return count


def _parent_ids(anchor: Mapping[str, Any], valid: set[int]) -> tuple[int, ...]:
    raw = anchor.get("trace_parent_ids")
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[int] = []
    for value in raw:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item in valid and item not in result:
            result.append(item)
    return tuple(result)


def _generation(anchor: Mapping[str, Any]) -> int:
    try:
        return int(anchor.get("generation", -1))
    except (TypeError, ValueError):
        return -1


def _intersection(first, second) -> np.ndarray | None:
    _, n1, o1 = first
    _, n2, o2 = second
    matrix = np.vstack([n1, n2])
    if abs(float(np.linalg.det(matrix))) < 1e-9:
        return None
    return np.linalg.solve(matrix, np.array([o1, o2], dtype=float))


def _boundary_contact(line, legacy_point: np.ndarray, maximum: float) -> np.ndarray | None:
    direction, normal, offset = line
    x, y = [float(value) for value in legacy_point]
    _, coordinate, boundary = min(
        (
            (abs(x), 0, 0.0),
            (abs(x - maximum), 0, maximum),
            (abs(y), 1, 0.0),
            (abs(y - maximum), 1, maximum),
        ),
        key=lambda item: item[0],
    )
    base = normal * offset
    component = float(direction[coordinate])
    if abs(component) < 1e-9:
        return None
    parameter = (boundary - float(base[coordinate])) / component
    return base + parameter * direction


def _ray_offset(orientation: int, point: np.ndarray) -> float:
    return float(_line_geometry(orientation, 0.0)[1] @ point)


def _affected_descendants(anchors: Mapping[int, Mapping[str, Any]], root_id: int) -> set[int]:
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


def _selected_route(report: Mapping[str, Any]) -> Mapping[str, Any] | None:
    routes = [
        item
        for item in list(report.get("suspicious_seed_routes") or [])
        if isinstance(item, Mapping) and item.get("route_improved")
    ]
    if not routes:
        return None
    return max(
        routes,
        key=lambda item: (
            float(item.get("score_improvement", 0.0) or 0.0),
            float(item.get("residual_improvement_px", 0.0) or 0.0),
        ),
    )


def _seed_offsets(route: Mapping[str, Any], root_id: int) -> dict[int, float]:
    seeds: dict[int, float] = {}
    for operation in list(route.get("proof_operations") or []):
        if not isinstance(operation, Mapping) or operation.get("kind") != "ray_from_point":
            continue
        if operation.get("offset_px") is None:
            continue
        try:
            trace_id = int(operation["target_trace_id"])
            offset = float(operation["offset_px"])
        except (KeyError, TypeError, ValueError):
            continue
        if trace_id != root_id:
            seeds[trace_id] = offset
    return seeds


def _propagate_offsets(result: Mapping[str, Any], route: Mapping[str, Any]):
    trace = [x for x in list(result.get("playback_trace") or []) if isinstance(x, Mapping)]
    anchors = {_trace_id(anchor, index): anchor for index, anchor in enumerate(trace)}
    try:
        root_id = int(route["trace_id"])
        root_offset = float(route["selected_offset_px"])
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (KeyError, TypeError, ValueError):
        return {}, set(), []
    if root_id not in anchors or maximum <= 0:
        return {}, set(), []
    affected = _affected_descendants(anchors, root_id)
    valid = set(anchors)
    offsets = {
        trace_id: float(anchor["line_offset_px"])
        for trace_id, anchor in anchors.items()
        if trace_id not in affected and anchor.get("line_offset_px") is not None
    }
    offsets[root_id] = root_offset
    seeds = _seed_offsets(route, root_id)
    offsets.update(seeds)
    unresolved: list[int] = []
    for trace_id in sorted(
        affected - {root_id} - set(seeds),
        key=lambda value: (_generation(anchors[value]), value),
    ):
        anchor = anchors[trace_id]
        parents = _parent_ids(anchor, valid)
        point = None
        if len(parents) >= 2 and parents[0] in offsets and parents[1] in offsets:
            first_orientation = _orientation(anchors[parents[0]])
            second_orientation = _orientation(anchors[parents[1]])
            if first_orientation is not None and second_orientation is not None:
                point = _intersection(
                    _line_geometry(first_orientation, offsets[parents[0]]),
                    _line_geometry(second_orientation, offsets[parents[1]]),
                )
        elif len(parents) == 1 and parents[0] in offsets and "纸边交点" in str(anchor.get("source") or ""):
            parent_orientation = _orientation(anchors[parents[0]])
            raw_point = anchor.get("anchor_point_px")
            if parent_orientation is not None and isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                point = _boundary_contact(
                    _line_geometry(parent_orientation, offsets[parents[0]]),
                    np.asarray(raw_point[:2], dtype=float),
                    maximum,
                )
        orientation = _orientation(anchor)
        if point is None or orientation is None or np.any(point < -2.0) or np.any(point > maximum + 2.0):
            unresolved.append(trace_id)
            continue
        offsets[trace_id] = _ray_offset(orientation, point)
    return offsets, affected, unresolved


def _match_rays(rows: list[dict[str, Any]], anchors: Mapping[int, Mapping[str, Any]]) -> int:
    output_ids = [trace_id for trace_id, anchor in anchors.items() if anchor.get("forms_output")]
    unmatched = 0
    for row in rows:
        if row["line_type"] == 1:
            row["trace_id"] = None
            continue
        delta = row["end"] - row["start"]
        length = float(np.linalg.norm(delta))
        best = None
        if length > 1e-8:
            unit = delta / length
            for trace_id in output_ids:
                anchor = anchors[trace_id]
                orientation = _orientation(anchor)
                if orientation is None or anchor.get("line_offset_px") is None:
                    continue
                direction, normal, offset = _line_geometry(orientation, float(anchor["line_offset_px"]))
                if abs(float(unit @ direction)) < 0.999999:
                    continue
                residual = max(
                    abs(float(normal @ row["start"]) - offset),
                    abs(float(normal @ row["end"]) - offset),
                )
                if residual <= 0.15 and (best is None or residual < best[0]):
                    best = (residual, trace_id)
        row["trace_id"] = best[1] if best else None
        unmatched += int(best is None)
    return unmatched


def _node_key(point: np.ndarray) -> tuple[float, float]:
    return round(float(point[0]), 5), round(float(point[1]), 5)


def _rebuild_nodes(rows, anchors, offsets, maximum: float):
    nodes: dict[tuple[float, float], dict[str, Any]] = {}
    for edge_index, row in enumerate(rows):
        if row["line_type"] == 1:
            continue
        for point in (row["start"], row["end"]):
            nodes.setdefault(_node_key(point), {"old": point.copy(), "edges": []})["edges"].append(edge_index)
    changed_rays = {
        trace_id
        for trace_id, anchor in anchors.items()
        if trace_id in offsets
        and anchor.get("line_offset_px") is not None
        and abs(float(offsets[trace_id]) - float(anchor["line_offset_px"])) > 1e-6
    }
    rebuilt: dict[tuple[float, float], np.ndarray] = {}
    max_residual = 0.0
    changed_nodes = 0
    for key, node in nodes.items():
        incident = {
            rows[index].get("trace_id")
            for index in node["edges"]
            if rows[index].get("trace_id") is not None
        }
        if not (incident & changed_rays):
            rebuilt[key] = node["old"].copy()
            continue
        equations: list[np.ndarray] = []
        values: list[float] = []
        for trace_id in sorted(incident):
            anchor = anchors[trace_id]
            orientation = _orientation(anchor)
            if orientation is None:
                continue
            candidate_offset = float(offsets.get(trace_id, anchor["line_offset_px"]))
            equations.append(_line_geometry(orientation, candidate_offset)[1])
            values.append(candidate_offset)
        old = node["old"]
        if abs(float(old[0])) <= 1e-3:
            equations.append(np.array([1.0, 0.0])); values.append(0.0)
        elif abs(float(old[0]) - maximum) <= 1e-3:
            equations.append(np.array([1.0, 0.0])); values.append(maximum)
        if abs(float(old[1])) <= 1e-3:
            equations.append(np.array([0.0, 1.0])); values.append(0.0)
        elif abs(float(old[1]) - maximum) <= 1e-3:
            equations.append(np.array([0.0, 1.0])); values.append(maximum)
        if len(equations) < 2:
            rebuilt[key] = old.copy()
            continue
        matrix = np.vstack(equations)
        if int(np.linalg.matrix_rank(matrix)) < 2:
            rebuilt[key] = old.copy()
            continue
        vector = np.asarray(values, dtype=float)
        point = np.linalg.lstsq(matrix, vector, rcond=None)[0]
        residual = float(np.max(np.abs(matrix @ point - vector)))
        max_residual = max(max_residual, residual)
        rebuilt[key] = point
        changed_nodes += 1
    return rebuilt, max_residual, changed_nodes


def _boundaries(internal, maximum: float):
    values = {"top": [0.0, maximum], "right": [0.0, maximum], "bottom": [0.0, maximum], "left": [0.0, maximum]}
    for _, start, end in internal:
        for point in (start, end):
            x, y = [float(value) for value in point]
            if abs(y) <= 1e-4: values["top"].append(x)
            if abs(x - maximum) <= 1e-4: values["right"].append(y)
            if abs(y - maximum) <= 1e-4: values["bottom"].append(x)
            if abs(x) <= 1e-4: values["left"].append(y)
    result = []
    for side, raw in values.items():
        ordered = sorted(set(round(min(maximum, max(0.0, float(value))), 8) for value in raw))
        for first, second in zip(ordered, ordered[1:]):
            if second - first <= 1e-5:
                continue
            if side == "top": start, end = np.array([first, 0.0]), np.array([second, 0.0])
            elif side == "right": start, end = np.array([maximum, first]), np.array([maximum, second])
            elif side == "bottom": start, end = np.array([second, maximum]), np.array([first, maximum])
            else: start, end = np.array([0.0, second]), np.array([0.0, first])
            result.append((1, start, end))
    return result


def _to_cp(segments, maximum: float) -> str:
    rows = [
        (
            int(line_type),
            _pixel_to_cp(float(start[0]), maximum),
            _pixel_to_cp(float(start[1]), maximum),
            _pixel_to_cp(float(end[0]), maximum),
            _pixel_to_cp(float(end[1]), maximum),
        )
        for line_type, start, end in segments
    ]
    rows.sort(key=lambda row: (row[0], round(row[2], 8), round(row[1], 8), round(row[4], 8), round(row[3], 8)))
    return "".join(
        f"{line_type} {_cp_value(x1)} {_cp_value(y1)} {_cp_value(x2)} {_cp_value(y2)}\n"
        for line_type, x1, y1, x2, y2 in rows
    )


def build_candidate_cp(result: Mapping[str, Any], shadow_report: Mapping[str, Any]):
    route = _selected_route(shadow_report)
    if route is None:
        return None
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None
    trace = [x for x in list(result.get("playback_trace") or []) if isinstance(x, Mapping)]
    anchors = {_trace_id(anchor, index): anchor for index, anchor in enumerate(trace)}
    offsets, affected, unresolved = _propagate_offsets(result, route)
    if unresolved or not offsets:
        return None
    rows = _parse_cp(result.get("cp", ""), maximum)
    unmatched = _match_rays(rows, anchors)
    internal_count = sum(row["line_type"] != 1 for row in rows)
    if unmatched or internal_count == 0:
        return None
    nodes, topology_residual, changed_nodes = _rebuild_nodes(rows, anchors, offsets, maximum)
    if topology_residual > 0.05:
        return None
    internal = []
    seen = set()
    dropped = 0
    for row in rows:
        if row["line_type"] == 1:
            continue
        start = nodes[_node_key(row["start"])]
        end = nodes[_node_key(row["end"])]
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
    boundaries = _boundaries(internal, maximum)
    segments = internal + boundaries
    root_id = int(route.get("trace_id", -1))
    changed_rays = sum(
        trace_id in offsets
        and anchor.get("line_offset_px") is not None
        and abs(float(offsets[trace_id]) - float(anchor["line_offset_px"])) > 1e-6
        for trace_id, anchor in anchors.items()
    )
    info = {
        "root_trace_id": root_id,
        "score_improvement": float(route.get("score_improvement", 0.0) or 0.0),
        "residual_improvement_px": float(route.get("residual_improvement_px", 0.0) or 0.0),
        "changed_rays": int(changed_rays),
        "changed_nodes": int(changed_nodes),
        "affected_rays": len(affected),
        "dropped_collapsed_segments": int(dropped),
        "topology_max_residual_px": float(topology_residual),
        "internal_segments": len(internal),
        "boundary_segments": len(boundaries),
        "proof_operations": list(route.get("proof_operations") or []),
    }
    return _to_cp(segments, maximum), info, segments


def _construction_rows(operations):
    labels = {
        "boundary_midpoint_point": "纸边中点",
        "midpoint_point": "中点",
        "symmetry_point": "对称点",
        "ray_from_point": "由点发射射线",
        "paper_midline_intersection": "纸张中线交点",
        "direct_point": "直接复用已有点",
    }
    result = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        kind = str(operation.get("kind") or "construction")
        details = [
            f"{key}={operation[key]}"
            for key in ("side", "axis_trace_id", "source_trace_id", "target_trace_id", "midline", "offset_px")
            if operation.get(key) is not None
        ]
        result.append({"label": labels.get(kind, kind), "expression": " · ".join(details) or kind, "support": 1.0})
    return result


def build_shadow_candidate_variant(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    shadow_report: Mapping[str, Any],
) -> dict[str, Any] | None:
    built = build_candidate_cp(result, shadow_report)
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
    stats.update(
        {
            "internal_segments": info["internal_segments"],
            "boundary_segments": info["boundary_segments"],
            "total_cp_segments": info["internal_segments"] + info["boundary_segments"],
            "camv_structural_completeness_score": structure["structural_completeness_score"],
            "camv_structure_violation_count": structure["violation_count"],
            "camv_structure": structure,
            "camv_full": full,
            "shadow_candidate_root_trace_id": info["root_trace_id"],
            "shadow_candidate_changed_rays": info["changed_rays"],
            "shadow_candidate_changed_nodes": info["changed_nodes"],
            "shadow_candidate_affected_rays": info["affected_rays"],
            "shadow_candidate_collapsed_segments": info["dropped_collapsed_segments"],
            "shadow_candidate_topology_max_residual_px": round(info["topology_max_residual_px"], 9),
            "shadow_candidate_score_improvement": round(info["score_improvement"], 6),
            "shadow_candidate_residual_improvement_px": round(info["residual_improvement_px"], 6),
        }
    )
    warnings = [
        "这是 construction-search v2 的影子候选，仅用于 A/B 对比；默认严格输出没有被替换。",
        f"v2 重新解释 {info['affected_rays']} 条相关射线，其中 {info['changed_rays']} 条几何发生变化；节点共线残差上限 {info['topology_max_residual_px']:.6f}px。",
    ]
    if info["dropped_collapsed_segments"]:
        warnings.append(f"新拓扑使 {info['dropped_collapsed_segments']} 条旧微小分段退化并被移除。")
    return {
        "id": "construction-v2-shadow",
        "label": "构造搜索 v2（影子）",
        "cp": cp_text,
        "stats": stats,
        "warnings": warnings,
        "constructions": _construction_rows(info["proof_operations"]),
        "overlay_data_uri": _png_data_uri(overlay),
        "reconstruction_data_uri": _png_data_uri(reconstruction),
    }


__all__ = ["refine_trace_offsets_from_cp", "build_candidate_cp", "build_shadow_candidate_variant"]
