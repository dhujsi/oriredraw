"""Fast browser entry for user-guided reconstruction from raw image evidence.

This module deliberately stops before strict reconstruction.  It prepares the
paper image, extracts only observed finite 22.5-degree crease entities, builds
their finite topology, and lists boundary relations for a person to choose.
No CP is emitted and no legacy construction search is called from this path.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, Callable, Mapping

import cv2
import numpy as np

from guided_construction import build_boundary_relation_catalog_from_points
from raw_crease_evidence import (
    classify_topology_segment_line_types,
    detect_raw_crease_entities_from_square,
)
from raw_crease_topology import (
    apply_segment_line_type_evidence,
    build_raw_crease_topology_graph,
)
from reconstructor import (
    Settings,
    _decode_image,
    _png_data_uri,
    prepare_paper_square,
    validate_white_line_art,
)


_MODE = "guided_raw_primary_v1"


def _point(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    return int(round(x)), int(round(y))


def _candidate_points(candidates: list[dict[str, Any]]) -> set[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    for relation in candidates:
        for item in relation.get("points") or []:
            if not isinstance(item, Mapping):
                continue
            value = item.get("observed_point_px") or item.get("point_px") or item.get("point")
            parsed = _point(value)
            if parsed is not None:
                points.add(parsed)
    return points


def _render_raw_primary_previews(
    square: np.ndarray,
    raw_report: Mapping[str, Any],
    topology: Mapping[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[str, str]:
    """Render an observed-source overlay and a clean finite-topology preview."""

    overlay_lines = square.copy()
    clean = np.full_like(square, 255)
    for line in raw_report.get("lines") or []:
        if not isinstance(line, Mapping):
            continue
        for segment in line.get("visible_segments_px") or []:
            if not isinstance(segment, Mapping):
                continue
            start = _point(segment.get("start"))
            end = _point(segment.get("end"))
            if start is None or end is None:
                continue
            cv2.line(overlay_lines, start, end, (40, 176, 74), 2, cv2.LINE_AA)
            cv2.line(clean, start, end, (148, 148, 142), 1, cv2.LINE_AA)

    point_index = {
        str(item.get("id")): item
        for item in topology.get("points") or []
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    for segment in topology.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        first = point_index.get(str(segment.get("start_point_id")))
        second = point_index.get(str(segment.get("end_point_id")))
        start = _point(first.get("point") if first else None)
        end = _point(second.get("point") if second else None)
        if start is None or end is None:
            continue
        cv2.line(clean, start, end, (28, 28, 26), 1, cv2.LINE_AA)

    highlighted = _candidate_points(candidates)
    for item in topology.get("points") or []:
        if not isinstance(item, Mapping):
            continue
        center = _point(item.get("point"))
        if center is None:
            continue
        kind = str(item.get("point_kind") or "")
        if center in highlighted:
            color, radius = (47, 255, 199), 5
        elif kind == "boundary_contact":
            color, radius = (47, 255, 199), 3
        elif kind == "line_intersection":
            color, radius = (20, 154, 227), 2
        else:
            color, radius = (138, 138, 132), 1
        cv2.circle(overlay_lines, center, radius, color, -1, cv2.LINE_AA)
        cv2.circle(clean, center, radius, color, -1, cv2.LINE_AA)
        if radius >= 3:
            cv2.circle(overlay_lines, center, radius + 1, (23, 23, 20), 1, cv2.LINE_AA)
            cv2.circle(clean, center, radius + 1, (23, 23, 20), 1, cv2.LINE_AA)

    maximum = int(square.shape[0] - 1)
    cv2.rectangle(clean, (0, 0), (maximum, maximum), (23, 23, 20), 1, cv2.LINE_AA)
    overlay = cv2.addWeighted(square, 0.68, overlay_lines, 0.32, 0)
    return _png_data_uri(overlay), _png_data_uri(clean)


def analyze_raw_primary_from_square(
    square: np.ndarray,
    settings: Settings | None = None,
    *,
    preparation_stats: Mapping[str, Any] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Build the user-selection entry payload from an already squared image."""

    started = time.perf_counter()
    effective_settings = settings or Settings(analysis_size=int(square.shape[0]))

    def report(percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(percent), str(message))

    report(24, "正在读取原图有限折痕…")
    input_format = validate_white_line_art(square)
    raw_report = detect_raw_crease_entities_from_square(square, effective_settings)
    raw_report["paper_preparation"] = dict(preparation_stats or {})

    report(58, "正在连接原图中确实相交的有限线段…")
    _, _, topology = build_raw_crease_topology_graph(raw_report)
    line_type_evidence = classify_topology_segment_line_types(
        square,
        topology,
        mv_mode=effective_settings.mv_mode,
    )
    raw_report["segment_line_type_evidence"] = line_type_evidence
    applied_line_types = apply_segment_line_type_evidence(
        topology.get("segments") or [],
        line_type_evidence,
    )
    topology["segment_line_type_evidence"] = {
        key: value
        for key, value in line_type_evidence.items()
        if key != "segments"
    }
    topology["segment_line_type_evidence"].update(applied_line_types)
    contacts = topology.get("boundary_contacts") if topology.get("enabled") else []
    contacts = contacts if isinstance(contacts, list) else []
    maximum = float(topology.get("maximum_coordinate_px", square.shape[0] - 1))

    report(76, "正在按优先级整理纸边取点候选…")
    candidates = build_boundary_relation_catalog_from_points(
        contacts,
        maximum,
        evidence_source="raw_image_finite_topology",
        tolerance_px=max(5.0, maximum * 0.012),
        fit_algebraic_geometry=True,
    )

    report(90, "正在绘制原图折痕与有限拓扑…")
    overlay_uri, topology_uri = _render_raw_primary_previews(
        square,
        raw_report,
        topology,
        candidates,
    )
    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    warnings = [
        "这里还不是最终 CP：目前只提取了原图中可见的有限折痕、交点和纸边接触。请先由人选择一个取线起点。",
        "本流程不会枚举每个点的八个方向，也没有启动旧版严格重建；只有点击兼容按钮才会运行原来的长流程。",
    ]
    if not candidates:
        warnings.append("当前没有形成稳定的边界等分候选；请检查纸边是否完整、线稿是否清晰。")

    settings_mapping = asdict(effective_settings)
    stats = {
        "settings": settings_mapping,
        "analysis_size_used": int(square.shape[0]),
        "input_format": input_format,
        "raw_crease_count": int(raw_report.get("line_count", 0) or 0),
        "raw_finite_segment_count": int(raw_report.get("finite_segment_count", 0) or 0),
        "raw_topology_point_count": int(topology.get("point_count", 0) or 0),
        "raw_topology_segment_count": int(topology.get("segment_count", 0) or 0),
        "raw_boundary_contact_count": len(contacts),
        "raw_mv_assigned_segment_count": int(
            line_type_evidence.get("assigned_segment_count", 0) or 0
        ),
        "raw_mv_ambiguous_segment_count": int(
            line_type_evidence.get("ambiguous_segment_count", 0) or 0
        ),
        "raw_mv_unavailable_segment_count": int(
            line_type_evidence.get("unavailable_segment_count", 0) or 0
        ),
        "boundary_relation_candidate_count": len(candidates),
        "raw_analysis_duration_ms": duration_ms,
        "construction_search_executed": False,
        "strict_reconstruction_executed": False,
    }
    payload = {
        "id": "guided-raw-primary",
        "label": "原图取点",
        "mode": _MODE,
        "phase": "awaiting_boundary_relation",
        "output_ready": False,
        "cp": None,
        "overlay_data_uri": overlay_uri,
        "reconstruction_data_uri": topology_uri,
        "warnings": warnings,
        "stats": stats,
        "anchors": [],
        "playback_trace": [],
        "constructions": [],
        "variants": [],
        "shadow_search": {
            "enabled": True,
            "mode": _MODE,
            "legacy_search_executed": False,
            "raw_crease_evidence": raw_report,
            "raw_crease_topology": topology,
            "boundary_relation_candidates": candidates,
            "boundary_relation_candidate_source": "raw_image_finite_topology",
        },
        "invariants": {
            "strict_reconstruction_executed": False,
            "legacy_search_executed": False,
            "generated_crease_count": 0,
            "generated_direction_count": 0,
            "cp_emitted": False,
        },
    }
    report(100, "原图折痕已就绪，请选择取线起点")
    return payload


def analyze_raw_primary(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Prepare source paper and return the fast user-guided entry payload."""

    settings = Settings.from_mapping(dict(settings_mapping or {}))
    if progress_callback is not None:
        progress_callback(5, "正在裁剪并拉正纸张…")
    image = _decode_image(image_bytes)
    square, _, preparation_stats = prepare_paper_square(
        image,
        settings.analysis_size,
        settings.paper_corners,
    )
    return analyze_raw_primary_from_square(
        square,
        settings,
        preparation_stats=preparation_stats,
        progress_callback=progress_callback,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def analyze_raw_primary_json(
    image_bytes: bytes,
    settings_json: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> str:
    payload = analyze_raw_primary(
        image_bytes,
        json.loads(settings_json or "{}"),
        progress_callback=progress_callback,
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


__all__ = [
    "analyze_raw_primary",
    "analyze_raw_primary_from_square",
    "analyze_raw_primary_json",
]
