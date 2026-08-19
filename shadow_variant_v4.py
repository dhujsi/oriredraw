from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from isolated_ratio import infer_isolated_square_ratio_segments
from shadow_variant import _node_key, _parse_cp, _to_cp
from shadow_variant_v3 import build_candidate_cp_v3


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _segment_intersection(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
    d: np.ndarray,
) -> tuple[float, float, np.ndarray] | None:
    r = b - a
    s = d - c
    denominator = _cross(r, s)
    if abs(denominator) < 1e-10:
        return None
    delta = c - a
    t = _cross(delta, s) / denominator
    u = _cross(delta, r) / denominator
    if -1e-7 <= t <= 1.0 + 1e-7 and -1e-7 <= u <= 1.0 + 1e-7:
        return t, u, a + t * r
    return None


def _split_segments(segments, additions):
    base = [
        [int(line_type), np.asarray(start, dtype=float).copy(), np.asarray(end, dtype=float).copy()]
        for line_type, start, end in segments
    ]
    new = [
        [int(item["line_type"]), np.asarray(item["start"], dtype=float).copy(), np.asarray(item["end"], dtype=float).copy()]
        for item in additions
    ]
    all_segments = base + new
    parameters = [[0.0, 1.0] for _ in all_segments]
    for first in range(len(all_segments)):
        _, a, b = all_segments[first]
        for second in range(first + 1, len(all_segments)):
            _, c, d = all_segments[second]
            hit = _segment_intersection(a, b, c, d)
            if hit is None:
                continue
            t, u, _ = hit
            if 1e-6 < t < 1.0 - 1e-6:
                parameters[first].append(float(t))
            if 1e-6 < u < 1.0 - 1e-6:
                parameters[second].append(float(u))

    result = []
    seen = set()
    for (line_type, start, end), values in zip(all_segments, parameters):
        ordered = sorted(set(round(float(value), 10) for value in values))
        delta = end - start
        for first, second in zip(ordered, ordered[1:]):
            p = start + delta * first
            q = start + delta * second
            if float(np.linalg.norm(q - p)) <= 0.03:
                continue
            one = (round(float(p[0]), 7), round(float(p[1]), 7))
            two = (round(float(q[0]), 7), round(float(q[1]), 7))
            key = (int(line_type),) + tuple(sorted((one, two)))
            if key in seen:
                continue
            seen.add(key)
            result.append((int(line_type), p, q))
    return result


def _segments_from_cp(cp_text: str, maximum: float):
    return [
        (int(row["line_type"]), row["start"].copy(), row["end"].copy())
        for row in _parse_cp(cp_text, maximum)
    ]


def _synthetic_trace(playback_trace, isolated):
    trace = [dict(item) for item in playback_trace if isinstance(item, Mapping)]
    maximum_id = max((int(item.get("trace_id", -1)) for item in trace), default=-1)
    generation = max((int(item.get("generation", 0) or 0) for item in trace), default=0) + 1
    for index, item in enumerate(isolated, start=1):
        start = np.asarray(item["start"], dtype=float)
        end = np.asarray(item["end"], dtype=float)
        midpoint = (start + end) * 0.5
        orientation = int(item["orientation"])
        angle = orientation * 22.5
        radians = math.radians(angle)
        normal = np.array([-math.sin(radians), math.cos(radians)], dtype=float)
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
                    "线段取中点后对半段三等分，再过分点作正方形边的平行线"
                    if item.get("derivation") == "midpoint_then_trisection"
                    else "线段三等分后过分点作正方形边的平行线"
                ),
                "selected_provenance": "isolated_segment_ratio_parallel",
                "construction_ratio": str(item.get("ratio") or ""),
                "isolated_inference": True,
                "evidence_score": float(item.get("evidence_score", 0.0) or 0.0),
            }
        )
    return trace


def build_candidate_cp_v4(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    report: Mapping[str, Any],
):
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None

    base = build_candidate_cp_v3(result, report)
    if base is None:
        cp_text = str(result.get("cp") or "")
        segments = _segments_from_cp(cp_text, maximum)
        info = {
            "changed_rays": 0,
            "changed_output_rays": 0,
            "changed_nodes": 0,
            "dropped_collapsed_segments": 0,
            "topology_max_residual_px": 0.0,
            "internal_segments": sum(line_type != 1 for line_type, _, _ in segments),
            "boundary_segments": sum(line_type == 1 for line_type, _, _ in segments),
            "alternatives": list(report.get("selected_alternatives") or []),
            "selected_operations": list(report.get("selected_operations") or []),
            "playback_trace": list(result.get("playback_trace") or []),
        }
    else:
        cp_text, info, segments = base

    isolated = infer_isolated_square_ratio_segments(
        image_bytes,
        settings_mapping,
        result,
        cp_text,
    )
    if not isolated and base is None:
        return None

    if isolated:
        segments = _split_segments(segments, isolated)
        cp_text = _to_cp(segments, maximum)
    playback_trace = _synthetic_trace(info.get("playback_trace") or result.get("playback_trace") or [], isolated)

    info = dict(info)
    info.update(
        {
            "isolated_ratio_segments": isolated,
            "isolated_ratio_segment_count": len(isolated),
            "isolated_ratio_unknown_mv_count": sum(int(item["line_type"]) not in {2, 3} for item in isolated),
            "playback_trace": playback_trace,
            "internal_segments": sum(line_type != 1 for line_type, _, _ in segments),
            "boundary_segments": sum(line_type == 1 for line_type, _, _ in segments),
        }
    )
    return cp_text, info, segments


def _construction_rows(info: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for operation in list(info.get("alternatives") or []):
        if not isinstance(operation, Mapping):
            continue
        provenance = str(operation.get("provenance") or operation.get("kind") or "construction")
        details = []
        if operation.get("ratio") is not None:
            details.append(f"ratio={operation['ratio']}")
        if operation.get("corner") is not None:
            details.append(f"corner={operation['corner']}")
        if operation.get("target_trace_id") is not None:
            details.append(f"target={operation['target_trace_id']}")
        rows.append({"label": provenance, "expression": " · ".join(details) or provenance, "support": 1.0})
    for item in list(info.get("isolated_ratio_segments") or []):
        rows.append(
            {
                "label": "孤立线段等分取线",
                "expression": (
                    f"ratio={item['ratio']} · {item['derivation']} · evidence={item['evidence_score']:.3f}"
                ),
                "support": float(item.get("evidence_score", 0.0) or 0.0),
            }
        )
    return rows


def build_shadow_candidate_variant_v4(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any] | None:
    built = build_candidate_cp_v4(image_bytes, settings_mapping, result, report)
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
        else: color, width = (70, 70, 70), 1
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
            "shadow_candidate_changed_rays": int(info.get("changed_rays", 0)),
            "shadow_candidate_changed_nodes": int(info.get("changed_nodes", 0)),
            "shadow_candidate_isolated_ratio_segments": info["isolated_ratio_segment_count"],
            "shadow_candidate_isolated_unknown_mv": info["isolated_ratio_unknown_mv_count"],
            "shadow_candidate_provenance_mode": "global_v4_segment_ratios",
        }
    )
    warnings = [
        "这是 construction-search v2 的全局构造影子候选；strict 仍是默认输出。",
        "线段等分只作用于已构造线段/正方形局部，不再把纸边当成三等分对象。",
    ]
    if info["isolated_ratio_segment_count"]:
        warnings.append(f"局部证据补出了 {info['isolated_ratio_segment_count']} 条孤立的线段等分平行取线。")
    if info["isolated_ratio_unknown_mv_count"]:
        warnings.append(
            f"其中 {info['isolated_ratio_unknown_mv_count']} 条在单色证据下无法可靠判定峰谷，暂以辅助线型保留，不强猜 M/V。"
        )
    return {
        "id": "construction-v2-shadow",
        "label": "构造搜索 v2（影子）",
        "cp": cp_text,
        "stats": stats,
        "warnings": warnings,
        "constructions": _construction_rows(info),
        "playback_trace": info["playback_trace"],
        "overlay_data_uri": _png_data_uri(overlay),
        "reconstruction_data_uri": _png_data_uri(reconstruction),
    }


__all__ = ["build_candidate_cp_v4", "build_shadow_candidate_variant_v4"]
