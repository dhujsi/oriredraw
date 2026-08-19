from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from reconstructor import Settings, _adaptive_geometry_evidence, _decode_image, prepare_paper_square
from shadow_evidence import _bilinear
from shadow_variant import _parse_cp


_VERTEX_ROUND = 3
_SQUARE_VERTEX_TOLERANCE_PX = 1.25
_EXISTING_LINE_TOLERANCE_PX = 0.7


def _orientation(start: np.ndarray, end: np.ndarray) -> int | None:
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= 1e-7:
        return None
    angle = math.atan2(float(delta[1]), float(delta[0])) % math.pi
    raw = angle / (math.pi / 8.0)
    index = int(round(raw)) % 8
    snapped = index * math.pi / 8.0
    error = abs(((angle - snapped + math.pi / 2.0) % math.pi) - math.pi / 2.0)
    return index if error <= math.radians(0.6) else None


def _direction(orientation: int) -> np.ndarray:
    angle = orientation * math.pi / 8.0
    return np.array([math.cos(angle), math.sin(angle)], dtype=float)


def _normal(orientation: int) -> np.ndarray:
    d = _direction(orientation)
    return np.array([-d[1], d[0]], dtype=float)


def _vertex_key(point: np.ndarray) -> tuple[float, float]:
    return round(float(point[0]), _VERTEX_ROUND), round(float(point[1]), _VERTEX_ROUND)


def _nearest_vertex(point: np.ndarray, vertices: Mapping[tuple[float, float], np.ndarray]) -> np.ndarray | None:
    best = None
    for candidate in vertices.values():
        distance = float(np.linalg.norm(candidate - point))
        if distance <= _SQUARE_VERTEX_TOLERANCE_PX and (best is None or distance < best[0]):
            best = (distance, candidate)
    return None if best is None else best[1]


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


def _existing_same_line(rows, orientation: int, start: np.ndarray, end: np.ndarray) -> bool:
    normal = _normal(orientation)
    offset = float(normal @ ((start + end) * 0.5))
    for row in rows:
        if row["line_type"] == 1:
            continue
        row_orientation = _orientation(row["start"], row["end"])
        if row_orientation != orientation:
            continue
        row_offset = float(normal @ ((row["start"] + row["end"]) * 0.5))
        if abs(row_offset - offset) <= _EXISTING_LINE_TOLERANCE_PX:
            return True
    return False


def _inside_square(point: np.ndarray, a: np.ndarray, u: np.ndarray, v: np.ndarray, du: float, dv: float) -> bool:
    relative = point - a
    su = float(relative @ u) / du if abs(du) > 1e-9 else -1.0
    sv = float(relative @ v) / dv if abs(dv) > 1e-9 else -1.0
    return -0.03 <= su <= 1.03 and -0.03 <= sv <= 1.03


def _infer_line_type(
    square_image: np.ndarray,
    rows,
    orientation: int,
    start: np.ndarray,
    end: np.ndarray,
    a: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    du: float,
    dv: float,
) -> int:
    local_types = set()
    for row in rows:
        if row["line_type"] not in {2, 3}:
            continue
        if _orientation(row["start"], row["end"]) != orientation:
            continue
        midpoint = (row["start"] + row["end"]) * 0.5
        if _inside_square(midpoint, a, u, v, du, dv):
            local_types.add(int(row["line_type"]))
    if len(local_types) == 1:
        return next(iter(local_types))

    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= 1.0 or square_image.ndim != 3 or square_image.shape[2] < 3:
        return 0
    count = max(12, int(length))
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


def _square_candidates(rows) -> list[dict[str, Any]]:
    vertices: dict[tuple[float, float], np.ndarray] = {}
    incidence: dict[tuple[float, float], set[int]] = {}
    for row in rows:
        if row["line_type"] == 1:
            continue
        orientation = _orientation(row["start"], row["end"])
        if orientation is None:
            continue
        for point in (row["start"], row["end"]):
            key = _vertex_key(point)
            vertices.setdefault(key, point.copy())
            incidence.setdefault(key, set()).add(orientation)

    keys = list(vertices)
    found: dict[tuple[Any, ...], dict[str, Any]] = {}
    for first_index in range(len(keys)):
        first_key = keys[first_index]
        a = vertices[first_key]
        for second_index in range(first_index + 1, len(keys)):
            second_key = keys[second_index]
            b = vertices[second_key]
            diagonal = b - a
            diagonal_length = float(np.linalg.norm(diagonal))
            if not 12.0 <= diagonal_length <= 260.0:
                continue
            common = incidence.get(first_key, set()) & incidence.get(second_key, set())
            perpendicular_pairs = [
                (first, second)
                for first in common
                for second in common
                if first < second and (second - first) % 8 == 4
            ]
            for u_orientation, v_orientation in perpendicular_pairs:
                u = _direction(u_orientation)
                v = _direction(v_orientation)
                du = float(diagonal @ u)
                dv = float(diagonal @ v)
                if min(abs(du), abs(dv)) < 5.0:
                    continue
                if abs(abs(du) - abs(dv)) / max(abs(du), abs(dv)) > 0.08:
                    continue
                c_expected = a + du * u
                d_expected = a + dv * v
                c = _nearest_vertex(c_expected, vertices)
                d = _nearest_vertex(d_expected, vertices)
                if c is None or d is None:
                    continue
                signature = tuple(sorted(_vertex_key(point) for point in (a, b, c, d)))
                key = (signature, min(u_orientation, v_orientation), max(u_orientation, v_orientation))
                found[key] = {
                    "a": a.copy(),
                    "b": b.copy(),
                    "c": c.copy(),
                    "d": d.copy(),
                    "u": u,
                    "v": v,
                    "du": du,
                    "dv": dv,
                    "u_orientation": u_orientation,
                    "v_orientation": v_orientation,
                }
    return list(found.values())


def infer_isolated_square_ratio_segments(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    cp_text: str,
    *,
    max_candidates: int = 12,
) -> list[dict[str, Any]]:
    """Find locally isolated parallel creases from square-diagonal ratios.

    This pass runs after the main construction DAG.  It never divides the paper
    boundary.  Candidate geometry is generated from an already reconstructed
    square: the square diagonal may be trisected directly, or its half may be
    trisected after taking the midpoint (yielding 1/6 and 5/6 positions).  A
    candidate is retained only when the source raster contains strong line
    evidence at that exact location.
    """

    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 0) - 1)
    except (TypeError, ValueError):
        return []
    if maximum <= 0:
        return []
    rows = _parse_cp(cp_text, maximum)
    internal_rows = [row for row in rows if row["line_type"] != 1]
    if len(internal_rows) < 4:
        return []

    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square_image, _, _ = prepare_paper_square(image, settings.analysis_size, settings.paper_corners)
    _, confidence, _ = _adaptive_geometry_evidence(square_image)

    reference_scores = [
        _score_segment(confidence, row["start"], row["end"])["score"]
        for row in internal_rows
        if float(np.linalg.norm(row["end"] - row["start"])) >= 8.0
    ]
    if not reference_scores:
        return []
    evidence_floor = max(0.02, float(np.quantile(reference_scores, 0.35)) * 0.82)

    ratios = [
        (1.0 / 6.0, "1/6", "midpoint_then_trisection"),
        (1.0 / 3.0, "1/3", "segment_trisection"),
        (2.0 / 3.0, "2/3", "segment_trisection"),
        (5.0 / 6.0, "5/6", "midpoint_then_trisection"),
    ]
    candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for square in _square_candidates(rows):
        a = square["a"]
        b = square["b"]
        u = square["u"]
        v = square["v"]
        du = float(square["du"])
        dv = float(square["dv"])
        for t, ratio, derivation in ratios:
            options = [
                (
                    int(square["u_orientation"]),
                    a + t * dv * v,
                    a + du * u + t * dv * v,
                ),
                (
                    int(square["v_orientation"]),
                    a + t * du * u,
                    a + t * du * u + dv * v,
                ),
            ]
            for orientation, start, end in options:
                if _existing_same_line(rows, orientation, start, end):
                    continue
                evidence = _score_segment(confidence, start, end)
                if evidence["score"] < evidence_floor or evidence["contrast"] < -0.015:
                    continue
                line_type = _infer_line_type(
                    square_image,
                    rows,
                    orientation,
                    start,
                    end,
                    a,
                    u,
                    v,
                    du,
                    dv,
                )
                segment_key = (
                    orientation,
                    round(float(_normal(orientation) @ ((start + end) * 0.5)), 3),
                    round(float(np.linalg.norm(end - start)), 2),
                )
                item = {
                    "start": start.copy(),
                    "end": end.copy(),
                    "line_type": int(line_type),
                    "orientation": orientation,
                    "ratio": ratio,
                    "derivation": derivation,
                    "source_diagonal_start_px": [round(float(a[0]), 6), round(float(a[1]), 6)],
                    "source_diagonal_end_px": [round(float(b[0]), 6), round(float(b[1]), 6)],
                    "evidence_score": round(evidence["score"], 6),
                    "ridge_score": round(evidence["ridge"], 6),
                    "ridge_contrast": round(evidence["contrast"], 6),
                    "line_type_confident": line_type in {2, 3},
                }
                if segment_key not in candidates or item["evidence_score"] > candidates[segment_key]["evidence_score"]:
                    candidates[segment_key] = item

    ranked = sorted(candidates.values(), key=lambda item: (-item["evidence_score"], item["ratio"]))
    return ranked[:max_candidates]


__all__ = ["infer_isolated_square_ratio_segments"]
