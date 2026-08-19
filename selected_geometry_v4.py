from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from shadow_variant import _boundary_contact, _intersection, _line_geometry, _ray_offset


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


def _original_point(anchor: Mapping[str, Any]) -> np.ndarray | None:
    raw = anchor.get("anchor_point_px")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return np.asarray(raw[:2], dtype=float)
    except (TypeError, ValueError):
        return None


def _parents(anchor: Mapping[str, Any]) -> list[int]:
    raw = anchor.get("trace_parent_ids")
    if not isinstance(raw, (list, tuple)):
        return []
    values: list[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in values:
            values.append(value)
    return values


def _point_from_metadata(operation: Mapping[str, Any]) -> np.ndarray | None:
    for key in ("anchor_point_px", "reflected_point_px", "point_px"):
        raw = operation.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                return np.asarray(raw[:2], dtype=float)
            except (TypeError, ValueError):
                pass
    return None


def _selected_point(
    trace_id: int,
    anchors: Mapping[int, Mapping[str, Any]],
    points: Mapping[int, np.ndarray],
) -> np.ndarray | None:
    if trace_id in points:
        return points[trace_id]
    return _original_point(anchors.get(trace_id, {}))


def _reflect(point: np.ndarray, line) -> np.ndarray:
    _, normal, offset = line
    signed = float(normal @ point) - float(offset)
    return point - 2.0 * signed * normal


def _ratio_value(operation: Mapping[str, Any]) -> float | None:
    raw = str(operation.get("ratio") or "")
    if "/" not in raw:
        return None
    try:
        numerator, denominator = raw.split("/", 1)
        value = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return value if 0.0 < value < 1.0 else None


def resolve_selected_geometry_v4(
    result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[dict[int, float], dict[int, np.ndarray], list[int]]:
    """Resolve the selected proof DAG into actual ray offsets.

    Unlike the earlier shadow variant, legacy descendants are not frozen at
    their old offsets.  Once a parent ray changes, child intersections and
    paper-boundary contacts are recomputed from the selected parent geometry.
    Alternative point operations (corner, symmetry, segment ratio) also use the
    newest available parent point/axis whenever their metadata identifies them.
    """

    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    anchors = {_trace_id(anchor, index): anchor for index, anchor in enumerate(trace)}
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        maximum = 0.0
    selected = [item for item in list(report.get("selected_operations") or []) if isinstance(item, Mapping)]

    offsets: dict[int, float] = {}
    points: dict[int, np.ndarray] = {}
    unresolved: list[int] = []

    for operation in selected:
        if operation.get("target_trace_id") is None:
            continue
        try:
            trace_id = int(operation["target_trace_id"])
        except (TypeError, ValueError):
            continue
        anchor = anchors.get(trace_id)
        if anchor is None:
            continue
        orientation = _orientation(anchor)
        if orientation is None:
            unresolved.append(trace_id)
            continue
        provenance = str(operation.get("provenance") or "legacy")
        point: np.ndarray | None = None
        offset: float | None = None

        if provenance == "paper_corner_ray":
            point = _point_from_metadata(operation)

        elif provenance == "direct_point":
            try:
                source_id = int(operation["source_trace_id"])
            except (KeyError, TypeError, ValueError):
                source_id = -1
            point = _selected_point(source_id, anchors, points)

        elif provenance == "symmetry_point":
            try:
                source_id = int(operation["source_trace_id"])
                axis_id = int(operation["axis_trace_id"])
            except (KeyError, TypeError, ValueError):
                source_id = axis_id = -1
            source_point = _selected_point(source_id, anchors, points)
            axis_anchor = anchors.get(axis_id)
            axis_orientation = _orientation(axis_anchor or {})
            if source_point is not None and axis_anchor is not None and axis_orientation is not None:
                axis_offset = offsets.get(axis_id)
                if axis_offset is None and axis_anchor.get("line_offset_px") is not None:
                    axis_offset = float(axis_anchor["line_offset_px"])
                if axis_offset is not None:
                    point = _reflect(source_point, _line_geometry(axis_orientation, axis_offset))

        elif provenance == "segment_ratio_ray":
            ratio = _ratio_value(operation)
            raw_ids = operation.get("segment_endpoint_trace_ids")
            endpoint_ids = []
            if isinstance(raw_ids, (list, tuple)):
                for value in raw_ids[:2]:
                    try:
                        endpoint_ids.append(int(value))
                    except (TypeError, ValueError):
                        pass
            if ratio is not None and len(endpoint_ids) == 2:
                first = _selected_point(endpoint_ids[0], anchors, points)
                second = _selected_point(endpoint_ids[1], anchors, points)
                if first is not None and second is not None:
                    point = first + (second - first) * ratio

        elif provenance == "legacy":
            parent_ids = _parents(anchor)
            if len(parent_ids) >= 2:
                first_id, second_id = parent_ids[:2]
                first_anchor = anchors.get(first_id)
                second_anchor = anchors.get(second_id)
                first_orientation = _orientation(first_anchor or {})
                second_orientation = _orientation(second_anchor or {})
                if first_anchor is not None and second_anchor is not None and first_orientation is not None and second_orientation is not None:
                    first_offset = offsets.get(first_id)
                    second_offset = offsets.get(second_id)
                    if first_offset is None and first_anchor.get("line_offset_px") is not None:
                        first_offset = float(first_anchor["line_offset_px"])
                    if second_offset is None and second_anchor.get("line_offset_px") is not None:
                        second_offset = float(second_anchor["line_offset_px"])
                    if first_offset is not None and second_offset is not None:
                        point = _intersection(
                            _line_geometry(first_orientation, first_offset),
                            _line_geometry(second_orientation, second_offset),
                        )
            elif len(parent_ids) == 1 and "纸边交点" in str(anchor.get("source") or "") and maximum > 0:
                parent_id = parent_ids[0]
                parent_anchor = anchors.get(parent_id)
                parent_orientation = _orientation(parent_anchor or {})
                legacy_point = _original_point(anchor)
                if parent_anchor is not None and parent_orientation is not None and legacy_point is not None:
                    parent_offset = offsets.get(parent_id)
                    if parent_offset is None and parent_anchor.get("line_offset_px") is not None:
                        parent_offset = float(parent_anchor["line_offset_px"])
                    if parent_offset is not None:
                        point = _boundary_contact(
                            _line_geometry(parent_orientation, parent_offset),
                            legacy_point,
                            maximum,
                        )
            else:
                try:
                    offset = float(operation.get("candidate_offset_px", anchor["line_offset_px"]))
                except (KeyError, TypeError, ValueError):
                    offset = None
                point = _original_point(anchor)

        else:
            point = _point_from_metadata(operation)

        if point is not None:
            offset = _ray_offset(orientation, point)
            points[trace_id] = np.asarray(point, dtype=float)
        elif offset is None:
            try:
                offset = float(operation.get("candidate_offset_px", anchor["line_offset_px"]))
            except (KeyError, TypeError, ValueError):
                unresolved.append(trace_id)
                continue

        offsets[trace_id] = float(offset)

    # Preserve legacy geometry only for rays the selected proof did not touch.
    for trace_id, anchor in anchors.items():
        if trace_id in offsets:
            continue
        try:
            offsets[trace_id] = float(anchor["line_offset_px"])
        except (KeyError, TypeError, ValueError):
            unresolved.append(trace_id)
        original = _original_point(anchor)
        if original is not None and trace_id not in points:
            points[trace_id] = original

    return offsets, points, sorted(set(unresolved))


__all__ = ["resolve_selected_geometry_v4"]
