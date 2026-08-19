from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from reconstructor import Settings, _adaptive_geometry_evidence, _decode_image, prepare_paper_square
from shadow_evidence import _bilinear
from shadow_variant import _parse_cp


_EXISTING_LINE_TOLERANCE_PX = 0.72
_MIN_SOURCE_SEGMENT_PX = 8.0
_MIN_CANDIDATE_SEGMENT_PX = 6.0


def _orientation(start: np.ndarray, end: np.ndarray) -> int | None:
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= 1e-8:
        return None
    angle = math.atan2(float(delta[1]), float(delta[0])) % math.pi
    return int(round(angle / (math.pi / 8.0))) % 8


def _direction(orientation: int) -> np.ndarray:
    angle = orientation * math.pi / 8.0
    return np.array([math.cos(angle), math.sin(angle)], dtype=float)


def _normal(orientation: int) -> np.ndarray:
    direction = _direction(orientation)
    return np.array([-direction[1], direction[0]], dtype=float)


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _score_segment(confidence: np.ndarray, start: np.ndarray, end: np.ndarray) -> dict[str, float]:
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= 2.0:
        return {"score": 0.0, "ridge": 0.0, "lower": 0.0, "contrast": 0.0}
    count = max(12, int(math.ceil(length / 0.85)))
    parameters = np.linspace(0.06, 0.94, count)
    points = start[None, :] + parameters[:, None] * delta[None, :]
    unit = delta / length
    normal = np.array([-unit[1], unit[0]], dtype=float)
    center = _bilinear(confidence, points[:, 0], points[:, 1])
    left = _bilinear(confidence, points[:, 0] + normal[0] * 2.0, points[:, 1] + normal[1] * 2.0)
    right = _bilinear(confidence, points[:, 0] - normal[0] * 2.0, points[:, 1] - normal[1] * 2.0)
    ridge = float(np.mean(center))
    lower = float(np.quantile(center, 0.25))
    contrast = float(np.mean(center - 0.5 * (left + right)))
    score = 0.78 * ridge + 0.22 * lower + 0.30 * max(0.0, contrast)
    return {"score": score, "ridge": ridge, "lower": lower, "contrast": contrast}


def _line_segment_parameter(
    point: np.ndarray,
    direction: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
) -> float | None:
    segment_direction = end - start
    denominator = _cross(direction, segment_direction)
    if abs(denominator) <= 1e-10:
        return None
    delta = start - point
    t = _cross(delta, segment_direction) / denominator
    u = _cross(delta, direction) / denominator
    if -1e-7 <= u <= 1.0 + 1e-7:
        return float(t)
    return None


def _paper_parameters(point: np.ndarray, direction: np.ndarray, maximum: float) -> list[float]:
    values: list[float] = []
    for axis in (0, 1):
        component = float(direction[axis])
        if abs(component) <= 1e-10:
            continue
        for boundary in (0.0, maximum):
            t = (boundary - float(point[axis])) / component
            q = point + direction * t
            other = 1 - axis
            if -1e-7 <= q[other] <= maximum + 1e-7:
                values.append(float(t))
    return values


def _candidate_span(
    point: np.ndarray,
    orientation: int,
    rows,
    maximum: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    direction = _direction(orientation)
    parameters = _paper_parameters(point, direction, maximum)
    for row in rows:
        parameter = _line_segment_parameter(point, direction, row["start"], row["end"])
        if parameter is not None and abs(parameter) > 0.45:
            parameters.append(parameter)
    negative = [value for value in parameters if value < -0.45]
    positive = [value for value in parameters if value > 0.45]
    if not negative or not positive:
        return None
    low = max(negative)
    high = min(positive)
    start = point + direction * low
    end = point + direction * high
    if float(np.linalg.norm(end - start)) < _MIN_CANDIDATE_SEGMENT_PX:
        return None
    return start, end


def _overlap_length(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
) -> float:
    delta = first_end - first_start
    length = float(np.linalg.norm(delta))
    if length <= 1e-8:
        return 0.0
    unit = delta / length
    a0, a1 = sorted((float(unit @ first_start), float(unit @ first_end)))
    b0, b1 = sorted((float(unit @ second_start), float(unit @ second_end)))
    return max(0.0, min(a1, b1) - max(a0, b0))


def _existing_same_line(rows, orientation: int, start: np.ndarray, end: np.ndarray) -> bool:
    normal = _normal(orientation)
    offset = float(normal @ ((start + end) * 0.5))
    for row in rows:
        if int(row["line_type"]) == 1:
            continue
        if _orientation(row["start"], row["end"]) != orientation:
            continue
        row_offset = float(normal @ ((row["start"] + row["end"]) * 0.5))
        if abs(row_offset - offset) > _EXISTING_LINE_TOLERANCE_PX:
            continue
        if _overlap_length(start, end, row["start"], row["end"]) >= 3.0:
            return True
    return False


def _near_existing_vertex(point: np.ndarray, rows, tolerance: float = 0.85) -> bool:
    for row in rows:
        if float(np.linalg.norm(point - row["start"])) <= tolerance:
            return True
        if float(np.linalg.norm(point - row["end"])) <= tolerance:
            return True
    return False


def _infer_line_type(square_image: np.ndarray, start: np.ndarray, end: np.ndarray) -> int:
    if square_image.ndim != 3 or square_image.shape[2] < 3:
        return 0
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= 1.0:
        return 0
    count = max(12, min(240, int(length)))
    parameters = np.linspace(0.08, 0.92, count)
    points = start[None, :] + parameters[:, None] * delta[None, :]
    channels = [
        float(np.mean(_bilinear(square_image[:, :, channel].astype(float), points[:, 0], points[:, 1])))
        for channel in range(3)
    ]
    blue, green, red = channels
    if red - max(green, blue) >= 12.0:
        return 2
    if blue - max(green, red) >= 12.0:
        return 3
    return 0


def infer_isolated_segment_ratio_segments(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    cp_text: str,
    *,
    max_candidates: int = 16,
) -> list[dict[str, Any]]:
    """Recover image-supported creases through ratio points of known segments.

    No square, paper-edge division, or named region is assumed.  Every finite
    reconstructed segment can supply simple ratio points.  Through each point
    the pass tries canonical fold directions, clips the candidate between the
    nearest already-constructed intersections, and admits it only when the
    original raster supports the resulting finite line.
    """

    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return []
    if maximum <= 0:
        return []
    rows = _parse_cp(cp_text, maximum)
    internal = [row for row in rows if int(row["line_type"]) != 1]
    if not internal:
        return []

    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square_image, _, _ = prepare_paper_square(image, settings.analysis_size, settings.paper_corners)
    _, confidence, _ = _adaptive_geometry_evidence(square_image)

    reference_scores = [
        _score_segment(confidence, row["start"], row["end"])["score"]
        for row in internal
        if float(np.linalg.norm(row["end"] - row["start"])) >= 8.0
    ]
    if not reference_scores:
        return []
    evidence_floor = max(0.025, float(np.quantile(reference_scores, 0.42)) * 0.86)

    ratios = [
        (1.0 / 6.0, "1/6", "midpoint_then_half_trisection"),
        (1.0 / 3.0, "1/3", "segment_trisection"),
        (2.0 / 3.0, "2/3", "segment_trisection"),
        (5.0 / 6.0, "5/6", "midpoint_then_half_trisection"),
    ]
    candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source_index, source in enumerate(internal):
        source_delta = source["end"] - source["start"]
        source_length = float(np.linalg.norm(source_delta))
        if not (_MIN_SOURCE_SEGMENT_PX <= source_length <= maximum * 0.75):
            continue
        source_orientation = _orientation(source["start"], source["end"])
        for ratio_value, ratio, derivation in ratios:
            point = source["start"] + source_delta * ratio_value
            if _near_existing_vertex(point, internal):
                continue
            for orientation in range(8):
                if orientation == source_orientation:
                    continue
                span = _candidate_span(point, orientation, internal, maximum)
                if span is None:
                    continue
                start, end = span
                if _existing_same_line(internal, orientation, start, end):
                    continue
                evidence = _score_segment(confidence, start, end)
                if evidence["score"] < evidence_floor or evidence["contrast"] < -0.01:
                    continue
                normal = _normal(orientation)
                offset = float(normal @ point)
                key = (
                    orientation,
                    round(offset, 2),
                    round(float(point[0]), 1),
                    round(float(point[1]), 1),
                )
                item = {
                    "start": start.copy(),
                    "end": end.copy(),
                    "line_type": _infer_line_type(square_image, start, end),
                    "orientation": orientation,
                    "ratio": ratio,
                    "derivation": derivation,
                    "ratio_point_px": [round(float(point[0]), 6), round(float(point[1]), 6)],
                    "source_segment_index": source_index,
                    "source_segment_start_px": [round(float(source["start"][0]), 6), round(float(source["start"][1]), 6)],
                    "source_segment_end_px": [round(float(source["end"][0]), 6), round(float(source["end"][1]), 6)],
                    "evidence_score": round(evidence["score"], 6),
                    "ridge_score": round(evidence["ridge"], 6),
                    "ridge_contrast": round(evidence["contrast"], 6),
                }
                previous = candidates.get(key)
                if previous is None or item["evidence_score"] > previous["evidence_score"]:
                    candidates[key] = item

    ranked = sorted(
        candidates.values(),
        key=lambda item: (-item["evidence_score"], -item["ridge_contrast"], item["ratio"]),
    )
    return ranked[:max_candidates]


# Compatibility alias for the v4 caller while the public branch is still named
# construction-search-v2.
def infer_isolated_square_ratio_segments(*args, **kwargs):
    return infer_isolated_segment_ratio_segments(*args, **kwargs)


__all__ = ["infer_isolated_segment_ratio_segments", "infer_isolated_square_ratio_segments"]
