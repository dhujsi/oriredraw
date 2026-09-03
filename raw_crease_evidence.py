"""Observed 22.5-degree crease entities read directly from source pixels.

This module deliberately stops before construction reasoning.  It classifies
finite raster strokes by their observed legal direction, merges the two sides
of the same drawn stroke, and preserves every visible interval on the resulting
infinite line identity.  It never reads playback history, fits algebraic
anchors, invents a direction, or propagates a construction.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from typing import Any, Mapping

import cv2
import numpy as np

from reconstructor import (
    ALLOWED_ANGLES,
    Edge,
    Settings,
    _adaptive_geometry_evidence,
    _angle_admission_tolerance_deg,
    _boundary_hits,
    _closest_orientation,
    _color_geometry_masks,
    _decode_image,
    _directional_projection_segments,
    _edge_mv_evidence,
    _has_diffuse_color_bleed,
    _refine_centerline_offset,
    prepare_paper_square,
)


def _normalise_signal(signal: np.ndarray) -> np.ndarray:
    values = np.asarray(signal, dtype=np.float32)
    floor = float(np.percentile(values, 50.0))
    high = float(np.percentile(values, 99.9))
    if high <= floor + 1e-6:
        return np.zeros(values.shape, dtype=np.float32)
    return np.clip((values - floor) / (high - floor), 0.0, 1.0).astype(
        np.float32
    )


def _geometry_channels(
    square: np.ndarray,
    ink: np.ndarray,
    confidence: np.ndarray,
    color_guard_radius_px: float,
) -> list[dict[str, Any]]:
    """Pair color and neutral geometry masks with centerline signals.

    Red/blue masks keep differently colored crossings separable.  They are not
    allowed to become a geometry gate: a mixed CP can also contain ordinary
    black or gray crease strokes.  The neutral mask therefore comes from the
    general ink response after removing a small guard band around colored
    strokes.  Gaps introduced at crossings are joined later as finite
    intervals on the same geometric line.
    """

    values = square.astype(np.float32)
    blue, green, red = cv2.split(values)
    red_signal = _normalise_signal(
        np.maximum(red - np.maximum(blue, green), 0.0)
    )
    blue_signal = _normalise_signal(
        np.maximum(blue - np.maximum(red, green), 0.0)
    )
    ridge_signal = np.asarray(confidence, dtype=np.float32)

    channels: list[dict[str, Any]] = []
    color_masks: list[np.ndarray] = []
    for mask in _color_geometry_masks(square, ink):
        selected = mask > 0
        if not np.any(selected):
            continue
        red_score = float(np.mean(red_signal[selected]))
        blue_score = float(np.mean(blue_signal[selected]))
        if red_score >= 0.12 and red_score >= blue_score * 1.35:
            label, signal = "red", red_signal
        elif blue_score >= 0.12 and blue_score >= red_score * 1.35:
            label, signal = "blue", blue_signal
        else:
            label, signal = "monochrome", ridge_signal
        channels.append({"label": label, "mask": mask, "signal": signal})
        if label in {"red", "blue"}:
            color_masks.append(mask)

    if color_masks:
        chroma = np.max(values, axis=2) - np.min(values, axis=2)
        # Light neutral strokes can be weaker than the binary ink threshold
        # when saturated red/blue lines set the image-wide contrast scale.
        # Keep their continuous ridge response here; legal direction, minimum
        # length, and paper-frame rejection still validate the observation.
        neutral = (ridge_signal >= 0.12) & (chroma <= 24.0)
        color_union = np.maximum.reduce(
            [(mask > 0).astype(np.uint8) for mask in color_masks]
        )
        # Compression can leave alternating warm/cool flecks on an otherwise
        # neutral gray stroke.  Those weak flecks may enter an adaptive color
        # mask, but they must not grow a guard band that erases the gray line.
        # Require the same material chroma used by input validation before a
        # pixel is allowed to protect a genuinely colored stroke.
        color_union &= (chroma >= 24.0).astype(np.uint8)
        guard_radius = int(
            np.clip(math.ceil(float(color_guard_radius_px) + 0.25), 1, 5)
        )
        guard_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (guard_radius * 2 + 1, guard_radius * 2 + 1),
        )
        color_guard = cv2.dilate(color_union, guard_kernel)
        neutral &= color_guard == 0
        neutral_mask = neutral.astype(np.uint8) * 255
        if int(np.count_nonzero(neutral_mask)) >= 20:
            channels.append(
                {
                    "label": "monochrome",
                    "mask": neutral_mask,
                    "signal": ridge_signal,
                }
            )
    return channels


def _extract_finite_segments(
    square: np.ndarray,
    ink: np.ndarray,
    confidence: np.ndarray,
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return finite source observations before line clustering."""

    diffuse_input = _has_diffuse_color_bleed(square)
    rejected_angle = 0
    rejected_border = 0
    rejected_short = 0
    center_shifts: list[float] = []
    raw: list[dict[str, Any]] = []
    noncanonical_observations: list[dict[str, Any]] = []

    channels = _geometry_channels(
        square,
        ink,
        confidence,
        settings.evidence_distance_px,
    )
    if diffuse_input:
        projected = _directional_projection_segments(square, settings)
        for item in projected:
            raw.append(
                {
                    "channel": "red" if int(item["mask"]) == 0 else "blue",
                    "orientation": int(item["orientation"]),
                    "offset": float(item["offset"]),
                    "measured_offset": float(item["offset"]),
                    "length": float(item["length"]),
                    "start": np.asarray(item["start"], dtype=float),
                    "end": np.asarray(item["end"], dtype=float),
                    "angle_error_deg": 0.0,
                    "center_response": float(item.get("center_response", 0.0)),
                }
            )
        lsd_channels = [
            channel for channel in channels if channel["label"] == "monochrome"
        ]
        detector_kind = (
            "exact_direction_projection_with_neutral_lsd"
            if lsd_channels
            else "exact_direction_projection"
        )
    else:
        lsd_channels = channels
        detector_kind = "finite_lsd_centerlines"

    first_lsd_item = len(raw)
    if lsd_channels:
        detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_ADV)
        for channel in lsd_channels:
            detected = detector.detect(channel["mask"])[0]
            if detected is None:
                continue
            for values in detected[:, 0]:
                start = np.asarray(values[:2], dtype=float)
                end = np.asarray(values[2:], dtype=float)
                delta = end - start
                length = float(np.linalg.norm(delta))
                orientation, angle_error = _closest_orientation(
                    math.atan2(float(delta[1]), float(delta[0]))
                )
                angle_error_deg = math.degrees(angle_error)
                if length < 3.0:
                    rejected_short += 1
                    continue
                admission_tolerance = _angle_admission_tolerance_deg(
                    length,
                    settings,
                )
                if angle_error_deg > admission_tolerance:
                    rejected_angle += 1
                    measured_angle = math.degrees(
                        math.atan2(float(delta[1]), float(delta[0]))
                    ) % 180.0
                    noncanonical_observations.append(
                        {
                            "source": "raw_image_noncanonical_finite_stroke_observation",
                            "channel": str(channel["label"]),
                            "start_px": [round(float(value), 6) for value in start],
                            "end_px": [round(float(value), 6) for value in end],
                            "length_px": round(length, 6),
                            "observed_angle_deg": round(measured_angle, 6),
                            "nearest_22_5_direction_index": int(orientation),
                            "nearest_22_5_angle_deg": round(orientation * 22.5, 6),
                            "angle_residual_deg": round(angle_error_deg, 6),
                            "angle_admission_tolerance_deg": round(
                                admission_tolerance,
                                6,
                            ),
                        }
                    )
                    continue
                theta = ALLOWED_ANGLES[orientation]
                direction = np.array(
                    [math.cos(theta), math.sin(theta)], dtype=float
                )
                normal = np.array(
                    [-direction[1], direction[0]], dtype=float
                )
                measured_offset = float(normal @ ((start + end) / 2.0))
                centered_offset, center_response = _refine_centerline_offset(
                    channel["signal"],
                    start,
                    end,
                    orientation,
                    measured_offset,
                    search_radius=float(
                        np.clip(settings.evidence_distance_px * 2.0 + 1.0, 3.0, 5.5)
                    ),
                )
                center_shifts.append(abs(centered_offset - measured_offset))
                raw.append(
                    {
                        "channel": channel["label"],
                        "orientation": orientation,
                        "offset": centered_offset,
                        "measured_offset": measured_offset,
                        "length": length,
                        "start": start,
                        "end": end,
                        "angle_error_deg": angle_error_deg,
                        "center_response": float(center_response),
                    }
                )

    if lsd_channels:
        # On a native one-pixel drawing a tiny image-wide correction is mostly
        # staircase noise.  A real two-sided band yields a materially larger
        # common movement toward its center, as in the existing precision path.
        nonzero_shifts = [value for value in center_shifts if value > 1e-6]
        mean_shift = float(np.mean(nonzero_shifts)) if nonzero_shifts else 0.0
        if mean_shift < 1.4:
            for item in raw[first_lsd_item:]:
                item["offset"] = item["measured_offset"]
            center_shifts = []
    channel_count = len(channels)

    size = int(square.shape[0])
    maximum = float(size - 1)
    stroke_radius = max(0.75, settings.evidence_distance_px - 0.75)
    border_tolerance = max(3.0, stroke_radius * 3.0 + 0.5)
    kept: list[dict[str, Any]] = []
    for item in raw:
        orientation = int(item["orientation"])
        offset = float(item["offset"])
        is_frame = (
            orientation == 0
            and min(abs(offset), abs(offset - maximum)) <= border_tolerance
        ) or (
            orientation == 4
            and min(abs(offset), abs(offset + maximum)) <= border_tolerance
        )
        if is_frame:
            rejected_border += 1
            continue
        kept.append(item)

    ordered_noncanonical = sorted(
        noncanonical_observations,
        key=lambda item: (
            -float(item["length_px"]),
            float(item["observed_angle_deg"]),
            item["start_px"],
            item["end_px"],
            item["channel"],
        ),
    )[:512]
    for index, item in enumerate(ordered_noncanonical, start=1):
        item["id"] = f"noncanonical-stroke:{index}"

    return kept, ordered_noncanonical, {
        "detector": detector_kind,
        "diffuse_input": bool(diffuse_input),
        "geometry_channel_count": channel_count,
        "neutral_geometry_channel_count": sum(
            channel["label"] == "monochrome" for channel in channels
        ),
        "accepted_finite_segments": len(kept),
        "angle_rejected_segments": rejected_angle,
        "short_rejected_segments": rejected_short,
        "retained_noncanonical_observation_count": len(ordered_noncanonical),
        "noncanonical_observation_limit": 512,
        "border_rejected_segments": rejected_border,
        "centered_segment_count": len(center_shifts),
        "mean_center_shift_px": round(
            float(np.mean(center_shifts)) if center_shifts else 0.0, 6
        ),
        "max_center_shift_px": round(
            max(center_shifts, default=0.0), 6
        ),
    }


def _merge_intervals(
    intervals: list[list[float]], gap_tolerance: float
) -> list[list[float]]:
    merged: list[list[float]] = []
    for first, second in sorted(intervals, key=lambda item: item[0]):
        if not merged or first - merged[-1][1] > gap_tolerance:
            merged.append([float(first), float(second)])
        else:
            merged[-1][1] = max(merged[-1][1], float(second))
    return merged


def _line_basis(orientation: int) -> tuple[np.ndarray, np.ndarray]:
    theta = ALLOWED_ANGLES[orientation]
    direction = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    return direction, np.array([-direction[1], direction[0]], dtype=float)


def _cluster_finite_segments(
    raw: list[dict[str, Any]],
    size: int,
    confidence: np.ndarray,
    settings: Settings,
    *,
    diffuse_input: bool,
) -> list[dict[str, Any]]:
    """Merge raster observations into geometric line identities."""

    stroke_radius = max(0.75, settings.evidence_distance_px - 0.75)
    rho_tolerance = float(np.clip(stroke_radius * 1.5 + 1.5, 2.6, 4.0))
    interval_gap = float(np.clip(stroke_radius * 3.5 + 2.5, 5.0, 10.0))
    clusters: list[dict[str, Any]] = []

    channels = sorted({str(item["channel"]) for item in raw})
    for channel in channels:
        for orientation in range(8):
            indices = sorted(
                (
                    index
                    for index, item in enumerate(raw)
                    if item["channel"] == channel
                    and int(item["orientation"]) == orientation
                ),
                key=lambda index: float(raw[index]["offset"]),
            )
            local: list[dict[str, Any]] = []
            for index in indices:
                item = raw[index]
                nearest = min(
                    (
                        (
                            abs(float(item["offset"]) - float(cluster["offset"])),
                            cluster_index,
                        )
                        for cluster_index, cluster in enumerate(local)
                        if abs(float(item["offset"]) - float(cluster["offset"]))
                        <= rho_tolerance
                    ),
                    default=None,
                )
                if nearest is None:
                    local.append(
                        {
                            "channel": channel,
                            "orientation": orientation,
                            "offset": float(item["offset"]),
                            "weight": float(item["length"]),
                            "indices": [index],
                        }
                    )
                    continue
                cluster = local[nearest[1]]
                weight = float(item["length"])
                total = float(cluster["weight"]) + weight
                cluster["offset"] = (
                    float(cluster["offset"]) * float(cluster["weight"])
                    + float(item["offset"]) * weight
                ) / total
                cluster["weight"] = total
                cluster["indices"].append(index)
            clusters.extend(local)

    preliminary: list[dict[str, Any]] = []
    for cluster in clusters:
        orientation = int(cluster["orientation"])
        direction, _ = _line_basis(orientation)
        intervals: list[list[float]] = []
        for index in cluster["indices"]:
            first_t = float(direction @ raw[index]["start"])
            second_t = float(direction @ raw[index]["end"])
            intervals.append([min(first_t, second_t), max(first_t, second_t)])
        merged = _merge_intervals(intervals, interval_gap)

        # Clip interval evidence to the finite paper.  This changes only the
        # observation support; the line identity remains offset + direction.
        hits = _boundary_hits(float(cluster["offset"]), orientation, size)
        if len(hits) < 2:
            continue
        lower_t = min(float(hit[0]) for hit in hits)
        upper_t = max(float(hit[0]) for hit in hits)
        merged = [
            [max(lower_t, first), min(upper_t, second)]
            for first, second in merged
            if min(upper_t, second) - max(lower_t, first) >= 1.0
        ]
        if not merged:
            continue
        longest = max(second - first for first, second in merged)
        minimum_length = max(
            float(settings.min_run_length_px),
            size * (0.015 if diffuse_input else 0.008),
        )
        if longest < minimum_length:
            continue

        angle_errors = [
            float(raw[index]["angle_error_deg"])
            for index in cluster["indices"]
        ]
        responses = [
            float(raw[index].get("center_response", 0.0))
            for index in cluster["indices"]
        ]
        preliminary.append(
            {
                "orientation": orientation,
                "offset": float(cluster["offset"]),
                "weight": float(cluster["weight"]),
                "intervals": merged,
                "channels": {str(cluster["channel"])},
                "source_segment_count": len(cluster["indices"]),
                "angle_error_sum": sum(angle_errors),
                "angle_error_max": max(angle_errors, default=0.0),
                "center_response_sum": sum(responses),
            }
        )

    # Red and blue observations can be collinear on opposite sides of a CP
    # node.  At this raster scale two rays closer than this tolerance are not
    # independently resolvable, so they are one observed geometric identity.
    cross_channel_tolerance = float(
        np.clip(stroke_radius * 1.25 + 0.9, 1.8, 3.0)
    )
    merged_entities: list[dict[str, Any]] = []
    for item in sorted(
        preliminary, key=lambda value: (value["orientation"], value["offset"])
    ):
        nearest = min(
            (
                (
                    abs(float(item["offset"]) - float(existing["offset"])),
                    index,
                )
                for index, existing in enumerate(merged_entities)
                if int(existing["orientation"]) == int(item["orientation"])
                and abs(float(item["offset"]) - float(existing["offset"]))
                <= cross_channel_tolerance
            ),
            default=None,
        )
        if nearest is None:
            merged_entities.append(item)
            continue
        existing = merged_entities[nearest[1]]
        total = float(existing["weight"]) + float(item["weight"])
        existing["offset"] = (
            float(existing["offset"]) * float(existing["weight"])
            + float(item["offset"]) * float(item["weight"])
        ) / total
        existing["weight"] = total
        existing["intervals"] = _merge_intervals(
            existing["intervals"] + item["intervals"], interval_gap
        )
        existing["channels"].update(item["channels"])
        existing["source_segment_count"] += item["source_segment_count"]
        existing["angle_error_sum"] += item["angle_error_sum"]
        existing["angle_error_max"] = max(
            existing["angle_error_max"], item["angle_error_max"]
        )
        existing["center_response_sum"] += item["center_response_sum"]

    output: list[dict[str, Any]] = []
    for order, item in enumerate(
        sorted(
            merged_entities,
            key=lambda value: (value["orientation"], value["offset"]),
        ),
        start=1,
    ):
        orientation = int(item["orientation"])
        direction, normal = _line_basis(orientation)
        offset = float(item["offset"])
        intervals = _merge_intervals(item["intervals"], interval_gap)
        visible_segments: list[dict[str, Any]] = []
        sample_values: list[np.ndarray] = []
        for first_t, second_t in intervals:
            start = normal * offset + direction * first_t
            end = normal * offset + direction * second_t
            length = second_t - first_t
            visible_segments.append(
                {
                    "start": [round(float(value), 6) for value in start],
                    "end": [round(float(value), 6) for value in end],
                    "length_px": round(float(length), 6),
                }
            )
            samples_t = np.linspace(
                first_t,
                second_t,
                max(3, int(math.ceil(length * 1.5))),
            )
            center = normal * offset + samples_t[:, None] * direction
            band = np.zeros(len(samples_t), dtype=np.float32)
            for shift in np.linspace(-stroke_radius, stroke_radius, 5):
                points = center + normal * shift
                map_x = np.clip(points[:, 0], 0, size - 1).astype(np.float32)
                map_y = np.clip(points[:, 1], 0, size - 1).astype(np.float32)
                values = cv2.remap(
                    confidence,
                    map_x.reshape(1, -1),
                    map_y.reshape(1, -1),
                    cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REFLECT,
                ).ravel()
                band = np.maximum(band, values)
            sample_values.append(band)
        evidence_samples = (
            np.concatenate(sample_values)
            if sample_values
            else np.zeros(0, dtype=np.float32)
        )
        support_fraction = (
            float(np.mean(evidence_samples >= 0.12))
            if len(evidence_samples)
            else 0.0
        )
        mean_confidence = (
            float(np.mean(evidence_samples)) if len(evidence_samples) else 0.0
        )
        source_count = max(1, int(item["source_segment_count"]))
        total_length = sum(
            segment["length_px"] for segment in visible_segments
        )
        output.append(
            {
                "id": f"raw-crease:{orientation}:{order}",
                "source": "raw_image_finite_line_evidence",
                "geometry_role": "observed_crease_entity",
                "orientation": orientation,
                "orientation_deg": round(math.degrees(ALLOWED_ANGLES[orientation]), 6),
                "direction": [round(float(value), 9) for value in direction],
                "normal": [round(float(value), 9) for value in normal],
                "observed_offset_px": round(offset, 6),
                "evidence_intervals_px": [
                    [round(first, 6), round(second, 6)]
                    for first, second in intervals
                ],
                "visible_segments_px": visible_segments,
                "visible_interval_count": len(intervals),
                "total_visible_length_px": round(float(total_length), 6),
                "longest_visible_length_px": round(
                    max(
                        (segment["length_px"] for segment in visible_segments),
                        default=0.0,
                    ),
                    6,
                ),
                "support_fraction": round(support_fraction, 6),
                "mean_confidence": round(mean_confidence, 6),
                "source_segment_count": source_count,
                "source_channels": sorted(item["channels"]),
                "mean_angle_residual_deg": round(
                    float(item["angle_error_sum"]) / source_count, 6
                ),
                "max_angle_residual_deg": round(
                    float(item["angle_error_max"]), 6
                ),
                "mean_center_response": round(
                    float(item["center_response_sum"]) / source_count, 6
                ),
            }
        )
    return output


def _pixel_agreement(
    square: np.ndarray,
    ink: np.ndarray,
    confidence: np.ndarray,
    entities: list[dict[str, Any]],
    evidence_distance_px: float,
) -> dict[str, Any]:
    size = int(square.shape[0])
    reference = np.zeros((size, size), dtype=np.uint8)
    for channel in _geometry_channels(
        square,
        ink,
        confidence,
        evidence_distance_px,
    ):
        reference = np.maximum(
            reference,
            (channel["mask"] > 0).astype(np.uint8),
        )
    # The paper frame is not a crease entity.  Remove only a narrow tangent
    # band; genuine rays entering from a side remain represented immediately
    # after that band.
    margin = max(2, int(math.ceil(evidence_distance_px + 0.75)))
    reference[:margin, :] = 0
    reference[-margin:, :] = 0
    reference[:, :margin] = 0
    reference[:, -margin:] = 0

    observed = np.zeros_like(reference)
    for entity in entities:
        for segment in entity["visible_segments_px"]:
            start = tuple(np.rint(segment["start"]).astype(int))
            end = tuple(np.rint(segment["end"]).astype(int))
            cv2.line(observed, start, end, 1, 1, cv2.LINE_AA)

    tolerance = float(evidence_distance_px + 0.75)
    reference_distance = cv2.distanceTransform(
        np.where(reference > 0, 0, 255).astype(np.uint8),
        cv2.DIST_L2,
        5,
    )
    observed_distance = cv2.distanceTransform(
        np.where(observed > 0, 0, 255).astype(np.uint8),
        cv2.DIST_L2,
        5,
    )
    precision = (
        float(np.mean(reference_distance[observed > 0] <= tolerance))
        if np.any(observed)
        else 0.0
    )
    recall = (
        float(np.mean(observed_distance[reference > 0] <= tolerance))
        if np.any(reference)
        else 0.0
    )
    return {
        "tolerance_px": round(tolerance, 6),
        "observed_centerline_precision": round(precision, 6),
        "source_geometry_recall": round(recall, 6),
        "observed_centerline_pixels": int(np.count_nonzero(observed)),
        "source_geometry_pixels": int(np.count_nonzero(reference)),
    }


def _interval_endpoint_gap(
    intervals: list[list[float]],
    value: float,
) -> tuple[float, float | None]:
    """Return distance to finite evidence and the nearest interval endpoint."""

    best: tuple[float, float | None] = (math.inf, None)
    for first, second in intervals:
        if first - 1e-9 <= value <= second + 1e-9:
            return 0.0, None
        endpoint = first if value < first else second
        candidate = (abs(value - endpoint), float(endpoint))
        if candidate[0] < best[0]:
            best = candidate
    return best


def _bridge_confidence(
    confidence: np.ndarray,
    line: Mapping[str, Any],
    endpoint_t: float,
    intersection_t: float,
    *,
    band_radius: float,
) -> dict[str, Any]:
    """Measure actual source ink between one line endpoint and an intersection."""

    direction = np.asarray(line["direction"], dtype=float)
    normal = np.asarray(line["normal"], dtype=float)
    offset = float(line["observed_offset_px"])
    length = abs(float(intersection_t) - float(endpoint_t))
    sample_count = max(3, int(math.ceil(length * 2.0)) + 1)
    values_t = np.linspace(endpoint_t, intersection_t, sample_count)
    centers = normal * offset + values_t[:, None] * direction
    strongest = np.zeros(sample_count, dtype=np.float32)
    for shift in np.linspace(-band_radius, band_radius, 7):
        points = centers + normal * shift
        map_x = np.clip(points[:, 0], 0, confidence.shape[1] - 1).astype(
            np.float32
        )
        map_y = np.clip(points[:, 1], 0, confidence.shape[0] - 1).astype(
            np.float32
        )
        sampled = cv2.remap(
            confidence,
            map_x.reshape(1, -1),
            map_y.reshape(1, -1),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        ).ravel()
        strongest = np.maximum(strongest, sampled)
    return {
        "sample_count": sample_count,
        "coverage": float(np.mean(strongest >= 0.12)),
        "mean_confidence": float(np.mean(strongest)),
        "minimum_confidence": float(np.min(strongest)),
    }


def _source_verified_endpoint_connections(
    entities: list[dict[str, Any]],
    confidence: np.ndarray,
    evidence_distance_px: float,
) -> list[dict[str, Any]]:
    """Record short endpoint occlusions supported by continuous source ink.

    One supporting line must already carry finite evidence at the intersection.
    This deliberately rejects a junction that exists only after extending both
    nearby strokes, even when their infinite canonical lines cross.
    """

    incidence_margin = float(np.clip(evidence_distance_px * 2.0, 3.5, 5.5))
    maximum_gap = float(np.clip(evidence_distance_px * 3.5, 6.0, 8.0))
    maximum = float(min(confidence.shape[:2]) - 1)
    records: list[dict[str, Any]] = []
    for first_index, first in enumerate(entities):
        for second in entities[first_index + 1 :]:
            if int(first["orientation"]) == int(second["orientation"]):
                continue
            matrix = np.asarray([first["normal"], second["normal"]], dtype=float)
            if abs(float(np.linalg.det(matrix))) < 1e-8:
                continue
            point = np.linalg.solve(
                matrix,
                np.asarray(
                    [first["observed_offset_px"], second["observed_offset_px"]],
                    dtype=float,
                ),
            )
            if np.any(point < -incidence_margin) or np.any(
                point > maximum + incidence_margin
            ):
                continue

            pair: list[dict[str, Any]] = []
            for line in (first, second):
                direction = np.asarray(line["direction"], dtype=float)
                intersection_t = float(direction @ point)
                gap, endpoint_t = _interval_endpoint_gap(
                    line["evidence_intervals_px"],
                    intersection_t,
                )
                pair.append(
                    {
                        "line": line,
                        "intersection_t": intersection_t,
                        "gap": gap,
                        "endpoint_t": endpoint_t,
                    }
                )

            extended = [item for item in pair if item["gap"] > incidence_margin]
            # Exactly one observed stroke may be hidden by a node marker.  If
            # both need extension, the source does not establish a junction.
            if len(extended) != 1:
                continue
            target = extended[0]
            support = pair[0] if pair[1] is target else pair[1]
            if (
                target["gap"] > maximum_gap
                or support["gap"] > incidence_margin
                or target["endpoint_t"] is None
            ):
                continue
            bridge = _bridge_confidence(
                confidence,
                target["line"],
                float(target["endpoint_t"]),
                float(target["intersection_t"]),
                band_radius=evidence_distance_px,
            )
            if bridge["coverage"] < 0.80 or bridge["mean_confidence"] < 0.16:
                continue
            records.append(
                {
                    "id": f"source-endpoint-connection:{len(records) + 1}",
                    "source": "source_image_continuous_endpoint_evidence",
                    "point_px": [round(float(value), 6) for value in point],
                    "line_ids": sorted(
                        [str(first["id"]), str(second["id"])]
                    ),
                    "occluded_line_id": str(target["line"]["id"]),
                    "supporting_line_id": str(support["line"]["id"]),
                    "endpoint_gap_px": round(float(target["gap"]), 6),
                    "supporting_interval_gap_px": round(
                        float(support["gap"]), 6
                    ),
                    "bridge_coverage": round(float(bridge["coverage"]), 6),
                    "bridge_mean_confidence": round(
                        float(bridge["mean_confidence"]), 6
                    ),
                    "bridge_minimum_confidence": round(
                        float(bridge["minimum_confidence"]), 6
                    ),
                    "bridge_sample_count": int(bridge["sample_count"]),
                    "incidence_margin_px": round(incidence_margin, 6),
                    "maximum_endpoint_gap_px": round(maximum_gap, 6),
                }
            )
    return records


def classify_topology_segment_line_types(
    square: np.ndarray,
    topology: Mapping[str, Any],
    *,
    mv_mode: str = "auto",
) -> dict[str, Any]:
    """Measure source-image M/V evidence on each finite topology segment.

    Geometry is already split at every retained topology point before this
    function runs.  Sampling here therefore preserves a possible colour change
    across a vertex on one supporting line instead of assigning one type to the
    whole infinite crease identity.

    Clear red/blue evidence keeps the source-image colour.  A visible segment
    whose colour is black, neutral, or too ambiguous to trust is still an
    observed crease, so it receives the explicit red/mountain fallback used by
    the CP exporter.  Geometry detection and M/V colour assignment are thus
    separate: lack of colour evidence never removes a crease or opens a manual
    "gray line" workflow.
    """

    image = np.asarray(square)
    if (
        image.ndim != 3
        or image.shape[2] != 3
        or image.shape[0] != image.shape[1]
    ):
        raise ValueError("segment colour evidence requires a square BGR image")

    normalized_mode = mv_mode if mv_mode in {"auto", "color", "monochrome"} else "auto"
    points: dict[str, np.ndarray] = {}
    for item in topology.get("points") or []:
        if not isinstance(item, Mapping) or item.get("id") is None:
            continue
        point = item.get("point")
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            parsed = np.asarray([float(point[0]), float(point[1])], dtype=float)
        except (TypeError, ValueError):
            continue
        if np.all(np.isfinite(parsed)):
            points[str(item["id"])] = parsed

    records: dict[str, dict[str, Any]] = {}
    unavailable_ids: list[str] = []
    assigned_ids: list[str] = []
    default_mountain_ids: list[str] = []
    mountain_count = 0
    valley_count = 0
    color_evidence_count = 0

    for segment in topology.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        segment_id = str(segment.get("id") or "")
        if not segment_id:
            continue
        start = points.get(str(segment.get("start_point_id") or ""))
        end = points.get(str(segment.get("end_point_id") or ""))
        if start is None or end is None:
            unavailable_ids.append(segment_id)
            records[segment_id] = {
                "status": "unavailable",
                "line_type": None,
                "source": None,
                "reason": "missing_observed_segment_endpoint",
            }
            continue

        evidence = _edge_mv_evidence(image, Edge(start, end, 0))
        total_color = float(evidence["red_score"]) + float(evidence["blue_score"])
        if float(evidence["coverage"]) >= 0.10 and total_color >= 40.0:
            color_evidence_count += 1

        record: dict[str, Any] = {**evidence}
        if normalized_mode == "monochrome":
            record.update(
                {
                    "status": "assigned",
                    "line_type": 2,
                    "source": "source_image_default_mountain",
                    "reason": "monochrome_mode_default_mountain",
                }
            )
            default_mountain_ids.append(segment_id)
            assigned_ids.append(segment_id)
            mountain_count += 1
        elif bool(evidence["ambiguous"]):
            record.update(
                {
                    "status": "assigned",
                    "line_type": 2,
                    "source": "source_image_default_mountain",
                    "reason": "ambiguous_source_image_default_mountain",
                }
            )
            default_mountain_ids.append(segment_id)
            assigned_ids.append(segment_id)
            mountain_count += 1
        else:
            line_type = 2 if float(evidence["red_probability"]) >= 0.5 else 3
            record.update(
                {
                    "status": "assigned",
                    "line_type": line_type,
                    "source": "source_image_color_evidence",
                    "reason": None,
                }
            )
            assigned_ids.append(segment_id)
            if line_type == 2:
                mountain_count += 1
            else:
                valley_count += 1
        records[segment_id] = record

    return {
        "enabled": True,
        "mode": "raw_topology_segment_mv_evidence_v1",
        "source": "source_image_color_evidence",
        "mv_mode": normalized_mode,
        "segment_count": len(records),
        "assigned_segment_count": len(assigned_ids),
        "ambiguous_segment_count": 0,
        "unavailable_segment_count": len(unavailable_ids),
        "mountain_segment_count": mountain_count,
        "valley_segment_count": valley_count,
        "default_mountain_segment_count": len(default_mountain_ids),
        "color_evidence_segment_count": color_evidence_count,
        "detected_monochrome": bool(records) and color_evidence_count == 0,
        "assigned_segment_ids": assigned_ids,
        "ambiguous_segment_ids": [],
        "default_mountain_segment_ids": default_mountain_ids,
        "unavailable_segment_ids": unavailable_ids,
        "segments": records,
    }


def detect_raw_crease_entities_from_square(
    square: np.ndarray,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Extract observed crease line identities from an already squared image."""

    started = time.perf_counter()
    image = np.asarray(square)
    if (
        image.ndim != 3
        or image.shape[2] != 3
        or image.shape[0] != image.shape[1]
        or image.shape[0] < 40
    ):
        raise ValueError("crease extraction requires a square BGR image of at least 40 pixels")

    base_settings = settings or Settings(analysis_size=int(image.shape[0]))
    ink, confidence, evidence_stats = _adaptive_geometry_evidence(image)
    effective_settings = replace(
        base_settings,
        evidence_distance_px=float(
            evidence_stats.get("adaptive_evidence_distance_px", 1.75)
        ),
    )
    raw, noncanonical_observations, detector_stats = _extract_finite_segments(
        image,
        ink,
        confidence,
        effective_settings,
    )
    entities = _cluster_finite_segments(
        raw,
        int(image.shape[0]),
        confidence,
        effective_settings,
        diffuse_input=bool(detector_stats["diffuse_input"]),
    )
    endpoint_connections = _source_verified_endpoint_connections(
        entities,
        confidence,
        effective_settings.evidence_distance_px,
    )
    orientation_counts = {
        str(orientation): sum(
            int(entity["orientation"]) == orientation for entity in entities
        )
        for orientation in range(8)
    }
    orientation_counts = {
        key: value for key, value in orientation_counts.items() if value
    }
    return {
        "enabled": True,
        "mode": "raw_finite_crease_entities_v1",
        "source": "raw_image_finite_line_evidence",
        "observed_only": True,
        "construction_search_executed": False,
        "directions_generated": 0,
        "analysis_size": int(image.shape[0]),
        "maximum_coordinate_px": int(image.shape[0] - 1),
        "line_count": len(entities),
        "finite_segment_count": int(detector_stats["accepted_finite_segments"]),
        "evidence_interval_count": sum(
            int(entity["visible_interval_count"]) for entity in entities
        ),
        "orientation_counts": orientation_counts,
        "observed_orientation_classes": [
            int(key) for key in sorted(orientation_counts, key=int)
        ],
        "lines": entities,
        "endpoint_connection_evidence": endpoint_connections,
        "endpoint_connection_count": len(endpoint_connections),
        "noncanonical_angle_observations": noncanonical_observations,
        "detector_stats": detector_stats,
        "pixel_agreement": _pixel_agreement(
            image,
            ink,
            confidence,
            entities,
            effective_settings.evidence_distance_px,
        ),
        "evidence_stats": {
            "evidence_threshold": evidence_stats.get("evidence_threshold"),
            "evidence_contrast": evidence_stats.get("evidence_contrast"),
            "estimated_stroke_radius_px": evidence_stats.get(
                "estimated_stroke_radius_px"
            ),
            "adaptive_evidence_distance_px": effective_settings.evidence_distance_px,
            "endpoint_connection_requires_one_supported_line": True,
            "endpoint_connection_requires_continuous_source_ink": True,
            "two_extended_lines_may_not_create_a_junction": True,
        },
        "extraction_duration_ms": round(
            (time.perf_counter() - started) * 1000.0, 3
        ),
    }


def extract_raw_crease_entities(
    image_bytes: bytes,
    settings_mapping: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Prepare source paper and return observed creases without playback/search."""

    started = time.perf_counter()
    settings = Settings.from_mapping(dict(settings_mapping or {}))
    image = _decode_image(image_bytes)
    square, _, preparation_stats = prepare_paper_square(
        image,
        settings.analysis_size,
        settings.paper_corners,
    )
    report = detect_raw_crease_entities_from_square(square, settings)
    report["paper_preparation"] = preparation_stats
    report["total_duration_ms"] = round(
        (time.perf_counter() - started) * 1000.0, 3
    )
    return report


__all__ = [
    "classify_topology_segment_line_types",
    "detect_raw_crease_entities_from_square",
    "extract_raw_crease_entities",
]
