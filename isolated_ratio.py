from __future__ import annotations

import math
from typing import Any, Mapping

import cv2
import numpy as np

from reconstructor import Settings, _adaptive_geometry_evidence, _decode_image, prepare_paper_square
from shadow_evidence import _bilinear
from shadow_variant import _parse_cp


_EXISTING_LINE_TOLERANCE_PX = 0.72
_MIN_SOURCE_SEGMENT_PX = 8.0
_MIN_CANDIDATE_SEGMENT_PX = 6.0
_MIN_ATOMIC_INTERVAL_PX = 1.5
_AUX_LINE_BUCKET_PX = 0.45
_AUX_LINE_RESIDUAL_PX = 0.55
_AUX_MAX_LENGTH_FRACTION = 0.45
_MIN_OBSERVATION_LENGTH_PX = 6.0
_MAX_OBSERVATIONS = 80
_MAX_CANDIDATE_RAYS = 32
_OBSERVATION_ANGLE_TOLERANCE_DEG = 4.0
_OBSERVATION_LINE_SAMPLE_STEP_PX = 0.7
_OBSERVATION_GAP_SAMPLES = 5
_OBSERVATION_SUPPORT_THRESHOLD = 0.12
_OBSERVATION_MIN_COVERAGE = 0.45
_RATIO_POINT_SPAN_MARGIN_PX = 7.0
_CUT_MERGE_TOLERANCE_PX = 0.45


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
    """Legacy helper kept for tests/debugging; v11 recovery is observation-first."""
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


def _point_key(point: np.ndarray, digits: int = 3) -> tuple[float, float]:
    return round(float(point[0]), digits), round(float(point[1]), digits)


def _segment_key(start: np.ndarray, end: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    return tuple(sorted((_point_key(start), _point_key(end))))  # type: ignore[return-value]


def _unique_vertices(rows) -> list[np.ndarray]:
    vertices: dict[tuple[float, float], np.ndarray] = {}
    for row in rows:
        for point in (row["start"], row["end"]):
            vertices[_point_key(point)] = np.asarray(point, dtype=float)
    return list(vertices.values())


def _ratio_source_segments(rows, maximum: float) -> list[dict[str, Any]]:
    """Return real crease segments plus finite auxiliary segments between exact points.

    The auxiliary segment is not assumed to be a fold and no enclosing shape is
    detected. Existing exact vertices are grouped on canonical rays; adjacent
    point pairs on one ray may be divided just like a ruler segment.
    """
    output: list[dict[str, Any]] = []
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for index, row in enumerate(rows):
        start = np.asarray(row["start"], dtype=float)
        end = np.asarray(row["end"], dtype=float)
        length = float(np.linalg.norm(end - start))
        if not (_MIN_SOURCE_SEGMENT_PX <= length <= maximum * 0.75):
            continue
        key = _segment_key(start, end)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "start": start,
                "end": end,
                "kind": "crease_segment",
                "source_index": index,
                "cost": 1,
                "orientation": _orientation(start, end),
            }
        )

    vertices = _unique_vertices(rows)
    for orientation in range(8):
        direction = _direction(orientation)
        normal = _normal(orientation)
        groups: dict[int, list[tuple[float, np.ndarray, float]]] = {}
        for point in vertices:
            offset = float(normal @ point)
            bucket = int(round(offset / _AUX_LINE_BUCKET_PX))
            groups.setdefault(bucket, []).append((float(direction @ point), point, offset))
        for values in groups.values():
            values.sort(key=lambda item: item[0])
            for first, second in zip(values, values[1:]):
                if abs(first[2] - second[2]) > _AUX_LINE_RESIDUAL_PX:
                    continue
                start = first[1]
                end = second[1]
                length = float(np.linalg.norm(end - start))
                if not (_MIN_SOURCE_SEGMENT_PX <= length <= maximum * _AUX_MAX_LENGTH_FRACTION):
                    continue
                key = _segment_key(start, end)
                if key in seen:
                    continue
                seen.add(key)
                output.append(
                    {
                        "start": start.copy(),
                        "end": end.copy(),
                        "kind": "constructed_point_segment",
                        "source_index": None,
                        "cost": 2,
                        "orientation": orientation,
                    }
                )
    return output


def _ratio_points(source_segments, rows) -> list[dict[str, Any]]:
    ratios = [
        (1.0 / 6.0, "1/6", "midpoint_then_half_trisection"),
        (1.0 / 3.0, "1/3", "segment_trisection"),
        (2.0 / 3.0, "2/3", "segment_trisection"),
        (5.0 / 6.0, "5/6", "midpoint_then_half_trisection"),
    ]
    output: list[dict[str, Any]] = []
    for source in source_segments:
        delta = source["end"] - source["start"]
        for ratio_value, ratio, derivation in ratios:
            point = source["start"] + delta * ratio_value
            if _near_existing_vertex(point, rows):
                continue
            output.append(
                {
                    "point": point,
                    "ratio": ratio,
                    "derivation": derivation,
                    "source": source,
                }
            )
    return output


def _render_explained_mask(rows, shape: tuple[int, int], tolerance: float) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), dtype=np.uint8)
    thickness = max(3, int(math.ceil(tolerance * 2.0 + 1.0)))
    for row in rows:
        start = tuple(np.rint(row["start"]).astype(int))
        end = tuple(np.rint(row["end"]).astype(int))
        cv2.line(mask, start, end, 255, thickness, cv2.LINE_AA)
    return mask


def _paper_parameter_range(point: np.ndarray, direction: np.ndarray, maximum: float) -> tuple[float, float] | None:
    values = _paper_parameters(point, direction, maximum)
    if len(values) < 2:
        return None
    return min(values), max(values)


def _extend_observation_run(
    ink: np.ndarray,
    confidence: np.ndarray,
    orientation: int,
    observed_offset: float,
    hint_low: float,
    hint_high: float,
    maximum: float,
) -> dict[str, Any] | None:
    direction = _direction(orientation)
    normal = _normal(orientation)
    origin = normal * observed_offset
    limits = _paper_parameter_range(origin, direction, maximum)
    if limits is None:
        return None
    low, high = limits
    parameters = np.arange(low, high + _OBSERVATION_LINE_SAMPLE_STEP_PX, _OBSERVATION_LINE_SAMPLE_STEP_PX)
    points = origin[None, :] + parameters[:, None] * direction[None, :]
    ink_field = ink.astype(float) / 255.0
    sampled = np.maximum.reduce(
        [
            _bilinear(
                ink_field,
                points[:, 0] + normal[0] * shift,
                points[:, 1] + normal[1] * shift,
            )
            for shift in (-1.2, 0.0, 1.2)
        ]
    )
    active = (sampled >= _OBSERVATION_SUPPORT_THRESHOLD).astype(np.uint8)
    closed = cv2.morphologyEx(
        active.reshape(1, -1),
        cv2.MORPH_CLOSE,
        np.ones((1, _OBSERVATION_GAP_SAMPLES), dtype=np.uint8),
    ).reshape(-1).astype(bool)

    runs: list[dict[str, Any]] = []
    start_index: int | None = None
    for index, enabled in enumerate(closed):
        if enabled and start_index is None:
            start_index = index
        if start_index is None:
            continue
        if enabled and index != len(closed) - 1:
            continue
        end_index = index if enabled else index - 1
        if end_index < start_index:
            start_index = None
            continue
        t0 = float(parameters[start_index])
        t1 = float(parameters[end_index])
        if t1 - t0 >= _MIN_OBSERVATION_LENGTH_PX:
            overlap = max(0.0, min(t1, hint_high) - max(t0, hint_low))
            hint_length = max(_MIN_OBSERVATION_LENGTH_PX, hint_high - hint_low)
            runs.append(
                {
                    "t0": t0,
                    "t1": t1,
                    "hint_overlap": overlap / hint_length,
                }
            )
        start_index = None
    if not runs:
        return None
    selected = max(
        runs,
        key=lambda item: (
            item["hint_overlap"],
            item["t1"] - item["t0"],
        ),
    )
    start = origin + direction * selected["t0"]
    end = origin + direction * selected["t1"]
    evidence = _score_segment(confidence, start, end)
    return {
        "start": start,
        "end": end,
        "t0": selected["t0"],
        "t1": selected["t1"],
        **evidence,
    }


def _unexplained_observations(
    ink: np.ndarray,
    confidence: np.ndarray,
    square_image: np.ndarray,
    rows,
    maximum: float,
    construction_tolerance: float,
    evidence_floor: float,
) -> list[dict[str, Any]]:
    explained = _render_explained_mask(rows, ink.shape[:2], construction_tolerance)
    residual = cv2.bitwise_and(ink, cv2.bitwise_not(explained))
    # A slight dilation reconnects a residual ridge whose middle was removed by
    # an already-explained crossing crease. The exact candidate is still checked
    # later against the undilated confidence field.
    search = cv2.dilate(residual, np.ones((3, 3), dtype=np.uint8), iterations=1)
    lines = cv2.HoughLinesP(
        search,
        1.0,
        np.pi / 180.0,
        threshold=5,
        minLineLength=max(4, int(round(_MIN_OBSERVATION_LENGTH_PX))),
        maxLineGap=max(3, int(round(construction_tolerance + 1.0))),
    )
    if lines is None:
        return []

    candidates: dict[tuple[int, float], dict[str, Any]] = {}
    for raw in lines[:, 0, :]:
        start = np.array([float(raw[0]), float(raw[1])], dtype=float)
        end = np.array([float(raw[2]), float(raw[3])], dtype=float)
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length < _MIN_OBSERVATION_LENGTH_PX:
            continue
        angle = math.degrees(math.atan2(float(delta[1]), float(delta[0]))) % 180.0
        orientation = int(round(angle / 22.5)) % 8
        exact_angle = orientation * 22.5
        angular_error = abs(((angle - exact_angle + 90.0) % 180.0) - 90.0)
        if angular_error > _OBSERVATION_ANGLE_TOLERANCE_DEG:
            continue
        direction = _direction(orientation)
        normal = _normal(orientation)
        midpoint = (start + end) * 0.5
        observed_offset = float(normal @ midpoint)
        hint_low, hint_high = sorted((float(direction @ start), float(direction @ end)))
        run = _extend_observation_run(
            residual,
            confidence,
            orientation,
            observed_offset,
            hint_low,
            hint_high,
            maximum,
        )
        if run is None:
            continue
        run_length = float(run["t1"] - run["t0"])
        if run_length < _MIN_OBSERVATION_LENGTH_PX:
            continue
        coverage = min(1.0, run_length / max(length, 1.0))
        if coverage < _OBSERVATION_MIN_COVERAGE:
            continue
        evidence = _score_segment(confidence, run["start"], run["end"])
        if evidence["score"] < evidence_floor * 0.72:
            continue
        key = (orientation, round(observed_offset, 1))
        item = {
            "orientation": orientation,
            "observed_offset_px": observed_offset,
            "t0": float(run["t0"]),
            "t1": float(run["t1"]),
            "start": run["start"],
            "end": run["end"],
            "evidence_score": float(evidence["score"]),
            "ridge_score": float(evidence["ridge"]),
            "ridge_contrast": float(evidence["contrast"]),
            "line_type": _infer_line_type(square_image, run["start"], run["end"]),
        }
        previous = candidates.get(key)
        rank = item["evidence_score"] * max(1.0, item["t1"] - item["t0"])
        previous_rank = (
            previous["evidence_score"] * max(1.0, previous["t1"] - previous["t0"])
            if previous is not None
            else -math.inf
        )
        if rank > previous_rank:
            candidates[key] = item

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            -item["evidence_score"],
            -(item["t1"] - item["t0"]),
        ),
    )
    return ranked[:_MAX_OBSERVATIONS]


def _match_ratio_rays(
    observations,
    ratio_points,
    internal,
    construction_tolerance: float,
) -> list[dict[str, Any]]:
    rays: dict[tuple[int, float], dict[str, Any]] = {}
    for observation_index, observation in enumerate(observations):
        orientation = int(observation["orientation"])
        direction = _direction(orientation)
        normal = _normal(orientation)
        observed_offset = float(observation["observed_offset_px"])
        for ratio_point in ratio_points:
            source = ratio_point["source"]
            # Dividing a crease and then drawing the same infinite line again is
            # not a new explanation. Auxiliary point-segments are different:
            # they are construction rulers, not already-existing fold lines.
            if source["kind"] == "crease_segment" and source.get("orientation") == orientation:
                continue
            point = ratio_point["point"]
            exact_offset = float(normal @ point)
            normal_error = abs(exact_offset - observed_offset)
            if normal_error > construction_tolerance:
                continue
            point_parameter = float(direction @ point)
            if not (
                observation["t0"] - _RATIO_POINT_SPAN_MARGIN_PX
                <= point_parameter
                <= observation["t1"] + _RATIO_POINT_SPAN_MARGIN_PX
            ):
                continue
            key = (orientation, round(exact_offset, 2))
            support = {
                "point": point,
                "point_parameter": point_parameter,
                "ratio": ratio_point["ratio"],
                "derivation": ratio_point["derivation"],
                "source": source,
                "normal_error_px": normal_error,
                "observation_index": observation_index,
            }
            rank = (
                float(observation["evidence_score"])
                - 0.045 * normal_error
                - 0.02 * int(source.get("cost", 1))
                - (0.02 if ratio_point["ratio"] in {"1/6", "5/6"} else 0.0)
            )
            current = rays.get(key)
            if current is None:
                rays[key] = {
                    "orientation": orientation,
                    "offset_px": exact_offset,
                    "observations": [observation],
                    "supports": [support],
                    "rank": rank,
                    "best_support": support,
                }
                continue
            current["supports"].append(support)
            if all(id(item) != id(observation) for item in current["observations"]):
                current["observations"].append(observation)
            if rank > current["rank"]:
                current["rank"] = rank
                current["offset_px"] = exact_offset
                current["best_support"] = support

    return sorted(rays.values(), key=lambda item: -item["rank"])[:_MAX_CANDIDATE_RAYS]


def _ray_origin(ray: Mapping[str, Any]) -> np.ndarray:
    return _normal(int(ray["orientation"])) * float(ray["offset_px"])


def _ray_intersection_parameters(first: Mapping[str, Any], second: Mapping[str, Any]) -> tuple[float, float] | None:
    first_direction = _direction(int(first["orientation"]))
    second_direction = _direction(int(second["orientation"]))
    denominator = _cross(first_direction, second_direction)
    if abs(denominator) <= 1e-10:
        return None
    delta = _ray_origin(second) - _ray_origin(first)
    first_parameter = _cross(delta, second_direction) / denominator
    second_parameter = _cross(delta, first_direction) / denominator
    return float(first_parameter), float(second_parameter)


def _merge_cuts(cuts: list[tuple[str, float]]) -> list[tuple[str, float]]:
    cuts.sort(key=lambda item: item[1])
    merged: list[tuple[str, float]] = []
    for kind, value in cuts:
        if merged and abs(value - merged[-1][1]) <= _CUT_MERGE_TOLERANCE_PX:
            merged[-1] = (f"{merged[-1][0]}+{kind}", (merged[-1][1] + value) * 0.5)
        else:
            merged.append((kind, value))
    return merged


def _row_intersection_parameter(ray: Mapping[str, Any], row) -> float | None:
    origin = _ray_origin(ray)
    direction = _direction(int(ray["orientation"]))
    return _line_segment_parameter(origin, direction, row["start"], row["end"])


def _observation_evidence_on_interval(
    ray: Mapping[str, Any],
    low: float,
    high: float,
    confidence: np.ndarray,
) -> tuple[float, float, float, Mapping[str, Any] | None]:
    direction = _direction(int(ray["orientation"]))
    normal = _normal(int(ray["orientation"]))
    best: tuple[float, float, float, Mapping[str, Any] | None] = (0.0, 0.0, 0.0, None)
    for observation in ray["observations"]:
        overlap_low = max(low, float(observation["t0"]))
        overlap_high = min(high, float(observation["t1"]))
        if overlap_high - overlap_low <= 0.4:
            continue
        observed_offset = float(observation["observed_offset_px"])
        observed_origin = normal * observed_offset
        start = observed_origin + direction * overlap_low
        end = observed_origin + direction * overlap_high
        evidence = _score_segment(confidence, start, end)
        coverage = (overlap_high - overlap_low) / max(high - low, 1e-6)
        score = float(evidence["score"]) * min(1.0, coverage * 1.18)
        if score > best[0]:
            best = (score, float(evidence["ridge"]), coverage, observation)
    return best


def _support_for_interval(ray: Mapping[str, Any], low: float, high: float) -> Mapping[str, Any] | None:
    inside = [
        support
        for support in ray["supports"]
        if low - 0.5 <= float(support["point_parameter"]) <= high + 0.5
    ]
    if inside:
        # Prefer simpler thirds over sixths when both describe the same point.
        return min(
            inside,
            key=lambda item: (
                1 if item["ratio"] in {"1/6", "5/6"} else 0,
                float(item["normal_error_px"]),
                int(item["source"].get("cost", 1)),
            ),
        )
    return ray.get("best_support")


def _resolve_supported_segments(
    candidate_rays,
    rows,
    internal,
    square_image: np.ndarray,
    confidence: np.ndarray,
    maximum: float,
    evidence_floor: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ray_index, ray in enumerate(candidate_rays):
        direction = _direction(int(ray["orientation"]))
        origin = _ray_origin(ray)
        limits = _paper_parameter_range(origin, direction, maximum)
        if limits is None:
            continue
        cuts: list[tuple[str, float]] = [("paper", limits[0]), ("paper", limits[1])]
        for row in internal:
            parameter = _row_intersection_parameter(ray, row)
            if parameter is not None and limits[0] - 1e-6 <= parameter <= limits[1] + 1e-6:
                cuts.append(("crease", parameter))
        for other_index, other in enumerate(candidate_rays):
            if other_index == ray_index:
                continue
            parameters = _ray_intersection_parameters(ray, other)
            if parameters is None:
                continue
            parameter = parameters[0]
            if limits[0] - 1e-6 <= parameter <= limits[1] + 1e-6:
                cuts.append(("candidate", parameter))
        for support in ray["supports"]:
            parameter = float(support["point_parameter"])
            if limits[0] - 1e-6 <= parameter <= limits[1] + 1e-6:
                cuts.append(("ratio", parameter))
        merged = _merge_cuts(cuts)
        if len(merged) < 2:
            continue

        atomic: list[dict[str, Any]] = []
        for first, second in zip(merged, merged[1:]):
            low, high = float(first[1]), float(second[1])
            length = high - low
            if length < _MIN_ATOMIC_INTERVAL_PX:
                continue
            score, ridge, coverage, observation = _observation_evidence_on_interval(
                ray,
                low,
                high,
                confidence,
            )
            atomic.append(
                {
                    "low": low,
                    "high": high,
                    "length": length,
                    "score": score,
                    "ridge": ridge,
                    "coverage": coverage,
                    "observation": observation,
                    "supported": score >= evidence_floor * 0.68 and coverage >= 0.24,
                }
            )
        if not atomic:
            continue

        # Bridge one short interior structural interval between two strong
        # observed runs (usually a crossing crease removed from the residual).
        for index in range(1, len(atomic) - 1):
            item = atomic[index]
            if item["supported"] or item["length"] > 5.5:
                continue
            if atomic[index - 1]["supported"] and atomic[index + 1]["supported"]:
                item["supported"] = True
                item["bridged"] = True

        groups: list[list[dict[str, Any]]] = []
        active: list[dict[str, Any]] = []
        for item in atomic:
            if item["supported"]:
                active.append(item)
            elif active:
                groups.append(active)
                active = []
        if active:
            groups.append(active)

        for group in groups:
            low = float(group[0]["low"])
            high = float(group[-1]["high"])
            if high - low < _MIN_CANDIDATE_SEGMENT_PX:
                continue
            start = origin + direction * low
            end = origin + direction * high
            if _existing_same_line(internal, int(ray["orientation"]), start, end):
                continue
            evidence_scores = [float(item["score"]) for item in group if item["score"] > 0.0]
            if not evidence_scores:
                continue
            evidence_score = float(np.average(evidence_scores))
            support = _support_for_interval(ray, low, high)
            if support is None:
                continue
            observation = max(
                (item["observation"] for item in group if item.get("observation") is not None),
                key=lambda item: float(item.get("evidence_score", 0.0)),
                default=None,
            )
            if observation is not None:
                observed_offset = float(observation["observed_offset_px"])
                observed_origin = _normal(int(ray["orientation"])) * observed_offset
                observed_start = observed_origin + direction * low
                observed_end = observed_origin + direction * high
                line_type = _infer_line_type(square_image, observed_start, observed_end)
            else:
                line_type = 0
                observed_offset = float(ray["offset_px"])
            key = (
                int(ray["orientation"]),
                round(float(ray["offset_px"]), 2),
                round(low, 1),
                round(high, 1),
            )
            if key in seen:
                continue
            seen.add(key)
            source = support["source"]
            output.append(
                {
                    "start": start,
                    "end": end,
                    "line_type": line_type,
                    "orientation": int(ray["orientation"]),
                    "ratio": str(support["ratio"]),
                    "derivation": str(support["derivation"]),
                    "ratio_point_px": [
                        round(float(support["point"][0]), 6),
                        round(float(support["point"][1]), 6),
                    ],
                    "source_segment_index": source.get("source_index"),
                    "source_segment_kind": str(source.get("kind") or "segment"),
                    "source_segment_start_px": [
                        round(float(source["start"][0]), 6),
                        round(float(source["start"][1]), 6),
                    ],
                    "source_segment_end_px": [
                        round(float(source["end"][0]), 6),
                        round(float(source["end"][1]), 6),
                    ],
                    "evidence_score": round(evidence_score, 6),
                    "ridge_score": round(max(float(item["ridge"]) for item in group), 6),
                    "ridge_contrast": round(
                        max(
                            float(item.get("observation", {}).get("ridge_contrast", 0.0))
                            if isinstance(item.get("observation"), Mapping)
                            else 0.0
                            for item in group
                        ),
                        6,
                    ),
                    "observed_offset_px": round(observed_offset, 6),
                    "exact_offset_px": round(float(ray["offset_px"]), 6),
                    "raster_normal_error_px": round(
                        abs(float(ray["offset_px"]) - observed_offset),
                        6,
                    ),
                    "construction_rank": round(float(ray["rank"]), 6),
                }
            )
    return output


def infer_isolated_segment_ratio_segments(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    cp_text: str,
    *,
    max_candidates: int = 24,
) -> list[dict[str, Any]]:
    """Recover unresolved raster creases through exact segment-ratio points.

    The pass is observation-first. It first finds raster ridges not already
    explained by the current CP, then asks whether an exact line through a
    simple ratio point can explain each ridge. Ratio source segments include
    real crease segments and finite auxiliary segments between already-built
    exact vertices; no enclosing square or named region is assumed.

    Raster evidence may be parallel-shifted by the ordinary construction
    tolerance. That displacement remains evidence metadata only: emitted CP
    geometry always stays on the exact ratio-derived line.
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
    ink, confidence, _ = _adaptive_geometry_evidence(square_image)

    reference_scores = [
        _score_segment(confidence, row["start"], row["end"])["score"]
        for row in internal
        if float(np.linalg.norm(row["end"] - row["start"])) >= _MIN_SOURCE_SEGMENT_PX
    ]
    if not reference_scores:
        return []
    evidence_floor = max(0.025, float(np.quantile(reference_scores, 0.42)) * 0.86)
    construction_tolerance = float(settings.construction_offset_tolerance_px)

    observations = _unexplained_observations(
        ink,
        confidence,
        square_image,
        rows,
        maximum,
        construction_tolerance,
        evidence_floor,
    )
    if not observations:
        return []

    source_segments = _ratio_source_segments(internal, maximum)
    ratio_points = _ratio_points(source_segments, internal)
    if not ratio_points:
        return []

    candidate_rays = _match_ratio_rays(
        observations,
        ratio_points,
        internal,
        construction_tolerance,
    )
    if not candidate_rays:
        return []

    segments = _resolve_supported_segments(
        candidate_rays,
        rows,
        internal,
        square_image,
        confidence,
        maximum,
        evidence_floor,
    )
    ranked = sorted(
        segments,
        key=lambda item: (
            -float(item["evidence_score"]),
            -float(item["construction_rank"]),
            -float(np.linalg.norm(item["end"] - item["start"])),
            str(item["ratio"]),
        ),
    )
    return ranked[:max_candidates]


# Compatibility alias for older callers while the public branch is still named
# construction-search-v2.
def infer_isolated_square_ratio_segments(*args, **kwargs):
    return infer_isolated_segment_ratio_segments(*args, **kwargs)


__all__ = [
    "infer_isolated_segment_ratio_segments",
    "infer_isolated_square_ratio_segments",
    "_candidate_span",
    "_ratio_source_segments",
    "_ratio_points",
    "_match_ratio_rays",
]
