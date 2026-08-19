from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from reconstructor import Settings, _adaptive_geometry_evidence, _decode_image, prepare_paper_square


def _bilinear(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    xs = np.clip(xs, 0.0, max(0.0, width - 1.001))
    ys = np.clip(ys, 0.0, max(0.0, height - 1.001))
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = xs - x0
    fy = ys - y0
    return (
        image[y0, x0] * (1.0 - fx) * (1.0 - fy)
        + image[y0, x1] * fx * (1.0 - fy)
        + image[y1, x0] * (1.0 - fx) * fy
        + image[y1, x1] * fx * fy
    )


def _segment_samples(anchor: Mapping[str, Any], spacing: float = 0.8) -> np.ndarray:
    rows: list[np.ndarray] = []
    for segment in list(anchor.get("formed_segments_px") or []):
        try:
            start = np.asarray(segment["start"][:2], dtype=float)
            end = np.asarray(segment["end"][:2], dtype=float)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length < 1.0:
            continue
        count = max(3, int(math.ceil(length / spacing)))
        parameters = np.linspace(0.06, 0.94, count)
        rows.append(start[None, :] + parameters[:, None] * delta[None, :])
    if not rows:
        return np.empty((0, 2), dtype=float)
    points = np.concatenate(rows, axis=0)
    if len(points) > 900:
        indices = np.linspace(0, len(points) - 1, 900).astype(np.int32)
        points = points[indices]
    return points


def estimate_observed_offsets(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: Mapping[str, Any],
) -> dict[int, dict[str, float]]:
    """Estimate each output ray's raster ridge without changing reconstruction.

    The current exact ray is shifted only along its normal and scored against
    the same adaptive line-confidence image used by reconstruction. This gives
    v2 a signed observed offset so alternative exact constructions can be
    compared to legacy geometry on equal image evidence.
    """

    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square, _, _ = prepare_paper_square(
        image,
        settings.analysis_size,
        settings.paper_corners,
    )
    _, confidence, evidence_stats = _adaptive_geometry_evidence(square)
    adaptive = float(evidence_stats.get("adaptive_evidence_distance_px", 1.75) or 1.75)
    search_radius = float(np.clip(max(2.2, adaptive * 1.8), 2.2, 4.0))
    deltas = np.linspace(-search_radius, search_radius, 81)

    output: dict[int, dict[str, float]] = {}
    for fallback, anchor in enumerate(list(result.get("playback_trace") or [])):
        if not isinstance(anchor, Mapping):
            continue
        try:
            trace_id = int(anchor.get("trace_id", fallback))
            angle = math.radians(float(anchor["angle"]))
            legacy_offset = float(anchor["line_offset_px"])
        except (KeyError, TypeError, ValueError):
            continue
        points = _segment_samples(anchor)
        if len(points) == 0:
            continue
        normal = np.array([-math.sin(angle), math.cos(angle)], dtype=float)
        shifted = points[None, :, :] + deltas[:, None, None] * normal[None, None, :]
        samples = _bilinear(
            confidence,
            shifted[:, :, 0].reshape(-1),
            shifted[:, :, 1].reshape(-1),
        ).reshape(len(deltas), len(points))
        means = np.mean(samples, axis=1)
        lower = np.quantile(samples, 0.25, axis=1)
        scores = 0.82 * means + 0.18 * lower
        best_index = int(np.argmax(scores))
        best_delta = float(deltas[best_index])
        output[trace_id] = {
            "observed_offset_px": legacy_offset + best_delta,
            "legacy_image_residual_px": abs(best_delta),
            "ridge_shift_px": best_delta,
            "ridge_score": float(scores[best_index]),
            "legacy_ridge_score": float(scores[int(np.argmin(np.abs(deltas)))]),
        }
    return output


def attach_observed_offsets(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
    result: dict[str, Any],
) -> dict[int, dict[str, float]]:
    estimates = estimate_observed_offsets(image_bytes, settings_mapping, result)
    for fallback, anchor in enumerate(list(result.get("playback_trace") or [])):
        if not isinstance(anchor, dict):
            continue
        try:
            trace_id = int(anchor.get("trace_id", fallback))
        except (TypeError, ValueError):
            continue
        item = estimates.get(trace_id)
        if item is not None:
            anchor.update(item)
    return estimates
