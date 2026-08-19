from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

import numpy as np

from shadow_variant import _parse_cp


_NEAR_MISS_MIN_PX = 0.28
_NEAR_MISS_MAX_PX = 2.35
_DUPLICATE_MIN_PX = 0.28
_DUPLICATE_MAX_PX = 2.35
_MATCH_OFFSET_PX = 0.28
_CAMV_GEOMETRY_RADIUS_PX = 3.0


def _trace_id(anchor: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(anchor.get("trace_id", fallback))
    except (TypeError, ValueError):
        return fallback


def _orientation(start: np.ndarray, end: np.ndarray) -> int | None:
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= 1e-8:
        return None
    angle = math.atan2(float(delta[1]), float(delta[0])) % math.pi
    return int(round(angle / (math.pi / 8.0))) % 8


def _line_normal(orientation: int) -> np.ndarray:
    angle = orientation * math.pi / 8.0
    return np.array([-math.sin(angle), math.cos(angle)], dtype=float)


def _point_segment_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> tuple[float, float]:
    delta = end - start
    denominator = float(delta @ delta)
    if denominator <= 1e-12:
        return float(np.linalg.norm(point - start)), 0.0
    t = float(np.clip(((point - start) @ delta) / denominator, 0.0, 1.0))
    closest = start + delta * t
    return float(np.linalg.norm(point - closest)), t


def _row_offset(row: Mapping[str, Any]) -> tuple[int | None, float | None]:
    orientation = _orientation(row["start"], row["end"])
    if orientation is None:
        return None, None
    normal = _line_normal(orientation)
    return orientation, float(normal @ ((row["start"] + row["end"]) * 0.5))


def _anchor_offset(anchor: Mapping[str, Any]) -> tuple[int | None, float | None]:
    try:
        orientation = int(round(float(anchor["angle"]) / 22.5)) % 8
        offset = float(anchor["line_offset_px"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return orientation, offset


def _row_trace_ids(rows, trace) -> list[list[int]]:
    anchors = [item for item in trace if isinstance(item, Mapping)]
    anchor_geometry = []
    for fallback, anchor in enumerate(anchors):
        orientation, offset = _anchor_offset(anchor)
        if orientation is None or offset is None:
            continue
        anchor_geometry.append((_trace_id(anchor, fallback), orientation, offset))

    result: list[list[int]] = []
    for row in rows:
        orientation, offset = _row_offset(row)
        if orientation is None or offset is None:
            result.append([])
            continue
        matches = [
            trace_id
            for trace_id, anchor_orientation, anchor_offset in anchor_geometry
            if anchor_orientation == orientation and abs(anchor_offset - offset) <= _MATCH_OFFSET_PX
        ]
        result.append(matches)
    return result


def _overlap_ratio(first: Mapping[str, Any], second: Mapping[str, Any]) -> tuple[float, float]:
    delta = first["end"] - first["start"]
    length = float(np.linalg.norm(delta))
    if length <= 1e-8:
        return 0.0, 0.0
    unit = delta / length
    a0, a1 = sorted((float(unit @ first["start"]), float(unit @ first["end"])))
    b0, b1 = sorted((float(unit @ second["start"]), float(unit @ second["end"])))
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    denominator = max(1e-8, min(a1 - a0, b1 - b0))
    return overlap, overlap / denominator


def _near_misses(rows, row_traces, maximum: float) -> list[dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for first_index, first in enumerate(rows):
        first_orientation = _orientation(first["start"], first["end"])
        if first_orientation is None:
            continue
        for endpoint_name, point in (("start", first["start"]), ("end", first["end"])):
            if min(point[0], point[1], maximum - point[0], maximum - point[1]) <= 1.2:
                continue
            for second_index, second in enumerate(rows):
                if first_index == second_index:
                    continue
                second_orientation = _orientation(second["start"], second["end"])
                if second_orientation is None or second_orientation == first_orientation:
                    continue
                distance, parameter = _point_segment_distance(point, second["start"], second["end"])
                if not (_NEAR_MISS_MIN_PX < distance <= _NEAR_MISS_MAX_PX and 0.025 < parameter < 0.975):
                    continue
                key = (
                    round(float(point[0]), 2),
                    round(float(point[1]), 2),
                    min(first_index, second_index),
                    max(first_index, second_index),
                )
                item = {
                    "kind": "near_miss_endpoint",
                    "point_px": [round(float(point[0]), 6), round(float(point[1]), 6)],
                    "distance_px": round(distance, 6),
                    "endpoint": endpoint_name,
                    "row_indices": [first_index, second_index],
                    "trace_ids": sorted(set(row_traces[first_index] + row_traces[second_index])),
                }
                previous = result.get(key)
                if previous is None or item["distance_px"] < previous["distance_px"]:
                    result[key] = item
    return sorted(result.values(), key=lambda item: item["distance_px"])


def _duplicate_parallel(rows, row_traces) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for first_index, first in enumerate(rows):
        first_orientation, first_offset = _row_offset(first)
        if first_orientation is None or first_offset is None:
            continue
        for second_index in range(first_index + 1, len(rows)):
            second = rows[second_index]
            second_orientation, second_offset = _row_offset(second)
            if second_orientation != first_orientation or second_offset is None:
                continue
            separation = abs(first_offset - second_offset)
            if not (_DUPLICATE_MIN_PX < separation <= _DUPLICATE_MAX_PX):
                continue
            overlap, ratio = _overlap_ratio(first, second)
            if overlap < 6.0 or ratio < 0.35:
                continue
            midpoint = (
                first["start"] + first["end"] + second["start"] + second["end"]
            ) * 0.25
            result.append(
                {
                    "kind": "duplicate_parallel_ridge",
                    "point_px": [round(float(midpoint[0]), 6), round(float(midpoint[1]), 6)],
                    "separation_px": round(separation, 6),
                    "overlap_px": round(overlap, 6),
                    "overlap_ratio": round(ratio, 6),
                    "orientation": first_orientation,
                    "row_indices": [first_index, second_index],
                    "trace_ids": sorted(set(row_traces[first_index] + row_traces[second_index])),
                }
            )
    return sorted(result, key=lambda item: (item["separation_px"], -item["overlap_ratio"]))


def _incident_trace_ids(point: np.ndarray, rows, row_traces, tolerance: float = 0.5) -> list[int]:
    values: set[int] = set()
    for row_index, row in enumerate(rows):
        distance, _ = _point_segment_distance(point, row["start"], row["end"])
        if distance <= tolerance:
            values.update(row_traces[row_index])
    return sorted(values)


def _camv_diagnosis(stats: Mapping[str, Any], rows, row_traces, geometry_issues) -> list[dict[str, Any]]:
    structure = stats.get("camv_structure") if isinstance(stats.get("camv_structure"), Mapping) else {}
    violations = structure.get("violations") if isinstance(structure, Mapping) else []
    if not isinstance(violations, list):
        return []
    issue_points = [
        np.asarray(item["point_px"], dtype=float)
        for item in geometry_issues
        if isinstance(item.get("point_px"), (list, tuple)) and len(item["point_px"]) >= 2
    ]
    mv_mode = str(stats.get("mv_input_mode") or "")
    output: list[dict[str, Any]] = []
    for violation in violations:
        if not isinstance(violation, Mapping):
            continue
        raw_point = violation.get("point")
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
            continue
        point = np.asarray(raw_point[:2], dtype=float)
        rule = str(violation.get("rule") or "cAMV")
        near_geometry = any(float(np.linalg.norm(point - issue)) <= _CAMV_GEOMETRY_RADIUS_PX for issue in issue_points)
        if rule == "maekawa" and mv_mode != "color":
            cause = "mv_underdetermined"
            severity = 0.0
        elif near_geometry:
            cause = "reconstruction_geometry"
            severity = 3.0
        else:
            # cAMV remains a strong prior, but a clean isolated violation can
            # still be caused by a source omission or an off-grid crease.
            cause = "structural_unresolved"
            severity = 1.15
        output.append(
            {
                "rule": rule,
                "point_px": [round(float(point[0]), 6), round(float(point[1]), 6)],
                "cause": cause,
                "severity": severity,
                "trace_ids": _incident_trace_ids(point, rows, row_traces),
            }
        )
    return output


def build_quality_report_v5(result: Mapping[str, Any]) -> dict[str, Any]:
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        maximum = 0.0
    if maximum <= 0:
        return {"enabled": False, "mode": "quality_v5", "reason": "invalid_analysis_size"}

    rows = [row for row in _parse_cp(str(result.get("cp") or ""), maximum) if int(row["line_type"]) != 1]
    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    row_traces = _row_trace_ids(rows, trace)
    near_misses = _near_misses(rows, row_traces, maximum)
    duplicates = _duplicate_parallel(rows, row_traces)
    geometry_issues = near_misses + duplicates
    camv = _camv_diagnosis(result.get("stats") or {}, rows, row_traces, geometry_issues)

    penalties: dict[int, float] = defaultdict(float)
    for issue in near_misses:
        for trace_id in issue["trace_ids"]:
            penalties[int(trace_id)] += 2.4
    for issue in duplicates:
        for trace_id in issue["trace_ids"]:
            penalties[int(trace_id)] += 3.2
    for item in camv:
        for trace_id in item["trace_ids"]:
            penalties[int(trace_id)] += float(item["severity"])

    # Keep source incompleteness separate from false-positive geometry.  The
    # core currently exposes only a count for unresolved rays, so it remains a
    # search target signal rather than being blamed on any existing crease.
    stats = result.get("stats") or {}
    try:
        unresolved = int(stats.get("unresolved_rays", 0) or 0)
    except (TypeError, ValueError):
        unresolved = 0

    reconstruction_camv = sum(item["cause"] == "reconstruction_geometry" for item in camv)
    structural_unresolved = sum(item["cause"] == "structural_unresolved" for item in camv)
    geometry_score = 3.2 * len(duplicates) + 2.4 * len(near_misses) + 2.0 * reconstruction_camv
    structural_score = 1.15 * structural_unresolved
    return {
        "enabled": True,
        "mode": "quality_v5",
        "duplicate_parallel_count": len(duplicates),
        "near_miss_count": len(near_misses),
        "unresolved_observation_count": unresolved,
        "camv_reconstruction_geometry_count": reconstruction_camv,
        "camv_structural_unresolved_count": structural_unresolved,
        "geometry_error_score": round(geometry_score, 6),
        "structural_prior_score": round(structural_score, 6),
        "suspect_trace_penalties": {str(key): round(value, 6) for key, value in sorted(penalties.items())},
        "duplicate_parallel": duplicates[:32],
        "near_misses": near_misses[:48],
        "camv_diagnosis": camv,
        "notes": [
            "A strong raster match does not make a geometry hypothesis valid by itself.",
            "Near-miss endpoints and duplicate parallel ridges are treated as reconstruction-quality failures.",
            "Unresolved image evidence is tracked separately and never blamed on an already accepted crease.",
            "Clean isolated cAMV failures remain a strong structural prior, not a hard veto, because the source CP may be locally incomplete or off-grid.",
        ],
    }


__all__ = ["build_quality_report_v5"]
