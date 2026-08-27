"""Independent boundary-contact evidence read directly from the source image.

The reconstruction trace is intentionally not an input to this module.  Each
paper side is scanned inward in the seven non-tangent legal 22.5-degree
directions.  Local maxima describe observed crease contacts; they are evidence
only and are not exact construction points by themselves.
"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

import numpy as np

from reconstructor import (
    ALLOWED_ANGLES,
    Settings,
    _adaptive_geometry_evidence,
    _decode_image,
    prepare_paper_square,
)
from shadow_evidence import _bilinear


_SIDE_LABELS = {
    "top": "上边",
    "right": "右边",
    "bottom": "下边",
    "left": "左边",
}


def _side_geometry(side: str, coordinate: np.ndarray, maximum: float) -> tuple[np.ndarray, np.ndarray]:
    if side == "top":
        boundary = np.column_stack((coordinate, np.zeros_like(coordinate)))
        inward = np.array([0.0, 1.0])
    elif side == "right":
        boundary = np.column_stack((np.full_like(coordinate, maximum), coordinate))
        inward = np.array([-1.0, 0.0])
    elif side == "bottom":
        boundary = np.column_stack((maximum - coordinate, np.full_like(coordinate, maximum)))
        inward = np.array([0.0, -1.0])
    elif side == "left":
        boundary = np.column_stack((np.zeros_like(coordinate), maximum - coordinate))
        inward = np.array([1.0, 0.0])
    else:
        raise ValueError(f"unknown paper side: {side}")
    return boundary, inward


def _point_on_side(side: str, coordinate: float, maximum: float) -> list[float]:
    if side == "top":
        return [coordinate, 0.0]
    if side == "right":
        return [maximum, coordinate]
    if side == "bottom":
        return [maximum - coordinate, maximum]
    if side == "left":
        return [0.0, maximum - coordinate]
    raise ValueError(f"unknown paper side: {side}")


def _scan_side(
    confidence: np.ndarray,
    side: str,
    *,
    stroke_radius_px: float,
    maximum_contacts: int,
) -> list[dict[str, Any]]:
    size = int(confidence.shape[0])
    maximum = float(size - 1)
    coordinates = np.arange(size, dtype=float)
    boundary, inward = _side_geometry(side, coordinates, maximum)

    start_distance = max(3.5, stroke_radius_px * 2.25 + 1.0)
    end_distance = float(np.clip(size * 0.055, 22.0, 34.0))
    distances = np.arange(start_distance, end_distance + 0.01, 0.5)
    lateral_radius = float(np.clip(stroke_radius_px + 0.6, 1.35, 2.4))
    lateral_offsets = np.linspace(-lateral_radius, lateral_radius, 5)

    profiles: list[dict[str, Any]] = []
    for orientation, theta in enumerate(ALLOWED_ANGLES):
        direction = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        if float(direction @ inward) < 0.0:
            direction = -direction
        inward_component = float(direction @ inward)
        # A tangent ray only measures the paper frame, not a crease entering
        # the paper.  The next legal direction is already 22.5 degrees inward.
        if inward_component < 0.20:
            continue
        normal = np.array([-direction[1], direction[0]], dtype=float)
        sampled_bands: list[np.ndarray] = []
        for lateral in lateral_offsets:
            points = (
                boundary[:, None, :]
                + distances[None, :, None] * direction[None, None, :]
                + lateral * normal[None, None, :]
            )
            sampled_bands.append(
                _bilinear(
                    confidence,
                    points[:, :, 0].reshape(-1),
                    points[:, :, 1].reshape(-1),
                ).reshape(size, len(distances))
            )
        samples = np.maximum.reduce(sampled_bands)
        mean = np.mean(samples, axis=1)
        lower = np.quantile(samples, 0.20, axis=1)
        support = np.mean(samples >= 0.18, axis=1)
        score = 0.52 * mean + 0.30 * support + 0.18 * lower
        profiles.append(
            {
                "orientation": orientation,
                "orientation_deg": round(math.degrees(theta), 6),
                "score": score,
                "mean": mean,
                "lower": lower,
                "support": support,
            }
        )

    if not profiles:
        return []
    score_matrix = np.stack([item["score"] for item in profiles], axis=0)
    best_profile = np.argmax(score_matrix, axis=0)
    best_score = score_matrix[best_profile, np.arange(size)]
    # Corners are exact paper primitives added by the catalog.  Their thick
    # frame junctions must not reappear as nearby raw crease contacts.
    corner_margin = max(6, int(round(size * 0.015)))
    peaks: list[dict[str, Any]] = []
    for index in range(corner_margin, size - corner_margin):
        left = max(corner_margin, index - 2)
        right = min(size - corner_margin, index + 3)
        if best_score[index] + 1e-9 < float(np.max(best_score[left:right])):
            continue
        profile = profiles[int(best_profile[index])]
        if (
            float(best_score[index]) < 0.16
            or float(profile["mean"][index]) < 0.16
            or float(profile["support"][index]) < 0.24
        ):
            continue
        peaks.append(
            {
                "coordinate": float(index),
                "score": float(best_score[index]),
                "mean": float(profile["mean"][index]),
                "lower": float(profile["lower"][index]),
                "support": float(profile["support"][index]),
                "orientation": int(profile["orientation"]),
                "orientation_deg": float(profile["orientation_deg"]),
            }
        )

    # A thick contact often creates two nearby maxima.  Keep the stronger one
    # within a stroke-scale cluster, while preserving genuinely separate short
    # contacts such as the two observations that support a trisection.
    cluster_distance = max(4.0, stroke_radius_px * 3.0)
    selected: list[dict[str, Any]] = []
    for peak in sorted(peaks, key=lambda item: (-item["score"], item["coordinate"])):
        if any(abs(peak["coordinate"] - old["coordinate"]) <= cluster_distance for old in selected):
            continue
        selected.append(peak)
    selected = sorted(selected[: max(1, int(maximum_contacts))], key=lambda item: item["coordinate"])

    output: list[dict[str, Any]] = []
    for order, peak in enumerate(selected, start=1):
        coordinate = float(peak["coordinate"])
        output.append(
            {
                "id": f"raw:{side}:{coordinate:.2f}:{peak['orientation']}",
                "label": f"{_SIDE_LABELS[side]}原图接触 {order}",
                "point": [round(value, 6) for value in _point_on_side(side, coordinate, maximum)],
                "side": side,
                "side_coordinate_px": round(coordinate, 6),
                "source": "raw_image_boundary_contact",
                "orientation": int(peak["orientation"]),
                "orientation_deg": float(peak["orientation_deg"]),
                "directional_score": round(float(peak["score"]), 6),
                "mean_confidence": round(float(peak["mean"]), 6),
                "lower_confidence": round(float(peak["lower"]), 6),
                "support_fraction": round(float(peak["support"]), 6),
            }
        )
    return output


def detect_boundary_contacts_from_confidence(
    confidence: np.ndarray,
    *,
    stroke_radius_px: float = 1.0,
    maximum_contacts_per_side: int = 48,
) -> list[dict[str, Any]]:
    """Detect observed paper-edge crease contacts from a confidence image."""

    values = np.asarray(confidence, dtype=np.float32)
    if values.ndim != 2 or values.shape[0] != values.shape[1] or values.shape[0] < 40:
        raise ValueError("boundary scan requires a square confidence image of at least 40 pixels")
    points: list[dict[str, Any]] = []
    for side in ("top", "right", "bottom", "left"):
        points.extend(
            _scan_side(
                values,
                side,
                stroke_radius_px=max(0.5, float(stroke_radius_px)),
                maximum_contacts=max(1, int(maximum_contacts_per_side)),
            )
        )
    return points


def extract_raw_boundary_contacts(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Prepare the paper and return boundary contacts without reading playback."""

    started = time.perf_counter()
    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square, _, preparation_stats = prepare_paper_square(
        image,
        settings.analysis_size,
        settings.paper_corners,
    )
    _, confidence, evidence_stats = _adaptive_geometry_evidence(square)
    stroke_radius = float(evidence_stats.get("estimated_stroke_radius_px", 1.0) or 1.0)
    points = detect_boundary_contacts_from_confidence(
        confidence,
        stroke_radius_px=stroke_radius,
    )
    side_counts = {
        side: sum(point.get("side") == side for point in points)
        for side in ("top", "right", "bottom", "left")
    }
    return {
        "enabled": True,
        "mode": "raw_boundary_directional_scan_v1",
        "source": "raw_image_directional_scan",
        "analysis_size": int(square.shape[0]),
        "maximum_coordinate_px": int(square.shape[0] - 1),
        "candidate_count": len(points),
        "side_candidate_counts": side_counts,
        "points": points,
        "scan_duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "evidence_stats": {
            "evidence_threshold": evidence_stats.get("evidence_threshold"),
            "evidence_contrast": evidence_stats.get("evidence_contrast"),
            "estimated_stroke_radius_px": stroke_radius,
        },
        "paper_preparation": preparation_stats,
    }


__all__ = [
    "detect_boundary_contacts_from_confidence",
    "extract_raw_boundary_contacts",
]
