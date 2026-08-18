"""Small JSON-safe bridge shared by Flask-free browser builds."""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Mapping

import numpy as np

from reconstructor import (
    Settings,
    _decode_image,
    _png_data_uri,
    prepare_paper_square,
    reconstruct,
)


_PUBLIC_RESULT_KEYS = (
    "cp",
    "stats",
    "anchors",
    "playback_trace",
    "warnings",
    "id",
    "label",
    "constructions",
    "variants",
    "overlay_data_uri",
    "reconstruction_data_uri",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _final_cp_segments(
    cp_text: str, maximum: float
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return only internal final-output CP segments in reconstruction pixels."""
    if maximum <= 0:
        return []
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    for row in str(cp_text or "").splitlines():
        parts = row.split()
        if len(parts) < 5:
            continue
        try:
            line_type = int(parts[0])
            values = [float(value) for value in parts[1:5]]
        except ValueError:
            continue
        if line_type not in {2, 3}:
            continue
        x1, y1, x2, y2 = [
            (value + 200.0) * maximum / 400.0 for value in values
        ]
        segments.append(
            (
                np.array([x1, y1], dtype=float),
                np.array([x2, y2], dtype=float),
            )
        )
    return segments


def _anchor_line_geometry(
    anchor: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray, float] | None:
    try:
        angle = math.radians(float(anchor["angle"]))
        offset = float(anchor["line_offset_px"])
    except (KeyError, TypeError, ValueError):
        return None
    direction = np.array([math.cos(angle), math.sin(angle)], dtype=float)
    normal = np.array([-direction[1], direction[0]], dtype=float)
    return direction, normal, offset


def _segments_on_anchor(
    anchor: Mapping[str, Any],
    segments: list[tuple[np.ndarray, np.ndarray]],
) -> list[dict[str, list[float]]]:
    geometry = _anchor_line_geometry(anchor)
    if geometry is None:
        return []
    direction, normal, offset = geometry
    matched: list[dict[str, list[float]]] = []
    for start, end in segments:
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length <= 1e-7:
            continue
        parallel = abs(float(delta @ direction) / length)
        if parallel < 0.999999:
            continue
        if (
            abs(float(normal @ start) - offset) <= 0.12
            and abs(float(normal @ end) - offset) <= 0.12
        ):
            matched.append(
                {
                    "start": [round(float(value), 6) for value in start],
                    "end": [round(float(value), 6) for value in end],
                }
            )
    return matched


def _anchor_passes_point(
    anchor: Mapping[str, Any],
    point: np.ndarray,
    tolerance: float = 0.22,
) -> bool:
    geometry = _anchor_line_geometry(anchor)
    if geometry is None:
        return False
    _, normal, offset = geometry
    return abs(float(normal @ point) - offset) <= tolerance


def _anchor_generation(anchor: Mapping[str, Any]) -> int:
    try:
        return int(anchor.get("generation", -1))
    except (TypeError, ValueError):
        return -1


def _infer_trace_parents(
    anchors: list[dict[str, Any]],
    child_index: int,
) -> list[int]:
    child = anchors[child_index]
    generation = _anchor_generation(child)
    if generation <= 0:
        return []
    try:
        point = np.asarray(child["anchor_point_px"][:2], dtype=float)
    except (KeyError, TypeError, ValueError, IndexError):
        return []

    source = str(child.get("source") or "")
    boundary_contact = "纸边交点" in source
    required_count = 1 if boundary_contact else 2

    # The core records true parent indices before the display list is sorted.
    # In builds where those indices still happen to line up with the serialized
    # list, use them. Validate geometrically first so a stale index can never
    # pull an unrelated search ray into the public derivation.
    declared: list[int] = []
    raw_parents = child.get("parents")
    if isinstance(raw_parents, (list, tuple)):
        for value in raw_parents:
            try:
                parent_index = int(value)
            except (TypeError, ValueError):
                continue
            if not 0 <= parent_index < len(anchors) or parent_index == child_index:
                continue
            parent = anchors[parent_index]
            if (
                0 <= _anchor_generation(parent) < generation
                and _anchor_passes_point(parent, point)
            ):
                declared.append(parent_index)
        declared = list(dict.fromkeys(declared))
        if len(declared) >= required_count:
            return declared[:required_count]

    # Fallback for the normal browser payload: reconstruct the dependency at
    # the exact construction point from already-derived rays. This is still a
    # geometric construction relation, not a raster/search-history heuristic.
    candidates: list[tuple[int, float, int]] = []
    child_geometry = _anchor_line_geometry(child)
    child_direction = child_geometry[0] if child_geometry is not None else None
    for parent_index, parent in enumerate(anchors):
        if parent_index == child_index:
            continue
        parent_generation = _anchor_generation(parent)
        if not 0 <= parent_generation < generation:
            continue
        parent_geometry = _anchor_line_geometry(parent)
        if parent_geometry is None or not _anchor_passes_point(parent, point):
            continue
        if child_direction is not None:
            parallel = abs(float(parent_geometry[0] @ child_direction))
            if parallel >= 0.999999:
                continue
        distance = abs(float(parent_geometry[1] @ point) - parent_geometry[2])
        candidates.append((-parent_generation, distance, parent_index))

    chosen: list[int] = []
    chosen_directions: list[np.ndarray] = []
    for _, _, parent_index in sorted(candidates):
        direction = _anchor_line_geometry(anchors[parent_index])[0]
        if any(
            abs(float(direction @ existing)) >= 0.999999
            for existing in chosen_directions
        ):
            continue
        chosen.append(parent_index)
        chosen_directions.append(direction)
        if len(chosen) >= required_count:
            break
    return chosen


def _build_playback_trace(result: dict[str, Any]) -> None:
    """Build the derivation DAG that contributes to the exported CP.

    Final-output rays seed the trace. Their construction ancestors are retained
    even when those ancestors are pure auxiliary rays, while unrelated
    candidates explored by the reconstructor are excluded. Each retained ray
    also records the finite CP segments it actually forms and the last
    generation that still depends on its full construction line.
    """
    anchors = [
        dict(anchor)
        for anchor in list(result.get("anchors") or [])
        if _anchor_generation(anchor) >= 0
    ]
    size = int(result.get("stats", {}).get("analysis_size_used") or 0)
    maximum = float(size - 1)
    segments = _final_cp_segments(result.get("cp", ""), maximum)
    if not anchors or not segments:
        result["playback_trace"] = []
        return

    formed_segments = [
        _segments_on_anchor(anchor, segments) for anchor in anchors
    ]
    parents = [
        _infer_trace_parents(anchors, index) for index in range(len(anchors))
    ]

    required = {
        index for index, matched in enumerate(formed_segments) if matched
    }
    stack = list(required)
    while stack:
        child_index = stack.pop()
        for parent_index in parents[child_index]:
            if parent_index not in required:
                required.add(parent_index)
                stack.append(parent_index)

    children: dict[int, list[int]] = {index: [] for index in required}
    for child_index in required:
        for parent_index in parents[child_index]:
            if parent_index in required:
                children[parent_index].append(child_index)

    trace: list[dict[str, Any]] = []
    for index in sorted(
        required,
        key=lambda value: (
            _anchor_generation(anchors[value]),
            float(anchors[value].get("angle", 0.0)),
            float(anchors[value].get("line_offset_px", 0.0)),
            value,
        ),
    ):
        anchor = dict(anchors[index])
        retained_parents = [
            parent for parent in parents[index] if parent in required
        ]
        dependent_generations = [
            _anchor_generation(anchors[child])
            for child in children.get(index, [])
        ]
        anchor.update(
            {
                "trace_id": index,
                "trace_parent_ids": retained_parents,
                "formed_segments_px": formed_segments[index],
                "forms_output": bool(formed_segments[index]),
                "last_used_generation": max(
                    dependent_generations,
                    default=_anchor_generation(anchor),
                ),
            }
        )
        trace.append(anchor)
    result["playback_trace"] = trace


def _filter_anchors_to_final_output(result: dict[str, Any]) -> None:
    """Keep the diagnostic anchor table limited to rays present in final CP."""
    anchors = list(result.get("anchors") or [])
    size = int(result.get("stats", {}).get("analysis_size_used") or 0)
    maximum = float(size - 1)
    segments = _final_cp_segments(result.get("cp", ""), maximum)
    if not anchors or not segments:
        result["anchors"] = [] if anchors else anchors
        return
    result["anchors"] = [
        anchor for anchor in anchors if _segments_on_anchor(anchor, segments)
    ]


def reconstruct_for_web(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Run the normal reconstructor and omit native image arrays from the result."""
    settings = Settings.from_mapping(dict(settings_mapping or {}))
    result = reconstruct(
        image_bytes,
        settings=settings,
        progress_callback=progress_callback,
    )
    _build_playback_trace(result)
    _filter_anchors_to_final_output(result)
    optional_defaults = {
        "id": "strict",
        "label": "严格 22.5°",
        "constructions": [],
        "variants": [],
        "playback_trace": [],
    }
    return {
        key: result.get(key, optional_defaults[key])
        if key in optional_defaults
        else result[key]
        for key in _PUBLIC_RESULT_KEYS
    }


def reconstruct_for_web_json(
    image_bytes: bytes,
    settings_json: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> str:
    payload = reconstruct_for_web(
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


def rectify_for_web(image_bytes: bytes, corners: list[list[float]]) -> dict[str, Any]:
    """Return a high-resolution square PNG from four ordered corner points."""
    image = _decode_image(image_bytes)
    points = np.asarray(corners, dtype=np.float32)
    if points.shape == (4, 2) and float(np.max(np.abs(points))) <= 1.000001:
        source_points = points * np.array(
            [image.shape[1] - 1, image.shape[0] - 1], dtype=np.float32
        )
    else:
        source_points = points
    if source_points.shape != (4, 2):
        output_size = 1024
    else:
        sides = [
            float(np.linalg.norm(source_points[(index + 1) % 4] - source_points[index]))
            for index in range(4)
        ]
        output_size = int(
            np.clip(
                round(max((sides[0] + sides[2]) / 2.0, (sides[1] + sides[3]) / 2.0)),
                256,
                4096,
            )
        )
    square, bounds, stats = prepare_paper_square(
        image,
        output_size,
        paper_corners=corners,
    )
    return {
        "image_data_uri": _png_data_uri(square),
        "width": int(square.shape[1]),
        "height": int(square.shape[0]),
        "paper_bbox": list(bounds),
        "stats": stats,
    }


def rectify_for_web_json(image_bytes: bytes, corners_json: str) -> str:
    payload = rectify_for_web(image_bytes, json.loads(corners_json))
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
