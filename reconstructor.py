from __future__ import annotations

import base64
import itertools
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from foldability import GeometrySegment, audit_camv_structure


SQRT2 = math.sqrt(2.0)
ALLOWED_ANGLES = tuple(k * math.pi / 8.0 for k in range(8))


@dataclass
class Settings:
    analysis_size: int = 512
    # Detection tolerance only: every accepted output ray is still snapped to
    # an exact multiple of 22.5 degrees. Short raster strokes need more room
    # than long strokes when their pixel endpoints are rounded.
    # ``auto`` derives a raster admission gate from stroke width and observed
    # segment length.  This is never a construction-angle tolerance: accepted
    # strict rays are still replaced by exact multiples of 22.5 degrees.
    angle_tolerance_mode: str = "auto"
    angle_tolerance_deg: float = 3.0
    hough_threshold: int = 9
    min_hough_length_px: int = 5
    max_hough_gap_px: int = 2
    rho_merge_px: float = 2.8
    algebraic_coefficient_limit: int = 40
    algebraic_snap_px: float = 3.0
    evidence_distance_px: float = 1.8
    atomic_support: float = 0.70
    run_support: float = 0.64
    output_support: float = 0.58
    min_run_length_px: float = 5.0
    endpoint_snap_px: float = 6.0
    # Optional normalized source-image points in TL, TR, BR, BL order.  They
    # are supplied by the browser's four-rivet photo correction tool.
    paper_corners: list[list[float]] | None = None
    # Low-frequency exact constructions are returned as separate variants;
    # the strict 22.5-degree result remains the default export.
    construction_variants: bool = True
    mv_mode: str = "auto"

    @classmethod
    def from_mapping(cls, values: dict) -> "Settings":
        result = cls()
        converters = {
            "angle_tolerance_deg": float,
            "atomic_support": float,
            "run_support": float,
            "output_support": float,
            "algebraic_snap_px": float,
            "min_run_length_px": float,
        }
        for name, converter in converters.items():
            if name in values and values[name] not in (None, ""):
                setattr(result, name, converter(values[name]))
        if values.get("angle_tolerance_mode") in {"auto", "manual"}:
            result.angle_tolerance_mode = str(values["angle_tolerance_mode"])
        if values.get("mv_mode") in {"auto", "color", "monochrome"}:
            result.mv_mode = str(values["mv_mode"])
        raw_corners = values.get("paper_corners")
        if raw_corners not in (None, "", []):
            if isinstance(raw_corners, str):
                import json

                raw_corners = json.loads(raw_corners)
            result.paper_corners = [
                [float(point[0]), float(point[1])] for point in raw_corners
            ]
        raw_variants = values.get("construction_variants")
        if raw_variants is not None:
            result.construction_variants = (
                raw_variants
                if isinstance(raw_variants, bool)
                else str(raw_variants).lower() not in {"0", "false", "off", "no"}
            )
        result.angle_tolerance_deg = min(6.0, max(0.5, result.angle_tolerance_deg))
        result.atomic_support = min(0.95, max(0.35, result.atomic_support))
        result.run_support = min(0.95, max(0.30, result.run_support))
        result.output_support = min(0.95, max(0.25, result.output_support))
        result.algebraic_snap_px = min(6.0, max(0.5, result.algebraic_snap_px))
        result.min_run_length_px = min(30.0, max(3.0, result.min_run_length_px))
        return result


@dataclass
class AlgebraicValue:
    a: int
    b: int
    value: float
    error: float

    @property
    def expression(self) -> str:
        if self.b == 0:
            return str(self.a)
        if self.a == 0:
            if self.b == 1:
                return "√2"
            if self.b == -1:
                return "−√2"
            return f"{self.b}√2"
        sign = "+" if self.b > 0 else "−"
        magnitude = abs(self.b)
        root = "√2" if magnitude == 1 else f"{magnitude}√2"
        return f"{self.a}{sign}{root}"


@dataclass
class CandidateLine:
    orientation: int
    offset: float
    strength: float
    snap_error_px: float
    anchor_side: str
    anchor_value: AlgebraicValue
    anchor_point: np.ndarray
    generation: int = -1
    origin_kind: str = "unresolved"
    parent_lines: tuple[int, int] | None = None
    evidence_intervals: list[list[float]] | None = None
    anchor_coordinates: tuple[AlgebraicValue, AlgebraicValue] | None = None

    @property
    def angle_deg(self) -> float:
        return self.orientation * 22.5

    @property
    def u(self) -> np.ndarray:
        theta = ALLOWED_ANGLES[self.orientation]
        return np.array([math.cos(theta), math.sin(theta)], dtype=float)

    @property
    def n(self) -> np.ndarray:
        u = self.u
        return np.array([-u[1], u[0]], dtype=float)

    @property
    def p0(self) -> np.ndarray:
        return self.n * self.offset


@dataclass
class Run:
    line_index: int
    start_t: float
    end_t: float
    support: float


@dataclass
class Edge:
    start: np.ndarray
    end: np.ndarray
    line_type: int
    support: float = 1.0


@dataclass
class ConstructionProposal:
    kind: str
    start: np.ndarray
    end: np.ndarray
    support: float
    continuous_run: float
    novel_coverage: float
    expression: str
    label: str


@dataclass
class DetectedGraphEdge:
    first_vertex: int
    second_vertex: int
    orientation: int
    offset: float
    support: float
    length: float
    line_index: int = -1


class ReconstructionError(ValueError):
    pass


def snap_qsqrt2(value: float, coefficient_limit: int = 40) -> AlgebraicValue:
    """Return the nearest integer a+b√2 value.

    The search is deliberately finite. Without a complexity bound almost every
    noisy pixel could be explained by an unnecessarily complicated expression.
    """
    best: tuple[float, int, int, int, float] | None = None
    for b in range(-coefficient_limit, coefficient_limit + 1):
        a = round(value - b * SQRT2)
        candidate = a + b * SQRT2
        error = abs(candidate - value)
        rank = (error, abs(a) + abs(b), abs(b), a, candidate)
        if best is None or rank < best:
            best = rank
    assert best is not None
    return AlgebraicValue(a=int(best[3]), b=int(round((best[4] - best[3]) / SQRT2)), value=float(best[4]), error=float(best[0]))


def snap_qsqrt2_bounded(value: float, max_abs_coefficient: int = 10) -> AlgebraicValue:
    """Nearest a+b*sqrt(2) with both coefficients explicitly bounded."""
    best: tuple[float, int, int, int, float] | None = None
    for a in range(-max_abs_coefficient, max_abs_coefficient + 1):
        for b in range(-max_abs_coefficient, max_abs_coefficient + 1):
            candidate = a + b * SQRT2
            error = abs(candidate - value)
            rank = (error, abs(a) + abs(b), abs(b), a, candidate)
            if best is None or rank < best:
                best = rank
    assert best is not None
    return AlgebraicValue(
        a=int(best[3]),
        b=int(round((best[4] - best[3]) / SQRT2)),
        value=float(best[4]),
        error=float(best[0]),
    )


def _decode_image(data: bytes) -> np.ndarray:
    encoded = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise ReconstructionError("无法读取图片，请上传 PNG 或 JPG。")
    if image.shape[0] < 80 or image.shape[1] < 80:
        raise ReconstructionError("图片尺寸过小，最短边至少需要 80 像素。")
    return image


def validate_white_line_art(image: np.ndarray) -> dict:
    """Validate the supported white-background CP input without rewriting it."""
    values = image.astype(np.float32)
    darkest = np.min(values, axis=2)
    brightest = np.max(values, axis=2)
    chroma = brightest - darkest
    luma = np.mean(values, axis=2)
    neutral = chroma <= 24.0
    white = neutral & (darkest >= 235.0)
    red = (
        (values[:, :, 2] - np.maximum(values[:, :, 1], values[:, :, 0]) >= 24.0)
        & (values[:, :, 2] >= 96.0)
    )
    blue = (
        (values[:, :, 0] - np.maximum(values[:, :, 1], values[:, :, 2]) >= 24.0)
        & (values[:, :, 0] >= 96.0)
    )
    unsupported_color = ~(neutral | red | blue)
    neutral_midtone = neutral & (luma >= 42.0) & (luma <= 224.0)
    white_fraction = float(np.mean(white))
    unsupported_fraction = float(np.mean(unsupported_color))
    midtone_fraction = float(np.mean(neutral_midtone))
    stats = {
        "white_background_fraction": round(white_fraction, 6),
        "unsupported_color_fraction": round(unsupported_fraction, 6),
        "neutral_midtone_fraction": round(midtone_fraction, 6),
    }
    if (
        white_fraction < 0.55
        or unsupported_fraction > 0.08
        or midtone_fraction > 0.22
    ):
        raise ReconstructionError(
            "仅支持白底红蓝线或白底黑线 CP。照片、灰底、黑底及实物图请先交给豆包等 AI 图片工具处理成白底线稿后再上传。"
        )
    return stats


def _adaptive_geometry_evidence(
    image: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Build scale-normalized line evidence without assuming a white PNG.

    A large morphological background follows illumination and JPEG shading but
    not a crease-width dark/color stroke. Otsu separates the two modes in that
    relative signal, so an absolute RGB value never defines ink.
    """
    height, width = image.shape[:2]
    minimum = max(1, min(height, width))
    values = image.astype(np.float32)
    darkest_channel = np.min(values, axis=2)

    kernel_size = max(5, int(round(minimum / 28.0)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel_size = min(kernel_size, 31)
    background = cv2.morphologyEx(
        darkest_channel,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
        ),
    )
    local_darkness = np.maximum(background - darkest_channel, 0.0)
    chroma = np.max(values, axis=2) - np.min(values, axis=2)
    strength = np.maximum(local_darkness, chroma * 0.82)

    robust_high = float(np.percentile(strength, 99.5))
    if robust_high <= 1e-6:
        return (
            np.zeros((height, width), dtype=np.uint8),
            np.zeros((height, width), dtype=np.float32),
            {
                "evidence_threshold": 0.0,
                "evidence_contrast": 0.0,
                "estimated_stroke_radius_px": 1.0,
                "adaptive_evidence_distance_px": 1.75,
            },
        )

    scaled = np.clip(strength * (255.0 / robust_high), 0, 255).astype(
        np.uint8
    )
    otsu_threshold, _ = cv2.threshold(
        scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    lower = strength[strength <= np.percentile(strength, 75.0)]
    noise_median = float(np.median(lower)) if len(lower) else 0.0
    noise_mad = (
        float(np.median(np.abs(lower - noise_median))) if len(lower) else 0.0
    )
    # A robust 2.8-sigma floor retains the shoulders of a blurred crease. The
    # former six-sigma cutoff measured only its darkest core, so the estimated
    # width collapsed back to one pixel and downstream focus uncertainty could
    # not adapt. Directional/geometry validation removes isolated texture later.
    noise_ceiling = noise_median + 2.8 * 1.4826 * noise_mad
    threshold = max(
        robust_high * float(otsu_threshold) / 255.0,
        noise_ceiling,
        robust_high * 0.035,
    )
    ink = (strength >= threshold).astype(np.uint8) * 255
    confidence = np.clip(
        (strength - noise_median) / max(robust_high - noise_median, 1e-6),
        0.0,
        1.0,
    ).astype(np.float32)

    inside_distance = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    local_maximum = inside_distance >= (
        cv2.dilate(inside_distance, np.ones((3, 3), dtype=np.uint8)) - 1e-4
    )
    radii = inside_distance[(ink > 0) & local_maximum]
    if len(radii):
        upper = float(np.percentile(radii, 80.0))
        central = radii[radii <= upper + 1e-4]
        stroke_radius = float(np.median(central if len(central) else radii))
    else:
        stroke_radius = 1.0
    stroke_radius = float(np.clip(stroke_radius, 0.75, minimum / 60.0))
    evidence_distance = float(
        np.clip(stroke_radius + 0.75, 1.25, max(1.25, minimum / 80.0))
    )
    return ink, confidence, {
        "evidence_threshold": round(threshold, 4),
        "evidence_contrast": round(robust_high, 4),
        "estimated_stroke_radius_px": round(stroke_radius, 4),
        "adaptive_evidence_distance_px": round(evidence_distance, 4),
    }


def _paper_bbox(image: np.ndarray) -> tuple[int, int, int, int]:
    ink, confidence, evidence = _adaptive_geometry_evidence(image)
    if not np.any(ink):
        raise ReconstructionError("图片中没有检测到线条。")

    component_count, _, stats, _ = cv2.connectedComponentsWithStats(
        (ink > 0).astype(np.uint8), 8
    )
    candidates: list[tuple[float, int, int, int, int]] = []
    image_area = float(image.shape[0] * image.shape[1])
    for index in range(1, component_count):
        x, y, width, height, area = map(int, stats[index])
        if min(width, height) < 60:
            continue
        ratio = width / max(1.0, float(height))
        if not 0.78 <= ratio <= 1.28:
            continue
        bbox_area = float(width * height)
        if bbox_area < image_area * 0.04:
            continue
        squareness = min(ratio, 1.0 / ratio)
        score = bbox_area * squareness + float(area) * 0.1
        candidates.append((score, x, y, width, height))

    if candidates:
        _, x0, y0, width, height = max(candidates, key=lambda item: item[0])
        x1, y1 = x0 + width - 1, y0 + height - 1
    else:
        ys, xs = np.where(ink > 0)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())

    # The component box follows the outside of a blurred border. Fit the four
    # border *centerlines* from their long-axis response so blur and JPEG halos
    # do not change the paper coordinate system.
    search = max(
        4,
        int(math.ceil(evidence["estimated_stroke_radius_px"] * 4.0)),
    )

    def weighted_peak(
        positions: np.ndarray, response: np.ndarray
    ) -> float:
        if not len(positions) or float(np.max(response)) <= 1e-9:
            return float(positions[len(positions) // 2])
        peak = float(np.max(response))
        selected = response >= peak * 0.72
        weights = np.maximum(response[selected], 1e-6)
        return float(np.average(positions[selected], weights=weights))

    left_positions = np.arange(x0, min(x1 + 1, x0 + search + 1))
    right_positions = np.arange(max(x0, x1 - search), x1 + 1)
    top_positions = np.arange(y0, min(y1 + 1, y0 + search + 1))
    bottom_positions = np.arange(max(y0, y1 - search), y1 + 1)
    left = weighted_peak(
        left_positions,
        np.array(
            [np.mean(confidence[y0 : y1 + 1, x]) for x in left_positions]
        ),
    )
    right = weighted_peak(
        right_positions,
        np.array(
            [np.mean(confidence[y0 : y1 + 1, x]) for x in right_positions]
        ),
    )
    top = weighted_peak(
        top_positions,
        np.array(
            [np.mean(confidence[y, x0 : x1 + 1]) for y in top_positions]
        ),
    )
    bottom = weighted_peak(
        bottom_positions,
        np.array(
            [np.mean(confidence[y, x0 : x1 + 1]) for y in bottom_positions]
        ),
    )
    horizontal_span = right - left
    vertical_span = bottom - top
    if min(horizontal_span, vertical_span) >= 60.0:
        # Preserve every side of a slightly stretched source rectangle.  The
        # following resize is the operation that restores the paper to a
        # square.  Making the *crop* square first used to shorten the longer
        # axis, cutting genuine corner rays off before reconstruction.
        x0 = int(round(left))
        x1 = int(round(right))
        y0 = int(round(top))
        y1 = int(round(bottom))
        x0, x1 = max(0, x0), min(image.shape[1] - 1, x1)
        y0, y1 = max(0, y0), min(image.shape[0] - 1, y1)
    width, height = x1 - x0, y1 - y0
    if width < 60 or height < 60:
        raise ReconstructionError("没有找到足够大的纸张区域。")
    ratio = width / height
    if not 0.82 <= ratio <= 1.22:
        raise ReconstructionError("检测到的纸张区域不像正方形；初版要求完整、近似正方形的外框。")
    return x0, y0, x1, y1


def prepare_paper_square(
    image: np.ndarray,
    maximum_analysis_size: int = 512,
    paper_corners: list[list[float]] | None = None,
) -> tuple[np.ndarray, tuple[int, int, int, int], dict]:
    """Crop and square the paper, upscaling small screenshots for analysis."""
    if paper_corners is not None:
        points = np.asarray(paper_corners, dtype=np.float32)
        if points.shape != (4, 2) or not np.all(np.isfinite(points)):
            raise ReconstructionError("四角校正点格式无效；需要左上、右上、右下、左下四点。")
        if float(np.max(np.abs(points))) <= 1.000001:
            points = points * np.array(
                [image.shape[1] - 1, image.shape[0] - 1], dtype=np.float32
            )
        contour = points.reshape((-1, 1, 2))
        if not cv2.isContourConvex(contour):
            raise ReconstructionError("四个准星必须按左上、右上、右下、左下围成凸四边形。")
        area = abs(float(cv2.contourArea(contour)))
        side_lengths = [
            float(np.linalg.norm(points[(index + 1) % 4] - points[index]))
            for index in range(4)
        ]
        if area < 16.0:
            raise ReconstructionError("四个准星几乎在同一直线上，请重新对准纸张四角。")
        native_paper_size = int(
            round(
                max(
                    (side_lengths[0] + side_lengths[2]) / 2.0,
                    (side_lengths[1] + side_lengths[3]) / 2.0,
                )
            )
        )
        if native_paper_size < 24 or min(side_lengths) < 12.0:
            raise ReconstructionError("四角圈出的纸张分辨率不足。")
        analysis_size = maximum_analysis_size
        maximum = float(analysis_size - 1)
        destination = np.array(
            [[0.0, 0.0], [maximum, 0.0], [maximum, maximum], [0.0, maximum]],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(points, destination)
        square = cv2.warpPerspective(
            image,
            transform,
            (analysis_size, analysis_size),
            flags=(
                cv2.INTER_AREA
                if native_paper_size > analysis_size
                else cv2.INTER_CUBIC
            ),
            borderMode=cv2.BORDER_REPLICATE,
        )
        x0 = max(0, int(math.floor(float(np.min(points[:, 0])))))
        y0 = max(0, int(math.floor(float(np.min(points[:, 1])))))
        x1 = min(
            image.shape[1] - 1,
            int(math.ceil(float(np.max(points[:, 0])))),
        )
        y1 = min(
            image.shape[0] - 1,
            int(math.ceil(float(np.max(points[:, 1])))),
        )
        return square, (x0, y0, x1, y1), {
            "native_paper_size": native_paper_size,
            "analysis_size_used": analysis_size,
            "source_upscaled": native_paper_size < analysis_size,
            "analysis_scale": round(analysis_size / native_paper_size, 6),
            "paper_transform": "four_corner_perspective",
            "paper_corners_source_px": [
                [round(float(value), 4) for value in point] for point in points
            ],
        }

    detection_scale = min(
        8.0,
        max(1.0, 256.0 / max(1.0, float(min(image.shape[:2])))),
    )
    if detection_scale > 1.000001:
        detection_image = cv2.resize(
            image,
            None,
            fx=detection_scale,
            fy=detection_scale,
            interpolation=cv2.INTER_CUBIC,
        )
        detected_x0, detected_y0, detected_x1, detected_y1 = _paper_bbox(
            detection_image
        )
        x0 = max(0, int(round(detected_x0 / detection_scale)))
        y0 = max(0, int(round(detected_y0 / detection_scale)))
        x1 = min(
            image.shape[1] - 1,
            int(round(detected_x1 / detection_scale)),
        )
        y1 = min(
            image.shape[0] - 1,
            int(round(detected_y1 / detection_scale)),
        )
    else:
        x0, y0, x1, y1 = _paper_bbox(image)
    crop = image[y0 : y1 + 1, x0 : x1 + 1]
    native_paper_size = max(crop.shape[:2])
    source_paper_aspect_ratio = crop.shape[1] / max(1.0, float(crop.shape[0]))
    analysis_size = maximum_analysis_size
    square = cv2.resize(
        crop,
        (analysis_size, analysis_size),
        interpolation=(
            cv2.INTER_AREA
            if native_paper_size > analysis_size
            else cv2.INTER_CUBIC
        ),
    )
    return square, (x0, y0, x1, y1), {
        "native_paper_size": native_paper_size,
        "analysis_size_used": analysis_size,
        "source_upscaled": native_paper_size < analysis_size,
        "analysis_scale": round(analysis_size / native_paper_size, 6),
        "paper_detection_scale": round(detection_scale, 6),
        "paper_transform": "automatic_axis_aligned_crop",
        "source_paper_aspect_ratio": round(source_paper_aspect_ratio, 6),
        "aspect_ratio_corrected": abs(source_paper_aspect_ratio - 1.0) > 0.005,
    }


def _closest_orientation(angle: float) -> tuple[int, float]:
    angle %= math.pi
    errors = [min(abs(angle - target), math.pi - abs(angle - target)) for target in ALLOWED_ANGLES]
    index = int(np.argmin(errors))
    return index, errors[index]


def _angle_admission_tolerance_deg(
    length: float, settings: Settings
) -> float:
    """Raster-only direction gate; exact construction happens afterwards."""

    if settings.angle_tolerance_mode == "manual":
        return settings.angle_tolerance_deg
    # Endpoint quantisation and a thick/blurred ridge can rotate a short pixel
    # segment even when the underlying crease is exact.  Long observations are
    # held much more tightly.  Four-corner rectification happens before this.
    endpoint_uncertainty = max(0.8, settings.evidence_distance_px)
    estimated = math.degrees(
        math.atan2(2.0 * endpoint_uncertainty, max(3.0, float(length)))
    )
    return float(np.clip(estimated, 0.65, 3.0))


def _boundary_hits(offset: float, orientation: int, size: int) -> list[tuple[float, np.ndarray, str]]:
    theta = ALLOWED_ANGLES[orientation]
    u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    n = np.array([-u[1], u[0]], dtype=float)
    p0 = n * offset
    maximum = float(size - 1)
    hits: list[tuple[float, np.ndarray, str]] = []
    definitions = ((0, 0.0, "左"), (0, maximum, "右"), (1, 0.0, "上"), (1, maximum, "下"))
    for dimension, target, side in definitions:
        if abs(u[dimension]) < 1e-9:
            continue
        t = (target - p0[dimension]) / u[dimension]
        point = p0 + t * u
        if -0.75 <= point[0] <= maximum + 0.75 and -0.75 <= point[1] <= maximum + 0.75:
            hits.append((float(t), point, side))
    return hits


def _extract_hough_clusters(ink: np.ndarray, settings: Settings) -> tuple[list[tuple[int, float, float]], dict]:
    lines = cv2.HoughLinesP(
        ink,
        1,
        np.pi / 720,
        threshold=settings.hough_threshold,
        minLineLength=settings.min_hough_length_px,
        maxLineGap=settings.max_hough_gap_px,
    )
    if lines is None:
        raise ReconstructionError("没有检测到直线。")

    size = ink.shape[0]
    raw: list[tuple[int, float, float, float]] = []
    rejected_angle = 0
    rejected_border = 0
    for x1, y1, x2, y2 in lines[:, 0]:
        dx, dy = float(x2 - x1), float(y2 - y1)
        length = math.hypot(dx, dy)
        orientation, angle_error = _closest_orientation(math.atan2(dy, dx))
        angle_error_deg = math.degrees(angle_error)
        if angle_error_deg > _angle_admission_tolerance_deg(length, settings):
            rejected_angle += 1
            continue
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        midpoint = np.array([(x1 + x2) / 2, (y1 + y2) / 2], dtype=float)
        offset = float(n @ midpoint)

        # The four paper borders are generated explicitly after the internal graph.
        if orientation == 0 and min(abs(offset), abs(offset - (size - 1))) < 3:
            rejected_border += 1
            continue
        if orientation == 4 and min(abs(offset), abs(offset + (size - 1))) < 3:
            rejected_border += 1
            continue
        raw.append((orientation, offset, length, angle_error_deg))

    clusters: list[tuple[int, float, float]] = []
    for orientation in range(8):
        ordered = sorted((item for item in raw if item[0] == orientation), key=lambda item: item[1])
        groups: list[list[tuple[int, float, float, float]]] = []
        for item in ordered:
            if not groups or item[1] - groups[-1][-1][1] > settings.rho_merge_px:
                groups.append([item])
            else:
                groups[-1].append(item)
        for group in groups:
            weights = np.array([item[2] for item in group], dtype=float)
            offsets = np.array([item[1] for item in group], dtype=float)
            strength = float(weights.sum())
            if strength < settings.min_hough_length_px + 3:
                continue
            clusters.append((orientation, float(np.average(offsets, weights=weights)), strength))

    return clusters, {
        "hough_segments": int(len(lines)),
        "angle_rejected_segments": rejected_angle,
        "border_rejected_segments": rejected_border,
        "hough_clusters": len(clusters),
    }


def _snap_lines_to_algebraic_anchors(
    clusters: Iterable[tuple[int, float, float]], size: int, settings: Settings
) -> tuple[list[CandidateLine], int]:
    maximum = float(size - 1)
    snapped: list[CandidateLine] = []
    rejected = 0
    for orientation, offset, strength in clusters:
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        options: list[tuple[float, float, str, AlgebraicValue, np.ndarray]] = []
        for _, point, side in _boundary_hits(offset, orientation, size):
            normalized = -1.0 + 2.0 * point / maximum
            if side in ("左", "右"):
                algebraic = snap_qsqrt2(float(normalized[1]), settings.algebraic_coefficient_limit)
                exact_normalized = np.array([-1.0 if side == "左" else 1.0, algebraic.value])
            else:
                algebraic = snap_qsqrt2(float(normalized[0]), settings.algebraic_coefficient_limit)
                exact_normalized = np.array([algebraic.value, -1.0 if side == "上" else 1.0])
            exact_point = (exact_normalized + 1.0) * maximum / 2.0
            exact_offset = float(n @ exact_point)
            error_px = abs(exact_offset - offset)
            options.append((error_px, abs(algebraic.a) + abs(algebraic.b), side, algebraic, exact_point))
        if not options:
            rejected += 1
            continue
        error_px, _, side, algebraic, point = min(options, key=lambda item: (item[0], item[1]))
        if error_px > settings.algebraic_snap_px:
            rejected += 1
            continue
        exact_offset = float(n @ point)
        snapped.append(
            CandidateLine(
                orientation=orientation,
                offset=exact_offset,
                strength=strength,
                snap_error_px=error_px,
                anchor_side=side,
                anchor_value=algebraic,
                anchor_point=point,
            )
        )

    # Multiple raster fragments can lead to the same exact construction ray.
    result: list[CandidateLine] = []
    for orientation in range(8):
        ordered = sorted((line for line in snapped if line.orientation == orientation), key=lambda line: line.offset)
        for line in ordered:
            if result and result[-1].orientation == orientation and abs(line.offset - result[-1].offset) < 0.65:
                previous = result[-1]
                if (line.strength, -line.snap_error_px) > (previous.strength, -previous.snap_error_px):
                    result[-1] = line
            else:
                result.append(line)
    return result, rejected


def _line_intersection(first: CandidateLine, second: CandidateLine) -> np.ndarray | None:
    matrix = np.array([first.n, second.n], dtype=float)
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) < 1e-8:
        return None
    return np.linalg.solve(matrix, np.array([first.offset, second.offset], dtype=float))


def _point_t(line: CandidateLine, point: np.ndarray) -> float:
    return float((point - line.p0) @ line.u)


def _sample_support(
    line: CandidateLine, start_t: float, end_t: float, distance: np.ndarray, threshold: float
) -> float:
    length = end_t - start_t
    if length <= 0:
        return 0.0
    count = max(3, int(length * 2) + 1)
    samples = np.linspace(start_t + 0.35, end_t - 0.35, count)
    points = line.p0 + samples[:, None] * line.u
    xs = np.clip(np.rint(points[:, 0]).astype(int), 0, distance.shape[1] - 1)
    ys = np.clip(np.rint(points[:, 1]).astype(int), 0, distance.shape[0] - 1)
    return float(np.mean(distance[ys, xs] <= threshold))


def _supported_runs(lines: list[CandidateLine], ink: np.ndarray, settings: Settings) -> tuple[list[Run], np.ndarray]:
    size = ink.shape[0]
    maximum = float(size - 1)
    vertices: list[list[float]] = [[] for _ in lines]
    for index, line in enumerate(lines):
        vertices[index].extend(hit[0] for hit in _boundary_hits(line.offset, line.orientation, size))
    for first_index, first in enumerate(lines):
        for second_index in range(first_index + 1, len(lines)):
            second = lines[second_index]
            point = _line_intersection(first, second)
            if point is None:
                continue
            if -1 <= point[0] <= maximum + 1 and -1 <= point[1] <= maximum + 1:
                vertices[first_index].append(_point_t(first, point))
                vertices[second_index].append(_point_t(second, point))

    inverse = np.where(ink > 0, 0, 255).astype(np.uint8)
    distance = cv2.distanceTransform(inverse, cv2.DIST_L2, 3)
    runs: list[Run] = []
    for line_index, line in enumerate(lines):
        ordered = sorted(vertices[line_index])
        merged: list[float] = []
        for value in ordered:
            if not merged or value - merged[-1] > 2.0:
                merged.append(value)
            else:
                merged[-1] = (merged[-1] + value) / 2.0
        atoms: list[tuple[float, float, bool]] = []
        for start_t, end_t in zip(merged, merged[1:]):
            length = end_t - start_t
            support = _sample_support(line, start_t, end_t, distance, settings.evidence_distance_px)
            atoms.append((start_t, end_t, length >= 2.5 and support >= settings.atomic_support))

        current: list[float] | None = None
        for start_t, end_t, accepted in atoms + [(0.0, 0.0, False)]:
            if accepted:
                if current is None:
                    current = [start_t, end_t]
                else:
                    current[1] = end_t
            elif current is not None:
                length = current[1] - current[0]
                support = _sample_support(line, current[0], current[1], distance, settings.evidence_distance_px)
                if length >= settings.min_run_length_px and support >= settings.run_support:
                    runs.append(Run(line_index, current[0], current[1], support))
                current = None
    return runs, distance


def _edges_from_runs(
    lines: list[CandidateLine], runs: list[Run], distance: np.ndarray, settings: Settings
) -> list[Edge]:
    size = distance.shape[0]
    maximum = float(size - 1)
    vertices: list[list[tuple[float, np.ndarray]]] = [[] for _ in runs]

    for run_index, run in enumerate(runs):
        line = lines[run.line_index]
        for t, point, _ in _boundary_hits(line.offset, line.orientation, size):
            if run.start_t - settings.endpoint_snap_px <= t <= run.end_t + settings.endpoint_snap_px:
                vertices[run_index].append((t, point))

    for first_index, first_run in enumerate(runs):
        first_line = lines[first_run.line_index]
        for second_index in range(first_index + 1, len(runs)):
            second_run = runs[second_index]
            if first_run.line_index == second_run.line_index:
                continue
            second_line = lines[second_run.line_index]
            point = _line_intersection(first_line, second_line)
            if point is None or not (-1 <= point[0] <= maximum + 1 and -1 <= point[1] <= maximum + 1):
                continue
            first_t = _point_t(first_line, point)
            second_t = _point_t(second_line, point)
            margin = 1.5
            if (
                first_run.start_t - margin <= first_t <= first_run.end_t + margin
                and second_run.start_t - margin <= second_t <= second_run.end_t + margin
            ):
                vertices[first_index].append((first_t, point))
                vertices[second_index].append((second_t, point))

    edges: list[Edge] = []
    for run_index, run in enumerate(runs):
        line = lines[run.line_index]
        ordered = sorted(vertices[run_index], key=lambda item: item[0])
        clustered: list[tuple[float, np.ndarray]] = []
        for t, point in ordered:
            if not clustered or t - clustered[-1][0] > 1.25:
                clustered.append((t, point))
        if len(clustered) < 2:
            continue
        start_index = int(np.argmin([abs(t - run.start_t) for t, _ in clustered]))
        end_index = int(np.argmin([abs(t - run.end_t) for t, _ in clustered]))
        if start_index >= end_index:
            continue
        if abs(clustered[start_index][0] - run.start_t) > settings.endpoint_snap_px:
            continue
        if abs(clustered[end_index][0] - run.end_t) > settings.endpoint_snap_px:
            continue

        selected = clustered[start_index : end_index + 1]
        for (start_t, start), (end_t, end) in zip(selected, selected[1:]):
            length = float(np.linalg.norm(end - start))
            if length < 1.2:
                continue
            support = _sample_support(line, start_t, end_t, distance, settings.evidence_distance_px)
            if support >= settings.output_support:
                edges.append(Edge(start.copy(), end.copy(), 4, support))

    # Canonical geometric de-duplication.
    unique: dict[tuple, Edge] = {}
    for edge in edges:
        first = (round(float(edge.start[0]), 3), round(float(edge.start[1]), 3))
        second = (round(float(edge.end[0]), 3), round(float(edge.end[1]), 3))
        key = tuple(sorted((first, second)))
        previous = unique.get(key)
        if previous is None or edge.support > previous.support:
            unique[key] = edge
    return list(unique.values())


def _cluster_hough_endpoints(points: np.ndarray, radius: float = 5.0, minimum_votes: int = 2) -> np.ndarray:
    if not len(points):
        return np.empty((0, 2), dtype=float)
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    radius_squared = radius * radius
    # Endpoint sets are normally below 1,500 points, so this direct search is
    # faster than adding another runtime dependency and remains deterministic.
    for first in range(len(points)):
        delta = points[first + 1 :] - points[first]
        nearby = np.where(np.sum(delta * delta, axis=1) <= radius_squared)[0]
        for relative in nearby:
            union(first, first + 1 + int(relative))

    groups: dict[int, list[np.ndarray]] = {}
    for index, point in enumerate(points):
        groups.setdefault(find(index), []).append(point)
    return np.array(
        [np.mean(group, axis=0) for group in groups.values() if len(group) >= minimum_votes],
        dtype=float,
    )


def _vertex_candidates(ink: np.ndarray, settings: Settings) -> tuple[np.ndarray, np.ndarray, dict]:
    lines = cv2.HoughLinesP(
        ink,
        1,
        np.pi / 720,
        threshold=max(10, settings.hough_threshold),
        minLineLength=max(6, settings.min_hough_length_px),
        maxLineGap=max(3, settings.max_hough_gap_px),
    )
    if lines is None:
        return np.empty((0, 2)), np.empty((0, 4)), {"endpoint_hough_segments": 0}
    endpoints: list[list[float]] = []
    accepted_segments: list[list[float]] = []
    rejected = 0
    for x1, y1, x2, y2 in lines[:, 0]:
        orientation, error = _closest_orientation(math.atan2(float(y2 - y1), float(x2 - x1)))
        if math.degrees(error) > _angle_admission_tolerance_deg(
            math.hypot(float(x2 - x1), float(y2 - y1)), settings
        ):
            rejected += 1
            continue
        endpoints.extend(([float(x1), float(y1)], [float(x2), float(y2)]))
        accepted_segments.append([float(x1), float(y1), float(x2), float(y2)])
    vertices = _cluster_hough_endpoints(np.array(endpoints, dtype=float), radius=5.0, minimum_votes=2)
    return vertices, np.array(accepted_segments, dtype=float), {
        "endpoint_hough_segments": len(lines),
        "endpoint_angle_rejected": rejected,
        "vertex_candidates": len(vertices),
    }


def _connect_vertex_graph(
    vertices: np.ndarray, distance: np.ndarray, settings: Settings
) -> list[DetectedGraphEdge]:
    graph_edges: list[DetectedGraphEdge] = []
    maximum_length = distance.shape[0] * 0.78
    minimum_support = max(0.72, settings.atomic_support + 0.06)

    for first in range(len(vertices)):
        for second in range(first + 1, len(vertices)):
            delta = vertices[second] - vertices[first]
            length = float(np.linalg.norm(delta))
            if length < settings.min_run_length_px or length > maximum_length:
                continue
            orientation, _ = _closest_orientation(math.atan2(float(delta[1]), float(delta[0])))
            theta = ALLOWED_ANGLES[orientation]
            u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
            n = np.array([-u[1], u[0]], dtype=float)
            # Endpoints must already be compatible with one exact direction.
            if abs(float(n @ delta)) > 3.0:
                continue
            offset = float(n @ ((vertices[first] + vertices[second]) / 2.0))
            p0 = n * offset
            first_t = float((vertices[first] - p0) @ u)
            second_t = float((vertices[second] - p0) @ u)
            if first_t > second_t:
                first_t, second_t = second_t, first_t
            samples = np.linspace(first_t + 0.4, second_t - 0.4, max(4, int(length * 2)))
            points = p0 + samples[:, None] * u
            xs = np.clip(np.rint(points[:, 0]).astype(int), 0, distance.shape[1] - 1)
            ys = np.clip(np.rint(points[:, 1]).astype(int), 0, distance.shape[0] - 1)
            support = float(np.mean(distance[ys, xs] <= settings.evidence_distance_px))
            if support < minimum_support:
                continue

            # Only connect adjacent nodes on a ray. A third node between the two
            # becomes the legitimate point at which the CP segment must split.
            has_middle_vertex = False
            for middle in range(len(vertices)):
                if middle in (first, second):
                    continue
                middle_t = float((vertices[middle] - p0) @ u)
                perpendicular = abs(float(n @ vertices[middle] - offset))
                if first_t + 3.0 < middle_t < second_t - 3.0 and perpendicular < 3.0:
                    has_middle_vertex = True
                    break
            if not has_middle_vertex:
                graph_edges.append(
                    DetectedGraphEdge(first, second, orientation, offset, support, length)
                )
    return graph_edges


def _snap_graph_lines(
    graph_edges: list[DetectedGraphEdge], size: int, settings: Settings
) -> tuple[list[CandidateLine], int]:
    groups: list[list[int]] = []
    for orientation in range(8):
        ordered = sorted(
            (index for index, edge in enumerate(graph_edges) if edge.orientation == orientation),
            key=lambda index: graph_edges[index].offset,
        )
        orientation_groups: list[list[int]] = []
        for index in ordered:
            if (
                not orientation_groups
                or graph_edges[index].offset - graph_edges[orientation_groups[-1][-1]].offset
                > min(settings.rho_merge_px, 1.8)
            ):
                orientation_groups.append([index])
            else:
                orientation_groups[-1].append(index)
        groups.extend(orientation_groups)

    exact_lines: list[CandidateLine] = []
    rejected_groups = 0
    for group in groups:
        weights = np.array([graph_edges[index].length * graph_edges[index].support for index in group])
        offsets = np.array([graph_edges[index].offset for index in group])
        orientation = graph_edges[group[0]].orientation
        cluster = (orientation, float(np.average(offsets, weights=weights)), float(weights.sum()))
        snapped, rejected = _snap_lines_to_algebraic_anchors([cluster], size, settings)
        if rejected or not snapped:
            rejected_groups += 1
            continue
        line = snapped[0]
        # Preserve the observed ray while recovering topology. Algebraic seed
        # selection happens only after the graph establishes which rays are
        # constructed from existing intersections.
        line.offset = float(cluster[1])
        existing_index = next(
            (
                index
                for index, existing in enumerate(exact_lines)
                if existing.orientation == line.orientation and abs(existing.offset - line.offset) < 0.65
            ),
            None,
        )
        if existing_index is None:
            existing_index = len(exact_lines)
            exact_lines.append(line)
        for edge_index in group:
            graph_edges[edge_index].line_index = existing_index
    return exact_lines, rejected_groups


def _refresh_anchor_metadata(line: CandidateLine, size: int, settings: Settings) -> None:
    maximum = float(size - 1)
    choices: list[tuple[float, str, AlgebraicValue, np.ndarray]] = []
    for _, point, side in _boundary_hits(line.offset, line.orientation, size):
        normalized = -1.0 + 2.0 * point / maximum
        variable = float(normalized[1] if side in ("左", "右") else normalized[0])
        algebraic = snap_qsqrt2(variable, settings.algebraic_coefficient_limit)
        choices.append((algebraic.error, side, algebraic, point))
    if choices:
        _, side, algebraic, point = min(choices, key=lambda item: item[0])
        line.anchor_side = side
        line.anchor_value = algebraic
        line.anchor_point = point


def _refine_concurrent_nodes(
    vertices: np.ndarray,
    graph_edges: list[DetectedGraphEdge],
    lines: list[CandidateLine],
    size: int,
    settings: Settings,
) -> list[list[int]]:
    incident: list[list[int]] = [[] for _ in vertices]
    for edge in graph_edges:
        if edge.line_index < 0:
            continue
        incident[edge.first_vertex].append(edge.line_index)
        incident[edge.second_vertex].append(edge.line_index)
    incident = [sorted(set(items)) for items in incident]

    # High-degree stars are the most reliable CP landmarks. Make nearby rays
    # exactly concurrent there before calculating final segment endpoints.
    node_order = sorted(range(len(vertices)), key=lambda index: len(incident[index]), reverse=True)
    for vertex_index in node_order:
        line_ids = incident[vertex_index]
        if len(line_ids) < 3:
            continue
        options: list[tuple[float, np.ndarray]] = []
        for first_position, first_id in enumerate(line_ids):
            for second_id in line_ids[first_position + 1 :]:
                point = _line_intersection(lines[first_id], lines[second_id])
                if point is not None:
                    options.append((float(np.linalg.norm(point - vertices[vertex_index])), point))
        if not options:
            continue
        distance_to_node, point = min(options, key=lambda item: item[0])
        if distance_to_node > settings.endpoint_snap_px:
            continue
        for line_id in line_ids:
            line = lines[line_id]
            perpendicular = abs(float(line.n @ point - line.offset))
            if perpendicular <= 3.5:
                line.offset = float(line.n @ point)

    for line in lines:
        _refresh_anchor_metadata(line, size, settings)
    return incident


def _point_for_incident_line(
    vertex: np.ndarray,
    line_id: int,
    incident_line_ids: list[int],
    lines: list[CandidateLine],
    size: int,
    settings: Settings,
) -> np.ndarray | None:
    maximum = float(size - 1)
    boundary_distance = min(vertex[0], vertex[1], maximum - vertex[0], maximum - vertex[1])
    options: list[tuple[float, np.ndarray]] = []
    if boundary_distance <= settings.endpoint_snap_px + 1:
        for _, point, _ in _boundary_hits(lines[line_id].offset, lines[line_id].orientation, size):
            options.append((float(np.linalg.norm(point - vertex)), point))
    for other_id in incident_line_ids:
        if other_id == line_id:
            continue
        point = _line_intersection(lines[line_id], lines[other_id])
        if point is not None:
            options.append((float(np.linalg.norm(point - vertex)), point))
    if options:
        error, point = min(options, key=lambda item: item[0])
        if error <= settings.endpoint_snap_px + 1.5:
            return point.copy()

    # A short raster line is sometimes detected only once, so its endpoint does
    # not receive a second endpoint vote. It may still terminate on another
    # already-established construction ray. Search the exact ray set instead of
    # inventing a free endpoint.
    fallback: list[tuple[float, np.ndarray]] = []
    for other_id, other in enumerate(lines):
        if other_id == line_id or other.orientation == lines[line_id].orientation:
            continue
        point = _line_intersection(lines[line_id], other)
        if point is not None:
            error = float(np.linalg.norm(point - vertex))
            if error <= settings.endpoint_snap_px + 1.5:
                fallback.append((error, point))
    if not fallback:
        return None
    return min(fallback, key=lambda item: item[0])[1].copy()


def _exact_edges_from_vertex_graph(
    vertices: np.ndarray,
    graph_edges: list[DetectedGraphEdge],
    lines: list[CandidateLine],
    incident: list[list[int]],
    distance: np.ndarray,
    settings: Settings,
) -> list[Edge]:
    output: list[Edge] = []
    for graph_edge in graph_edges:
        line_id = graph_edge.line_index
        if line_id < 0:
            continue
        start = _point_for_incident_line(
            vertices[graph_edge.first_vertex],
            line_id,
            incident[graph_edge.first_vertex],
            lines,
            distance.shape[0],
            settings,
        )
        end = _point_for_incident_line(
            vertices[graph_edge.second_vertex],
            line_id,
            incident[graph_edge.second_vertex],
            lines,
            distance.shape[0],
            settings,
        )
        if start is None or end is None or np.linalg.norm(end - start) < 1.5:
            continue
        line = lines[line_id]
        start_t, end_t = _point_t(line, start), _point_t(line, end)
        if start_t > end_t:
            start, end = end, start
            start_t, end_t = end_t, start_t
        support = _sample_support(line, start_t, end_t, distance, settings.evidence_distance_px)
        if support >= max(0.62, settings.output_support):
            output.append(Edge(start, end, 4, support))

    unique: dict[tuple, Edge] = {}
    for edge in output:
        first = (round(float(edge.start[0]), 3), round(float(edge.start[1]), 3))
        second = (round(float(edge.end[0]), 3), round(float(edge.end[1]), 3))
        key = tuple(sorted((first, second)))
        if key not in unique or edge.support > unique[key].support:
            unique[key] = edge
    return list(unique.values())


def _reconstruct_vertex_graph(ink: np.ndarray, settings: Settings) -> tuple[list[Edge], list[CandidateLine], dict]:
    vertices, _, vertex_stats = _vertex_candidates(ink, settings)
    if len(vertices) < 4:
        raise ReconstructionError("没有找到足够多的合法交点。")
    inverse = np.where(ink > 0, 0, 255).astype(np.uint8)
    distance = cv2.distanceTransform(inverse, cv2.DIST_L2, 3)
    graph_edges = _connect_vertex_graph(vertices, distance, settings)
    lines, rejected_groups = _snap_graph_lines(graph_edges, ink.shape[0], settings)
    incident = _refine_concurrent_nodes(vertices, graph_edges, lines, ink.shape[0], settings)
    edges = _exact_edges_from_vertex_graph(vertices, graph_edges, lines, incident, distance, settings)
    return edges, lines, {
        **vertex_stats,
        "vertex_graph_edges": len(graph_edges),
        "graph_line_groups_rejected": rejected_groups,
    }


def _lsd_vertex_candidates(
    square: np.ndarray, ink: np.ndarray, settings: Settings
) -> tuple[np.ndarray, dict]:
    """Recover CP nodes from finite LSD endpoints and finite crossings."""
    detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_ADV)
    segments: list[
        tuple[np.ndarray, np.ndarray, int, np.ndarray, np.ndarray, float, float, float]
    ] = []
    rejected = 0
    for mask in _color_geometry_masks(square, ink):
        detected = detector.detect(mask)[0]
        if detected is None:
            continue
        for values in detected[:, 0]:
            start = np.array(values[:2], dtype=float)
            end = np.array(values[2:], dtype=float)
            length = float(np.linalg.norm(end - start))
            orientation, error = _closest_orientation(
                math.atan2(float(end[1] - start[1]), float(end[0] - start[0]))
            )
            if length < 3.0 or math.degrees(error) > _angle_admission_tolerance_deg(
                length, settings
            ):
                rejected += 1
                continue
            theta = ALLOWED_ANGLES[orientation]
            u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
            n = np.array([-u[1], u[0]], dtype=float)
            offset = float(n @ ((start + end) / 2.0))
            first_t, second_t = float(u @ start), float(u @ end)
            segments.append(
                (start, end, orientation, u, n, offset, min(first_t, second_t), max(first_t, second_t))
            )

    votes: list[np.ndarray] = []
    for start, end, *_ in segments:
        votes.extend((start, end))

    # Long LSD strokes can pass through a crossing without ending there. Add
    # finite segment intersections so those crossings still become split nodes.
    for first_index, first in enumerate(segments):
        _, _, first_orientation, _, first_n, first_offset, first_min, first_max = first
        for second in segments[first_index + 1 :]:
            _, _, second_orientation, _, second_n, second_offset, second_min, second_max = second
            if first_orientation == second_orientation:
                continue
            matrix = np.array([first_n, second_n], dtype=float)
            determinant = float(np.linalg.det(matrix))
            if abs(determinant) < 1e-8:
                continue
            point = np.linalg.solve(matrix, np.array([first_offset, second_offset], dtype=float))
            first_t = float(first[3] @ point)
            second_t = float(second[3] @ point)
            margin = 4.0
            if (
                first_min - margin <= first_t <= first_max + margin
                and second_min - margin <= second_t <= second_max + margin
            ):
                votes.append(point)

    if not votes:
        return np.empty((0, 2), dtype=float), {
            "lsd_vertex_segments": 0,
            "lsd_vertex_candidates": 0,
            "lsd_vertex_angle_rejected": rejected,
        }
    vertices = _cluster_hough_endpoints(
        np.array(votes, dtype=float), radius=3.6, minimum_votes=2
    )
    maximum = float(ink.shape[0] - 1)
    vertices = np.array(
        [
            point
            for point in vertices
            if -1.0 <= point[0] <= maximum + 1.0 and -1.0 <= point[1] <= maximum + 1.0
        ],
        dtype=float,
    )
    return vertices, {
        "lsd_vertex_segments": len(segments),
        "lsd_vertex_candidates": len(vertices),
        "lsd_vertex_angle_rejected": rejected,
    }


def _reconstruct_lsd_vertex_graph(
    square: np.ndarray, ink: np.ndarray, settings: Settings
) -> tuple[list[Edge], list[CandidateLine], dict]:
    vertices, stats = _lsd_vertex_candidates(square, ink, settings)
    if len(vertices) < 4:
        raise ReconstructionError("没有找到足够多的 22.5° 折痕节点。")
    inverse = np.where(ink > 0, 0, 255).astype(np.uint8)
    distance = cv2.distanceTransform(inverse, cv2.DIST_L2, 3)
    graph_edges = _connect_vertex_graph(vertices, distance, settings)
    lines, rejected_groups = _snap_graph_lines(graph_edges, ink.shape[0], settings)
    incident = _refine_concurrent_nodes(vertices, graph_edges, lines, ink.shape[0], settings)
    edges = _exact_edges_from_vertex_graph(vertices, graph_edges, lines, incident, distance, settings)
    return edges, lines, {
        **stats,
        "vertex_graph_edges": len(graph_edges),
        "graph_line_groups_rejected": rejected_groups,
    }


def _recover_exact_skeleton_node_edges(
    square: np.ndarray,
    ink: np.ndarray,
    existing_edges: list[Edge],
    construction_lines: list[CandidateLine],
    settings: Settings,
) -> tuple[list[Edge], dict]:
    """Recover short edges between exact nodes using same-colour skeletons.

    Observed skeleton endpoints/forks only confirm that a node is visible.
    Exported endpoints remain intersections of already constructed 22.5-degree
    rays or their paper-boundary hits.  Directional support is measured on one
    colour mask at a time so crossing ink cannot approve an unrelated chord.
    """
    size = ink.shape[0]
    maximum = float(size - 1)
    masks = _color_geometry_masks(square, ink)
    topology_observations: list[tuple[np.ndarray, int]] = []
    topology_maps: list[np.ndarray] = []
    skeletons: list[np.ndarray] = []
    for mask_index, mask in enumerate(masks):
        skeleton = (_thin_binary_mask(mask) > 0).astype(np.uint8)
        skeletons.append(skeleton)
        padded = np.pad(skeleton, 1, mode="constant")
        ring = (
            padded[:-2, 1:-1],
            padded[:-2, 2:],
            padded[1:-1, 2:],
            padded[2:, 2:],
            padded[2:, 1:-1],
            padded[2:, :-2],
            padded[1:-1, :-2],
            padded[:-2, :-2],
        )
        # Count 0->1 transitions around the eight-neighbour ring. A slanted
        # one-pixel staircase can have three occupied neighbours but only one
        # connected arm; the old raw-neighbour count mislabeled it a fork.
        branches = np.zeros_like(skeleton, dtype=np.uint8)
        for ring_index, current in enumerate(ring):
            following = ring[(ring_index + 1) % len(ring)]
            branches += ((current == 0) & (following > 0)).astype(np.uint8)
        ys, xs = np.where(
            (skeleton > 0) & ((branches == 1) | (branches >= 3))
        )
        topology_map = np.zeros((size, size), dtype=np.uint8)
        topology_map[ys, xs] = 1
        topology_maps.append(topology_map)
        topology_observations.extend(
            (np.array([float(x), float(y)]), mask_index)
            for x, y in zip(xs, ys)
        )
    if not topology_observations:
        return existing_edges, {
            "skeleton_topology_observations": 0,
            "skeleton_exact_nodes": 0,
            "skeleton_exact_edges_recovered": 0,
            "skeleton_exact_rays_recovered": 0,
            "skeleton_recovery_rounds": [],
        }

    def edge_key(start: np.ndarray, end: np.ndarray) -> tuple:
        first = (round(float(start[0]), 4), round(float(start[1]), 4))
        second = (round(float(end[0]), 4), round(float(end[1]), 4))
        return tuple(sorted((first, second)))

    result_edges = list(existing_edges)
    keys = {edge_key(edge.start, edge.end) for edge in result_edges}

    def nearby_topology(point: np.ndarray) -> list[tuple[float, int]]:
        """Return colour-labelled topology pixels close to an exact point."""
        topology_radius = 3.0
        x0 = max(0, int(math.floor(float(point[0]) - topology_radius)))
        x1 = min(size - 1, int(math.ceil(float(point[0]) + topology_radius)))
        y0 = max(0, int(math.floor(float(point[1]) - topology_radius)))
        y1 = min(size - 1, int(math.ceil(float(point[1]) + topology_radius)))
        nearby: list[tuple[float, int]] = []
        for mask_index, topology_map in enumerate(topology_maps):
            local_y, local_x = np.where(
                topology_map[y0 : y1 + 1, x0 : x1 + 1] > 0
            )
            for local_point_y, local_point_x in zip(local_y, local_x):
                observed_x = float(x0 + local_point_x)
                observed_y = float(y0 + local_point_y)
                distance = math.hypot(
                    observed_x - float(point[0]),
                    observed_y - float(point[1]),
                )
                if distance <= topology_radius:
                    nearby.append((distance, mask_index))
        return nearby

    def directional_support(
        skeleton: np.ndarray,
        orientation: int,
        offset: float,
        first_t: float,
        second_t: float,
    ) -> float:
        length = second_t - first_t
        trim = min(1.5, length * 0.18)
        samples = np.linspace(
            first_t + trim,
            second_t - trim,
            max(5, int(length * 2.0)),
        )
        theta = ALLOWED_ANGLES[orientation]
        direction = np.array(
            [math.cos(theta), math.sin(theta)], dtype=float
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        bands = []
        for shift in (-0.75, 0.0, 0.75):
            points = normal * (offset + shift) + samples[:, None] * direction
            xs = np.clip(np.rint(points[:, 0]).astype(int), 0, size - 1)
            ys = np.clip(np.rint(points[:, 1]).astype(int), 0, size - 1)
            bands.append(skeleton[ys, xs] > 0)
        return float(np.mean(np.any(np.stack(bands), axis=0)))

    total_additions = 0
    recovered_rays = 0
    round_additions: list[int] = []
    maximum_exact_nodes = 0
    for _ in range(5):
        exact_nodes: list[dict] = []

        def observe_exact_node(
            point: np.ndarray, parent_indices: set[int]
        ) -> None:
            nearby = nearby_topology(point)
            if not nearby:
                return
            existing = next(
                (
                    value
                    for value in exact_nodes
                    if np.linalg.norm(value["point"] - point) <= 0.3
                ),
                None,
            )
            if existing is None:
                exact_nodes.append(
                    {
                        "point": point.copy(),
                        "parents": set(parent_indices),
                        "masks": {item[1] for item in nearby},
                        "observation_error": min(item[0] for item in nearby),
                    }
                )
            else:
                existing["parents"].update(parent_indices)
                existing["masks"].update(item[1] for item in nearby)
                existing["observation_error"] = min(
                    existing["observation_error"],
                    min(item[0] for item in nearby),
                )

        for first_index, first_line in enumerate(construction_lines):
            for second_index in range(
                first_index + 1, len(construction_lines)
            ):
                point = _line_intersection(
                    first_line, construction_lines[second_index]
                )
                if point is None or not np.all(
                    (0.0 <= point) & (point <= maximum)
                ):
                    continue
                observe_exact_node(point, {first_index, second_index})
            for _, point, _ in _boundary_hits(
                first_line.offset, first_line.orientation, size
            ):
                observe_exact_node(point, {first_index})

        maximum_exact_nodes = max(maximum_exact_nodes, len(exact_nodes))
        if len(exact_nodes) < 2:
            break

        candidates: list[dict] = []

        for first_index, first in enumerate(exact_nodes):
            for second_index in range(first_index + 1, len(exact_nodes)):
                second = exact_nodes[second_index]
                delta = second["point"] - first["point"]
                length = float(np.linalg.norm(delta))
                if not 4.0 <= length <= 80.0:
                    continue
                orientation, angle_error = _closest_orientation(
                    math.atan2(float(delta[1]), float(delta[0]))
                )
                if math.degrees(angle_error) > 0.02:
                    continue
                theta = ALLOWED_ANGLES[orientation]
                direction = np.array(
                    [math.cos(theta), math.sin(theta)], dtype=float
                )
                normal = np.array([-direction[1], direction[0]], dtype=float)
                offset = float(
                    normal @ ((first["point"] + second["point"]) / 2.0)
                )
                first_t, second_t = sorted(
                    (
                        float(direction @ first["point"]),
                        float(direction @ second["point"]),
                    )
                )
                if any(
                    first_t + 0.3
                    < float(direction @ middle["point"])
                    < second_t - 0.3
                    and abs(float(normal @ middle["point"]) - offset) <= 0.2
                    for middle_index, middle in enumerate(exact_nodes)
                    if middle_index not in (first_index, second_index)
                ):
                    continue
                key = edge_key(first["point"], second["point"])
                if key in keys:
                    continue
                common_masks = first["masks"].intersection(second["masks"])
                if not common_masks:
                    continue
                support = max(
                    directional_support(
                        skeletons[mask_index],
                        orientation,
                        offset,
                        first_t,
                        second_t,
                    )
                    for mask_index in common_masks
                )
                # Short exact-node chords contain very few raster samples and
                # are often overwritten at a red/blue crossing. Requiring the
                # former 93% leaves a real crease after one missing pixel. Both
                # endpoints and the span must still agree on one colour mask.
                minimum_support = 0.82 if length <= 24.0 else 0.93
                if support < minimum_support:
                    continue

                matching_line = next(
                    (
                        line_index
                        for line_index, line in enumerate(construction_lines)
                        if line.orientation == orientation
                        and abs(line.offset - offset) <= 0.15
                    ),
                    None,
                )
                parent_source = None
                if matching_line is None:
                    for node in (first, second):
                        parent_options = sorted(node["parents"])
                        parent_pair = next(
                            (
                                (first_parent, second_parent)
                                for parent_position, first_parent in enumerate(
                                    parent_options
                                )
                                for second_parent in parent_options[
                                    parent_position + 1 :
                                ]
                                if construction_lines[first_parent].orientation
                                != construction_lines[second_parent].orientation
                            ),
                            None,
                        )
                        if parent_pair is not None:
                            parent_source = (node, parent_pair)
                            break
                    # A new direction may only start at an already proven
                    # intersection. A boundary observation alone is not a seed.
                    if parent_source is None:
                        continue
                candidates.append(
                    {
                        "first": first["point"].copy(),
                        "second": second["point"].copy(),
                        "orientation": orientation,
                        "offset": offset,
                        "first_t": first_t,
                        "second_t": second_t,
                        "length": length,
                        "support": support,
                        "parent_source": parent_source,
                    }
                )

        if not candidates:
            break
        # Add the strongest independently supported spans first. Edge keys
        # prevent a later iteration from counting the same segment again.
        added_this_round = 0
        new_lines: list[CandidateLine] = []
        for item in sorted(
            candidates,
            key=lambda value: (-value["support"], value["length"]),
        ):
            key = edge_key(item["first"], item["second"])
            if key in keys:
                continue
            result_edges.append(
                Edge(
                    item["first"],
                    item["second"],
                    4,
                    item["support"],
                )
            )
            keys.add(key)
            total_additions += 1
            added_this_round += 1

            if any(
                line.orientation == item["orientation"]
                and abs(line.offset - item["offset"]) <= 0.15
                for line in construction_lines + new_lines
            ):
                continue
            parent_source = item["parent_source"]
            if parent_source is None:
                continue
            node, parent_pair = parent_source
            generation = max(
                construction_lines[parent_pair[0]].generation,
                construction_lines[parent_pair[1]].generation,
            ) + 1
            new_lines.append(
                CandidateLine(
                    orientation=item["orientation"],
                    offset=item["offset"],
                    strength=item["support"] * item["length"],
                    snap_error_px=0.0,
                    anchor_side="",
                    anchor_value=AlgebraicValue(0, 0, 0.0, 0.0),
                    anchor_point=node["point"].copy(),
                    generation=generation,
                    origin_kind="intersection",
                    parent_lines=parent_pair,
                    evidence_intervals=[
                        [item["first_t"], item["second_t"]]
                    ],
                )
            )
        construction_lines.extend(new_lines)
        recovered_rays += len(new_lines)
        round_additions.append(added_this_round)
        if not added_this_round or not new_lines:
            break

    # A recovered short chord is allowed to introduce a new constructed ray,
    # but not a second numerical identity for an already visible ray.  In a
    # clean one-pixel export, neighbouring exact-node pairs can calculate the
    # same centerline a few tenths of a pixel apart.  Leaving both identities
    # in the list makes every later intersection branch into a pair of almost
    # parallel CP rays.  Bind only a recovered ray that is very close to an
    # older construction ray *and whose observed finite interval overlaps it*;
    # truly separate close parallels have disjoint ink intervals and survive.
    primary_line_count = len(construction_lines) - recovered_rays
    rebound_rays = 0
    if recovered_rays and primary_line_count > 0:
        remove_indices: set[int] = set()
        for recovered_index in range(
            primary_line_count, len(construction_lines)
        ):
            recovered = construction_lines[recovered_index]
            recovered_intervals = recovered.evidence_intervals or []
            choices: list[tuple[float, int]] = []
            for primary_index in range(primary_line_count):
                primary = construction_lines[primary_index]
                if primary.orientation != recovered.orientation:
                    continue
                separation = abs(primary.offset - recovered.offset)
                if separation > 1.0:
                    continue
                overlap = max(
                    (
                        max(0.0, min(first_end, second_end) - max(first_start, second_start))
                        for first_start, first_end in recovered_intervals
                        for second_start, second_end in (primary.evidence_intervals or [])
                    ),
                    default=0.0,
                )
                if overlap >= 4.0:
                    choices.append((separation, primary_index))
            if not choices:
                continue
            _, primary_index = min(choices)
            primary = construction_lines[primary_index]
            for edge in result_edges:
                delta = edge.end - edge.start
                orientation, _ = _closest_orientation(
                    math.atan2(float(delta[1]), float(delta[0]))
                )
                if orientation != recovered.orientation:
                    continue
                measured_offset = float(
                    recovered.n @ ((edge.start + edge.end) / 2.0)
                )
                if abs(measured_offset - recovered.offset) > 0.15:
                    continue
                start_t = float(primary.u @ edge.start)
                end_t = float(primary.u @ edge.end)
                edge.start = primary.p0 + start_t * primary.u
                edge.end = primary.p0 + end_t * primary.u
            primary.evidence_intervals = (
                list(primary.evidence_intervals or [])
                + [value.copy() for value in recovered_intervals]
            )
            remove_indices.add(recovered_index)
            rebound_rays += 1
        if remove_indices:
            construction_lines[:] = [
                line
                for line_index, line in enumerate(construction_lines)
                if line_index not in remove_indices
            ]
            recovered_rays -= rebound_rays

    return result_edges, {
        "skeleton_topology_observations": len(topology_observations),
        "skeleton_exact_nodes": maximum_exact_nodes,
        "skeleton_exact_edges_recovered": total_additions,
        "skeleton_exact_rays_recovered": recovered_rays,
        "skeleton_duplicate_rays_rebound": rebound_rays,
        "skeleton_recovery_rounds": round_additions,
    }


def _bind_overlapping_parallel_ray_identities(
    edges: list[Edge],
    construction_lines: list[CandidateLine],
    tolerance_px: float = 0.95,
) -> tuple[list[Edge], int]:
    """Collapse unresolved sub-pixel copies of one visible construction ray.

    This pass is enabled only when the image-wide LSD center correction was
    active.  Two ray identities must have a substantial overlapping finite
    interval; proximity alone never merges disjoint parallel creases.
    """
    records: list[dict] = []
    for edge in edges:
        delta = edge.end - edge.start
        orientation, _ = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        direction = np.array(
            [
                math.cos(ALLOWED_ANGLES[orientation]),
                math.sin(ALLOWED_ANGLES[orientation]),
            ],
            dtype=float,
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((edge.start + edge.end) / 2.0))
        first_t, second_t = sorted(
            (float(direction @ edge.start), float(direction @ edge.end))
        )
        group = next(
            (
                item
                for item in records
                if item["orientation"] == orientation
                and abs(item["offset"] - offset) <= 0.12
            ),
            None,
        )
        if group is None:
            group = {
                "orientation": orientation,
                "direction": direction,
                "normal": normal,
                "offset": offset,
                "edge_indices": [],
                "intervals": [],
            }
            records.append(group)
        group["edge_indices"].append(len(group["edge_indices"]))
        group["intervals"].append([first_t, second_t])

    # Rebuild edge membership with global indices; the first pass above keeps
    # the geometric grouping readable without a parallel bookkeeping array.
    for group in records:
        group["edge_indices"] = []
    for edge_index, edge in enumerate(edges):
        delta = edge.end - edge.start
        orientation, _ = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        direction = np.array(
            [
                math.cos(ALLOWED_ANGLES[orientation]),
                math.sin(ALLOWED_ANGLES[orientation]),
            ],
            dtype=float,
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((edge.start + edge.end) / 2.0))
        group = min(
            (
                item
                for item in records
                if item["orientation"] == orientation
            ),
            key=lambda item: abs(item["offset"] - offset),
        )
        group["edge_indices"].append(edge_index)

    def merged_intervals(values: list[list[float]]) -> list[list[float]]:
        result: list[list[float]] = []
        for first, second in sorted(values):
            if not result or first - result[-1][1] > 0.75:
                result.append([first, second])
            else:
                result[-1][1] = max(result[-1][1], second)
        return result

    def interval_length(values: list[list[float]]) -> float:
        return sum(second - first for first, second in values)

    bindings: dict[int, int] = {}
    ordered = sorted(
        range(len(records)),
        key=lambda index: (records[index]["orientation"], records[index]["offset"]),
    )
    for position, first_index in enumerate(ordered):
        if first_index in bindings:
            continue
        first = records[first_index]
        first_intervals = merged_intervals(first["intervals"])
        for second_index in ordered[position + 1 :]:
            if second_index in bindings:
                continue
            second = records[second_index]
            if second["orientation"] != first["orientation"]:
                if second["orientation"] > first["orientation"]:
                    break
                continue
            separation = second["offset"] - first["offset"]
            if separation > tolerance_px:
                break
            second_intervals = merged_intervals(second["intervals"])
            overlap = sum(
                max(0.0, min(a1, b1) - max(a0, b0))
                for a0, a1 in first_intervals
                for b0, b1 in second_intervals
            )
            shorter = min(
                interval_length(first_intervals),
                interval_length(second_intervals),
            )
            if overlap < 8.0 or overlap < shorter * 0.72:
                continue
            # Retain the identity carrying the larger visible span.  Its
            # offset came from an existing constructed ray, not an average of
            # two raster measurements.
            if interval_length(second_intervals) > interval_length(first_intervals):
                bindings[first_index] = second_index
                first_index = second_index
                first = second
                first_intervals = second_intervals
            else:
                bindings[second_index] = first_index

    if not bindings:
        return edges, 0

    def root(index: int) -> int:
        while index in bindings:
            index = bindings[index]
        return index

    result = [Edge(edge.start.copy(), edge.end.copy(), edge.line_type, edge.support) for edge in edges]
    for loser_index in bindings:
        winner = records[root(loser_index)]
        direction = winner["direction"]
        normal = winner["normal"]
        p0 = normal * winner["offset"]
        for edge_index in records[loser_index]["edge_indices"]:
            edge = result[edge_index]
            first_t = float(direction @ edge.start)
            second_t = float(direction @ edge.end)
            edge.start = p0 + first_t * direction
            edge.end = p0 + second_t * direction

    for line in construction_lines:
        candidate = next(
            (
                index
                for index, item in enumerate(records)
                if item["orientation"] == line.orientation
                and abs(item["offset"] - line.offset) <= 0.15
                and index in bindings
            ),
            None,
        )
        if candidate is not None:
            line.offset = records[root(candidate)]["offset"]
    return result, len(bindings)


def _has_diffuse_color_bleed(square: np.ndarray) -> bool:
    values = square.astype(np.float32)
    blue, green, red = cv2.split(values)
    ratios: list[float] = []
    for dominance in (
        np.maximum(red - np.maximum(blue, green), 0.0),
        np.maximum(blue - np.maximum(red, green), 0.0),
    ):
        high = float(np.percentile(dominance, 99.0))
        if high > 1e-6:
            ratios.append(float(np.percentile(dominance, 75.0)) / high)
    return bool(ratios) and max(ratios) > 0.08


def _thin_binary_mask(mask: np.ndarray) -> np.ndarray:
    """Topology-preserving Zhang-Suen thinning for blurred crease bands."""
    image = (mask > 0).astype(np.uint8)
    if not np.any(image):
        return image * 255

    def neighbors(value: np.ndarray) -> tuple[np.ndarray, ...]:
        padded = np.pad(value, 1, mode="constant")
        return (
            padded[:-2, 1:-1],
            padded[:-2, 2:],
            padded[1:-1, 2:],
            padded[2:, 2:],
            padded[2:, 1:-1],
            padded[2:, :-2],
            padded[1:-1, :-2],
            padded[:-2, :-2],
        )

    for _ in range(64):
        changed = False
        for second_step in (False, True):
            p2, p3, p4, p5, p6, p7, p8, p9 = neighbors(image)
            count = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            transitions = (
                ((p2 == 0) & (p3 == 1)).astype(np.uint8)
                + ((p3 == 0) & (p4 == 1)).astype(np.uint8)
                + ((p4 == 0) & (p5 == 1)).astype(np.uint8)
                + ((p5 == 0) & (p6 == 1)).astype(np.uint8)
                + ((p6 == 0) & (p7 == 1)).astype(np.uint8)
                + ((p7 == 0) & (p8 == 1)).astype(np.uint8)
                + ((p8 == 0) & (p9 == 1)).astype(np.uint8)
                + ((p9 == 0) & (p2 == 1)).astype(np.uint8)
            )
            if second_step:
                first_product = p2 * p4 * p8
                second_product = p2 * p6 * p8
            else:
                first_product = p2 * p4 * p6
                second_product = p4 * p6 * p8
            remove = (
                (image == 1)
                & (count >= 2)
                & (count <= 6)
                & (transitions == 1)
                & (first_product == 0)
                & (second_product == 0)
            )
            if np.any(remove):
                image[remove] = 0
                changed = True
        if not changed:
            break
    return image * 255


def _color_geometry_masks(
    square: np.ndarray,
    ink: np.ndarray,
    include_ridge: bool = False,
) -> list[np.ndarray]:
    values = square.astype(np.float32)
    blue, green, red = cv2.split(values)
    # Color is used only to keep two crossing strokes separate during geometry
    # detection; it is not exported as a mountain/valley assignment. Relative
    # channel dominance survives brightness and contrast changes better than
    # fixed RGB cutoffs.
    red_dominance = red - np.maximum(blue, green)
    blue_dominance = blue - np.maximum(red, green)

    def adaptive_dominance_mask(dominance: np.ndarray) -> np.ndarray:
        robust_high = float(np.percentile(dominance, 99.9))
        # Resampled/JPEG monochrome art often has 1--8 channel levels of
        # harmless warm/cool fringe.  Treating that fringe as red/blue split
        # the black drawing into two incomplete masks and could discard most
        # ray directions.  Real red/blue crease pixels have materially larger
        # channel dominance, even after ordinary antialiasing.
        if robust_high < 12.0:
            return np.zeros(dominance.shape, dtype=np.uint8)
        # The 90th percentile limits diffuse JPEG color bleed without assuming
        # how bright or saturated a true crease is. Sparse diagrams naturally
        # have a zero percentile and fall back to a fraction of their own peak.
        threshold = max(
            float(np.percentile(dominance, 90.0)),
            robust_high * 0.035,
        )
        return (dominance >= threshold).astype(np.uint8) * 255

    red_mask = adaptive_dominance_mask(red_dominance)
    blue_mask = adaptive_dominance_mask(blue_dominance)

    if _has_diffuse_color_bleed(square):
        centerline_masks: list[np.ndarray] = []
        for dominance in (red_dominance, blue_dominance):
            robust_high = float(np.percentile(dominance, 99.9))
            if robust_high <= 1e-6:
                continue
            threshold = max(
                float(np.percentile(dominance, 85.0)),
                robust_high * 0.035,
            )
            band = (dominance >= threshold).astype(np.uint8) * 255
            neighbors = cv2.filter2D(
                (band > 0).astype(np.uint8),
                cv2.CV_16U,
                np.ones((3, 3), dtype=np.uint8),
            )
            band[(band > 0) & (neighbors < 3)] = 0
            centerline = _thin_binary_mask(band)
            if int(np.count_nonzero(centerline)) >= 20:
                centerline_masks.append(centerline)
        if centerline_masks:
            red_mask = centerline_masks[0]
            blue_mask = (
                centerline_masks[1]
                if len(centerline_masks) > 1
                else np.zeros_like(red_mask)
            )
    masks = [
        mask
        for mask in (red_mask, blue_mask)
        if int(np.count_nonzero(mask)) >= 20
    ]

    # LSD on a hard color mask is excellent for a clean export but brittle
    # after resampling or JPEG compression. Add the continuous, locally
    # normalized line response as a geometry-only channel. It nominates rays;
    # exact construction propagation remains responsible for every endpoint.
    if include_ridge:
        _, confidence, _ = _adaptive_geometry_evidence(square)
        ridge_channel = np.clip(confidence * 255.0, 0, 255).astype(np.uint8)
        if int(np.count_nonzero(ridge_channel)) >= 20:
            masks.append(ridge_channel)

    # A degenerate monochrome fixture can have no locally varying confidence.
    if not masks:
        masks = [ink.copy()]
    return masks


def _refine_centerline_offset(
    signal: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    orientation: int,
    measured_offset: float,
    search_radius: float,
) -> tuple[float, float]:
    """Move a detected band edge onto the local crease centerline.

    LSD often reports the two sides of a thick or blurred stroke.  We sample
    the continuous color/darkness response parallel to that finite observation
    and choose the middle of its strongest normal-direction plateau.  Only the
    infinite ray offset changes; raster endpoints are never returned.
    """
    theta = ALLOWED_ANGLES[orientation]
    u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    n = np.array([-u[1], u[0]], dtype=float)
    first_t, second_t = sorted((float(u @ start), float(u @ end)))
    length = second_t - first_t
    if length < 2.0 or search_radius <= 0.0:
        return measured_offset, 0.0

    trim = min(1.5, length * 0.12)
    sample_t = np.linspace(
        first_t + trim,
        second_t - trim,
        max(5, min(80, int(length * 1.5))),
    )
    shifts = np.linspace(
        -search_radius,
        search_radius,
        max(17, int(round(search_radius * 8.0)) + 1),
    )
    responses: list[float] = []
    height, width = signal.shape
    for shift in shifts:
        points = (
            n * (measured_offset + shift)
            + sample_t[:, None] * u
        )
        map_x = np.clip(points[:, 0], 0, width - 1).astype(np.float32)
        map_y = np.clip(points[:, 1], 0, height - 1).astype(np.float32)
        samples = cv2.remap(
            signal,
            map_x.reshape(1, -1),
            map_y.reshape(1, -1),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        responses.append(float(np.mean(samples)))

    values = np.array(responses, dtype=float)
    peak = float(np.max(values))
    if peak <= 0.02:
        return measured_offset, peak
    # A wide blurred crease has a flat maximum. Averaging its upper plateau is
    # more stable than selecting one quantized pixel peak.
    selected = values >= max(peak * 0.90, peak - 0.06)
    selected_values = np.maximum(values[selected], 1e-6)
    correction = float(np.average(shifts[selected], weights=selected_values))
    return measured_offset + correction, peak


def _directional_projection_segments(
    square: np.ndarray,
    settings: Settings,
) -> list[dict]:
    """Find center rays directly in the eight legal directions.

    This path is used for blurred/compressed screenshots where a binary band
    detector sees two unstable sides. Each normal-position peak nominates an
    infinite ray. A separate one-dimensional scan records only its locally
    visible intervals; those raster interval ends remain evidence and are never
    exported as CP nodes.
    """
    size = square.shape[0]
    maximum = float(size - 1)
    values = square.astype(np.float32)
    blue, green, red = cv2.split(values)
    signals = (
        np.maximum(red - np.maximum(blue, green), 0.0),
        np.maximum(blue - np.maximum(red, green), 0.0),
    )
    y_grid, x_grid = np.mgrid[0:size, 0:size]
    maximum_rho = math.sqrt(2.0) * maximum
    bin_width = 0.5
    bin_count = int(math.ceil(2.0 * maximum_rho / bin_width)) + 5
    raw: list[dict] = []
    stroke_radius = max(0.75, settings.evidence_distance_px - 0.75)

    def sample(signal: np.ndarray, points: np.ndarray) -> np.ndarray:
        map_x = np.clip(points[:, 0], 0, size - 1).astype(np.float32)
        map_y = np.clip(points[:, 1], 0, size - 1).astype(np.float32)
        return cv2.remap(
            signal,
            map_x.reshape(1, -1),
            map_y.reshape(1, -1),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        ).ravel()

    for mask_index, original_signal in enumerate(signals):
        noise_floor = float(np.percentile(original_signal, 50.0))
        robust_high = float(np.percentile(original_signal, 99.9))
        if robust_high <= noise_floor + 1e-6:
            continue
        signal = np.clip(
            (original_signal - noise_floor)
            / (robust_high - noise_floor),
            0.0,
            1.0,
        ).astype(np.float32)
        positive = signal[signal > 0]
        signal_threshold = max(
            0.10,
            float(np.percentile(positive, 58.0)) if len(positive) else 1.0,
        )

        for orientation, theta in enumerate(ALLOWED_ANGLES):
            u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
            n = np.array([-u[1], u[0]], dtype=float)
            rho = (n[0] * x_grid + n[1] * y_grid).ravel()
            positions = (rho + maximum_rho) / bin_width
            lower = np.floor(positions).astype(np.int32)
            fraction = positions - lower
            weights = signal.ravel()
            histogram = np.bincount(
                lower,
                weights=weights * (1.0 - fraction),
                minlength=bin_count,
            ) + np.bincount(
                lower + 1,
                weights=weights * fraction,
                minlength=bin_count,
            )
            histogram = cv2.GaussianBlur(
                histogram.reshape(1, -1).astype(np.float32),
                (0, 0),
                sigmaX=1.4,
            ).ravel()

            peak_candidates: list[tuple[float, int, float]] = []
            height_floor = size * 0.006
            prominence_floor = size * 0.012
            window = 14
            for index in range(1, len(histogram) - 1):
                height = float(histogram[index])
                if (
                    height < height_floor
                    or height < histogram[index - 1]
                    or height <= histogram[index + 1]
                ):
                    continue
                left = histogram[max(0, index - window) : index]
                right = histogram[index + 1 : min(len(histogram), index + window + 1)]
                if not len(left) or not len(right):
                    continue
                prominence = height - max(float(np.min(left)), float(np.min(right)))
                if prominence >= prominence_floor:
                    peak_candidates.append((prominence, index, height))

            # Non-maximum suppression is in normalized paper space. A blurred
            # band produces one ray peak even if several neighboring bins tie.
            selected_peaks: list[tuple[float, int, float]] = []
            for item in sorted(peak_candidates, reverse=True):
                if any(abs(item[1] - existing[1]) < 4 for existing in selected_peaks):
                    continue
                selected_peaks.append(item)

            for prominence, peak_index, height in selected_peaks:
                offset = peak_index * bin_width - maximum_rho
                hits = _boundary_hits(offset, orientation, size)
                if len(hits) < 2:
                    continue
                start_bound = min(hit[0] for hit in hits)
                end_bound = max(hit[0] for hit in hits)
                sample_t = np.arange(start_bound, end_bound + 0.01, 0.5)
                center = n * offset + sample_t[:, None] * u

                # Max over the estimated center band handles sub-pixel peak
                # quantization. Side bands provide a local baseline so diffuse
                # JPEG tint cannot look like a long finite crease.
                center_values = np.zeros(len(sample_t), dtype=np.float32)
                for shift in np.linspace(-stroke_radius, stroke_radius, 5):
                    center_values = np.maximum(
                        center_values,
                        sample(signal, center + n * shift),
                    )
                side_distance = stroke_radius * 3.0 + 1.0
                side_values = 0.5 * (
                    sample(signal, center + n * side_distance)
                    + sample(signal, center - n * side_distance)
                )
                visible = (
                    (center_values >= signal_threshold)
                    & (
                        (center_values - side_values >= 0.045)
                        | (center_values >= signal_threshold * 1.45)
                    )
                ).astype(np.uint8)
                # Crossings can erase a few center samples. Close gaps relative
                # to the estimated stroke width, never relative to this image's
                # answer coordinates.
                close_width = max(3, int(round(stroke_radius * 4.0)))
                visible = cv2.morphologyEx(
                    visible.reshape(1, -1),
                    cv2.MORPH_CLOSE,
                    np.ones((1, close_width), dtype=np.uint8),
                ).ravel()

                padded = np.pad(visible, (1, 1), mode="constant")
                changes = np.diff(padded.astype(np.int8))
                starts = np.where(changes == 1)[0]
                ends = np.where(changes == -1)[0]
                for start_index, end_index in zip(starts, ends):
                    first_t = float(sample_t[start_index])
                    second_t = float(sample_t[min(end_index - 1, len(sample_t) - 1)])
                    length = second_t - first_t
                    if length < max(3.0, size * 0.006):
                        continue
                    raw.append(
                        {
                            "mask": mask_index,
                            "orientation": orientation,
                            "offset": offset,
                            "length": length,
                            "start": n * offset + first_t * u,
                            "end": n * offset + second_t * u,
                            "center_response": height + prominence,
                        }
                    )
    return raw


def _propagate_constructible_rays(
    lines: list[CandidateLine],
    intervals: list[list[list[float]]],
    size: int,
    settings: Settings,
    observed_vertices: np.ndarray | None = None,
) -> tuple[list[CandidateLine], list[list[list[float]]], dict]:
    """Propagate from eight free square points and one optional Q(√2) point.

    Every later construction point must be an exact active-ray intersection or
    an exact active-ray contact with the paper boundary.
    """
    maximum = float(size - 1)
    corners = [
        np.array([0.0, 0.0]),
        np.array([maximum, 0.0]),
        np.array([maximum, maximum]),
        np.array([0.0, maximum]),
    ]
    midpoints = [
        np.array([maximum / 2.0, 0.0]),
        np.array([maximum, maximum / 2.0]),
        np.array([maximum / 2.0, maximum]),
        np.array([0.0, maximum / 2.0]),
    ]
    observed_offsets = [line.offset for line in lines]
    uncertainty_scale = float(
        np.clip(settings.evidence_distance_px / 1.75, 1.0, 2.5)
    )
    visual_line_tolerance = 3.2 * uncertainty_scale
    visual_focus_tolerance = 7.5 * uncertainty_scale
    evidence_margin = max(7.0, settings.endpoint_snap_px * 1.25)
    # A child arm can be hidden for longer than a parent at a dense red/blue
    # focus.  This margin only nominates an inactive ray from an already exact
    # construction point; it never supplies an exported endpoint.
    child_focus_evidence_margin = 30.0
    # Image uncertainty and construction displacement are different things.
    # Blur widens the region in which we may *observe* a stroke/focus, but it
    # must never let a correctly centred ray jump to a different legal nearby
    # intersection.  Clean one-pixel drawings retain the established 4.8 px
    # raster tolerance; once the measured stroke is diffuse, displacement is
    # capped to roughly one stroke radius instead of scaling with the blur.
    derivation_tolerance = (
        4.8
        if settings.evidence_distance_px <= 2.0
        else min(3.6, settings.evidence_distance_px + 0.75)
    )

    def has_evidence_with_margin(line_index: int, value: float, margin: float) -> bool:
        return any(
            first_t - margin <= value <= second_t + margin
            for first_t, second_t in intervals[line_index]
        )

    def has_evidence(line_index: int, value: float) -> bool:
        return has_evidence_with_margin(line_index, value, evidence_margin)

    def boundary_side(point: np.ndarray) -> str:
        if abs(float(point[0])) < 1e-5:
            return "左"
        if abs(float(point[0] - maximum)) < 1e-5:
            return "右"
        if abs(float(point[1])) < 1e-5:
            return "上"
        return "下"

    def seed_algebraic(point: np.ndarray) -> AlgebraicValue:
        normalized = -1.0 + 2.0 * point / maximum
        variable = float(
            normalized[1]
            if abs(float(point[0])) < 1e-5 or abs(float(point[0] - maximum)) < 1e-5
            else normalized[0]
        )
        return snap_qsqrt2(variable, settings.algebraic_coefficient_limit)

    active: set[int] = set()
    fallback_seed_points = 0
    corner_seed_points = 0
    midpoint_seed_points = 0
    algebraic_seed_points = 0
    internal_algebraic_seed_points = 0
    boundary_algebraic_seed_points = 0
    deferred_seed_points = 0
    corner_seed_rays = 0
    midpoint_seed_rays = 0
    algebraic_seed_rays = 0
    deferred_seed_rays = 0
    derived_count = 0
    boundary_contact_derived_count = 0
    internal_candidate_count = 0
    internal_candidate_evaluated_count = 0
    internal_candidate_best_payoff = 0
    internal_seed_coordinates: list[float] | None = None
    selected_seed_details: dict | None = None
    internal_candidate_ranking: list[dict] = []

    def activate(
        line_index: int,
        point: np.ndarray,
        kind: str,
        generation: int,
        parents: tuple[int, int] | None,
        algebraic: AlgebraicValue | None = None,
        algebraic_coordinates: tuple[AlgebraicValue, AlgebraicValue] | None = None,
    ) -> None:
        line = lines[line_index]
        line.offset = float(line.n @ point)
        line.anchor_point = point.copy()
        line.generation = generation
        line.origin_kind = kind
        line.parent_lines = parents
        line.anchor_coordinates = None
        if kind in ("algebraic_internal", "algebraic_boundary"):
            line.anchor_side = (
                "internal" if kind == "algebraic_internal" else boundary_side(point)
            )
            line.anchor_coordinates = algebraic_coordinates
            line.anchor_value = (
                algebraic_coordinates[0]
                if algebraic_coordinates is not None
                else algebraic or AlgebraicValue(0, 0, 0.0, 0.0)
            )
        elif kind == "boundary_contact":
            line.anchor_side = boundary_side(point)
        elif kind != "intersection":
            line.anchor_side = boundary_side(point)
            line.anchor_value = algebraic or seed_algebraic(point)
        active.add(line_index)

    # Corners and edge midpoints are the complete free point set. Keep at most
    # one ray per direction at each point so nearby parallel strokes cannot
    # both claim the same origin.
    for seed_kind, seed_points in (("corner", corners), ("midpoint", midpoints)):
        for seed_point in seed_points:
            choices: dict[int, tuple[float, int]] = {}
            for line_index, line in enumerate(lines):
                error = abs(
                    float(line.n @ seed_point) - observed_offsets[line_index]
                )
                if error > derivation_tolerance:
                    continue
                if not has_evidence(line_index, float(line.u @ seed_point)):
                    continue
                previous = choices.get(line.orientation)
                rank = (error, line_index)
                if previous is None or rank < previous:
                    choices[line.orientation] = rank
            activated_here = 0
            for _, line_index in choices.values():
                if line_index in active:
                    continue
                activate(line_index, seed_point, seed_kind, 0, None)
                activated_here += 1
                if seed_kind == "corner":
                    corner_seed_rays += 1
                else:
                    midpoint_seed_rays += 1
            if activated_here:
                if seed_kind == "corner":
                    corner_seed_points += 1
                else:
                    midpoint_seed_points += 1

    def construction_points() -> list[dict]:
        # Corners and edge midpoints are consumed exactly once by the seed
        # pass above.  Reintroducing them in every propagation round allowed a
        # second nearby parallel observation to claim the same free
        # point/direction after the first ray had already been activated.
        raw: list[dict] = []
        ordered = sorted(active)

        # Once an active ray reaches the paper, that exact ray-boundary hit is
        # a derived construction point. It is not another free a+b√2 seed.
        for line_index in ordered:
            line = lines[line_index]
            for hit_t, point, _ in _boundary_hits(
                line.offset, line.orientation, size
            ):
                if not has_evidence_with_margin(line_index, hit_t, 22.0):
                    continue
                raw.append(
                    {
                        "point": point.copy(),
                        "parents": None,
                        "generation": line.generation + 1,
                        "origin_kind": "boundary_contact",
                        "boundary_parent": line_index,
                    }
                )

        # Let a visually detected focus nominate its best active parent pair.
        # Raster rounding can move the exact pair intersection outside the two
        # finite LSD intervals even though both strokes clearly meet at the
        # same visible node.
        if observed_vertices is not None:
            for focus_index, observed_point in enumerate(observed_vertices):
                by_orientation: dict[int, tuple[float, int]] = {}
                for line_index in ordered:
                    line = lines[line_index]
                    visual_error = abs(
                        float(line.n @ observed_point) - observed_offsets[line_index]
                    )
                    if visual_error > visual_line_tolerance or not has_evidence_with_margin(
                        line_index,
                        float(line.u @ observed_point),
                        22.0,
                    ):
                        continue
                    previous = by_orientation.get(line.orientation)
                    if previous is None or (visual_error, line_index) < previous:
                        by_orientation[line.orientation] = (visual_error, line_index)
                incident = [value[1] for value in by_orientation.values()]
                pair_options: list[tuple[float, np.ndarray, tuple[int, int]]] = []
                for position, first_index in enumerate(incident):
                    for second_index in incident[position + 1 :]:
                        point = _line_intersection(lines[first_index], lines[second_index])
                        if point is None:
                            continue
                        distance = float(np.linalg.norm(point - observed_point))
                        if distance <= 12.0:
                            pair_options.append(
                                (distance, point, (first_index, second_index))
                            )
                # Keep all credible exact parent intersections around this
                # visual focus.  Focus grouping below selects one shared node
                # after measuring how many active and inactive ray directions
                # each option can explain.
                for _, point, parents in sorted(pair_options, key=lambda item: item[0]):
                    raw.append(
                        {
                            "point": point,
                            "parents": parents,
                            "generation": max(
                                lines[parents[0]].generation,
                                lines[parents[1]].generation,
                            )
                            + 1,
                            "observed_focus": focus_index,
                        }
                    )

        for position, first_index in enumerate(ordered):
            first = lines[first_index]
            for second_index in ordered[position + 1 :]:
                second = lines[second_index]
                if first.orientation == second.orientation:
                    continue
                point = _line_intersection(first, second)
                if point is None or not (
                    -1.0 <= point[0] <= maximum + 1.0
                    and -1.0 <= point[1] <= maximum + 1.0
                ):
                    continue
                if not (
                    has_evidence(first_index, float(first.u @ point))
                    and has_evidence(second_index, float(second.u @ point))
                ):
                    continue
                raw.append(
                    {
                        "point": point,
                        "parents": (first_index, second_index),
                        "generation": max(first.generation, second.generation) + 1,
                    }
                )

        for item in raw:
            support = 0
            for line_index in active:
                line = lines[line_index]
                if (
                    abs(float(line.n @ item["point"]) - line.offset) <= 1.2
                    and has_evidence(line_index, float(line.u @ item["point"]))
                ):
                    support += 1
            item["support"] = support

            # A visible focus may surround several nearby exact pairwise
            # intersections.  Prefer the exact parent intersection which can
            # also explain the largest number of still-inactive ray
            # directions.  Counting only already-active parents tends to pick
            # a locally strong crossing which strands the remaining arms of a
            # dense focus.
            if observed_vertices is not None and item["parents"] is not None:
                distances = np.linalg.norm(observed_vertices - item["point"], axis=1)
                focus_index = int(np.argmin(distances))
                observed_point = observed_vertices[focus_index]
                compatible_by_orientation: dict[int, tuple[float, float, int]] = {}
                for line_index, line in enumerate(lines):
                    exact_error = abs(
                        float(line.n @ item["point"]) - observed_offsets[line_index]
                    )
                    visual_error = abs(
                        float(line.n @ observed_point) - observed_offsets[line_index]
                    )
                    if (
                        exact_error > derivation_tolerance
                        or visual_error > visual_line_tolerance
                    ):
                        continue
                    if not has_evidence_with_margin(
                        line_index,
                        float(line.u @ observed_point),
                        22.0,
                    ):
                        continue
                    rank = (exact_error, -line.strength, line_index)
                    previous = compatible_by_orientation.get(line.orientation)
                    if previous is None or rank < previous:
                        compatible_by_orientation[line.orientation] = rank
                item["potential_support"] = len(compatible_by_orientation)
                item["potential_strength"] = sum(
                    lines[value[2]].strength
                    for value in compatible_by_orientation.values()
                )
                item["potential_residual"] = sum(
                    value[0] * value[0]
                    for value in compatible_by_orientation.values()
                )
            else:
                item["potential_support"] = item["support"]
                item["potential_strength"] = 0.0
                item["potential_residual"] = 0.0

        # Many pairwise intersections can surround one raster focus. Bind them
        # to the observed focus first, then retain one exact parent intersection
        # for the whole group. This prevents child rays from selecting different
        # nearby points that only look like one focus in the source image.
        unique: list[dict] = [item for item in raw if item["parents"] is None]
        focus_groups: dict[int, list[dict]] = {}
        ungrouped: list[dict] = []
        for item in (value for value in raw if value["parents"] is not None):
            if observed_vertices is None or len(observed_vertices) == 0:
                ungrouped.append(item)
                continue
            distances = np.linalg.norm(observed_vertices - item["point"], axis=1)
            focus_index = int(np.argmin(distances))
            item["focus_distance"] = float(distances[focus_index])
            if item["focus_distance"] <= 7.0:
                focus_groups.setdefault(focus_index, []).append(item)
            else:
                ungrouped.append(item)
        for focus_index, items in focus_groups.items():
            chosen = min(
                items,
                key=lambda item: (
                    -item["potential_support"],
                    -item["support"],
                    item["potential_residual"],
                    item["focus_distance"],
                    -item["potential_strength"],
                    item["generation"],
                ),
            )
            chosen["observed_focus"] = focus_index
            unique.append(chosen)
        for item in sorted(ungrouped, key=lambda value: value["generation"]):
            if any(np.linalg.norm(item["point"] - other["point"]) < 0.45 for other in unique):
                continue
            unique.append(item)
        return unique

    def propagate_existing_points() -> int:
        nonlocal derived_count, boundary_contact_derived_count
        total_added = 0
        while active:
            points = construction_points()
            candidates: list[tuple[tuple, int, int, float]] = []
            for line_index, line in enumerate(lines):
                if line_index in active:
                    continue
                for point_index, item in enumerate(points):
                    point = item["point"]
                    if any(
                        lines[active_index].orientation == line.orientation
                        and abs(
                            float(lines[active_index].n @ point)
                            - lines[active_index].offset
                        )
                        <= 1e-5
                        for active_index in active
                    ):
                        continue
                    error = abs(float(line.n @ point) - observed_offsets[line_index])
                    if error > derivation_tolerance:
                        continue
                    evidence_ok = has_evidence(line_index, float(line.u @ point))
                    focus_index = item.get("observed_focus")
                    if (
                        not evidence_ok
                        and focus_index is not None
                        and observed_vertices is not None
                    ):
                        observed_point = observed_vertices[focus_index]
                        evidence_ok = (
                            abs(
                                float(line.n @ observed_point)
                                - observed_offsets[line_index]
                            )
                            <= 2.5
                            and has_evidence_with_margin(
                                line_index,
                                float(line.u @ observed_point),
                                child_focus_evidence_margin,
                            )
                        )
                    if not evidence_ok:
                        continue
                    # Existing high-degree foci and geometrically close rays
                    # outrank low-degree accidental pairwise intersections.
                    score = (
                        -int(item.get("support", 0)),
                        item["generation"],
                        error,
                        -line.strength,
                    )
                    candidates.append((score, line_index, point_index, error))
            if not candidates:
                break

            used_lines: set[int] = set()
            used_slots: set[tuple[int, int]] = set()
            additions: list[tuple[int, dict]] = []
            for _, line_index, point_index, _ in sorted(candidates, key=lambda value: value[0]):
                slot = (point_index, lines[line_index].orientation)
                if line_index in used_lines or slot in used_slots:
                    continue
                used_lines.add(line_index)
                used_slots.add(slot)
                additions.append((line_index, points[point_index]))
            if not additions:
                break
            for line_index, item in additions:
                origin_kind = item.get("origin_kind", "intersection")
                activate(
                    line_index,
                    item["point"],
                    origin_kind,
                    item["generation"],
                    item["parents"],
                )
                derived_count += 1
                if origin_kind == "boundary_contact":
                    boundary_contact_derived_count += 1
                total_added += 1
        return total_added

    def propagate_observed_intersections() -> int:
        """Recover child rays from exact active-parent intersections.

        A raster focus only nominates nearby parents.  The construction point
        used below is always their exact mathematical intersection.
        """
        nonlocal derived_count
        if observed_vertices is None or len(observed_vertices) == 0:
            return 0
        total_added = 0
        while active:
            candidates: list[tuple[tuple, int, np.ndarray, tuple[int, int]]] = []
            for focus_index, observed_point in enumerate(observed_vertices):
                incident_by_orientation: dict[int, tuple[float, int]] = {}
                for parent_index in active:
                    parent = lines[parent_index]
                    visual_error = abs(
                        float(parent.n @ observed_point)
                        - observed_offsets[parent_index]
                    )
                    if visual_error > visual_line_tolerance or not has_evidence_with_margin(
                        parent_index,
                        float(parent.u @ observed_point),
                        22.0,
                    ):
                        continue
                    rank = (visual_error, parent_index)
                    previous = incident_by_orientation.get(parent.orientation)
                    if previous is None or rank < previous:
                        incident_by_orientation[parent.orientation] = rank
                parents = [value[1] for value in incident_by_orientation.values()]
                exact_options: list[tuple[float, np.ndarray, tuple[int, int]]] = []
                for position, first_index in enumerate(parents):
                    for second_index in parents[position + 1 :]:
                        point = _line_intersection(lines[first_index], lines[second_index])
                        if point is None:
                            continue
                        focus_distance = float(np.linalg.norm(point - observed_point))
                        if focus_distance <= visual_focus_tolerance:
                            exact_options.append(
                                (focus_distance, point, (first_index, second_index))
                            )
                if not exact_options:
                    continue
                # One visual focus must resolve to one exact construction
                # point. Letting every child choose its own nearby parent-pair
                # intersection splits a single node into shifted parallel ray
                # families.
                focus_options: list[
                    tuple[tuple, np.ndarray, tuple[int, int], list[tuple]]
                ] = []
                for focus_distance, point, parents_pair in exact_options:
                    compatible: dict[int, tuple] = {}
                    for line_index, line in enumerate(lines):
                        if line_index in active:
                            continue
                        visual_error = abs(
                            float(line.n @ observed_point)
                            - observed_offsets[line_index]
                        )
                        if visual_error > visual_line_tolerance or not has_evidence_with_margin(
                            line_index,
                            float(line.u @ observed_point),
                            child_focus_evidence_margin,
                        ):
                            continue
                        exact_error = abs(
                            float(line.n @ point) - observed_offsets[line_index]
                        )
                        if exact_error > derivation_tolerance:
                            continue
                        value = (
                            exact_error,
                            visual_error,
                            -line.strength,
                            line_index,
                        )
                        previous = compatible.get(line.orientation)
                        if previous is None or value < previous:
                            compatible[line.orientation] = value
                    if not compatible:
                        continue
                    values = list(compatible.values())
                    residual = sum(value[0] * value[0] for value in values)
                    focus_options.append(
                        (
                            (-len(values), residual, focus_distance),
                            point,
                            parents_pair,
                            values,
                        )
                    )
                if not focus_options:
                    continue
                option_score, point, parents_pair, values = min(
                    focus_options, key=lambda value: value[0]
                )
                focus_distance = option_score[2]
                for exact_error, visual_error, negative_strength, line_index in values:
                    candidates.append(
                        (
                            (
                                exact_error,
                                focus_distance,
                                visual_error,
                                negative_strength,
                                focus_index,
                            ),
                            line_index,
                            point,
                            parents_pair,
                        )
                    )
            if not candidates:
                break
            additions: list[tuple[int, np.ndarray, tuple[int, int]]] = []
            used_lines: set[int] = set()
            for _, line_index, point, parents_pair in sorted(
                candidates, key=lambda item: item[0]
            ):
                if line_index in used_lines:
                    continue
                used_lines.add(line_index)
                additions.append((line_index, point, parents_pair))
            if not additions:
                break
            for line_index, point, parents_pair in additions:
                activate(
                    line_index,
                    point,
                    "intersection",
                    max(
                        lines[parents_pair[0]].generation,
                        lines[parents_pair[1]].generation,
                    )
                    + 1,
                    parents_pair,
                )
                derived_count += 1
                total_added += 1
        return total_added

    def fallback_candidates() -> list[dict]:
        candidates: list[dict] = []

        def incident_lines(point: np.ndarray, tolerance: float) -> list[int]:
            by_orientation: dict[int, tuple[float, int]] = {}
            for line_index, line in enumerate(lines):
                if line_index in active:
                    continue
                error = abs(float(line.n @ point) - observed_offsets[line_index])
                if error > tolerance or not has_evidence(line_index, float(line.u @ point)):
                    continue
                previous = by_orientation.get(line.orientation)
                if previous is None or (error, line_index) < previous:
                    by_orientation[line.orientation] = (error, line_index)
            return [value[1] for value in by_orientation.values()]

        def boundary_has_evidence(line_index: int, point: np.ndarray) -> bool:
            line = lines[line_index]
            value = float(line.u @ point)
            if has_evidence_with_margin(line_index, value, 22.0):
                return True
            # Color overwrite and antialiasing often make a short stroke stop
            # a few pixels before the paper edge.  A detected visual endpoint
            # near the same exact boundary ray is sufficient evidence for a
            # fallback seed; the exported endpoint remains the exact ray/paper
            # intersection, never the raster endpoint.
            if observed_vertices is None:
                return False
            for observed_point in observed_vertices:
                if min(
                    abs(float(observed_point[0])),
                    abs(float(observed_point[1])),
                    abs(maximum - float(observed_point[0])),
                    abs(maximum - float(observed_point[1])),
                ) > 3.0:
                    continue
                if (
                    abs(
                        float(line.n @ observed_point)
                        - observed_offsets[line_index]
                    )
                    <= 2.5
                    and abs(float(line.u @ observed_point) - value) <= 22.0
                ):
                    return True
            return False

        # Midpoints are fallback points and require close visual agreement.
        for point in midpoints:
            incident = incident_lines(point, min(2.0, settings.algebraic_snap_px))
            if incident:
                candidates.append(
                    {
                        "kind": "midpoint",
                        "point": point.copy(),
                        "lines": incident,
                        "algebraic": seed_algebraic(point),
                        "complexity": 0,
                        "error": float(
                            np.mean(
                                [abs(float(lines[index].n @ point) - observed_offsets[index]) for index in incident]
                            )
                        ),
                    }
                )

        algebraic_groups: dict[tuple, dict] = {}
        for line_index, line in enumerate(lines):
            if line_index in active:
                continue
            for hit_t, hit, _ in _boundary_hits(observed_offsets[line_index], line.orientation, size):
                if not boundary_has_evidence(line_index, hit):
                    continue
                normalized = -1.0 + 2.0 * hit / maximum
                vertical = abs(float(hit[0])) < 1.0 or abs(float(hit[0] - maximum)) < 1.0
                variable = float(normalized[1] if vertical else normalized[0])
                algebraic = snap_qsqrt2(variable, settings.algebraic_coefficient_limit)
                if not -1.000001 <= algebraic.value <= 1.000001:
                    continue
                exact_normalized = normalized.copy()
                if vertical:
                    exact_normalized[0] = -1.0 if hit[0] < maximum / 2.0 else 1.0
                    exact_normalized[1] = algebraic.value
                else:
                    exact_normalized[0] = algebraic.value
                    exact_normalized[1] = -1.0 if hit[1] < maximum / 2.0 else 1.0
                point = (exact_normalized + 1.0) * maximum / 2.0
                error = abs(float(line.n @ point) - observed_offsets[line_index])
                if error > settings.algebraic_snap_px:
                    continue
                key = (round(float(point[0]), 3), round(float(point[1]), 3))
                group = algebraic_groups.setdefault(
                    key,
                    {
                        "kind": "a+b√2",
                        "point": point,
                        "lines": [],
                        "algebraic": algebraic,
                        "complexity": abs(algebraic.a) + abs(algebraic.b),
                        "errors": [],
                    },
                )
                if all(lines[index].orientation != line.orientation for index in group["lines"]):
                    group["lines"].append(line_index)
                    group["errors"].append(error)
        for group in algebraic_groups.values():
            group["error"] = float(np.mean(group.pop("errors")))
            candidates.append(group)

        # The seed can be geometrically valid even when the visible crease is
        # far from the paper edge. Admit such a last-resort boundary seed only
        # when a detected interval is bracketed by two distinct, already-active
        # parent rays at exact intersections. Raster endpoints are evidence
        # only and never become CP nodes.
        represented = {
            line_index
            for item in candidates
            for line_index in item["lines"]
        }
        for line_index, line in enumerate(lines):
            if line_index in active or line_index in represented:
                continue
            seed_point = line.anchor_point.copy()
            if min(
                abs(float(seed_point[0])),
                abs(float(seed_point[1])),
                abs(maximum - float(seed_point[0])),
                abs(maximum - float(seed_point[1])),
            ) > 1e-4:
                continue
            exact_offset = float(line.n @ seed_point)
            seed_error = abs(exact_offset - observed_offsets[line_index])
            if seed_error > 0.5:
                continue

            contacts: list[tuple[float, np.ndarray, int]] = []
            for parent_index in active:
                parent = lines[parent_index]
                if parent.orientation == line.orientation:
                    continue
                matrix = np.array([line.n, parent.n], dtype=float)
                if abs(float(np.linalg.det(matrix))) < 1e-9:
                    continue
                point = np.linalg.solve(
                    matrix,
                    np.array([exact_offset, parent.offset], dtype=float),
                )
                if not (
                    0.0 <= point[0] <= maximum
                    and 0.0 <= point[1] <= maximum
                ):
                    continue
                if not has_evidence_with_margin(
                    parent_index,
                    float(parent.u @ point),
                    22.0,
                ):
                    continue
                if observed_vertices is None or not any(
                    np.linalg.norm(observed_point - point) <= 4.5
                    and abs(
                        float(line.n @ observed_point)
                        - observed_offsets[line_index]
                    )
                    <= 2.5
                    and abs(
                        float(parent.n @ observed_point)
                        - observed_offsets[parent_index]
                    )
                    <= 3.2
                    for observed_point in observed_vertices
                ):
                    continue
                contacts.append((float(line.u @ point), point, parent_index))

            bracketed = False
            for raw_start, raw_end in intervals[line_index]:
                start_options = [
                    item for item in contacts if abs(item[0] - raw_start) <= 8.0
                ]
                end_options = [
                    item for item in contacts if abs(item[0] - raw_end) <= 8.0
                ]
                if any(
                    start_parent != end_parent and start_t < end_t
                    for start_t, _, start_parent in start_options
                    for end_t, _, end_parent in end_options
                ):
                    bracketed = True
                    break
            if not bracketed:
                continue

            midpoint = any(
                np.linalg.norm(seed_point - point) <= 1e-4
                for point in midpoints
            )
            candidates.append(
                {
                    "kind": "midpoint" if midpoint else "a+b√2",
                    "point": seed_point,
                    "lines": [line_index],
                    "algebraic": line.anchor_value,
                    "complexity": (
                        0
                        if midpoint
                        else abs(line.anchor_value.a) + abs(line.anchor_value.b)
                    ),
                    "error": seed_error,
                    "deferred": True,
                }
            )

        # Estimate the immediate cascade unlocked by each expensive seed.
        points = construction_points() if active else []
        inactive = [index for index in range(len(lines)) if index not in active]
        for item in candidates:
            unlocked: set[int] = set(item["lines"])
            for seeded_index in item["lines"]:
                seeded = lines[seeded_index]
                seeded_offset = float(seeded.n @ item["point"])
                for active_index in active:
                    parent = lines[active_index]
                    if parent.orientation == seeded.orientation:
                        continue
                    matrix = np.array([seeded.n, parent.n], dtype=float)
                    if abs(float(np.linalg.det(matrix))) < 1e-8:
                        continue
                    point = np.linalg.solve(matrix, np.array([seeded_offset, parent.offset]))
                    if not (0 <= point[0] <= maximum and 0 <= point[1] <= maximum):
                        continue
                    for line_index in inactive:
                        candidate = lines[line_index]
                        if (
                            abs(float(candidate.n @ point) - observed_offsets[line_index]) <= derivation_tolerance
                            and has_evidence(line_index, float(candidate.u @ point))
                        ):
                            unlocked.add(line_index)
            item["payoff"] = len(unlocked)
            item["strength"] = sum(lines[index].strength for index in item["lines"])
        return candidates

    def internal_algebraic_candidates() -> tuple[list[dict], int]:
        """Nominate the one permitted interior Q(sqrt(2)) reference."""
        if observed_vertices is None or len(observed_vertices) == 0:
            return [], 0
        candidates: dict[tuple[float, float], dict] = {}

        def snapped_options(value: float) -> list[AlgebraicValue]:
            low = snap_qsqrt2_bounded(value, 10)
            broad = snap_qsqrt2(
                value, settings.algebraic_coefficient_limit
            )
            values = [low]
            if (broad.a, broad.b) != (low.a, low.b):
                values.append(broad)
            return values

        def point_hypotheses(observed_point: np.ndarray) -> list[dict]:
            normalized = -1.0 + 2.0 * observed_point / maximum
            nx, ny = float(normalized[0]), float(normalized[1])
            tolerance = max(2.5, settings.algebraic_snap_px + 0.75)
            zero = AlgebraicValue(0, 0, 0.0, abs(nx))
            one = AlgebraicValue(1, 0, 1.0, 0.0)
            minus_one = AlgebraicValue(-1, 0, -1.0, 0.0)
            result: list[dict] = []

            def add(
                location: str,
                location_rank: int,
                x_value: AlgebraicValue,
                y_value: AlgebraicValue,
            ) -> None:
                result.append(
                    {
                        "location": location,
                        "location_rank": location_rank,
                        "coordinates": (x_value, y_value),
                    }
                )

            # Symmetry axes are x=1/2 or y=1/2 in paper coordinates, hence
            # x=0 or y=0 in the normalized [-1, 1] system.
            if abs(nx) * maximum / 2.0 <= tolerance:
                for y_value in snapped_options(ny):
                    add("symmetry_axis", 0, zero, y_value)
            if abs(ny) * maximum / 2.0 <= tolerance:
                zero_y = AlgebraicValue(0, 0, 0.0, abs(ny))
                for x_value in snapped_options(nx):
                    add("symmetry_axis", 0, x_value, zero_y)

            # Both square diagonals are structural candidates. Average the
            # two noisy coordinates before algebraic snapping so a visual
            # diagonal point stays exactly on the diagonal.
            if abs(nx - ny) * maximum / math.sqrt(8.0) <= tolerance:
                for value in snapped_options((nx + ny) / 2.0):
                    add("diagonal", 1, value, value)
            if abs(nx + ny) * maximum / math.sqrt(8.0) <= tolerance:
                for value in snapped_options((nx - ny) / 2.0):
                    opposite = AlgebraicValue(
                        -value.a,
                        -value.b,
                        -value.value,
                        value.error,
                    )
                    add("diagonal", 1, value, opposite)

            # One additional algebraic reference is allowed on an edge too;
            # corners and edge midpoints are removed below because they are
            # already part of the fixed free point set.
            if abs(float(observed_point[0])) <= tolerance:
                for y_value in snapped_options(ny):
                    add("boundary", 2, minus_one, y_value)
            if abs(maximum - float(observed_point[0])) <= tolerance:
                for y_value in snapped_options(ny):
                    add("boundary", 2, one, y_value)
            if abs(float(observed_point[1])) <= tolerance:
                for x_value in snapped_options(nx):
                    add("boundary", 2, x_value, minus_one)
            if abs(maximum - float(observed_point[1])) <= tolerance:
                for x_value in snapped_options(nx):
                    add("boundary", 2, x_value, one)

            low_x, broad_x = snapped_options(nx)[0], snapped_options(nx)[-1]
            low_y, broad_y = snapped_options(ny)[0], snapped_options(ny)[-1]
            add("other", 3, low_x, low_y)
            if (broad_x.a, broad_x.b, broad_y.a, broad_y.b) != (
                low_x.a,
                low_x.b,
                low_y.a,
                low_y.b,
            ):
                add("other", 3, broad_x, broad_y)
            return result

        def local_line_shape(point: np.ndarray, incident: list[int]) -> dict:
            openness = 0.0
            throughness = 0.0
            coverage = 0.0
            complete_lines = 0
            for line_index in incident:
                line = lines[line_index]
                values = intervals[line_index]
                if not values:
                    continue
                exact_offset = float(line.n @ point)
                hits = _boundary_hits(exact_offset, line.orientation, size)
                if len(hits) < 2:
                    continue
                chord = max(1.0, abs(hits[-1][0] - hits[0][0]))
                start_t = min(value[0] for value in values)
                end_t = max(value[1] for value in values)
                visible = sum(value[1] - value[0] for value in values)
                point_t = float(line.u @ point)
                left = max(0.0, point_t - start_t)
                right = max(0.0, end_t - point_t)
                envelope_ratio = min(1.0, (end_t - start_t) / chord)
                through_ratio = min(1.0, 2.0 * min(left, right) / chord)
                coverage_ratio = min(1.0, visible / chord)
                openness += envelope_ratio
                throughness += through_ratio
                coverage += coverage_ratio
                if envelope_ratio >= 0.60 and (
                    through_ratio >= 0.18
                    or min(
                        float(point[0]),
                        float(point[1]),
                        maximum - float(point[0]),
                        maximum - float(point[1]),
                    ) <= 1.0
                ):
                    complete_lines += 1
            return {
                "line_openness": openness,
                "line_throughness": throughness,
                "line_coverage": coverage,
                "complete_lines": complete_lines,
            }

        for focus_index, observed_point in enumerate(observed_vertices):
            for hypothesis in point_hypotheses(observed_point):
                algebraic_x, algebraic_y = hypothesis["coordinates"]
                exact_normalized = np.array(
                    [algebraic_x.value, algebraic_y.value], dtype=float
                )
                if not np.all((-1.000001 <= exact_normalized) & (exact_normalized <= 1.000001)):
                    continue
                point = (exact_normalized + 1.0) * maximum / 2.0
                if any(
                    np.linalg.norm(point - free_point) <= 0.75
                    for free_point in corners + midpoints
                ):
                    continue
                snap_error = float(np.linalg.norm(point - observed_point))
                if snap_error > settings.algebraic_snap_px:
                    continue

                incident_by_orientation: dict[int, tuple[tuple, int]] = {}
                for line_index, line in enumerate(lines):
                    exact_error = abs(
                        float(line.n @ point) - observed_offsets[line_index]
                    )
                    visual_error = abs(
                        float(line.n @ observed_point) - observed_offsets[line_index]
                    )
                    if (
                        exact_error > derivation_tolerance
                        or visual_error > visual_line_tolerance
                        or not has_evidence_with_margin(
                            line_index,
                            float(line.u @ observed_point),
                            child_focus_evidence_margin,
                        )
                    ):
                        continue
                    rank = (
                        line_index in active,
                        exact_error,
                        visual_error,
                        -line.strength,
                        line_index,
                    )
                    previous = incident_by_orientation.get(line.orientation)
                    if previous is None or rank < previous[0]:
                        incident_by_orientation[line.orientation] = (rank, line_index)

                incident = [value[1] for value in incident_by_orientation.values()]
                inactive = [index for index in incident if index not in active]
                active_incident = [index for index in incident if index in active]
                if not inactive or len(incident) < 2:
                    continue
                coefficients = (
                    algebraic_x.a,
                    algebraic_x.b,
                    algebraic_y.a,
                    algebraic_y.b,
                )
                complexity = sum(abs(value) for value in coefficients)
                shape = local_line_shape(point, incident)
                key = (round(float(point[0]), 5), round(float(point[1]), 5))
                item = {
                    "kind": (
                        "algebraic_boundary"
                        if hypothesis["location"] == "boundary"
                        else "algebraic_internal"
                    ),
                    "location": hypothesis["location"],
                    "location_rank": hypothesis["location_rank"],
                    "point": point,
                    "lines": inactive,
                    "algebraic_coordinates": (algebraic_x, algebraic_y),
                    "complexity": complexity,
                    "max_coefficient": max(abs(value) for value in coefficients),
                    "low_coefficients": all(abs(value) <= 10 for value in coefficients),
                    "center_distance": float(np.linalg.norm(exact_normalized)),
                    "error": snap_error,
                    "strength": sum(lines[index].strength for index in inactive),
                    "active_incident": len(active_incident),
                    "focus_index": focus_index,
                    **shape,
                }
                previous = candidates.get(key)
                item_rank = (
                    not item["low_coefficients"],
                    item["location_rank"],
                    item["center_distance"],
                    -item["complete_lines"],
                    -item["line_throughness"],
                    -item["line_openness"],
                    item["error"],
                )
                if previous is None:
                    candidates[key] = item
                else:
                    previous_rank = (
                        not previous["low_coefficients"],
                        previous["location_rank"],
                        previous["center_distance"],
                        -previous["complete_lines"],
                        -previous["line_throughness"],
                        -previous["line_openness"],
                        previous["error"],
                    )
                    if item_rank < previous_rank:
                        candidates[key] = item

        candidate_pool = list(candidates.values())
        if not candidate_pool:
            return [], 0

        # Full cascade evaluation is deliberately limited. On a dense CP,
        # evaluating every raster focus repeats the same quadratic closure
        # dozens of times. Use a small union of complementary cheap ranks so
        # locally sparse but simple or already-connected seeds remain eligible.
        shortlist_by_key: dict[tuple[float, float], dict] = {}

        def retain(values: list[dict]) -> None:
            for value in values[:2]:
                point_key = (
                    round(float(value["point"][0]), 5),
                    round(float(value["point"][1]), 5),
                )
                shortlist_by_key[point_key] = value

        prior_key = lambda item: (
            not item["low_coefficients"],
            item["center_distance"],
            -item["complete_lines"],
            -item["line_throughness"],
            -item["line_openness"],
            item["error"],
            item["complexity"],
        )
        for location_rank in range(4):
            location_items = [
                item
                for item in candidate_pool
                if item["location_rank"] == location_rank
            ]
            retain(
                sorted(
                    [item for item in location_items if item["low_coefficients"]],
                    key=prior_key,
                )
            )
            retain(
                sorted(
                    [item for item in location_items if not item["low_coefficients"]],
                    key=lambda item: (
                        item["center_distance"],
                        item["error"],
                        -item["line_openness"],
                    ),
                )
            )
            retain(
                sorted(
                    location_items,
                    key=lambda item: (
                        item["error"],
                        item["center_distance"],
                        item["complexity"],
                    ),
                )
            )
        retain(sorted(candidate_pool, key=lambda item: (not item["low_coefficients"], item["complexity"], item["error"])))
        retain(sorted(candidate_pool, key=lambda item: (-item["complete_lines"], -item["line_throughness"], -item["line_openness"], item["error"])))
        retain(sorted(candidate_pool, key=lambda item: (-len(item["lines"]), item["error"])))
        shortlisted = list(shortlist_by_key.values())

        # The right unique reference is the one that unlocks the largest
        # exact-intersection cascade, not merely the focus with the most local
        # arms. Evaluate each candidate by temporarily running the real
        # propagation rules, then restore the graph byte-for-byte.
        nonlocal derived_count, boundary_contact_derived_count
        base_active = active.copy()
        base_derived_count = derived_count
        base_boundary_contact_derived_count = boundary_contact_derived_count
        for item in shortlisted:
            line_state = [
                (
                    line.offset,
                    line.anchor_point.copy(),
                    line.generation,
                    line.origin_kind,
                    line.parent_lines,
                    line.anchor_side,
                    line.anchor_value,
                    line.anchor_coordinates,
                )
                for line in lines
            ]
            for line_index in item["lines"]:
                activate(
                    line_index,
                    item["point"],
                    "algebraic_internal",
                    0,
                    None,
                    algebraic_coordinates=item["algebraic_coordinates"],
                )
            propagate_existing_points()
            propagate_observed_intersections()
            propagate_existing_points()
            activated = active.difference(base_active)
            item["payoff"] = len(activated)
            item["payoff_strength"] = float(
                sum(lines[index].strength for index in activated)
            )
            item["payoff_visible_length"] = float(
                sum(
                    end_t - start_t
                    for index in activated
                    for start_t, end_t in intervals[index]
                )
            )
            residual_weight = 0.0
            residual_square_sum = 0.0
            residual_max = 0.0
            for index in active:
                visible_length = sum(
                    end_t - start_t for start_t, end_t in intervals[index]
                )
                weight = max(1.0, visible_length)
                residual = abs(lines[index].offset - observed_offsets[index])
                residual_weight += weight
                residual_square_sum += weight * residual * residual
                residual_max = max(residual_max, residual)
            item["payoff_offset_rms"] = math.sqrt(
                residual_square_sum / max(residual_weight, 1.0)
            )
            item["payoff_offset_max"] = residual_max

            active.clear()
            active.update(base_active)
            derived_count = base_derived_count
            boundary_contact_derived_count = base_boundary_contact_derived_count
            for line, state in zip(lines, line_state):
                (
                    line.offset,
                    anchor_point,
                    line.generation,
                    line.origin_kind,
                    line.parent_lines,
                    line.anchor_side,
                    line.anchor_value,
                    line.anchor_coordinates,
                ) = state
                line.anchor_point = anchor_point
        return shortlisted, len(candidate_pool)

    propagate_existing_points()
    propagate_observed_intersections()
    propagate_existing_points()
    # This is intentionally a single selection, not a fallback loop. After
    # this optional reference, only exact intersections may activate rays.
    candidates, internal_candidate_count = internal_algebraic_candidates()
    internal_candidate_evaluated_count = len(candidates)
    if candidates:
        internal_candidate_ranking = [
            {
                "point": [
                    round(float(item["point"][0]), 5),
                    round(float(item["point"][1]), 5),
                ],
                "payoff": int(item["payoff"]),
                "payoff_visible_length": round(
                    float(item["payoff_visible_length"]), 3
                ),
                "payoff_strength": round(float(item["payoff_strength"]), 3),
                "payoff_offset_rms": round(float(item["payoff_offset_rms"]), 5),
                "payoff_offset_max": round(float(item["payoff_offset_max"]), 5),
                "location": item["location"],
                "location_rank": int(item["location_rank"]),
                "center_distance": round(float(item["center_distance"]), 6),
                "max_coefficient": int(item["max_coefficient"]),
                "low_coefficients": bool(item["low_coefficients"]),
                "complete_lines": int(item["complete_lines"]),
                "line_throughness": round(float(item["line_throughness"]), 5),
                "line_openness": round(float(item["line_openness"]), 5),
                "line_coverage": round(float(item["line_coverage"]), 5),
                "core_shape_score": round(
                    float(
                        2.0 * item["complete_lines"]
                        + item["line_throughness"]
                        + item["line_openness"]
                    ),
                    5,
                ),
                "soft_prior_score": round(
                    float(
                        0.18 * item["location_rank"]
                        + item["center_distance"]
                        + min(
                            0.10,
                            0.003 * max(0, item["max_coefficient"] - 10),
                        )
                        - 0.025
                        * min(
                            12.0,
                            2.0 * item["complete_lines"]
                            + item["line_throughness"]
                            + item["line_openness"],
                        )
                        + 0.030 * item["error"]
                        + 0.080 * item["payoff_offset_rms"]
                        + 0.15
                        * max(
                            0.0,
                            item["payoff_offset_max"]
                            / max(derivation_tolerance, 1e-6)
                            - 0.65,
                        )
                    ),
                    6,
                ),
                "seed_rays": len(item["lines"]),
                "active_incident": int(item["active_incident"]),
                "complexity": int(item["complexity"]),
                "snap_error": round(float(item["error"]), 5),
            }
            for item in sorted(
                candidates,
                key=lambda item: (
                    -item["payoff"],
                    0.18 * item["location_rank"]
                    + item["center_distance"]
                    + min(
                        0.10,
                        0.003 * max(0, item["max_coefficient"] - 10),
                    )
                    - 0.025
                    * min(
                        12.0,
                        2.0 * item["complete_lines"]
                        + item["line_throughness"]
                        + item["line_openness"],
                    )
                    + 0.030 * item["error"]
                    + 0.080 * item["payoff_offset_rms"]
                    + 0.15
                    * max(
                        0.0,
                        item["payoff_offset_max"]
                        / max(derivation_tolerance, 1e-6)
                        - 0.65,
                    ),
                    item["payoff_offset_rms"],
                    item["error"],
                    item["complexity"],
                ),
            )[:10]
        ]
        best_payoff = max(item["payoff"] for item in candidates)
        payoff_margin = (
            0
            if best_payoff < 20
            else max(1, int(round(best_payoff * 0.02)))
        )
        eligible = [
            item
            for item in candidates
            if item["payoff"] >= best_payoff - payoff_margin
        ]
        # Broad, two-sided straight lines are only a soft clue. A true core
        # may still look fragmented because of occlusion or short visible
        # arms, so shape and coefficient size must never disqualify it.
        for item in eligible:
            item["core_shape_score"] = (
                2.0 * item["complete_lines"]
                + item["line_throughness"]
                + item["line_openness"]
            )
            coefficient_excess = max(0, item["max_coefficient"] - 10)
            residual_limit_ratio = (
                item["payoff_offset_max"] / max(derivation_tolerance, 1e-6)
            )
            item["seed_prior_score"] = (
                0.18 * item["location_rank"]
                + item["center_distance"]
                + min(0.10, 0.003 * coefficient_excess)
                - 0.025 * min(12.0, item["core_shape_score"])
                + 0.030 * item["error"]
                + 0.080 * item["payoff_offset_rms"]
                + 0.15 * max(0.0, residual_limit_ratio - 0.65)
            )
        chosen = min(
            eligible,
            key=lambda item: (
                item["seed_prior_score"],
                best_payoff - item["payoff"],
                item["payoff_offset_rms"],
                item["error"],
                item["complexity"],
            ),
        )
        internal_candidate_best_payoff = int(chosen["payoff"])
        chosen_internal_point = [
            float(chosen["point"][0]),
            float(chosen["point"][1]),
        ]
        internal_seed_coordinates = [
            float(chosen["algebraic_coordinates"][0].value),
            float(chosen["algebraic_coordinates"][1].value),
        ]
        selected_seed_details = {
            "location": chosen["location"],
            "center_distance": round(float(chosen["center_distance"]), 6),
            "max_coefficient": int(chosen["max_coefficient"]),
            "low_coefficients": bool(chosen["low_coefficients"]),
            "complete_lines": int(chosen["complete_lines"]),
            "line_throughness": round(float(chosen["line_throughness"]), 5),
            "line_openness": round(float(chosen["line_openness"]), 5),
            "payoff": int(chosen["payoff"]),
            "payoff_offset_rms": round(float(chosen["payoff_offset_rms"]), 5),
        }
        fallback_seed_points += 1
        algebraic_seed_points = 1
        if chosen["kind"] == "algebraic_boundary":
            boundary_algebraic_seed_points = 1
        else:
            internal_algebraic_seed_points = 1
        for line_index in chosen["lines"]:
            activate(
                line_index,
                chosen["point"],
                chosen["kind"],
                0,
                None,
                algebraic_coordinates=chosen["algebraic_coordinates"],
            )
            algebraic_seed_rays += 1
        propagate_existing_points()
        propagate_observed_intersections()
        propagate_existing_points()

    # Correct a rare dense-focus failure after the construction graph is
    # complete. A child can initially bind to a legal but secondary parent
    # pair several pixels away from its measured stroke. Rebind only when a
    # different pair of already-active rays has an exact intersection very
    # close to the same observed focus and materially reduces that offset.
    reanchored_rays = 0
    if observed_vertices is not None and len(observed_vertices):
        for line_index in sorted(active):
            line = lines[line_index]
            if line.origin_kind in (
                "corner",
                "midpoint",
                "algebraic_internal",
                "algebraic_boundary",
            ):
                continue
            current_error = abs(line.offset - observed_offsets[line_index])
            if current_error < 3.0:
                continue
            options: list[tuple[float, float, np.ndarray, tuple[int, int]]] = []
            for observed_point in observed_vertices:
                visual_error = abs(
                    float(line.n @ observed_point) - observed_offsets[line_index]
                )
                if visual_error > 2.5 or not has_evidence_with_margin(
                    line_index,
                    float(line.u @ observed_point),
                    22.0,
                ):
                    continue
                possible_parents: list[int] = []
                for parent_index in active:
                    if parent_index == line_index:
                        continue
                    parent = lines[parent_index]
                    if abs(
                        float(parent.n @ observed_point)
                        - observed_offsets[parent_index]
                    ) > 3.2:
                        continue
                    if not has_evidence_with_margin(
                        parent_index,
                        float(parent.u @ observed_point),
                        45.0,
                    ):
                        continue
                    possible_parents.append(parent_index)
                for position, first_index in enumerate(possible_parents):
                    for second_index in possible_parents[position + 1 :]:
                        if (
                            lines[first_index].orientation
                            == lines[second_index].orientation
                        ):
                            continue
                        point = _line_intersection(
                            lines[first_index], lines[second_index]
                        )
                        if point is None:
                            continue
                        focus_distance = float(
                            np.linalg.norm(point - observed_point)
                        )
                        if focus_distance > 2.5:
                            continue
                        candidate_error = abs(
                            float(line.n @ point) - observed_offsets[line_index]
                        )
                        if (
                            candidate_error > 1.5
                            or current_error - candidate_error < 2.0
                        ):
                            continue
                        options.append(
                            (
                                candidate_error,
                                focus_distance,
                                point,
                                (first_index, second_index),
                            )
                        )
            if not options:
                continue
            _, _, point, parents_pair = min(
                options,
                key=lambda item: (item[0], item[1]),
            )
            activate(
                line_index,
                point,
                "intersection",
                max(
                    lines[parents_pair[0]].generation,
                    lines[parents_pair[1]].generation,
                )
                + 1,
                parents_pair,
            )
            reanchored_rays += 1

    kept_indices = sorted(active)
    kept_lines = [lines[index] for index in kept_indices]
    kept_intervals = [intervals[index] for index in kept_indices]
    generation = max((line.generation for line in kept_lines), default=0)
    return kept_lines, kept_intervals, {
        "construction_seed_rays": corner_seed_rays + midpoint_seed_rays + algebraic_seed_rays,
        "corner_seed_rays": corner_seed_rays,
        "midpoint_seed_rays": midpoint_seed_rays,
        "algebraic_seed_rays": algebraic_seed_rays,
        "fallback_seed_points": fallback_seed_points,
        "corner_seed_points": corner_seed_points,
        "midpoint_seed_points": midpoint_seed_points,
        "algebraic_seed_points": algebraic_seed_points,
        "internal_algebraic_seed_points": internal_algebraic_seed_points,
        "boundary_algebraic_seed_points": boundary_algebraic_seed_points,
        "internal_algebraic_seed_limit": 1,
        "algebraic_seed_limit": 1,
        "internal_algebraic_candidate_count": internal_candidate_count,
        "internal_algebraic_candidates_evaluated": internal_candidate_evaluated_count,
        "internal_algebraic_best_payoff": internal_candidate_best_payoff,
        "internal_algebraic_seed_coordinates": internal_seed_coordinates,
        "selected_algebraic_seed": selected_seed_details,
        "internal_algebraic_seed_point_px": chosen_internal_point if candidates else None,
        "internal_algebraic_candidate_ranking": internal_candidate_ranking,
        "deferred_seed_points": deferred_seed_points,
        "derived_rays": derived_count,
        "boundary_contact_derived_rays": boundary_contact_derived_count,
        "reanchored_rays": reanchored_rays,
        "deferred_seed_rays": deferred_seed_rays,
        "construction_generations": generation if kept_lines else 0,
        "construction_snap_tolerance_px": round(derivation_tolerance, 4),
        "unresolved_rays": len(lines) - len(kept_lines),
    }


def _recover_fragmented_rays_from_primary(
    primary_edges: list[Edge],
    primary_lines: list[CandidateLine],
    raw: list[dict],
    weak_groups: list[list[int]],
    size: int,
    distance: np.ndarray,
    settings: Settings,
) -> tuple[list[Edge], list[CandidateLine], dict]:
    """Recover fragmented rays only from nodes in the high-confidence graph.

    Weak projection groups are not allowed to validate one another.  A group
    must attach to a degree-four primary node (two already exported rays), and
    every recovered finite span ends at another primary edge or the boundary.
    Pixel intervals decide visibility only; they never become CP endpoints.
    """
    if not primary_edges or not weak_groups:
        return [], [], {"fragmented_rays_recovered": 0, "fragmented_edges_recovered": 0}

    maximum = float(size - 1)

    def point_segment_distance(point: np.ndarray, edge: Edge) -> float:
        delta = edge.end - edge.start
        denominator = float(delta @ delta)
        if denominator <= 1e-12:
            return float(np.linalg.norm(point - edge.start))
        factor = float(
            np.clip(((point - edge.start) @ delta) / denominator, 0.0, 1.0)
        )
        return float(np.linalg.norm(point - (edge.start + factor * delta)))

    def edge_orientation(edge: Edge) -> int:
        delta = edge.end - edge.start
        return _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )[0]

    nodes: list[np.ndarray] = []
    degrees: list[int] = []
    for edge in primary_edges:
        for point in (edge.start, edge.end):
            existing = next(
                (
                    index
                    for index, value in enumerate(nodes)
                    if np.linalg.norm(point - value) <= 0.2
                ),
                None,
            )
            if existing is None:
                nodes.append(point.copy())
                degrees.append(1)
            else:
                degrees[existing] += 1

    def merged_intervals(group: list[int], line: CandidateLine) -> list[list[float]]:
        values = []
        for raw_index in group:
            first_t = float(line.u @ raw[raw_index]["start"])
            second_t = float(line.u @ raw[raw_index]["end"])
            values.append(sorted((first_t, second_t)))
        merged: list[list[float]] = []
        for first_t, second_t in sorted(values):
            if not merged or first_t - merged[-1][1] > 8.0:
                merged.append([first_t, second_t])
            else:
                merged[-1][1] = max(merged[-1][1], second_t)
        return merged

    proposals: list[dict] = []
    for group in weak_groups:
        weights = np.array([raw[index]["length"] for index in group], dtype=float)
        total_length = float(weights.sum())
        longest = float(weights.max())
        continuous_share = longest / max(total_length, 1e-9)
        if (
            longest < size * 0.03
            or total_length < size * 0.115
            or continuous_share < 0.20
        ):
            continue
        orientation = raw[group[0]]["orientation"]
        measured_offset = float(
            np.average(
                [raw[index]["offset"] for index in group],
                weights=weights,
            )
        )
        # Do not recover a second raster ridge beside an existing primary ray.
        if any(
            line.orientation == orientation
            and abs(line.offset - measured_offset) <= 2.25
            for line in primary_lines
        ):
            continue
        reference = AlgebraicValue(0, 0, 0.0, 0.0)
        measured_line = CandidateLine(
            orientation,
            measured_offset,
            total_length,
            0.0,
            "",
            reference,
            np.zeros(2, dtype=float),
        )
        evidence_intervals = merged_intervals(group, measured_line)

        hosts: list[tuple[float, int, int, np.ndarray]] = []
        for node_index, point in enumerate(nodes):
            if degrees[node_index] < 4:
                continue
            error = abs(float(measured_line.n @ point) - measured_offset)
            if error > 2.0:
                continue
            point_t = float(measured_line.u @ point)
            local_evidence = max(
                (
                    max(
                        0.0,
                        min(second_t, point_t + 18.0)
                        - max(first_t, point_t - 18.0),
                    )
                    for first_t, second_t in evidence_intervals
                ),
                default=0.0,
            )
            if local_evidence >= 7.0:
                hosts.append((error, -degrees[node_index], node_index, point))
        if not hosts:
            continue
        error, negative_degree, node_index, host = min(hosts)
        proposals.append(
            {
                "group": group,
                "orientation": orientation,
                "measured_offset": measured_offset,
                "strength": total_length,
                "intervals": evidence_intervals,
                "host_index": node_index,
                "host": host.copy(),
                "host_error": error,
                "host_degree": -negative_degree,
            }
        )

    # One primary focus cannot emit two nearly identical weak rays.  Rank by
    # centre agreement first, then topology and accumulated finite evidence.
    selected: list[dict] = []
    for item in sorted(
        proposals,
        key=lambda value: (
            value["host_index"],
            value["orientation"],
            value["host_error"],
            -value["host_degree"],
            -value["strength"],
        ),
    ):
        if any(
            other["host_index"] == item["host_index"]
            and other["orientation"] == item["orientation"]
            for other in selected
        ):
            continue
        selected.append(item)

    recovered_edges: list[Edge] = []
    recovered_lines: list[CandidateLine] = []
    for item in selected:
        orientation = item["orientation"]
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        host = item["host"]
        exact_offset = float(n @ host)
        host_t = float(u @ host)

        contacts: list[tuple[float, np.ndarray, bool]] = [
            (host_t, host.copy(), True)
        ]
        for edge in primary_edges:
            other_orientation = edge_orientation(edge)
            if other_orientation == orientation:
                continue
            other_theta = ALLOWED_ANGLES[other_orientation]
            other_u = np.array(
                [math.cos(other_theta), math.sin(other_theta)], dtype=float
            )
            other_n = np.array([-other_u[1], other_u[0]], dtype=float)
            other_offset = float(
                other_n @ ((edge.start + edge.end) / 2.0)
            )
            matrix = np.array([n, other_n], dtype=float)
            if abs(float(np.linalg.det(matrix))) < 1e-9:
                continue
            point = np.linalg.solve(
                matrix, np.array([exact_offset, other_offset], dtype=float)
            )
            if point_segment_distance(point, edge) <= 0.15:
                contacts.append((float(u @ point), point, False))
        contacts.extend(
            (hit_t, point.copy(), False)
            for hit_t, point, _ in _boundary_hits(
                exact_offset, orientation, size
            )
        )
        ordered: list[tuple[float, np.ndarray, bool]] = []
        for contact in sorted(contacts, key=lambda value: value[0]):
            if not ordered or contact[0] - ordered[-1][0] > 0.3:
                ordered.append(contact)
            elif contact[2]:
                ordered[-1] = contact
        host_position = next(
            (index for index, value in enumerate(ordered) if value[2]),
            None,
        )
        if host_position is None:
            continue

        admissible: list[tuple[int, Edge]] = []
        exact_line = CandidateLine(
            orientation,
            exact_offset,
            item["strength"],
            item["host_error"],
            "",
            AlgebraicValue(0, 0, 0.0, 0.0),
            host.copy(),
        )
        for interval_index, (first, second) in enumerate(
            zip(ordered, ordered[1:])
        ):
            length = second[0] - first[0]
            if length < 2.0:
                continue
            overlap = sum(
                max(
                    0.0,
                    min(second[0], raw_end) - max(first[0], raw_start),
                )
                for raw_start, raw_end in item["intervals"]
            )
            if overlap / length < 0.45:
                continue
            support = _sample_support(
                exact_line,
                first[0],
                second[0],
                distance,
                settings.evidence_distance_px + 0.3,
            )
            if support >= 0.68:
                admissible.append(
                    (
                        interval_index,
                        Edge(first[1].copy(), second[1].copy(), 4, support),
                    )
                )

        # Traverse only the admissible component incident to the primary host.
        reachable = {host_position}
        chosen: list[tuple[int, Edge]] = []
        changed = True
        while changed:
            changed = False
            for interval_index, edge in admissible:
                if any(interval_index == value[0] for value in chosen):
                    continue
                if interval_index in reachable or interval_index + 1 in reachable:
                    chosen.append((interval_index, edge))
                    reachable.update((interval_index, interval_index + 1))
                    changed = True
        if not chosen:
            continue

        parent_indices = [
            index
            for index, line in enumerate(primary_lines)
            if abs(float(line.n @ host) - line.offset) <= 0.2
        ]
        parent_pair = next(
            (
                (first, second)
                for position, first in enumerate(parent_indices)
                for second in parent_indices[position + 1 :]
                if primary_lines[first].orientation
                != primary_lines[second].orientation
            ),
            None,
        )
        if parent_pair is None:
            continue
        exact_line.anchor_point = host.copy()
        exact_line.origin_kind = "intersection"
        exact_line.parent_lines = parent_pair
        exact_line.generation = max(
            primary_lines[parent_pair[0]].generation,
            primary_lines[parent_pair[1]].generation,
        ) + 1
        exact_line.evidence_intervals = [
            value.copy() for value in item["intervals"]
        ]
        recovered_lines.append(exact_line)
        recovered_edges.extend(edge for _, edge in chosen)

    return recovered_edges, recovered_lines, {
        "fragmented_rays_recovered": len(recovered_lines),
        "fragmented_edges_recovered": len(recovered_edges),
    }


def _reconstruct_lsd_rays(
    square: np.ndarray, ink: np.ndarray, settings: Settings
) -> tuple[list[Edge], list[CandidateLine], dict]:
    """Build exact rays from color-separated finite-line evidence.

    LSD endpoints are never exported. They only delimit evidence intervals;
    every final endpoint is replaced by a boundary hit or another exact ray.
    """
    size = ink.shape[0]
    # The continuous ridge channel is extracted separately and must be
    # center-refined before joining these hard-mask candidates. Feeding its
    # two blurred edges directly into this path creates duplicate parallel
    # rays and needlessly expands construction propagation.
    masks = _color_geometry_masks(square, ink)
    diffuse_input = _has_diffuse_color_bleed(square)
    detector = cv2.createLineSegmentDetector(
        cv2.LSD_REFINE_NONE if diffuse_input else cv2.LSD_REFINE_ADV
    )
    raw: list[dict] = []
    rejected_angle = 0
    centered_segment_count = 0
    center_shift_sum = 0.0
    center_shift_max = 0.0
    if diffuse_input:
        raw = _directional_projection_segments(square, settings)
    else:
        values = square.astype(np.float32)
        blue, green, red = cv2.split(values)
        continuous_signals = (
            np.maximum(red - np.maximum(blue, green), 0.0) / 255.0,
            np.maximum(blue - np.maximum(red, green), 0.0) / 255.0,
        )
        for mask_index, mask in enumerate(masks):
            detected = detector.detect(mask)[0]
            if detected is None:
                continue
            for values in detected[:, 0]:
                x1, y1, x2, y2 = map(float, values)
                length = math.hypot(x2 - x1, y2 - y1)
                orientation, error = _closest_orientation(
                    math.atan2(y2 - y1, x2 - x1)
                )
                if (
                    math.degrees(error)
                    > _angle_admission_tolerance_deg(length, settings)
                    or length < 3.0
                ):
                    rejected_angle += 1
                    continue
                theta = ALLOWED_ANGLES[orientation]
                u = np.array(
                    [math.cos(theta), math.sin(theta)], dtype=float
                )
                n = np.array([-u[1], u[0]], dtype=float)
                midpoint = np.array(
                    [(x1 + x2) / 2.0, (y1 + y2) / 2.0]
                )
                measured_offset = float(n @ midpoint)
                # Use the continuous colour response, not the thresholded
                # mask, to find the drawn stroke's sub-pixel ridge.  The hard
                # mask is deliberately generous and its staircase boundary
                # can put otherwise identical finite pieces a fraction of a
                # pixel apart after later intersection propagation.
                centered_offset, _ = _refine_centerline_offset(
                    continuous_signals[min(mask_index, 1)],
                    np.array([x1, y1]),
                    np.array([x2, y2]),
                    orientation,
                    measured_offset,
                    4.5,
                )
                center_shift = abs(centered_offset - measured_offset)
                if center_shift > 1e-6:
                    centered_segment_count += 1
                    center_shift_sum += center_shift
                    center_shift_max = max(center_shift_max, center_shift)
                raw.append(
                    {
                        "mask": mask_index,
                        "orientation": orientation,
                        "offset": centered_offset,
                        "measured_offset": measured_offset,
                        "length": length,
                        "start": np.array([x1, y1]),
                        "end": np.array([x2, y2]),
                        "center_response": 1.0,
                    }
                )

        # Center correction is needed when resampling makes LSD consistently
        # see the two shoulders of a crease.  On a native one-pixel drawing,
        # however, small sub-pixel corrections are mostly staircase noise and
        # can perturb an already exact construction.  Enable the correction
        # only when it is a strong image-wide phenomenon, never per segment.
        mean_center_shift = center_shift_sum / max(1, centered_segment_count)
        if mean_center_shift < 1.4:
            for item in raw:
                item["offset"] = item.get("measured_offset", item["offset"])
            centered_segment_count = 0
            center_shift_sum = 0.0
            center_shift_max = 0.0

    groups: list[list[int]] = []
    for mask_index in range(len(masks)):
        for orientation in range(8):
            ordered = sorted(
                (
                    index
                    for index, item in enumerate(raw)
                    if item["mask"] == mask_index and item["orientation"] == orientation
                ),
                key=lambda index: raw[index]["offset"],
            )
            orientation_groups: list[list[int]] = []
            for index in ordered:
                if (
                    not orientation_groups
                    or raw[index]["offset"] - raw[orientation_groups[-1][-1]]["offset"] > 3.0
                ):
                    orientation_groups.append([index])
                else:
                    orientation_groups[-1].append(index)
            groups.extend(orientation_groups)

    weak_groups: list[list[int]] = []
    if diffuse_input:
        minimum_continuous_length = size * 0.06
        minimum_continuous_share = 0.34
        strong_groups: list[list[int]] = []
        for group in groups:
            strong = (
                max(raw[index]["length"] for index in group)
                >= minimum_continuous_length
                and max(raw[index]["length"] for index in group)
                / sum(raw[index]["length"] for index in group)
                >= minimum_continuous_share
            )
            (strong_groups if strong else weak_groups).append(group)
        groups = strong_groups

    # Single-linkage is useful for joining the two detected sides of one
    # raster stroke, but it can also chain two nearby parallel rays through
    # their inner sides.  Only split such a group when separated visible
    # intervals each carry substantial evidence and settle at materially
    # different normal offsets.  Construction propagation below remains the
    # final authority on whether either proposed ray is legal.
    refined_groups: list[list[int]] = []
    split_line_groups = 0
    for group in groups:
        orientation = raw[group[0]]["orientation"]
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        records: list[tuple[float, float, int]] = []
        for index in group:
            first_t = float(u @ raw[index]["start"])
            second_t = float(u @ raw[index]["end"])
            records.append((min(first_t, second_t), max(first_t, second_t), index))
        records.sort(key=lambda item: item[0])
        interval_components: list[list[int]] = []
        component_end = -float("inf")
        for first_t, second_t, index in records:
            if not interval_components or first_t - component_end > 20.0:
                interval_components.append([index])
                component_end = second_t
            else:
                interval_components[-1].append(index)
                component_end = max(component_end, second_t)

        component_data: list[tuple[float, float, list[int]]] = []
        for component in interval_components:
            weights = np.array([raw[index]["length"] for index in component])
            offsets = np.array([raw[index]["offset"] for index in component])
            component_data.append(
                (float(np.average(offsets, weights=weights)), float(weights.sum()), component)
            )
        strong = [item for item in component_data if item[1] >= 24.0]
        should_split = (
            len(strong) >= 2
            and max(item[0] for item in strong) - min(item[0] for item in strong) > 3.2
        )
        if not should_split:
            refined_groups.append(group)
            continue

        buckets: list[list[int]] = [item[2].copy() for item in strong]
        strong_centers = [item[0] for item in strong]
        for center, _, component in component_data:
            if any(component is item[2] for item in strong):
                continue
            nearest = int(np.argmin([abs(center - value) for value in strong_centers]))
            buckets[nearest].extend(component)
        refined_groups.extend(buckets)
        split_line_groups += len(buckets) - 1
    groups = refined_groups

    lines: list[CandidateLine] = []
    line_masks: list[int] = []
    intervals: list[list[list[float]]] = []
    rejected_anchor = 0
    for group in groups:
        weights = np.array([raw[index]["length"] for index in group])
        offsets = np.array([raw[index]["offset"] for index in group])
        cluster = (
            raw[group[0]]["orientation"],
            float(np.average(offsets, weights=weights)),
            float(weights.sum()),
        )
        snapped, rejected = _snap_lines_to_algebraic_anchors([cluster], size, settings)
        if rejected or not snapped:
            rejected_anchor += 1
            continue
        line = snapped[0]
        # Topology must be recovered before algebraic seed propagation.  If
        # every raster line is independently snapped to a boundary reference,
        # lines which visibly meet at one node drift to several nearby nodes.
        # Keep the measured normal offset here; exact 22.5-degree direction is
        # already enforced.  The nearest algebraic boundary value remains as
        # metadata and is used later when construction seeds are selected.
        line.offset = float(cluster[1])
        evidence_intervals: list[list[float]] = []
        for index in group:
            first_t = _point_t(line, raw[index]["start"])
            second_t = _point_t(line, raw[index]["end"])
            evidence_intervals.append([min(first_t, second_t), max(first_t, second_t)])
        evidence_intervals.sort(key=lambda item: item[0])
        merged: list[list[float]] = []
        for first_t, second_t in evidence_intervals:
            if not merged or first_t - merged[-1][1] > 20.0:
                merged.append([first_t, second_t])
            else:
                merged[-1][1] = max(merged[-1][1], second_t)
        lines.append(line)
        line_masks.append(raw[group[0]]["mask"])
        intervals.append(merged)

    # A red and blue stroke can be collinear. Keep one geometric construction
    # ray but union all of its evidence intervals.
    keep: list[int] = []
    remap: dict[int, int] = {}
    for index in sorted(range(len(lines)), key=lambda item: (lines[item].orientation, lines[item].offset)):
        duplicate = next(
            (
                kept
                for kept in reversed(keep[-4:])
                if lines[kept].orientation == lines[index].orientation
                and abs(lines[kept].offset - lines[index].offset) < 2.25
            ),
            None,
        )
        if duplicate is None:
            remap[index] = len(keep)
            keep.append(index)
        else:
            remap[index] = remap[duplicate]
            intervals[duplicate].extend(intervals[index])
    lines = [lines[index] for index in keep]
    line_masks = [line_masks[index] for index in keep]
    intervals = [intervals[index] for index in keep]
    for line_intervals in intervals:
        line_intervals.sort(key=lambda item: item[0])
        merged: list[list[float]] = []
        for first_t, second_t in line_intervals:
            if not merged or first_t - merged[-1][1] > 20.0:
                merged.append([first_t, second_t])
            else:
                merged[-1][1] = max(merged[-1][1], second_t)
        line_intervals[:] = merged

    observed_vertices, _ = _lsd_vertex_candidates(square, ink, settings)
    lines, intervals, construction_stats = _propagate_constructible_rays(
        lines, intervals, size, settings, observed_vertices
    )
    for line, line_intervals in zip(lines, intervals):
        line.evidence_intervals = [item.copy() for item in line_intervals]

    vertices: list[list[tuple[float, np.ndarray]]] = [[] for _ in lines]
    maximum = float(size - 1)
    for index, line in enumerate(lines):
        vertices[index].extend((t, point) for t, point, _ in _boundary_hits(line.offset, line.orientation, size))
    for first_index, first in enumerate(lines):
        for second_index in range(first_index + 1, len(lines)):
            point = _line_intersection(first, lines[second_index])
            if point is None or not (-1 <= point[0] <= maximum + 1 and -1 <= point[1] <= maximum + 1):
                continue
            vertices[first_index].append((_point_t(first, point), point))
            vertices[second_index].append((_point_t(lines[second_index], point), point))

    generic_distance = cv2.distanceTransform(
        np.where(ink > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3
    )
    evidence_margin = max(10.0, settings.endpoint_snap_px * 1.8)

    def has_evidence(line_index: int, value: float) -> bool:
        return any(
            first_t - evidence_margin <= value <= second_t + evidence_margin
            for first_t, second_t in intervals[line_index]
        )

    def has_evidence_with_margin(
        line_index: int,
        value: float,
        margin: float,
    ) -> bool:
        return any(
            first_t - margin <= value <= second_t + margin
            for first_t, second_t in intervals[line_index]
        )

    def arm_support(line: CandidateLine, value: float, direction: float) -> float:
        """Measure a short directed stroke incident to a proposed node."""
        samples = value + direction * np.linspace(2.0, 14.0, 17)
        points = line.p0 + samples[:, None] * line.u
        xs = np.clip(np.rint(points[:, 0]).astype(int), 0, size - 1)
        ys = np.clip(np.rint(points[:, 1]).astype(int), 0, size - 1)
        return float(np.mean(generic_distance[ys, xs] <= settings.evidence_distance_px + 0.6))

    def is_incident(line_index: int, value: float) -> bool:
        if not has_evidence(line_index, value):
            return False
        line = lines[line_index]
        return max(arm_support(line, value, -1.0), arm_support(line, value, 1.0)) >= 0.58

    def observed_focus_incident(
        first_index: int,
        second_index: int,
        exact_point: np.ndarray,
    ) -> bool:
        """Accept an exact crossing represented by one raster focus.

        At a red/blue crossing one color can erase the other at the center, so
        requiring both detected intervals to reach the exact mathematical
        point loses real nodes.  The raster focus is only evidence: the CP node
        remains the exact intersection of the two constructed rays.
        """
        if observed_vertices is None or len(observed_vertices) == 0:
            return False
        distances = np.linalg.norm(observed_vertices - exact_point, axis=1)
        for focus_index in np.argsort(distances):
            if distances[focus_index] > 7.5:
                break
            observed_point = observed_vertices[focus_index]
            credible = True
            for line_index in (first_index, second_index):
                line = lines[line_index]
                if abs(float(line.n @ observed_point) - line.offset) > 3.2:
                    credible = False
                    break
                observed_t = float(line.u @ observed_point)
                if not has_evidence_with_margin(line_index, observed_t, 22.0):
                    credible = False
                    break
                if max(
                    arm_support(line, observed_t, -1.0),
                    arm_support(line, observed_t, 1.0),
                ) < 0.46:
                    credible = False
                    break
            if credible:
                return True
        return False

    # A theoretical intersection becomes a real CP node only when both rays
    # have nearby finite-line evidence and the source image contains ink there.
    active_vertices: list[list[tuple[float, np.ndarray]]] = [[] for _ in lines]
    for line_index, line in enumerate(lines):
        for t, point, _ in _boundary_hits(line.offset, line.orientation, size):
            boundary_focus = (
                observed_vertices is not None
                and any(
                    np.linalg.norm(observed_point - point) <= 3.5
                    and abs(float(line.n @ observed_point) - line.offset) <= 2.5
                    and has_evidence_with_margin(
                        line_index,
                        float(line.u @ observed_point),
                        22.0,
                    )
                    for observed_point in observed_vertices
                )
            )
            if has_evidence(line_index, t) or boundary_focus:
                active_vertices[line_index].append((t, point))
    for first_index, first in enumerate(lines):
        for second_index in range(first_index + 1, len(lines)):
            point = _line_intersection(first, lines[second_index])
            if point is None or not (0 <= point[0] <= maximum and 0 <= point[1] <= maximum):
                continue
            first_t = _point_t(first, point)
            second_t = _point_t(lines[second_index], point)
            if (
                is_incident(first_index, first_t)
                and is_incident(second_index, second_t)
            ) or observed_focus_incident(first_index, second_index, point):
                active_vertices[first_index].append((first_t, point))
                active_vertices[second_index].append((second_t, point))

    output: list[Edge] = []
    minimum_interval_coverage = 0.68 if diffuse_input else 0.28
    for line_index, line in enumerate(lines):
        ordered = sorted(active_vertices[line_index], key=lambda item: item[0])
        clustered: list[tuple[float, np.ndarray]] = []
        for t, point in ordered:
            if not clustered or t - clustered[-1][0] > 3.5:
                clustered.append((t, point))
            elif np.linalg.norm(point - line.anchor_point) < np.linalg.norm(clustered[-1][1] - line.anchor_point):
                clustered[-1] = (t, point)
        for (start_t, start), (end_t, end) in zip(clustered, clustered[1:]):
            segment_length = end_t - start_t
            if segment_length < 2.0:
                continue
            interval_overlaps = [
                max(0.0, min(end_t, raw_end) - max(start_t, raw_start))
                for raw_start, raw_end in intervals[line_index]
            ]
            overlap = (
                max(interval_overlaps)
                if diffuse_input
                else sum(interval_overlaps)
            )
            if overlap / (end_t - start_t) < minimum_interval_coverage:
                continue
            if diffuse_input:
                interior_t = np.linspace(
                    start_t + min(1.5, segment_length * 0.12),
                    end_t - min(1.5, segment_length * 0.12),
                    max(7, int(segment_length * 1.5)),
                )
                interior_points = (
                    line.p0 + interior_t[:, None] * line.u
                )
                interior_x = np.clip(
                    np.rint(interior_points[:, 0]).astype(int), 0, size - 1
                )
                interior_y = np.clip(
                    np.rint(interior_points[:, 1]).astype(int), 0, size - 1
                )
                visible_samples = (
                    generic_distance[interior_y, interior_x]
                    <= settings.evidence_distance_px
                )
                # Do not let endpoint/focus ink alone approve a short chord.
                # At least half of the middle third must lie on actual source
                # geometry at the image-adaptive stroke tolerance.
                third = len(visible_samples) // 3
                middle = visible_samples[
                    third : max(third + 1, len(visible_samples) - third)
                ]
                if float(np.mean(middle)) < 0.65:
                    continue
            support = _sample_support(
                line, start_t, end_t, generic_distance, settings.evidence_distance_px
            )
            if support >= max(0.54, settings.output_support - 0.04):
                output.append(Edge(start.copy(), end.copy(), 4, support))

    fragment_stats = {
        "fragmented_rays_recovered": 0,
        "fragmented_edges_recovered": 0,
    }
    if diffuse_input and weak_groups:
        recovered_edges, recovered_lines, fragment_stats = (
            _recover_fragmented_rays_from_primary(
                output,
                lines,
                raw,
                weak_groups,
                size,
                generic_distance,
                settings,
            )
        )
        output.extend(recovered_edges)
        lines.extend(recovered_lines)

    unique: dict[tuple, Edge] = {}
    for edge in output:
        first = (round(float(edge.start[0]), 3), round(float(edge.start[1]), 3))
        second = (round(float(edge.end[0]), 3), round(float(edge.end[1]), 3))
        key = tuple(sorted((first, second)))
        if key not in unique or edge.support > unique[key].support:
            unique[key] = edge
    return list(unique.values()), lines, {
        "lsd_segments": len(raw),
        "lsd_angle_rejected": rejected_angle,
        "lsd_line_groups": len(lines),
        "lsd_parallel_groups_split": split_line_groups,
        "lsd_anchor_rejected": rejected_anchor,
        "lsd_evidence_intervals": sum(len(items) for items in intervals),
        "lsd_stroke_edges_centered": centered_segment_count,
        "lsd_mean_center_shift_px": round(
            center_shift_sum / max(1, centered_segment_count), 4
        ),
        "lsd_max_center_shift_px": round(center_shift_max, 4),
        **fragment_stats,
        **construction_stats,
    }


def _fuse_edge_sets(
    primary: list[Edge],
    secondary: list[Edge],
    tertiary: list[Edge],
    allowed_lines: list[CandidateLine],
    size: int,
) -> tuple[list[Edge], dict]:
    """Add only genuinely new evidence from the two recall-oriented graphs.

    The finite-ray reconstruction is deliberately the precision backbone.
    Vertex and Hough graphs may fill a missed short stroke, but are not allowed
    to lay a nearly identical second stroke over an existing construction ray.
    """
    selected = _remove_boundary_coincident_edges(primary, size)
    canvas = np.zeros((size, size), dtype=np.uint8)
    keys: set[tuple] = set()

    def edge_key(edge: Edge) -> tuple:
        first = (round(float(edge.start[0]), 3), round(float(edge.start[1]), 3))
        second = (round(float(edge.end[0]), 3), round(float(edge.end[1]), 3))
        return tuple(sorted((first, second)))

    def draw(edge: Edge) -> None:
        cv2.line(
            canvas,
            tuple(np.rint(edge.start).astype(int)),
            tuple(np.rint(edge.end).astype(int)),
            255,
            1,
            cv2.LINE_8,
        )

    deduplicated: list[Edge] = []
    for edge in selected:
        key = edge_key(edge)
        if key in keys:
            continue
        keys.add(key)
        deduplicated.append(edge)
        draw(edge)
    selected = deduplicated

    added_counts: list[int] = []
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    for candidates, novelty_threshold in ((secondary, 0.34), (tertiary, 0.42)):
        added = 0
        candidates = _remove_boundary_coincident_edges(candidates, size)
        for edge in sorted(candidates, key=lambda item: -float(np.linalg.norm(item.end - item.start))):
            delta = edge.end - edge.start
            orientation, _ = _closest_orientation(
                math.atan2(float(delta[1]), float(delta[0]))
            )
            theta = ALLOWED_ANGLES[orientation]
            u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
            n = np.array([-u[1], u[0]], dtype=float)
            measured_offset = float(n @ ((edge.start + edge.end) / 2.0))
            compatible = [
                line for line in allowed_lines if line.orientation == orientation
            ]
            if not compatible:
                continue
            construction_line = min(
                compatible, key=lambda line: abs(line.offset - measured_offset)
            )
            if abs(construction_line.offset - measured_offset) > 3.5:
                continue
            start_t = float(u @ edge.start)
            end_t = float(u @ edge.end)
            edge = Edge(
                construction_line.p0 + start_t * construction_line.u,
                construction_line.p0 + end_t * construction_line.u,
                edge.line_type,
                edge.support,
            )
            key = edge_key(edge)
            if key in keys or edge.support < 0.70:
                continue
            length = float(np.linalg.norm(edge.end - edge.start))
            count = max(5, int(length * 2.0))
            points = edge.start + np.linspace(0.0, 1.0, count)[:, None] * (edge.end - edge.start)
            xs = np.clip(np.rint(points[:, 0]).astype(int), 0, size - 1)
            ys = np.clip(np.rint(points[:, 1]).astype(int), 0, size - 1)
            covered = cv2.dilate(canvas, kernel)
            novelty = float(np.mean(covered[ys, xs] == 0))
            if novelty < novelty_threshold:
                continue
            selected.append(edge)
            keys.add(key)
            draw(edge)
            added += 1
        added_counts.append(added)
    return selected, {
        "fused_primary_edges": len(primary),
        "fused_vertex_additions": added_counts[0],
        "fused_hough_additions": added_counts[1],
        "constructible_rays": len(allowed_lines),
    }


def _snap_and_prune_dangling_edges(
    edges: list[Edge],
    size: int,
    snap_radius: float = 8.0,
    construction_lines: list[CandidateLine] | None = None,
) -> tuple[list[Edge], dict]:
    """Make every finite endpoint a paper hit or a second-ray intersection."""
    maximum = float(size - 1)
    # Recall passes often describe one visible crease as several collinear
    # pieces. Their shared same-direction endpoint is not a construction node;
    # merge touching pieces first so only true boundary/intersection endpoints
    # are validated below.
    collinear_groups: list[dict] = []
    for edge in edges:
        delta = edge.end - edge.start
        orientation, _ = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        offset = float(n @ ((edge.start + edge.end) / 2.0))
        group = next(
            (
                item
                for item in collinear_groups
                if item["orientation"] == orientation
                and abs(item["offset"] - offset) <= 0.2
            ),
            None,
        )
        if group is None:
            group = {
                "orientation": orientation,
                "offset": offset,
                "u": u,
                "n": n,
                "intervals": [],
            }
            collinear_groups.append(group)
        group["intervals"].append(
            (
                min(float(u @ edge.start), float(u @ edge.end)),
                max(float(u @ edge.start), float(u @ edge.end)),
                edge,
            )
        )

    merged_edges: list[Edge] = []
    collinear_merges = 0
    for group in collinear_groups:
        merged: list[list] = []
        for start_t, end_t, edge in sorted(group["intervals"], key=lambda item: item[0]):
            if not merged or start_t - merged[-1][1] > 0.75:
                merged.append([start_t, end_t, [edge]])
            else:
                merged[-1][1] = max(merged[-1][1], end_t)
                merged[-1][2].append(edge)
                collinear_merges += 1
        p0 = group["n"] * group["offset"]
        for start_t, end_t, sources in merged:
            merged_edges.append(
                Edge(
                    p0 + start_t * group["u"],
                    p0 + end_t * group["u"],
                    sources[0].line_type,
                    max(source.support for source in sources),
                )
            )
    edges = merged_edges
    records: list[dict] = []
    for edge in edges:
        delta = edge.end - edge.start
        orientation, _ = _closest_orientation(math.atan2(float(delta[1]), float(delta[0])))
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        offset = float(n @ ((edge.start + edge.end) / 2.0))
        first_t, second_t = float(u @ edge.start), float(u @ edge.end)
        records.append(
            {
                "edge": edge,
                "orientation": orientation,
                "u": u,
                "n": n,
                "offset": offset,
                "minimum": min(first_t, second_t),
                "maximum": max(first_t, second_t),
            }
        )

    snapped = 0
    valid_endpoints: list[list[bool]] = [[False, False] for _ in records]
    adjusted: list[list[np.ndarray]] = [
        [record["edge"].start.copy(), record["edge"].end.copy()] for record in records
    ]
    for index, record in enumerate(records):
        for endpoint_index, endpoint in enumerate(adjusted[index]):
            choices: list[tuple[float, np.ndarray]] = []
            boundary_choices: list[tuple[float, np.ndarray]] = []
            # A boundary endpoint is derived by the construction ray meeting
            # the square, not by retaining the raster detector's truncation.
            if min(endpoint[0], endpoint[1], maximum - endpoint[0], maximum - endpoint[1]) <= snap_radius:
                for _, point, _ in _boundary_hits(record["offset"], record["orientation"], size):
                    distance = float(np.linalg.norm(point - endpoint))
                    if distance <= snap_radius + 2.0:
                        boundary_choices.append((distance, point))

            for other_index, other in enumerate(records):
                if other_index == index or other["orientation"] == record["orientation"]:
                    continue
                matrix = np.array([record["n"], other["n"]], dtype=float)
                determinant = float(np.linalg.det(matrix))
                if abs(determinant) < 1e-8:
                    continue
                point = np.linalg.solve(
                    matrix, np.array([record["offset"], other["offset"]], dtype=float)
                )
                other_t = float(other["u"] @ point)
                if not (other["minimum"] - 5.0 <= other_t <= other["maximum"] + 5.0):
                    continue
                distance = float(np.linalg.norm(point - endpoint))
                if distance <= snap_radius:
                    choices.append((distance, point))
            # A finite parent segment can itself be missing from the raster
            # result. The already-proven construction ray is still a legal
            # length delimiter, so use it as a conservative fallback.
            if not choices and construction_lines:
                for other in construction_lines:
                    if other.orientation == record["orientation"]:
                        continue
                    matrix = np.array([record["n"], other.n], dtype=float)
                    determinant = float(np.linalg.det(matrix))
                    if abs(determinant) < 1e-8:
                        continue
                    point = np.linalg.solve(
                        matrix,
                        np.array([record["offset"], other.offset], dtype=float),
                    )
                    if not (
                        -1.0 <= point[0] <= maximum + 1.0
                        and -1.0 <= point[1] <= maximum + 1.0
                    ):
                        continue
                    distance = float(np.linalg.norm(point - endpoint))
                    if distance <= snap_radius:
                        choices.append((distance, point))
            # Near the square, prefer the exact paper hit. Otherwise an almost
            # coincident internal crossing can leave coordinates such as
            # y=0.14, which are neither truly on the boundary nor constructible.
            if boundary_choices:
                choices = boundary_choices
            if choices:
                distance, point = min(choices, key=lambda item: item[0])
                adjusted[index][endpoint_index] = point.copy()
                valid_endpoints[index][endpoint_index] = True
                if distance > 0.35:
                    snapped += 1

    kept: list[Edge] = []
    rejected = 0
    for index, record in enumerate(records):
        if not all(valid_endpoints[index]):
            rejected += 1
            continue
        start, end = adjusted[index]
        if float(np.linalg.norm(end - start)) < 1.5:
            rejected += 1
            continue
        kept.append(Edge(start, end, record["edge"].line_type, record["edge"].support))

    unique: dict[tuple, Edge] = {}
    for edge in kept:
        first = (round(float(edge.start[0]), 3), round(float(edge.start[1]), 3))
        second = (round(float(edge.end[0]), 3), round(float(edge.end[1]), 3))
        key = tuple(sorted((first, second)))
        if key not in unique or edge.support > unique[key].support:
            unique[key] = edge
    return list(unique.values()), {
        "collinear_fragments_merged": collinear_merges,
        "construction_endpoints_snapped": snapped,
        "dangling_edges_rejected": rejected,
    }


def _close_internal_lineheads(
    edges: list[Edge],
    construction_lines: list[CandidateLine],
    ink: np.ndarray,
    settings: Settings,
    conservative_evidence: bool = False,
) -> tuple[list[Edge], dict]:
    """Close every internal degree-one endpoint using visible legal rays.

    A theoretical ray alone is not a connection. A bridge is admitted only if
    it follows an already-constructed ray, has source-image support, and reaches
    the paper boundary or an actually exported finite edge.
    """
    size = ink.shape[0]
    maximum = float(size - 1)
    distance = cv2.distanceTransform(
        np.where(ink > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3
    )
    result = list(edges)
    added = 0

    def point_segment_distance(point: np.ndarray, edge: Edge) -> float:
        delta = edge.end - edge.start
        denominator = float(delta @ delta)
        if denominator <= 1e-12:
            return float(np.linalg.norm(point - edge.start))
        factor = float(np.clip(((point - edge.start) @ delta) / denominator, 0.0, 1.0))
        return float(np.linalg.norm(point - (edge.start + factor * delta)))

    def edge_orientation(edge: Edge) -> int:
        delta = edge.end - edge.start
        return _closest_orientation(math.atan2(float(delta[1]), float(delta[0])))[0]

    def is_boundary(point: np.ndarray) -> bool:
        return min(
            abs(float(point[0])),
            abs(float(point[1])),
            abs(maximum - float(point[0])),
            abs(maximum - float(point[1])),
        ) <= 1e-5

    def is_connected(point: np.ndarray, own_index: int) -> bool:
        if is_boundary(point):
            return True
        return any(
            index != own_index and point_segment_distance(point, other) <= 1e-4
            for index, other in enumerate(result)
        )

    # Bridge lineheads to the first real finite edge encountered along a legal
    # parent ray. Both bridge endpoints therefore belong to the exported graph.
    for _ in range(3):
        orphans: list[tuple[int, np.ndarray, int]] = []
        for edge_index, edge in enumerate(result):
            orientation = edge_orientation(edge)
            for endpoint in (edge.start, edge.end):
                if not is_connected(endpoint, edge_index):
                    orphans.append((edge_index, endpoint.copy(), orientation))
        if not orphans:
            break

        round_additions: list[Edge] = []
        for own_index, endpoint, own_orientation in orphans:
            candidates: list[tuple[float, float, np.ndarray, CandidateLine]] = []
            for line in construction_lines:
                if line.orientation == own_orientation:
                    continue
                if abs(float(line.n @ endpoint) - line.offset) > 1e-4:
                    continue
                endpoint_t = float(line.u @ endpoint)
                contacts: list[tuple[float, np.ndarray]] = []
                for other_index, other_edge in enumerate(result):
                    if other_index == own_index:
                        continue
                    other_orientation = edge_orientation(other_edge)
                    if other_orientation == line.orientation:
                        continue
                    theta = ALLOWED_ANGLES[other_orientation]
                    other_u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
                    other_n = np.array([-other_u[1], other_u[0]], dtype=float)
                    other_offset = float(other_n @ ((other_edge.start + other_edge.end) / 2.0))
                    matrix = np.array([line.n, other_n], dtype=float)
                    if abs(float(np.linalg.det(matrix))) < 1e-9:
                        continue
                    point = np.linalg.solve(matrix, np.array([line.offset, other_offset], dtype=float))
                    if point_segment_distance(point, other_edge) > 0.15:
                        continue
                    delta_t = float(line.u @ point) - endpoint_t
                    if abs(delta_t) > 0.75:
                        contacts.append((delta_t, point))
                for hit_t, point, _ in _boundary_hits(line.offset, line.orientation, size):
                    delta_t = hit_t - endpoint_t
                    if abs(delta_t) > 0.75:
                        contacts.append((delta_t, point))

                # Only the first graph contact in either direction can delimit
                # a legal bridge; jumping over it would create another bad node.
                directional: list[tuple[float, np.ndarray]] = []
                negative = [item for item in contacts if item[0] < 0]
                positive = [item for item in contacts if item[0] > 0]
                if negative:
                    directional.append(max(negative, key=lambda item: item[0]))
                if positive:
                    directional.append(min(positive, key=lambda item: item[0]))
                for delta_t, point in directional:
                    start_t, end_t = sorted((endpoint_t, endpoint_t + delta_t))
                    support = _sample_support(
                        line,
                        start_t,
                        end_t,
                        distance,
                        settings.evidence_distance_px + 0.6,
                    )
                    length = abs(delta_t)
                    required_support = (
                        0.48
                        if length <= 12.0
                        else 0.64
                        if length <= 20.0
                        else 0.68
                    )
                    if support >= required_support and length <= 60.0:
                        candidates.append((-support, length, point, line))
            if not candidates:
                continue
            _, _, point, _ = min(candidates, key=lambda item: (item[0], item[1]))
            round_additions.append(Edge(endpoint.copy(), point.copy(), 4, 1.0))

        if not round_additions:
            break
        result.extend(round_additions)
        added += len(round_additions)

    # A line can be represented by two valid pieces with a missed interval
    # between them. Fill only strongly supported collinear gaps.
    grouped: dict[int, list[list[float]]] = {}
    for edge in result:
        orientation = edge_orientation(edge)
        theta = ALLOWED_ANGLES[orientation]
        u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        n = np.array([-u[1], u[0]], dtype=float)
        measured_offset = float(n @ ((edge.start + edge.end) / 2.0))
        compatible = [
            (abs(line.offset - measured_offset), index, line)
            for index, line in enumerate(construction_lines)
            if line.orientation == orientation
        ]
        if not compatible:
            continue
        error, line_index, line = min(compatible, key=lambda item: item[0])
        if error > 0.15:
            continue
        grouped.setdefault(line_index, []).append(
            sorted((float(line.u @ edge.start), float(line.u @ edge.end)))
        )

    gap_additions: list[Edge] = []
    for line_index, intervals in grouped.items():
        merged: list[list[float]] = []
        for start_t, end_t in sorted(intervals):
            if not merged or start_t - merged[-1][1] > 0.6:
                merged.append([start_t, end_t])
            else:
                merged[-1][1] = max(merged[-1][1], end_t)
        line = construction_lines[line_index]
        for left, right in zip(merged, merged[1:]):
            start_t, end_t = left[1], right[0]
            if not 0.75 <= end_t - start_t <= 60.0:
                continue
            support = _sample_support(
                line,
                start_t,
                end_t,
                distance,
                settings.evidence_distance_px + 0.4,
            )
            if support >= 0.78:
                gap_additions.append(
                    Edge(
                        line.p0 + start_t * line.u,
                        line.p0 + end_t * line.u,
                        4,
                        support,
                    )
                )
    result.extend(gap_additions)

    # Recover a visible interval which survived ray construction but was lost
    # by the finite-edge recall passes.  Raster interval endpoints never enter
    # the CP: they merely select the nearest exact contacts with actual finite
    # edges or the paper boundary.
    interval_additions: list[Edge] = []
    coverage = np.zeros((size, size), dtype=np.uint8)
    for edge in result:
        cv2.line(
            coverage,
            tuple(np.rint(edge.start).astype(int)),
            tuple(np.rint(edge.end).astype(int)),
            255,
            1,
            cv2.LINE_8,
        )
    covered_nearby = cv2.dilate(
        coverage,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )
    for line in construction_lines:
        for raw_start, raw_end in line.evidence_intervals or []:
            raw_length = raw_end - raw_start
            if raw_length < 12.0:
                continue
            sample_t = np.linspace(
                raw_start,
                raw_end,
                max(8, int(raw_length) + 1),
            )
            sample_points = line.p0 + sample_t[:, None] * line.u
            xs = np.clip(np.rint(sample_points[:, 0]).astype(int), 0, size - 1)
            ys = np.clip(np.rint(sample_points[:, 1]).astype(int), 0, size - 1)
            uncovered = float(np.mean(covered_nearby[ys, xs] == 0))
            if uncovered < 0.52:
                continue

            contacts: list[tuple[float, np.ndarray]] = []
            for other_edge in result:
                other_orientation = edge_orientation(other_edge)
                if other_orientation == line.orientation:
                    continue
                theta = ALLOWED_ANGLES[other_orientation]
                other_u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
                other_n = np.array([-other_u[1], other_u[0]], dtype=float)
                other_offset = float(
                    other_n @ ((other_edge.start + other_edge.end) / 2.0)
                )
                matrix = np.array([line.n, other_n], dtype=float)
                if abs(float(np.linalg.det(matrix))) < 1e-9:
                    continue
                point = np.linalg.solve(
                    matrix,
                    np.array([line.offset, other_offset], dtype=float),
                )
                if point_segment_distance(point, other_edge) <= 0.15:
                    contacts.append((float(line.u @ point), point))
            contacts.extend(
                (hit_t, point)
                for hit_t, point, _ in _boundary_hits(
                    line.offset,
                    line.orientation,
                    size,
                )
            )
            if len(contacts) < 2:
                continue
            start_options = sorted(
                contacts,
                key=lambda item: abs(item[0] - raw_start),
            )
            end_options = sorted(
                contacts,
                key=lambda item: abs(item[0] - raw_end),
            )
            candidates: list[tuple[float, float, np.ndarray, np.ndarray]] = []
            for start_t, start_point in start_options[:6]:
                for end_t, end_point in end_options[:6]:
                    if end_t <= start_t + 1.0:
                        continue
                    endpoint_extension = abs(start_t - raw_start) + abs(end_t - raw_end)
                    if max(abs(start_t - raw_start), abs(end_t - raw_end)) > 30.5:
                        continue
                    support = _sample_support(
                        line,
                        start_t,
                        end_t,
                        distance,
                        settings.evidence_distance_px + 0.6,
                    )
                    if support < 0.67:
                        continue
                    candidates.append(
                        (
                            -support,
                            endpoint_extension,
                            start_point,
                            end_point,
                        )
                    )
            if not candidates:
                continue
            _, _, start_point, end_point = min(
                candidates,
                key=lambda item: (item[0], item[1]),
            )
            interval_additions.append(
                Edge(start_point.copy(), end_point.copy(), 4, 1.0)
            )
            cv2.line(
                coverage,
                tuple(np.rint(start_point).astype(int)),
                tuple(np.rint(end_point).astype(int)),
                255,
                1,
                cv2.LINE_8,
            )
            covered_nearby = cv2.dilate(
                coverage,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            )
    result.extend(interval_additions)

    # LSD can miss a whole ray when alternating red/blue crossings divide it
    # into fragments shorter than the detector accepts.  Hough evidence may
    # nominate that ray, but it is never allowed to supply endpoints: recover
    # only a direct link between two nodes already present in the exact graph.
    # The nodes must be consecutive on the proposed ray and the complete span
    # must be strongly visible in the source image.
    hough_clusters = []
    if not conservative_evidence:
        hough_clusters, _ = _extract_hough_clusters(ink, settings)
    node_values: list[np.ndarray] = []
    existing_links: set[tuple[int, int]] = set()

    def canonical_node(point: np.ndarray) -> int:
        for index, existing in enumerate(node_values):
            if np.linalg.norm(existing - point) <= 0.15:
                return index
        node_values.append(point.copy())
        return len(node_values) - 1

    for edge in result:
        first_node = canonical_node(edge.start)
        second_node = canonical_node(edge.end)
        existing_links.add(tuple(sorted((first_node, second_node))))

    exact_node_links: list[Edge] = []
    for first_index, start in enumerate(node_values):
        for second_index, end in enumerate(
            node_values[first_index + 1 :],
            start=first_index + 1,
        ):
            delta = end - start
            length = float(np.linalg.norm(delta))
            if not 8.0 <= length <= 140.0:
                continue
            orientation, angle_error = _closest_orientation(
                math.atan2(float(delta[1]), float(delta[0]))
            )
            if math.degrees(angle_error) > 0.02:
                continue
            theta = ALLOWED_ANGLES[orientation]
            u = np.array([math.cos(theta), math.sin(theta)], dtype=float)
            n = np.array([-u[1], u[0]], dtype=float)
            offset = float(n @ ((start + end) / 2.0))
            matching_hough = [
                (abs(offset - candidate_offset), strength)
                for candidate_orientation, candidate_offset, strength in hough_clusters
                if candidate_orientation == orientation
                and abs(offset - candidate_offset) <= 1.1
                and strength >= 10.0
            ]
            if not matching_hough:
                continue

            start_t, end_t = sorted((float(u @ start), float(u @ end)))
            # Never jump over another exact node; lengths are delimited only
            # by consecutive points already present in the construction.
            if any(
                start_t + 0.5 < float(u @ point) < end_t - 0.5
                and abs(float(n @ point) - offset) <= 0.15
                for point in node_values
            ):
                continue
            link_key = (first_index, second_index)
            if link_key in existing_links:
                continue

            sample_t = np.linspace(start_t, end_t, max(8, int(length) + 1))
            sample_points = n * offset + sample_t[:, None] * u
            xs = np.clip(np.rint(sample_points[:, 0]).astype(int), 0, size - 1)
            ys = np.clip(np.rint(sample_points[:, 1]).astype(int), 0, size - 1)
            support = float(
                np.mean(
                    distance[ys, xs]
                    <= settings.evidence_distance_px + 0.6
                )
            )
            # A nearby focus can make a nonexistent short chord look fully
            # supported under a wide distance tolerance.  Verify the middle
            # on a narrow direction-parallel band, excluding endpoint ink.
            trim = min(4.0, max(1.5, length * 0.22))
            middle_t = np.arange(start_t + trim, end_t - trim + 0.01, 0.5)
            directional_support = 0.0
            if len(middle_t):
                for normal_shift in np.linspace(-1.5, 1.5, 7):
                    middle_points = (
                        n * (offset + normal_shift)
                        + middle_t[:, None] * u
                    )
                    middle_xs = np.clip(
                        np.rint(middle_points[:, 0]).astype(int),
                        0,
                        size - 1,
                    )
                    middle_ys = np.clip(
                        np.rint(middle_points[:, 1]).astype(int),
                        0,
                        size - 1,
                    )
                    directional_support = max(
                        directional_support,
                        float(np.mean(distance[middle_ys, middle_xs] <= 0.75)),
                    )
            if support < 0.94 or directional_support < 0.60:
                continue
            exact_node_links.append(Edge(start.copy(), end.copy(), 4, support))
            existing_links.add(link_key)
    result.extend(exact_node_links)

    # Recover a visibly supported final arm from an existing exact node to the
    # paper boundary.  Both endpoints are already legal construction points;
    # the raster image only decides whether the finite interval is present.
    # Evaluate its marginal gain so an infinite ray with unrelated evidence
    # elsewhere cannot create a spurious boundary arm.
    boundary_arms_recovered = 0
    current_mask = np.zeros((size, size), dtype=np.uint8)
    for edge in result:
        cv2.line(
            current_mask,
            tuple(np.rint(edge.start).astype(int)),
            tuple(np.rint(edge.end).astype(int)),
            255,
            1,
            cv2.LINE_8,
        )
    source_geometry = ink > 0
    source_geometry[:3, :] = False
    source_geometry[-3:, :] = False
    source_geometry[:, :3] = False
    source_geometry[:, -3:] = False
    source_distance_for_arms = cv2.distanceTransform(
        np.where(source_geometry, 0, 255).astype(np.uint8),
        cv2.DIST_L2,
        3,
    )

    def arm_errors(mask: np.ndarray) -> tuple[int, int]:
        predicted_distance = cv2.distanceTransform(
            np.where(mask > 0, 0, 255).astype(np.uint8),
            cv2.DIST_L2,
            3,
        )
        return (
            int(np.sum(source_geometry & (predicted_distance > 2.0))),
            int(np.sum((mask > 0) & (source_distance_for_arms > 2.0))),
        )

    existing_edge_keys = {
        tuple(
            sorted(
                (
                    (round(float(edge.start[0]), 4), round(float(edge.start[1]), 4)),
                    (round(float(edge.end[0]), 4), round(float(edge.end[1]), 4)),
                )
            )
        )
        for edge in result
    }
    boundary_candidates: list[tuple[int, int, float, Edge]] = []
    base_missing, base_false = arm_errors(current_mask)
    for line in ([] if conservative_evidence else construction_lines):
        points: list[tuple[float, np.ndarray, bool]] = []
        for edge in result:
            for point in (edge.start, edge.end):
                if abs(float(line.n @ point) - line.offset) <= 0.15:
                    points.append((float(line.u @ point), point.copy(), False))
        points.extend(
            (hit_t, point.copy(), True)
            for hit_t, point, _ in _boundary_hits(
                line.offset,
                line.orientation,
                size,
            )
        )
        ordered: list[tuple[float, np.ndarray, bool]] = []
        for item in sorted(points, key=lambda value: value[0]):
            if not ordered or item[0] - ordered[-1][0] > 0.3:
                ordered.append(item)
        for first, second in zip(ordered, ordered[1:]):
            if first[2] == second[2]:
                continue
            start, end = first[1], second[1]
            length = float(np.linalg.norm(end - start))
            if not 5.0 <= length <= 80.0:
                continue
            key = tuple(
                sorted(
                    (
                        (round(float(start[0]), 4), round(float(start[1]), 4)),
                        (round(float(end[0]), 4), round(float(end[1]), 4)),
                    )
                )
            )
            if key in existing_edge_keys:
                continue
            trial = current_mask.copy()
            cv2.line(
                trial,
                tuple(np.rint(start).astype(int)),
                tuple(np.rint(end).astype(int)),
                255,
                1,
                cv2.LINE_8,
            )
            trial_missing, trial_false = arm_errors(trial)
            missing_gain = base_missing - trial_missing
            false_cost = trial_false - base_false
            if missing_gain >= 12 and missing_gain - false_cost >= 10:
                boundary_candidates.append(
                    (
                        missing_gain - false_cost,
                        missing_gain,
                        length,
                        Edge(start.copy(), end.copy(), 4, 1.0),
                    )
                )
    # Re-evaluate after every accepted arm because their covered pixels can
    # overlap; this keeps only arms with independent visible contribution.
    for _, _, _, edge in sorted(
        boundary_candidates,
        key=lambda item: (-item[0], -item[1], item[2]),
    ):
        base_missing, base_false = arm_errors(current_mask)
        trial = current_mask.copy()
        cv2.line(
            trial,
            tuple(np.rint(edge.start).astype(int)),
            tuple(np.rint(edge.end).astype(int)),
            255,
            1,
            cv2.LINE_8,
        )
        trial_missing, trial_false = arm_errors(trial)
        if (
            base_missing - trial_missing >= 12
            and (base_missing - trial_missing) - (trial_false - base_false) >= 10
        ):
            result.append(edge)
            current_mask = trial
            boundary_arms_recovered += 1

    # Audit short edges by their *marginal* contribution.  A false chord near
    # a dense focus may pass a distance or centerline test because other arms
    # surround both endpoints.  It is safely redundant only when removing it
    # adds no missing source pixels, reduces false pixels, and cannot create an
    # internal degree-one node.  This also tolerates legitimate raster strokes
    # displaced several pixels from their exact construction coordinates.
    geometry = ink > 0
    geometry[:3, :] = False
    geometry[-3:, :] = False
    geometry[:, :3] = False
    geometry[:, -3:] = False
    source_distance = cv2.distanceTransform(
        np.where(geometry, 0, 255).astype(np.uint8),
        cv2.DIST_L2,
        3,
    )

    def rendered_mask(edge_values: list[Edge], skipped: int | None = None) -> np.ndarray:
        mask = np.zeros((size, size), dtype=np.uint8)
        for index, edge in enumerate(edge_values):
            if index == skipped:
                continue
            cv2.line(
                mask,
                tuple(np.rint(edge.start).astype(int)),
                tuple(np.rint(edge.end).astype(int)),
                255,
                1,
                cv2.LINE_8,
            )
        return mask

    def coverage_errors(mask: np.ndarray) -> tuple[int, int]:
        prediction_distance = cv2.distanceTransform(
            np.where(mask > 0, 0, 255).astype(np.uint8),
            cv2.DIST_L2,
            3,
        )
        missing = int(np.sum(geometry & (prediction_distance > 2.0)))
        false = int(np.sum((mask > 0) & (source_distance > 2.0)))
        return missing, false

    redundant_short_edges = 0
    while result:
        base_missing, base_false = coverage_errors(rendered_mask(result))
        node_degree: dict[tuple[float, float], int] = {}
        for edge in result:
            for point in (edge.start, edge.end):
                key = (round(float(point[0]), 5), round(float(point[1]), 5))
                node_degree[key] = node_degree.get(key, 0) + 1

        removable: list[tuple[int, float, int]] = []
        for edge_index, edge in enumerate(result):
            length = float(np.linalg.norm(edge.end - edge.start))
            if not 3.0 <= length <= 18.0:
                continue
            topology_safe = True
            for point in (edge.start, edge.end):
                if is_boundary(point):
                    continue
                key = (round(float(point[0]), 5), round(float(point[1]), 5))
                if node_degree.get(key, 0) < 3:
                    topology_safe = False
                    break
            if not topology_safe:
                continue
            trial_missing, trial_false = coverage_errors(
                rendered_mask(result, skipped=edge_index)
            )
            if trial_missing == base_missing and trial_false < base_false:
                removable.append((base_false - trial_false, length, edge_index))
        if not removable:
            break
        _, _, edge_index = max(removable, key=lambda item: (item[0], -item[1]))
        result.pop(edge_index)
        redundant_short_edges += 1

    # A closed little branch can evade the degree-one audit even when one of
    # its long sides has almost no source ink. Reject only materially long,
    # very-low-support edges here; the invariant pass below then removes any
    # short arms that this exposes. This is vector-to-source validation, not
    # an attempt to measure missing lines.
    unsupported_edges_rejected = 0
    supported_result: list[Edge] = []
    for edge in result:
        length = float(np.linalg.norm(edge.end - edge.start))
        orientation = edge_orientation(edge)
        theta = ALLOWED_ANGLES[orientation]
        direction = np.array(
            [math.cos(theta), math.sin(theta)], dtype=float
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((edge.start + edge.end) / 2.0))
        line = CandidateLine(
            orientation,
            offset,
            0.0,
            0.0,
            "",
            AlgebraicValue(0, 0, 0.0, 0.0),
            edge.start.copy(),
        )
        start_t, end_t = sorted(
            (float(direction @ edge.start), float(direction @ edge.end))
        )
        validation_t = np.linspace(
            start_t,
            end_t,
            max(5, int(length * 2.0) + 1),
        )
        validation_points = (
            line.p0 + validation_t[:, None] * line.u
        )
        validation_x = np.clip(
            np.rint(validation_points[:, 0]).astype(int), 0, size - 1
        )
        validation_y = np.clip(
            np.rint(validation_points[:, 1]).astype(int), 0, size - 1
        )
        image_support = float(
            np.mean(
                distance[validation_y, validation_x]
                <= settings.evidence_distance_px + 0.4
            )
        )
        low_support_long_edge = length >= 8.0 and image_support < 0.50
        low_support_short_edge = (
            3.0 <= length < 8.0 and image_support < 0.72
        )
        if low_support_long_edge or low_support_short_edge:
            unsupported_edges_rejected += 1
            continue
        supported_result.append(edge)
    result = supported_result

    # Enforce the invariant. Any remaining internal linehead has no supported
    # path into the exported graph and cannot survive in the final CP.
    pruned = 0
    while result:
        bad_edges: set[int] = set()
        for edge_index, edge in enumerate(result):
            for endpoint in (edge.start, edge.end):
                if not is_connected(endpoint, edge_index):
                    bad_edges.add(edge_index)
                    break
        if not bad_edges:
            break
        result = [edge for index, edge in enumerate(result) if index not in bad_edges]
        pruned += len(bad_edges)

    return result, {
        "linehead_bridges_added": added,
        "visible_collinear_gaps_filled": len(gap_additions),
        "visible_intervals_recovered": len(interval_additions),
        "exact_node_links_recovered": len(exact_node_links),
        "boundary_arms_recovered": boundary_arms_recovered,
        "redundant_short_edges_rejected": redundant_short_edges,
        "unsupported_edges_rejected": unsupported_edges_rejected,
        "unclosed_edges_pruned": pruned,
    }


def _recover_supported_graph_chords(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
    settings: Settings,
) -> tuple[list[Edge], dict]:
    """Recover legal node-to-node chords and very short boundary closures.

    This pass deliberately runs on the already planar, exact construction
    graph.  Raster pixels nominate an omitted chord, but its endpoints must be
    existing graph nodes (or an exact paper hit) and its direction must belong
    to the 22.5-degree family.  Colour-separated skeleton evidence prevents an
    unrelated crossing from approving a chord through a dense focus.
    """
    size = ink.shape[0]
    maximum = float(size - 1)
    result = list(edges)
    skeletons = [
        (_thin_binary_mask(mask) > 0).astype(np.uint8)
        for mask in _color_geometry_masks(square, ink)
    ]
    if not skeletons:
        return result, {
            "supported_graph_chords_recovered": 0,
            "supported_boundary_chords_recovered": 0,
        }

    def point_key(point: np.ndarray) -> tuple[float, float]:
        return (round(float(point[0]), 4), round(float(point[1]), 4))

    def edge_key(start: np.ndarray, end: np.ndarray) -> tuple:
        return tuple(sorted((point_key(start), point_key(end))))

    def orientation_values(
        start: np.ndarray, end: np.ndarray
    ) -> tuple[int, np.ndarray, np.ndarray, float, float, float] | None:
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length <= 1e-8:
            return None
        orientation, error = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        if math.degrees(error) > 0.02:
            return None
        direction = np.array(
            [math.cos(ALLOWED_ANGLES[orientation]), math.sin(ALLOWED_ANGLES[orientation])],
            dtype=float,
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((start + end) / 2.0))
        first_t, second_t = sorted(
            (float(direction @ start), float(direction @ end))
        )
        return orientation, direction, normal, offset, first_t, second_t

    def skeleton_profile(
        start: np.ndarray, end: np.ndarray
    ) -> tuple[float, float, float]:
        values = orientation_values(start, end)
        if values is None:
            return 0.0, 0.0, 0.0
        _, direction, normal, offset, first_t, second_t = values
        length = second_t - first_t
        trim = min(1.5, length * 0.18)
        sample_t = np.linspace(
            first_t + trim,
            second_t - trim,
            max(7, int(length * 3.0)),
        )
        best_union = 0.0
        best_center = 0.0
        best_run = 0.0
        for skeleton in skeletons:
            bands: list[np.ndarray] = []
            for shift in np.linspace(-1.25, 1.25, 11):
                points = (
                    normal * (offset + shift)
                    + sample_t[:, None] * direction
                )
                xs = np.clip(
                    np.rint(points[:, 0]).astype(int), 0, size - 1
                )
                ys = np.clip(
                    np.rint(points[:, 1]).astype(int), 0, size - 1
                )
                bands.append(skeleton[ys, xs] > 0)
            stacked = np.stack(bands)
            union = np.any(stacked, axis=0).astype(np.uint8)
            padded = np.pad(union, (1, 1), mode="constant")
            changes = np.diff(padded.astype(np.int8))
            starts = np.where(changes == 1)[0]
            ends = np.where(changes == -1)[0]
            runs = ends - starts
            best_union = max(best_union, float(np.mean(union)))
            best_center = max(
                best_center,
                max(float(np.mean(band)) for band in stacked),
            )
            best_run = max(
                best_run,
                float(np.max(runs)) / len(union) if len(runs) else 0.0,
            )
        return best_union, best_center, best_run

    nodes: list[np.ndarray] = []
    degree: list[int] = []
    for edge in result:
        for point in (edge.start, edge.end):
            index = next(
                (
                    node_index
                    for node_index, node in enumerate(nodes)
                    if np.linalg.norm(node - point) <= 0.15
                ),
                None,
            )
            if index is None:
                nodes.append(point.copy())
                degree.append(1)
            else:
                degree[index] += 1

    existing = {edge_key(edge.start, edge.end) for edge in result}
    node_candidates: list[tuple[float, float, float, Edge]] = []
    for first_index, start in enumerate(nodes):
        if degree[first_index] < 3:
            continue
        for second_index in range(first_index + 1, len(nodes)):
            if degree[second_index] < 3:
                continue
            end = nodes[second_index]
            length = float(np.linalg.norm(end - start))
            if not 4.0 <= length <= 30.0:
                continue
            values = orientation_values(start, end)
            if values is None or edge_key(start, end) in existing:
                continue
            _, direction, normal, offset, first_t, second_t = values
            # Endpoints must be consecutive exact contacts on this ray.  This
            # keeps a longer chord from jumping over a real construction node.
            if any(
                first_t + 0.3 < float(direction @ point) < second_t - 0.3
                and abs(float(normal @ point) - offset) <= 0.2
                for node_index, point in enumerate(nodes)
                if node_index not in (first_index, second_index)
            ):
                continue
            union, center, run = skeleton_profile(start, end)
            # Long omitted chords are allowed some crossing damage, but must
            # cover most of one colour skeleton. Very short chords have too few
            # samples for a stable run score, so require a tighter union score.
            accepted = (
                length >= 12.0
                and union >= 0.86
                and center >= 0.70
            ) or (
                length < 12.0
                and union >= 0.70
                and center >= 0.60
                and run >= 0.25
            )
            if accepted:
                node_candidates.append(
                    (union, center, length, Edge(start.copy(), end.copy(), 4, union))
                )

    recovered_nodes = 0
    for _, _, _, edge in sorted(
        node_candidates, key=lambda item: (-item[0], -item[1], item[2])
    ):
        key = edge_key(edge.start, edge.end)
        if key in existing:
            continue
        result.append(edge)
        existing.add(key)
        recovered_nodes += 1

    # Boundary strokes shorter than a Hough/LSD detector's stable minimum can
    # still close an already-established node.  Only the exact ray/square hit
    # is exported, and the threshold is intentionally narrow enough to exclude
    # alternative long rays that merely share border ink.
    recovered_boundary = 0
    for node_index, start in enumerate(nodes):
        if degree[node_index] < 3:
            continue
        for orientation in range(8):
            direction = np.array(
                [math.cos(ALLOWED_ANGLES[orientation]), math.sin(ALLOWED_ANGLES[orientation])],
                dtype=float,
            )
            normal = np.array([-direction[1], direction[0]], dtype=float)
            offset = float(normal @ start)
            for _, end, _ in _boundary_hits(offset, orientation, size):
                length = float(np.linalg.norm(end - start))
                if not 3.0 <= length <= 8.0:
                    continue
                key = edge_key(start, end)
                if key in existing:
                    continue
                union, center, run = skeleton_profile(start, end)
                if union < 0.80 or center < 0.75 or run < 0.45:
                    continue
                result.append(Edge(start.copy(), end.copy(), 4, union))
                existing.add(key)
                recovered_boundary += 1

    return result, {
        "supported_graph_chords_recovered": recovered_nodes,
        "supported_boundary_chords_recovered": recovered_boundary,
    }


def _prune_unsupported_local_cycles(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
) -> tuple[list[Edge], dict]:
    """Remove false local cycle arms that are not a real colour centerline.

    Degree-one pruning cannot see an erroneous short edge once several such
    edges close into a small loop.  We therefore audit only compact internal
    edges on colour-separated, thinned masks. Removing an edge is permitted
    only when both endpoints stay connected to the rest of the graph; the
    normal linehead invariant is then re-applied by the caller.
    """
    size = ink.shape[0]
    maximum = float(size - 1)
    skeletons = [
        (_thin_binary_mask(mask) > 0).astype(np.uint8)
        for mask in _color_geometry_masks(square, ink)
    ]
    result = list(edges)
    removed = 0

    def key(point: np.ndarray) -> tuple[float, float]:
        return (round(float(point[0]), 5), round(float(point[1]), 5))

    def boundary(point: np.ndarray) -> bool:
        return min(
            abs(float(point[0])),
            abs(float(point[1])),
            abs(maximum - float(point[0])),
            abs(maximum - float(point[1])),
        ) <= 1e-5

    def profile(edge: Edge) -> tuple[float, float]:
        delta = edge.end - edge.start
        length = float(np.linalg.norm(delta))
        orientation, _ = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        direction = np.array(
            [math.cos(ALLOWED_ANGLES[orientation]), math.sin(ALLOWED_ANGLES[orientation])],
            dtype=float,
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((edge.start + edge.end) / 2.0))
        first_t, second_t = sorted(
            (float(direction @ edge.start), float(direction @ edge.end))
        )
        trim = min(1.5, length * 0.18)
        sample_t = np.linspace(
            first_t + trim,
            second_t - trim,
            max(7, int(length * 3.0)),
        )
        best_union = 0.0
        best_center = 0.0
        for skeleton in skeletons:
            bands: list[np.ndarray] = []
            for shift in np.linspace(-1.25, 1.25, 11):
                points = (
                    normal * (offset + shift)
                    + sample_t[:, None] * direction
                )
                xs = np.clip(
                    np.rint(points[:, 0]).astype(int), 0, size - 1
                )
                ys = np.clip(
                    np.rint(points[:, 1]).astype(int), 0, size - 1
                )
                bands.append(skeleton[ys, xs] > 0)
            stacked = np.stack(bands)
            best_union = max(
                best_union, float(np.mean(np.any(stacked, axis=0)))
            )
            best_center = max(
                best_center,
                max(float(np.mean(band)) for band in stacked),
            )
        return best_union, best_center

    # Remove one weakest locally unsupported edge at a time. Degrees are
    # recomputed after every removal because a whole false mini-cycle can peel
    # away only after its first weak side is gone.
    while result:
        degree: dict[tuple[float, float], int] = {}
        for edge in result:
            for point in (edge.start, edge.end):
                degree[key(point)] = degree.get(key(point), 0) + 1
        candidates: list[tuple[float, float, float, int]] = []
        for edge_index, edge in enumerate(result):
            length = float(np.linalg.norm(edge.end - edge.start))
            if not 3.0 <= length <= 12.0:
                continue
            if any(
                not boundary(point) and degree.get(key(point), 0) < 3
                for point in (edge.start, edge.end)
            ):
                continue
            union, center = profile(edge)
            # The union band tolerates one-pixel staircases; the best single
            # shifted centerline distinguishes a real crease from ink supplied
            # only by transverse arms near the endpoints.
            unsupported = union < 0.50 or center < 0.48
            if unsupported:
                candidates.append((center, union, -length, edge_index))
        if not candidates:
            break
        _, _, _, edge_index = min(candidates)
        result.pop(edge_index)
        removed += 1

    return result, {"unsupported_local_cycle_edges_rejected": removed}


def _recover_one_ended_exact_rays(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
) -> tuple[list[Edge], dict]:
    """Recover an extremely short ray from a proven node to its first contact.

    This is intentionally stricter than generic linehead closure.  It exists
    for a common raster failure: a five-pixel arm begins at a valid high-degree
    node and ends on the interior of another exact finite edge, so the second
    node cannot exist until the arm itself is added.  The candidate must be a
    short 22.5-degree segment, have a visible topology event at the new contact,
    and its color centerline may not cross through that contact and continue.
    """
    size = ink.shape[0]
    maximum = float(size - 1)
    result = list(edges)
    skeletons: list[np.ndarray] = []
    topology_points: list[np.ndarray] = []
    for mask in _color_geometry_masks(square, ink):
        skeleton = (_thin_binary_mask(mask) > 0).astype(np.uint8)
        skeletons.append(skeleton)
        padded = np.pad(skeleton, 1, mode="constant")
        ring = (
            padded[:-2, 1:-1],
            padded[:-2, 2:],
            padded[1:-1, 2:],
            padded[2:, 2:],
            padded[2:, 1:-1],
            padded[2:, :-2],
            padded[1:-1, :-2],
            padded[:-2, :-2],
        )
        branches = np.zeros_like(skeleton, dtype=np.uint8)
        for index, current in enumerate(ring):
            branches += (
                (current == 0) & (ring[(index + 1) % len(ring)] > 0)
            ).astype(np.uint8)
        ys, xs = np.where(
            (skeleton > 0) & ((branches == 1) | (branches >= 3))
        )
        topology_points.append(np.column_stack((xs, ys)).astype(float))

    def orientation(edge: Edge) -> int:
        delta = edge.end - edge.start
        return _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )[0]

    def edge_intersection(
        ray_orientation: int, ray_offset: float, edge: Edge
    ) -> np.ndarray | None:
        other_orientation = orientation(edge)
        if other_orientation == ray_orientation:
            return None
        ray_normal = np.array(
            [
                -math.sin(ALLOWED_ANGLES[ray_orientation]),
                math.cos(ALLOWED_ANGLES[ray_orientation]),
            ],
            dtype=float,
        )
        other_direction = np.array(
            [
                math.cos(ALLOWED_ANGLES[other_orientation]),
                math.sin(ALLOWED_ANGLES[other_orientation]),
            ],
            dtype=float,
        )
        other_normal = np.array(
            [-other_direction[1], other_direction[0]], dtype=float
        )
        other_offset = float(
            other_normal @ ((edge.start + edge.end) / 2.0)
        )
        matrix = np.array([ray_normal, other_normal], dtype=float)
        if abs(float(np.linalg.det(matrix))) < 1e-9:
            return None
        point = np.linalg.solve(
            matrix, np.array([ray_offset, other_offset], dtype=float)
        )
        delta = edge.end - edge.start
        factor = float(((point - edge.start) @ delta) / max(delta @ delta, 1e-9))
        return point if -1e-5 <= factor <= 1.0 + 1e-5 else None

    nodes: list[np.ndarray] = []
    degrees: list[int] = []
    incident: list[set[int]] = []
    for edge in result:
        edge_orientation = orientation(edge)
        for point in (edge.start, edge.end):
            index = next(
                (
                    node_index
                    for node_index, node in enumerate(nodes)
                    if np.linalg.norm(node - point) <= 0.15
                ),
                None,
            )
            if index is None:
                nodes.append(point.copy())
                degrees.append(1)
                incident.append({edge_orientation})
            else:
                degrees[index] += 1
                incident[index].add(edge_orientation)

    def best_color_profile(
        start: np.ndarray,
        end: np.ndarray,
        ray_orientation: int,
    ) -> tuple[float, float, float, int]:
        direction = np.array(
            [
                math.cos(ALLOWED_ANGLES[ray_orientation]),
                math.sin(ALLOWED_ANGLES[ray_orientation]),
            ],
            dtype=float,
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((start + end) / 2.0))
        first_t, second_t = sorted(
            (float(direction @ start), float(direction @ end))
        )
        length = second_t - first_t
        trim = min(1.2, length * 0.15)
        sample_t = np.linspace(
            first_t + trim,
            second_t - trim,
            max(9, int(length * 4.0)),
        )
        values: list[tuple[float, float, float, float, int]] = []
        for mask_index, skeleton in enumerate(skeletons):
            bands: list[np.ndarray] = []
            for shift in np.linspace(-1.25, 1.25, 11):
                points = (
                    normal * (offset + shift)
                    + sample_t[:, None] * direction
                )
                xs = np.clip(
                    np.rint(points[:, 0]).astype(int), 0, size - 1
                )
                ys = np.clip(
                    np.rint(points[:, 1]).astype(int), 0, size - 1
                )
                bands.append(skeleton[ys, xs] > 0)
            stacked = np.stack(bands)
            union = np.any(stacked, axis=0).astype(np.uint8)
            padded = np.pad(union, (1, 1), mode="constant")
            changes = np.diff(padded.astype(np.int8))
            starts = np.where(changes == 1)[0]
            ends = np.where(changes == -1)[0]
            runs = ends - starts
            values.append(
                (
                    float(np.mean(union)),
                    max(float(np.mean(band)) for band in stacked),
                    float(np.max(runs)) / len(union) if len(runs) else 0.0,
                    float(
                        np.min(
                            np.linalg.norm(
                                topology_points[mask_index] - end, axis=1
                            )
                        )
                    )
                    if len(topology_points[mask_index])
                    else float("inf"),
                    mask_index,
                )
            )
        return max(values, key=lambda item: (item[1], item[0], item[2]))

    candidates: list[tuple[float, float, Edge]] = []
    for node_index, start in enumerate(nodes):
        if degrees[node_index] < 3:
            continue
        for ray_orientation in range(8):
            if ray_orientation in incident[node_index]:
                continue
            direction = np.array(
                [
                    math.cos(ALLOWED_ANGLES[ray_orientation]),
                    math.sin(ALLOWED_ANGLES[ray_orientation]),
                ],
                dtype=float,
            )
            normal = np.array([-direction[1], direction[0]], dtype=float)
            offset = float(normal @ start)
            contacts: list[tuple[float, np.ndarray]] = []
            for edge in result:
                point = edge_intersection(ray_orientation, offset, edge)
                if point is None:
                    continue
                delta_t = float(direction @ (point - start))
                if abs(delta_t) > 0.75:
                    contacts.append((delta_t, point))
            nearest: list[tuple[float, np.ndarray]] = []
            negative = [item for item in contacts if item[0] < 0]
            positive = [item for item in contacts if item[0] > 0]
            if negative:
                nearest.append(max(negative, key=lambda item: item[0]))
            if positive:
                nearest.append(min(positive, key=lambda item: item[0]))
            for delta_t, end in nearest:
                length = abs(delta_t)
                if not 4.5 <= length <= 6.5:
                    continue
                union, center, run, endpoint_error, mask_index = best_color_profile(
                    start, end, ray_orientation
                )
                if union < 0.72 or center < 0.64 or run < 0.68:
                    continue
                # Near the paper boundary, the thick black frame can make a
                # wrong inward direction appear exceptionally continuous in a
                # colour mask. Keep this recovery for locally interrupted
                # strokes; an almost perfect skeleton belongs to a different
                # already-visible arm and is not a missing one-ended ray.
                if (
                    min(
                        float(start[0]),
                        float(start[1]),
                        maximum - float(start[0]),
                        maximum - float(start[1]),
                    )
                    <= 6.0
                    and union > 0.92
                ):
                    continue
                other_topology_error = min(
                    (
                        float(
                            np.min(
                                np.linalg.norm(observations - end, axis=1)
                            )
                        )
                        for other_index, observations in enumerate(topology_points)
                        if other_index != mask_index and len(observations)
                    ),
                    default=float("inf"),
                )
                # This pass targets a crease whose last pixels are overwritten
                # by the opposite colour at a crossing. A same-colour endpoint
                # is handled by the ordinary exact-node recovery; admitting it
                # here reintroduces many short tangential chords.
                if endpoint_error <= 1.5 or other_topology_error > 1.5:
                    continue

                candidates.append(
                    (center, union, Edge(start.copy(), end.copy(), 4, union))
                )

    recovered = 0
    existing = {
        tuple(
            sorted(
                (
                    (round(float(edge.start[0]), 4), round(float(edge.start[1]), 4)),
                    (round(float(edge.end[0]), 4), round(float(edge.end[1]), 4)),
                )
            )
        )
        for edge in result
    }
    for _, _, edge in sorted(candidates, key=lambda item: (-item[0], -item[1])):
        key = tuple(
            sorted(
                (
                    (round(float(edge.start[0]), 4), round(float(edge.start[1]), 4)),
                    (round(float(edge.end[0]), 4), round(float(edge.end[1]), 4)),
                )
            )
        )
        if key in existing:
            continue
        result.append(edge)
        existing.add(key)
        recovered += 1
    return result, {"one_ended_exact_rays_recovered": recovered}


def _experimental_recover_anchored_exact_cycles(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
) -> tuple[list[Edge], dict]:
    """Experimental search for a mutually dependent exact cycle.

    This is deliberately not called by the production pipeline yet. Some dense
    22.5-degree motifs contain no individually recoverable first
    edge: two new intersections and three short sides appear together.  This
    search is graph-based, not coordinate-based. It starts from an existing
    high-degree node, follows at most three strongly visible legal chords, and
    accepts only a closed proposal whose final node reconnects to an existing
    finite edge. Raster-derived points merely nominate which already-present
    infinite 22.5-degree rays intersect; every output point is recomputed from
    those rays.
    """
    size = ink.shape[0]
    maximum = float(size - 1)
    result = list(edges)
    masks = _color_geometry_masks(square, ink)
    skeletons = [(_thin_binary_mask(mask) > 0).astype(np.uint8) for mask in masks]
    if not skeletons:
        return result, {"anchored_exact_cycles_recovered": 0}

    def edge_orientation(edge: Edge) -> int:
        delta = edge.end - edge.start
        return _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )[0]

    def line_values(edge: Edge) -> tuple[int, float]:
        orientation = edge_orientation(edge)
        normal = np.array(
            [
                -math.sin(ALLOWED_ANGLES[orientation]),
                math.cos(ALLOWED_ANGLES[orientation]),
            ],
            dtype=float,
        )
        return orientation, float(normal @ ((edge.start + edge.end) / 2.0))

    ray_values: list[tuple[int, float]] = []
    for edge in result:
        value = line_values(edge)
        if not any(
            value[0] == existing[0] and abs(value[1] - existing[1]) <= 0.15
            for existing in ray_values
        ):
            ray_values.append(value)

    nodes: list[np.ndarray] = []
    degrees: list[int] = []
    for edge in result:
        for point in (edge.start, edge.end):
            index = next(
                (
                    node_index
                    for node_index, node in enumerate(nodes)
                    if np.linalg.norm(node - point) <= 0.15
                ),
                None,
            )
            if index is None:
                nodes.append(point.copy())
                degrees.append(1)
            else:
                degrees[index] += 1

    def ray_intersection(
        first: tuple[int, float], second: tuple[int, float]
    ) -> np.ndarray | None:
        if first[0] == second[0]:
            return None
        first_normal = np.array(
            [-math.sin(ALLOWED_ANGLES[first[0]]), math.cos(ALLOWED_ANGLES[first[0]])],
            dtype=float,
        )
        second_normal = np.array(
            [-math.sin(ALLOWED_ANGLES[second[0]]), math.cos(ALLOWED_ANGLES[second[0]])],
            dtype=float,
        )
        matrix = np.array([first_normal, second_normal], dtype=float)
        if abs(float(np.linalg.det(matrix))) < 1e-9:
            return None
        return np.linalg.solve(matrix, np.array([first[1], second[1]], dtype=float))

    exact_points: list[np.ndarray] = []
    for first_index, first in enumerate(ray_values):
        for second in ray_values[first_index + 1 :]:
            point = ray_intersection(first, second)
            if point is None or np.any(point < 0.0) or np.any(point > maximum):
                continue
            if not any(np.linalg.norm(point - value) <= 0.2 for value in exact_points):
                exact_points.append(point)

    # Only latent points close to an observed skeleton endpoint/fork can enter
    # a cycle. This turns the otherwise quadratic all-ray intersection field
    # into a small local graph and rejects mathematically valid but invisible
    # crossings before any path search.
    topology_observations: list[np.ndarray] = []
    for skeleton in skeletons:
        padded = np.pad(skeleton, 1, mode="constant")
        ring = (
            padded[:-2, 1:-1],
            padded[:-2, 2:],
            padded[1:-1, 2:],
            padded[2:, 2:],
            padded[2:, 1:-1],
            padded[2:, :-2],
            padded[1:-1, :-2],
            padded[:-2, :-2],
        )
        branches = np.zeros_like(skeleton, dtype=np.uint8)
        for index, current in enumerate(ring):
            branches += (
                (current == 0) & (ring[(index + 1) % len(ring)] > 0)
            ).astype(np.uint8)
        ys, xs = np.where(
            (skeleton > 0) & ((branches == 1) | (branches >= 3))
        )
        topology_observations.extend(
            np.array([float(x), float(y)]) for x, y in zip(xs, ys)
        )
    if topology_observations:
        observed = np.array(topology_observations, dtype=float)
        exact_points = [
            point
            for point in exact_points
            if float(np.min(np.linalg.norm(observed - point, axis=1))) <= 2.75
        ]
    else:
        exact_points = []

    def profile(
        start: np.ndarray, end: np.ndarray
    ) -> tuple[float, float, float, int] | None:
        delta = end - start
        length = float(np.linalg.norm(delta))
        orientation, error = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        if math.degrees(error) > 0.02 or not 3.5 <= length <= 18.0:
            return None
        direction = np.array(
            [math.cos(ALLOWED_ANGLES[orientation]), math.sin(ALLOWED_ANGLES[orientation])],
            dtype=float,
        )
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((start + end) / 2.0))
        first_t, second_t = sorted(
            (float(direction @ start), float(direction @ end))
        )
        trim = min(1.2, length * 0.15)
        sample_t = np.linspace(
            first_t + trim,
            second_t - trim,
            max(9, int(length * 4.0)),
        )
        profiles: list[tuple[float, float, float, int]] = []
        for mask_index, skeleton in enumerate(skeletons):
            bands: list[np.ndarray] = []
            for shift in np.linspace(-1.25, 1.25, 11):
                points = normal * (offset + shift) + sample_t[:, None] * direction
                xs = np.clip(np.rint(points[:, 0]).astype(int), 0, size - 1)
                ys = np.clip(np.rint(points[:, 1]).astype(int), 0, size - 1)
                bands.append(skeleton[ys, xs] > 0)
            stacked = np.stack(bands)
            union = np.any(stacked, axis=0).astype(np.uint8)
            changes = np.diff(np.pad(union, (1, 1)).astype(np.int8))
            runs = np.where(changes == -1)[0] - np.where(changes == 1)[0]
            profiles.append(
                (
                    float(np.mean(union)),
                    max(float(np.mean(band)) for band in stacked),
                    float(np.max(runs)) / len(union) if len(runs) else 0.0,
                    mask_index,
                )
            )
        value = max(profiles, key=lambda item: (item[0], item[1], item[2]))
        if value[0] < 0.80 or value[1] < 0.62:
            return None
        return value

    def point_on_edge(point: np.ndarray, edge: Edge) -> bool:
        delta = edge.end - edge.start
        factor = float(((point - edge.start) @ delta) / max(delta @ delta, 1e-9))
        projection = edge.start + np.clip(factor, 0.0, 1.0) * delta
        return (
            -1e-5 <= factor <= 1.0 + 1e-5
            and np.linalg.norm(point - projection) <= 0.15
        )

    existing_keys = {
        tuple(
            sorted(
                (
                    (round(float(edge.start[0]), 4), round(float(edge.start[1]), 4)),
                    (round(float(edge.end[0]), 4), round(float(edge.end[1]), 4)),
                )
            )
        )
        for edge in result
    }
    profile_cache: dict[tuple, tuple[float, float, float, int] | None] = {}

    def cached_profile(
        start: np.ndarray, end: np.ndarray
    ) -> tuple[float, float, float, int] | None:
        signature = tuple(
            sorted(
                (
                    (round(float(start[0]), 3), round(float(start[1]), 3)),
                    (round(float(end[0]), 3), round(float(end[1]), 3)),
                )
            )
        )
        if signature not in profile_cache:
            profile_cache[signature] = profile(start, end)
        return profile_cache[signature]

    proposals: list[tuple[float, list[Edge]]] = []
    # The first point is anchored in the graph. The second and third may be
    # latent exact intersections; the fourth side must reconnect to the graph.
    for anchor_index, anchor in enumerate(nodes):
        if degrees[anchor_index] < 3:
            continue
        neighbors = [
            point
            for point in exact_points
            if 3.5 <= np.linalg.norm(point - anchor) <= 18.0
            and cached_profile(anchor, point) is not None
        ]
        for second in neighbors:
            first_profile = cached_profile(anchor, second)
            assert first_profile is not None
            for third in exact_points:
                if np.linalg.norm(third - second) < 3.5:
                    continue
                second_profile = cached_profile(second, third)
                if second_profile is None:
                    continue
                for fourth in exact_points:
                    if np.linalg.norm(fourth - third) < 3.5:
                        continue
                    third_profile = cached_profile(third, fourth)
                    if third_profile is None:
                        continue
                    reconnecting_edges = [
                        edge for edge in result if point_on_edge(fourth, edge)
                    ]
                    if not reconnecting_edges:
                        continue
                    first_orientation = _closest_orientation(
                        math.atan2(
                            float(second[1] - anchor[1]),
                            float(second[0] - anchor[0]),
                        )
                    )[0]
                    second_orientation = _closest_orientation(
                        math.atan2(
                            float(third[1] - second[1]),
                            float(third[0] - second[0]),
                        )
                    )[0]
                    third_orientation = _closest_orientation(
                        math.atan2(
                            float(fourth[1] - third[1]),
                            float(fourth[0] - third[0]),
                        )
                    )[0]
                    # A compact dependent cycle is bounded by three different
                    # legal ray families and reconnects transversely. Paths
                    # whose first and last sides are parallel are ordinary
                    # zigzags along a visible mesh, not a latent closed motif.
                    if len(
                        {
                            first_orientation,
                            second_orientation,
                            third_orientation,
                        }
                    ) < 3:
                        continue
                    if any(
                        edge_orientation(edge) == third_orientation
                        for edge in reconnecting_edges
                    ):
                        continue
                    # At least two colors must participate across the motif;
                    # this rejects tracing one long same-colour staircase as a
                    # polygon. The last node must also be genuinely distinct.
                    mask_set = {
                        first_profile[3], second_profile[3], third_profile[3]
                    }
                    if len(mask_set) < 2 or np.linalg.norm(fourth - anchor) < 4.0:
                        continue
                    candidate_edges = [
                        Edge(anchor.copy(), second.copy(), 4, first_profile[0]),
                        Edge(second.copy(), third.copy(), 4, second_profile[0]),
                        Edge(third.copy(), fourth.copy(), 4, third_profile[0]),
                    ]
                    new_edges = [
                        edge
                        for edge in candidate_edges
                        if tuple(
                            sorted(
                                (
                                    (
                                        round(float(edge.start[0]), 4),
                                        round(float(edge.start[1]), 4),
                                    ),
                                    (
                                        round(float(edge.end[0]), 4),
                                        round(float(edge.end[1]), 4),
                                    ),
                                )
                            )
                        )
                        not in existing_keys
                    ]
                    if len(new_edges) < 2:
                        continue
                    score = sum(edge.support * np.linalg.norm(edge.end - edge.start) for edge in new_edges)
                    proposals.append((float(score), new_edges))

    if not proposals:
        return result, {"anchored_exact_cycles_recovered": 0}
    # A valid cycle produces the same edge set from both traversal directions.
    # Requiring repeated independent nomination sharply reduces chance paths in
    # a dense all-ray intersection field.
    grouped: dict[tuple, tuple[int, float, list[Edge]]] = {}
    for score, proposal in proposals:
        signature = tuple(
            sorted(
                tuple(
                    sorted(
                        (
                            (round(float(edge.start[0]), 3), round(float(edge.start[1]), 3)),
                            (round(float(edge.end[0]), 3), round(float(edge.end[1]), 3)),
                        )
                    )
                )
                for edge in proposal
            )
        )
        count, best_score, best_edges = grouped.get(signature, (0, -1.0, proposal))
        grouped[signature] = (
            count + 1,
            max(best_score, score),
            proposal if score >= best_score else best_edges,
        )
    repeated = [value for value in grouped.values() if value[0] >= 2]
    if not repeated:
        return result, {"anchored_exact_cycles_recovered": 0}
    _, _, chosen = max(repeated, key=lambda item: item[1])
    result.extend(chosen)
    return result, {"anchored_exact_cycles_recovered": len(chosen)}


def _add_boundaries(edges: list[Edge], size: int) -> list[Edge]:
    maximum = float(size - 1)
    side_values: dict[str, list[float]] = {
        "top": [0.0, maximum],
        "right": [0.0, maximum],
        "bottom": [0.0, maximum],
        "left": [0.0, maximum],
    }
    tolerance = 1.25
    for edge in edges:
        for point in (edge.start, edge.end):
            x, y = float(point[0]), float(point[1])
            if abs(y) <= tolerance:
                side_values["top"].append(min(maximum, max(0.0, x)))
            if abs(x - maximum) <= tolerance:
                side_values["right"].append(min(maximum, max(0.0, y)))
            if abs(y - maximum) <= tolerance:
                side_values["bottom"].append(min(maximum, max(0.0, x)))
            if abs(x) <= tolerance:
                side_values["left"].append(min(maximum, max(0.0, y)))

    boundary_edges: list[Edge] = []
    for side, values in side_values.items():
        merged: list[float] = []
        for value in sorted(values):
            if not merged or value - merged[-1] > 1.0:
                merged.append(value)
        for first, second in zip(merged, merged[1:]):
            if second - first < 0.5:
                continue
            if side == "top":
                start, end = np.array([first, 0.0]), np.array([second, 0.0])
            elif side == "right":
                start, end = np.array([maximum, first]), np.array([maximum, second])
            elif side == "bottom":
                start, end = np.array([second, maximum]), np.array([first, maximum])
            else:
                start, end = np.array([0.0, second]), np.array([0.0, first])
            boundary_edges.append(Edge(start, end, 1, 1.0))
    return boundary_edges + edges


def _planarize_edges(edges: list[Edge]) -> list[Edge]:
    """Split output segments at every real crossing and remove duplicates."""
    split_parameters: list[list[float]] = [[0.0, 1.0] for _ in edges]
    for first_index, first in enumerate(edges):
        first_delta = first.end - first.start
        for second_index in range(first_index + 1, len(edges)):
            second = edges[second_index]
            second_delta = second.end - second.start
            denominator = float(
                first_delta[0] * second_delta[1] - first_delta[1] * second_delta[0]
            )
            if abs(denominator) < 1e-9:
                continue
            relative = second.start - first.start
            first_t = float(
                (relative[0] * second_delta[1] - relative[1] * second_delta[0]) / denominator
            )
            second_t = float(
                (relative[0] * first_delta[1] - relative[1] * first_delta[0]) / denominator
            )
            # Split X crossings and T junctions. At a T junction the endpoint
            # of one segment lies in the interior of the other, so requiring
            # both parameters to be strictly interior leaves the parent edge
            # topologically unsplit.
            tolerance = 1e-6
            if (
                -tolerance <= first_t <= 1.0 + tolerance
                and -tolerance <= second_t <= 1.0 + tolerance
            ):
                if tolerance < first_t < 1.0 - tolerance:
                    split_parameters[first_index].append(first_t)
                if tolerance < second_t < 1.0 - tolerance:
                    split_parameters[second_index].append(second_t)

    pieces: list[Edge] = []
    for edge, parameters in zip(edges, split_parameters):
        ordered: list[float] = []
        for value in sorted(parameters):
            if not ordered or value - ordered[-1] > 1e-7:
                ordered.append(value)
        delta = edge.end - edge.start
        for first_t, second_t in zip(ordered, ordered[1:]):
            start = edge.start + first_t * delta
            end = edge.start + second_t * delta
            if np.linalg.norm(end - start) >= 0.2:
                pieces.append(Edge(start, end, edge.line_type, edge.support))

    # The same analytic intersection is evaluated once from each incident
    # segment. Floating-point roundoff can leave coordinates a few billionths
    # apart; after CP text formatting those become two distinct nodes. Bind
    # numerically identical endpoints to one shared coordinate before export.
    canonical_points: list[np.ndarray] = []
    for edge in pieces:
        for name in ("start", "end"):
            point = getattr(edge, name)
            canonical = next(
                (
                    existing
                    for existing in canonical_points
                    if np.linalg.norm(existing - point) <= 1e-6
                ),
                None,
            )
            if canonical is None:
                canonical = point.copy()
                canonical_points.append(canonical)
            setattr(edge, name, canonical.copy())

    unique: dict[tuple, Edge] = {}
    for edge in pieces:
        first = (round(float(edge.start[0]), 5), round(float(edge.start[1]), 5))
        second = (round(float(edge.end[0]), 5), round(float(edge.end[1]), 5))
        key = tuple(sorted((first, second)))
        if key not in unique or edge.support > unique[key].support:
            unique[key] = edge
    return list(unique.values())


def _remove_boundary_coincident_edges(edges: list[Edge], size: int) -> list[Edge]:
    maximum = float(size - 1)
    tolerance = 0.8
    result = []
    for edge in edges:
        x1, y1 = edge.start
        x2, y2 = edge.end
        on_same_boundary = (
            (abs(y1) <= tolerance and abs(y2) <= tolerance)
            or (abs(y1 - maximum) <= tolerance and abs(y2 - maximum) <= tolerance)
            or (abs(x1) <= tolerance and abs(x2) <= tolerance)
            or (abs(x1 - maximum) <= tolerance and abs(x2 - maximum) <= tolerance)
        )
        if not on_same_boundary:
            result.append(edge)
    return result


def _prune_post_planar_lineheads(
    edges: list[Edge], size: int
) -> tuple[list[Edge], int]:
    """Enforce the no-internal-linehead invariant after final planarization."""
    maximum = float(size - 1)
    result = list(edges)
    removed = 0

    def boundary(point: np.ndarray) -> bool:
        return min(
            abs(float(point[0])),
            abs(float(point[1])),
            abs(maximum - float(point[0])),
            abs(maximum - float(point[1])),
        ) <= 1e-5

    while result:
        degree: dict[tuple[float, float], int] = {}
        for edge in result:
            for point in (edge.start, edge.end):
                key = (round(float(point[0]), 6), round(float(point[1]), 6))
                degree[key] = degree.get(key, 0) + 1
        bad: set[int] = set()
        for edge_index, edge in enumerate(result):
            for point in (edge.start, edge.end):
                key = (round(float(point[0]), 6), round(float(point[1]), 6))
                if not boundary(point) and degree.get(key, 0) == 1:
                    bad.add(edge_index)
                    break
        if not bad:
            break
        result = [
            edge for edge_index, edge in enumerate(result) if edge_index not in bad
        ]
        removed += len(bad)
    return result, removed


def _repair_near_focus_camv_violations(
    edges: list[Edge],
    construction_lines: list[CandidateLine],
    ink: np.ndarray,
    settings: Settings,
) -> tuple[list[Edge], dict]:
    """Repair a narrowly defined false near-focus bridge.

    A common raster ambiguity creates two exact nodes less than two pixels
    apart, joins them with a tiny segment, and leaves both nodes cAMV-invalid.
    The safe repair replaces a non-protected boundary arm by the parallel
    22.5-degree ray through the existing focus, then removes the tiny bridge.
    No raster endpoint becomes a CP node: the new endpoints are the existing
    focus and the exact hit of its derived ray on the paper boundary.
    """

    size = ink.shape[0]
    maximum = float(size - 1)
    result = _planarize_edges(list(edges))
    distance = cv2.distanceTransform(
        np.where(ink > 0, 0, 255).astype(np.uint8),
        cv2.DIST_L2,
        3,
    )

    def is_boundary(point: np.ndarray) -> bool:
        return min(
            abs(float(point[0])),
            abs(float(point[1])),
            abs(maximum - float(point[0])),
            abs(maximum - float(point[1])),
        ) <= 1e-4

    def point_key(point: np.ndarray) -> tuple[float, float]:
        return round(float(point[0]), 6), round(float(point[1]), 6)

    def line_for_edge(edge: Edge) -> tuple[int, CandidateLine, float, float]:
        delta = edge.end - edge.start
        orientation, angle_error = _closest_orientation(
            math.atan2(float(delta[1]), float(delta[0]))
        )
        theta = ALLOWED_ANGLES[orientation]
        direction = np.array([math.cos(theta), math.sin(theta)], dtype=float)
        normal = np.array([-direction[1], direction[0]], dtype=float)
        offset = float(normal @ ((edge.start + edge.end) / 2.0))
        line = CandidateLine(
            orientation,
            offset,
            0.0,
            math.degrees(angle_error),
            "",
            AlgebraicValue(0, 0, 0.0, 0.0),
            edge.start.copy(),
        )
        first_t, second_t = sorted(
            (float(direction @ edge.start), float(direction @ edge.end))
        )
        return orientation, line, first_t, second_t

    def edge_support(edge: Edge) -> float:
        _, line, first_t, second_t = line_for_edge(edge)
        return _sample_support(
            line,
            first_t,
            second_t,
            distance,
            settings.evidence_distance_px + 0.4,
        )

    def graph_has_internal_linehead(values: list[Edge]) -> bool:
        degree: dict[tuple[float, float], int] = {}
        points: dict[tuple[float, float], np.ndarray] = {}
        for edge in values:
            for point in (edge.start, edge.end):
                key = point_key(point)
                degree[key] = degree.get(key, 0) + 1
                points[key] = point
        return any(
            count == 1 and not is_boundary(points[key])
            for key, count in degree.items()
        )

    def structure_report(values: list[Edge]) -> dict:
        all_edges = _add_boundaries(values, size)
        return audit_camv_structure(
            [
                GeometrySegment(
                    edge.line_type,
                    (float(edge.start[0]), float(edge.start[1])),
                    (float(edge.end[0]), float(edge.end[1])),
                )
                for edge in all_edges
            ]
        )

    initial_report = structure_report(result)
    current_report = initial_report
    repairs = 0
    protected_reanchors_rejected = 0
    evidence_rejected = 0

    for _ in range(8):
        violation_keys = {
            (round(float(item["point"][0]), 6), round(float(item["point"][1]), 6))
            for item in current_report["violations"]
        }
        incident: dict[tuple[float, float], list[int]] = {}
        for edge_index, edge in enumerate(result):
            incident.setdefault(point_key(edge.start), []).append(edge_index)
            incident.setdefault(point_key(edge.end), []).append(edge_index)

        proposals: list[tuple[int, float, float, list[Edge], dict]] = []
        for bridge_index, bridge in enumerate(result):
            bridge_length = float(np.linalg.norm(bridge.end - bridge.start))
            if not 0.2 <= bridge_length <= 2.0:
                continue
            first_key = point_key(bridge.start)
            second_key = point_key(bridge.end)
            if first_key not in violation_keys or second_key not in violation_keys:
                continue

            for satellite, focus in (
                (bridge.start, bridge.end),
                (bridge.end, bridge.start),
            ):
                satellite_key = point_key(satellite)
                focus_key = point_key(focus)
                if (
                    len(incident.get(satellite_key, [])) != 2
                    or len(incident.get(focus_key, [])) < 4
                ):
                    continue
                arm_indices = [
                    index
                    for index in incident[satellite_key]
                    if index != bridge_index
                ]
                if len(arm_indices) != 1:
                    continue
                arm_index = arm_indices[0]
                arm = result[arm_index]
                other = (
                    arm.end
                    if point_key(arm.start) == satellite_key
                    else arm.start
                )
                if not is_boundary(other):
                    continue

                orientation, old_line, old_first_t, old_second_t = line_for_edge(arm)
                if old_line.snap_error_px > 1e-5:
                    continue
                nearest_source = min(
                    (
                        line
                        for line in construction_lines
                        if line.orientation == orientation
                    ),
                    key=lambda line: abs(line.offset - old_line.offset),
                    default=None,
                )
                if (
                    nearest_source is not None
                    and abs(nearest_source.offset - old_line.offset) <= 0.9
                    and nearest_source.origin_kind in {"corner", "midpoint"}
                ):
                    protected_reanchors_rejected += 1
                    continue

                theta = ALLOWED_ANGLES[orientation]
                direction = np.array(
                    [math.cos(theta), math.sin(theta)], dtype=float
                )
                normal = np.array([-direction[1], direction[0]], dtype=float)
                new_offset = float(normal @ focus)
                hits = _boundary_hits(new_offset, orientation, size)
                if not hits:
                    continue
                _, boundary_point, _ = min(
                    hits,
                    key=lambda hit: float(np.linalg.norm(hit[1] - other)),
                )
                if float(np.linalg.norm(boundary_point - other)) > 3.0:
                    continue
                replacement = Edge(
                    focus.copy(),
                    boundary_point.copy(),
                    arm.line_type,
                    arm.support,
                )
                old_support = _sample_support(
                    old_line,
                    old_first_t,
                    old_second_t,
                    distance,
                    settings.evidence_distance_px + 0.4,
                )
                new_support = edge_support(replacement)
                if new_support < max(0.78, old_support - 0.03):
                    evidence_rejected += 1
                    continue
                replacement.support = new_support

                removed = {bridge_index, arm_index}
                candidate = [
                    edge
                    for index, edge in enumerate(result)
                    if index not in removed
                ]
                candidate.append(replacement)
                candidate = _planarize_edges(candidate)
                if graph_has_internal_linehead(candidate):
                    continue
                candidate_report = structure_report(candidate)
                improvement = (
                    current_report["violation_vertex_count"]
                    - candidate_report["violation_vertex_count"]
                )
                if improvement < 2:
                    continue
                proposals.append(
                    (
                        improvement,
                        new_support,
                        -float(np.linalg.norm(boundary_point - other)),
                        candidate,
                        candidate_report,
                    )
                )

        if not proposals:
            break
        _, _, _, result, current_report = max(
            proposals, key=lambda item: item[:3]
        )
        repairs += 1

    return result, {
        "camv_near_focus_repairs": repairs,
        "camv_protected_reanchors_rejected": protected_reanchors_rejected,
        "camv_repair_evidence_rejected": evidence_rejected,
        "camv_violations_before_repair": initial_report["violation_vertex_count"],
        "camv_violations_after_repair": current_report["violation_vertex_count"],
        "camv_score_before_repair": initial_report[
            "structural_completeness_score"
        ],
        "camv_score_after_repair": current_report[
            "structural_completeness_score"
        ],
    }


def _recover_camv_supported_paths(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
    settings: Settings,
) -> tuple[list[Edge], dict]:
    """Use strong raster evidence to complete paths exposed by cAMV.

    The normal recall passes intentionally avoid inventing geometry merely to
    satisfy foldability.  This final pass is narrower: it starts only at an
    already-existing cAMV-invalid node, follows a strict 22.5-degree ray, and
    stops at the first exact contact with another crease or the paper border.
    A contact that becomes odd can nominate the next arm, so a missing chain
    may be recovered over several rounds.  The whole chain is transactional:
    if the best audited state does not strictly improve the initial structure,
    every tentative arm is discarded.
    """

    size = ink.shape[0]
    maximum = float(size - 1)
    initial = _planarize_edges(list(edges))
    working = list(initial)
    skeletons = [
        (_thin_binary_mask(mask) > 0).astype(np.uint8)
        for mask in _color_geometry_masks(square, ink)
    ]

    def point_key(point: np.ndarray) -> tuple[float, float]:
        return round(float(point[0]), 5), round(float(point[1]), 5)

    def edge_key(start: np.ndarray, end: np.ndarray) -> tuple:
        return tuple(sorted((point_key(start), point_key(end))))

    def structure_report(values: list[Edge]) -> dict:
        all_edges = _add_boundaries(_planarize_edges(values), size)
        return audit_camv_structure(
            [
                GeometrySegment(
                    edge.line_type,
                    (float(edge.start[0]), float(edge.start[1])),
                    (float(edge.end[0]), float(edge.end[1])),
                )
                for edge in all_edges
            ]
        )

    def color_profile(start: np.ndarray, end: np.ndarray) -> tuple[float, float, float]:
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length <= 1e-8:
            return 0.0, 0.0, 0.0
        direction = delta / length
        normal = np.array([-direction[1], direction[0]], dtype=float)
        trim = min(1.5, length * 0.12)
        sample_t = np.linspace(
            trim,
            length - trim,
            max(9, int(length * 3.0)),
        )
        best = (0.0, 0.0, 0.0)
        for skeleton in skeletons:
            bands: list[np.ndarray] = []
            for shift in np.linspace(-1.25, 1.25, 11):
                points = (
                    start
                    + sample_t[:, None] * direction
                    + shift * normal
                )
                xs = np.clip(
                    np.rint(points[:, 0]).astype(int), 0, size - 1
                )
                ys = np.clip(
                    np.rint(points[:, 1]).astype(int), 0, size - 1
                )
                bands.append(skeleton[ys, xs] > 0)
            stacked = np.stack(bands)
            union = np.any(stacked, axis=0).astype(np.uint8)
            padded = np.pad(union, (1, 1), mode="constant")
            changes = np.diff(padded.astype(np.int8))
            starts = np.where(changes == 1)[0]
            ends = np.where(changes == -1)[0]
            runs = ends - starts
            best = max(
                best,
                (
                    float(np.mean(union)),
                    max(float(np.mean(band)) for band in stacked),
                    float(np.max(runs)) / len(union) if len(runs) else 0.0,
                ),
            )
        return best

    def nearest_contact(
        values: list[Edge], start: np.ndarray, direction: np.ndarray
    ) -> np.ndarray | None:
        contacts: list[tuple[float, np.ndarray]] = []
        for axis, target in (
            (0, 0.0),
            (0, maximum),
            (1, 0.0),
            (1, maximum),
        ):
            if abs(float(direction[axis])) <= 1e-10:
                continue
            parameter = float((target - start[axis]) / direction[axis])
            point = start + parameter * direction
            if (
                parameter > 0.5
                and -1e-6 <= float(point[0]) <= maximum + 1e-6
                and -1e-6 <= float(point[1]) <= maximum + 1e-6
            ):
                contacts.append((parameter, point))

        for edge in values:
            segment = edge.end - edge.start
            matrix = np.column_stack((direction, -segment))
            if abs(float(np.linalg.det(matrix))) <= 1e-10:
                continue
            parameter, edge_parameter = np.linalg.solve(
                matrix, edge.start - start
            )
            if (
                float(parameter) > 0.5
                and -1e-7 <= float(edge_parameter) <= 1.0 + 1e-7
            ):
                contacts.append(
                    (float(parameter), start + float(parameter) * direction)
                )
        if not contacts:
            return None
        return min(contacts, key=lambda item: item[0])[1]

    def canonical_node(values: list[Edge], point: np.ndarray) -> np.ndarray | None:
        candidates = [
            endpoint
            for edge in values
            for endpoint in (edge.start, edge.end)
            if np.linalg.norm(endpoint - point) <= 1e-4
        ]
        return candidates[0].copy() if candidates else None

    initial_report = structure_report(initial)
    best = list(initial)
    best_report = initial_report
    best_added_arms = 0
    attempted_arms = 0
    tentative_arms = 0
    rounds = 0
    seen_edges = {edge_key(edge.start, edge.end) for edge in working}
    minimum_union = max(0.84, min(0.90, settings.output_support + 0.30))
    minimum_center = max(0.70, min(0.80, settings.output_support + 0.16))

    for _ in range(16):
        current_report = structure_report(working)
        if not current_report["structure_violation_count"]:
            best = list(working)
            best_report = current_report
            break

        proposals: list[tuple[int, float, float, int, list[Edge], dict, list[Edge]]] = []
        for violation in current_report["violations"]:
            if violation["rule"] not in {"number_of_folds", "kawasaki_angles"}:
                continue
            reported_point = np.array(violation["point"], dtype=float)
            start = canonical_node(working, reported_point)
            if start is None:
                continue

            incident_directions: list[np.ndarray] = []
            for edge in working:
                if np.linalg.norm(edge.start - start) <= 1e-4:
                    delta = edge.end - start
                elif np.linalg.norm(edge.end - start) <= 1e-4:
                    delta = edge.start - start
                else:
                    continue
                length = float(np.linalg.norm(delta))
                if length > 1e-8:
                    incident_directions.append(delta / length)

            arms: list[tuple[float, float, float, Edge]] = []
            for theta in ALLOWED_ANGLES:
                base_direction = np.array(
                    [math.cos(theta), math.sin(theta)], dtype=float
                )
                for sign in (-1.0, 1.0):
                    direction = sign * base_direction
                    if any(
                        float(direction @ existing) >= 1.0 - 1e-8
                        for existing in incident_directions
                    ):
                        continue
                    end = nearest_contact(working, start, direction)
                    if end is None or float(np.linalg.norm(end - start)) < 3.0:
                        continue
                    key = edge_key(start, end)
                    if key in seen_edges:
                        continue
                    union, center, run = color_profile(start, end)
                    attempted_arms += 1
                    # This pass is allowed to move an incompleteness frontier,
                    # so evidence must be substantially stronger than normal
                    # output admission. A small run threshold tolerates a real
                    # line interrupted by a dense crossing.
                    if (
                        union < minimum_union
                        or center < minimum_center
                        or run < 0.12
                    ):
                        continue
                    arms.append(
                        (union, center, run, Edge(start.copy(), end.copy(), 4, union))
                    )

            arms.sort(key=lambda item: (-item[0], -item[1], -item[2]))
            arms = arms[:6]
            for arm_count in (1, 2):
                for selected in itertools.combinations(arms, arm_count):
                    additions = [item[3] for item in selected]
                    candidate = _planarize_edges(working + additions)
                    candidate_report = structure_report(candidate)
                    if any(
                        np.linalg.norm(
                            np.array(item["point"], dtype=float) - start
                        )
                        <= 1e-4
                        for item in candidate_report["violations"]
                    ):
                        continue
                    # A strong arm may move the frontier to its exact contact.
                    # Permit at most one temporary extra violation; the final
                    # transaction is still committed only on strict gain.
                    if (
                        candidate_report["structure_violation_count"]
                        > current_report["structure_violation_count"] + 1
                    ):
                        continue
                    proposal_minimum_union = min(item[0] for item in selected)
                    average_center = sum(item[1] for item in selected) / arm_count
                    proposals.append(
                        (
                            candidate_report["structure_violation_count"],
                            -proposal_minimum_union,
                            -average_center,
                            arm_count,
                            candidate,
                            candidate_report,
                            additions,
                        )
                    )

        if not proposals:
            break
        _, _, _, _, working, current_report, additions = min(
            proposals, key=lambda item: item[:4]
        )
        rounds += 1
        tentative_arms += len(additions)
        seen_edges.update(edge_key(edge.start, edge.end) for edge in additions)
        if (
            current_report["structure_violation_count"]
            < best_report["structure_violation_count"]
        ):
            best = list(working)
            best_report = current_report
            best_added_arms = tentative_arms

    improved = (
        best_report["structure_violation_count"]
        < initial_report["structure_violation_count"]
    )
    result = _planarize_edges(best if improved else initial)
    return result, {
        "camv_path_recheck_rounds": rounds,
        "camv_path_arms_examined": attempted_arms,
        "camv_path_tentative_arms": tentative_arms,
        "camv_path_committed_arms": best_added_arms if improved else 0,
        "camv_path_recheck_improved": int(improved),
        "camv_path_violations_before": initial_report["structure_violation_count"],
        "camv_path_violations_after": best_report["structure_violation_count"]
        if improved
        else initial_report["structure_violation_count"],
    }


def _discover_exact_construction_proposals(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
    settings: Settings,
) -> tuple[dict[str, list[ConstructionProposal]], dict]:
    """Explain residual strokes with exact, auditable construction rules.

    The strict graph is rasterised first and removed from the source ink.  A
    fallback rule is considered only near a remaining observed stroke.  This
    keeps the construction grammar exact without enumerating arbitrary rays.
    """

    size = ink.shape[0]
    maximum = float(size - 1)
    base = _planarize_edges(list(edges))
    base_mask = np.zeros_like(ink)
    for edge in base:
        cv2.line(
            base_mask,
            tuple(np.rint(edge.start).astype(int)),
            tuple(np.rint(edge.end).astype(int)),
            255,
            2,
            cv2.LINE_AA,
        )
    base_distance = cv2.distanceTransform(
        np.where(base_mask > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 5
    )
    residual = np.where(
        (ink > 0)
        & (base_distance > max(2.0, settings.evidence_distance_px + 0.5)),
        255,
        0,
    ).astype(np.uint8)
    residual[:3, :] = 0
    residual[-3:, :] = 0
    residual[:, :3] = 0
    residual[:, -3:] = 0
    observations_raw = cv2.HoughLinesP(
        residual,
        1,
        np.pi / 720.0,
        threshold=6,
        minLineLength=4,
        maxLineGap=3,
    )
    if observations_raw is None:
        return {}, {
            "construction_residual_pixels": int(np.count_nonzero(residual)),
            "construction_residual_observations": 0,
            "construction_candidates": 0,
        }

    observations = []
    for x1, y1, x2, y2 in observations_raw[:, 0]:
        start = np.array([float(x1), float(y1)])
        end = np.array([float(x2), float(y2)])
        length = float(np.linalg.norm(end - start))
        if length >= 4.0:
            observations.append((start, end, length))
    observations.sort(key=lambda item: -item[2])
    observations = observations[:180]

    node_map: dict[tuple[float, float], np.ndarray] = {}
    for edge in base:
        for point in (edge.start, edge.end):
            node_map.setdefault(
                (round(float(point[0]), 5), round(float(point[1]), 5)),
                point.copy(),
            )
    nodes = list(node_map.values())
    incident: dict[tuple[float, float], list[float]] = {
        key: [] for key in node_map
    }
    for edge in base:
        for start, end in ((edge.start, edge.end), (edge.end, edge.start)):
            delta = end - start
            length = float(np.linalg.norm(delta))
            if length <= 1e-8:
                continue
            key = (round(float(start[0]), 5), round(float(start[1]), 5))
            angle = math.atan2(float(delta[1]), float(delta[0])) % (
                2.0 * math.pi
            )
            if not any(
                abs(math.atan2(math.sin(angle - old), math.cos(angle - old)))
                <= 1e-7
                for old in incident[key]
            ):
                incident[key].append(angle)
    for values in incident.values():
        values.sort()

    inverse_ink = np.where(ink > 0, 0, 255).astype(np.uint8)
    ink_distance = cv2.distanceTransform(inverse_ink, cv2.DIST_L2, 5)

    def point_key(point: np.ndarray) -> tuple[float, float]:
        return round(float(point[0]), 4), round(float(point[1]), 4)

    def nearest_contact(start: np.ndarray, direction: np.ndarray) -> np.ndarray | None:
        contacts: list[tuple[float, np.ndarray]] = []
        for axis, target in ((0, 0.0), (0, maximum), (1, 0.0), (1, maximum)):
            if abs(float(direction[axis])) <= 1e-10:
                continue
            parameter = float((target - start[axis]) / direction[axis])
            point = start + parameter * direction
            if (
                parameter > 1.0
                and -1e-6 <= float(point[0]) <= maximum + 1e-6
                and -1e-6 <= float(point[1]) <= maximum + 1e-6
            ):
                contacts.append((parameter, point))
        for edge in base:
            segment = edge.end - edge.start
            matrix = np.column_stack((direction, -segment))
            if abs(float(np.linalg.det(matrix))) <= 1e-10:
                continue
            parameter, edge_parameter = np.linalg.solve(
                matrix, edge.start - start
            )
            if (
                float(parameter) > 1.0
                and -1e-7 <= float(edge_parameter) <= 1.0 + 1e-7
            ):
                contacts.append(
                    (float(parameter), start + float(parameter) * direction)
                )
        return min(contacts, key=lambda item: item[0])[1] if contacts else None

    def profile(start: np.ndarray, end: np.ndarray) -> tuple[float, float, float]:
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length < 3.0:
            return 0.0, 0.0, 0.0
        count = max(9, int(length * 2.2))
        parameters = np.linspace(min(1.0, length * 0.08), max(1.0, length * 0.92), count)
        points = start + parameters[:, None] * delta / length
        xs = np.clip(np.rint(points[:, 0]).astype(int), 0, size - 1)
        ys = np.clip(np.rint(points[:, 1]).astype(int), 0, size - 1)
        supported = ink_distance[ys, xs] <= settings.evidence_distance_px
        padded = np.pad(supported.astype(np.int8), (1, 1))
        changes = np.diff(padded)
        run_starts = np.where(changes == 1)[0]
        run_ends = np.where(changes == -1)[0]
        runs = run_ends - run_starts
        novel = supported & (
            base_distance[ys, xs]
            > max(2.0, settings.evidence_distance_px + 0.5)
        )
        return (
            float(np.mean(supported)),
            float(np.max(runs)) / len(supported) if len(runs) else 0.0,
            float(np.mean(novel)),
        )

    proposals: dict[str, dict[tuple, ConstructionProposal]] = {
        "flat_fold": {},
        "angle_bisector": {},
        "segment_division": {},
    }

    def add_proposal(
        kind: str,
        start: np.ndarray,
        end: np.ndarray,
        expression: str,
        label: str,
    ) -> None:
        length = float(np.linalg.norm(end - start))
        if length < 4.0:
            return
        support, continuous_run, novel_coverage = profile(start, end)
        if (
            support < max(0.82, settings.output_support + 0.18)
            or continuous_run < 0.38
            or novel_coverage < 0.12
        ):
            return
        key = tuple(sorted((point_key(start), point_key(end))))
        candidate = ConstructionProposal(
            kind,
            start.copy(),
            end.copy(),
            support,
            continuous_run,
            novel_coverage,
            expression,
            label,
        )
        previous = proposals[kind].get(key)
        if previous is None or (
            candidate.support,
            candidate.continuous_run,
            candidate.novel_coverage,
        ) > (
            previous.support,
            previous.continuous_run,
            previous.novel_coverage,
        ):
            proposals[kind][key] = candidate

    # Removing the already-explained graph also erases a short neighbourhood
    # around each junction.  Residual Hough fragments therefore normally start
    # several pixels away from their exact parent node.
    anchor_radius = max(14.0, settings.evidence_distance_px * 5.0)
    for raw_start, raw_end, raw_length in observations:
        endpoint_options = (raw_start, raw_end)
        for observed_endpoint in endpoint_options:
            nearby = sorted(
                (
                    (float(np.linalg.norm(node - observed_endpoint)), node)
                    for node in nodes
                ),
                key=lambda item: item[0],
            )[:3]
            for distance, anchor in nearby:
                if distance > anchor_radius:
                    continue
                far = raw_end if np.linalg.norm(raw_end - anchor) >= np.linalg.norm(raw_start - anchor) else raw_start
                raw_direction = far - anchor
                raw_direction_length = float(np.linalg.norm(raw_direction))
                if raw_direction_length < 3.0:
                    continue
                raw_direction /= raw_direction_length
                match_tolerance = math.radians(
                    min(
                        6.0,
                        max(
                            2.0,
                            _angle_admission_tolerance_deg(raw_length, settings) + 1.0,
                        ),
                    )
                )
                key = (round(float(anchor[0]), 5), round(float(anchor[1]), 5))
                angles = incident.get(key, [])
                total = len(angles)

                # Oriedita's angle-bisector action selects an existing vertex
                # angle and extends the exact bisector to its first target.
                if total >= 2:
                    for index, first_angle in enumerate(angles):
                        second_angle = angles[(index + 1) % total]
                        sector = (second_angle - first_angle) % (2.0 * math.pi)
                        if sector <= math.radians(2.0):
                            continue
                        direction_angle = (first_angle + sector / 2.0) % (2.0 * math.pi)
                        direction = np.array(
                            [math.cos(direction_angle), math.sin(direction_angle)], dtype=float
                        )
                        error = math.acos(float(np.clip(direction @ raw_direction, -1.0, 1.0)))
                        if error > match_tolerance:
                            continue
                        end = nearest_contact(anchor, direction)
                        if end is None:
                            continue
                        add_proposal(
                            "angle_bisector",
                            anchor,
                            end,
                            f"∠({math.degrees(first_angle):.6g}°, {math.degrees(second_angle):.6g}°) / 2 → 首个交点",
                            "已有夹角的精确角平分线",
                        )

                # Port of Oriedita's odd-ray angular-flat-fold completion:
                # alternating sector sum, divided by two, inside each sector.
                if total >= 3 and total % 2 == 1:
                    sectors = [
                        (angles[(index + 1) % total] - angles[index])
                        % (2.0 * math.pi)
                        for index in range(total)
                    ]
                    for index, first_angle in enumerate(angles):
                        alternating = sum(
                            (1.0 if offset % 2 == 0 else -1.0)
                            * sectors[(index + offset) % total]
                            for offset in range(total)
                        )
                        half = alternating / 2.0
                        if not 1e-7 < half < sectors[index] - 1e-7:
                            continue
                        direction_angle = (first_angle + half) % (2.0 * math.pi)
                        direction = np.array(
                            [math.cos(direction_angle), math.sin(direction_angle)], dtype=float
                        )
                        error = math.acos(float(np.clip(direction @ raw_direction, -1.0, 1.0)))
                        if error > match_tolerance:
                            continue
                        end = nearest_contact(anchor, direction)
                        if end is None:
                            continue
                        add_proposal(
                            "flat_fold",
                            anchor,
                            end,
                            f"奇数 {total} 射线交替夹角和 / 2 = {math.degrees(half):.6g}° → 首个交点",
                            "三线/奇数射线推可平折补线",
                        )

                # Target-segment rational division is tested against the far
                # endpoint of the observed residual stroke.  A division point
                # is never promoted to a free seed.
                for target_index, target in enumerate(base):
                    target_delta = target.end - target.start
                    target_length_squared = float(target_delta @ target_delta)
                    if target_length_squared < 36.0:
                        continue
                    fractions: set[tuple[int, int]] = set()
                    for denominator in range(2, 9):
                        for numerator in range(1, denominator):
                            divisor = math.gcd(numerator, denominator)
                            fractions.add(
                                (numerator // divisor, denominator // divisor)
                            )
                    for numerator, denominator in fractions:
                        fraction = numerator / denominator
                        destination = target.start + fraction * target_delta
                        direction = destination - anchor
                        length = float(np.linalg.norm(direction))
                        if length < 4.0:
                            continue
                        direction /= length
                        error = math.acos(float(np.clip(direction @ raw_direction, -1.0, 1.0)))
                        if error > match_tolerance:
                            continue
                        first_contact = nearest_contact(anchor, direction)
                        if first_contact is None or float(np.linalg.norm(first_contact - destination)) > 1.25:
                            continue
                        add_proposal(
                            "segment_division",
                            anchor,
                            destination,
                            f"已有节点 → 目标线段 {numerator}/{denominator} 点（目标线 {target_index + 1}）",
                            f"连接目标线段 {denominator} 等分点",
                        )

    flattened = {
        kind: sorted(
            values.values(),
            key=lambda item: (
                -item.support,
                -item.continuous_run,
                -item.novel_coverage,
                point_key(item.start),
                point_key(item.end),
            ),
        )
        for kind, values in proposals.items()
        if values
    }
    return flattened, {
        "construction_residual_pixels": int(np.count_nonzero(residual)),
        "construction_residual_observations": len(observations),
        "construction_candidates": sum(len(values) for values in flattened.values()),
        "construction_candidates_by_strategy": {
            kind: len(values) for kind, values in flattened.items()
        },
    }


def _build_exact_construction_variants(
    square: np.ndarray,
    ink: np.ndarray,
    edges: list[Edge],
    settings: Settings,
    base_stats: dict,
) -> tuple[list[dict], dict]:
    proposals, diagnostics = _discover_exact_construction_proposals(
        square, ink, edges, settings
    )
    if not proposals:
        return [], diagnostics

    size = square.shape[0]

    def structure_report(values: list[Edge]) -> dict:
        return audit_camv_structure(
            [
                GeometrySegment(
                    edge.line_type,
                    (float(edge.start[0]), float(edge.start[1])),
                    (float(edge.end[0]), float(edge.end[1])),
                )
                for edge in _add_boundaries(values, size)
            ]
        )

    initial_report = structure_report(edges)
    labels = {
        "flat_fold": "可平折补线版",
        "angle_bisector": "角平分构造版",
        "segment_division": "线段等分构造版",
    }
    variants: list[dict] = []
    admitted_by_strategy: dict[str, int] = {}
    for kind in ("flat_fold", "angle_bisector", "segment_division"):
        candidates = proposals.get(kind, [])
        if not candidates:
            continue
        selected: list[ConstructionProposal] = []
        working = list(edges)
        current_report = initial_report
        for candidate in candidates:
            trial = _planarize_edges(
                working
                + [
                    Edge(
                        candidate.start.copy(),
                        candidate.end.copy(),
                        4,
                        candidate.support,
                    )
                ]
            )
            trial_report = structure_report(trial)
            # cAMV is a hard, high-weight signal, but not an absolute veto:
            # exceptionally strong observed constructions may preserve or move
            # one violation.  The odd-ray completion itself may not worsen it.
            allowed_extra = 0 if kind == "flat_fold" else 1
            if (
                trial_report["structure_violation_count"]
                > current_report["structure_violation_count"] + allowed_extra
            ):
                continue
            working = trial
            current_report = trial_report
            selected.append(candidate)
            if len(selected) >= 5:
                break
        if not selected:
            continue

        colored, mv_stats, camv_full = _assign_and_optimize_mv(
            square, working, settings.mv_mode
        )
        all_edges = _add_boundaries(colored, size)
        camv_structure = structure_report(colored)
        overlay, reconstruction = _render_images(square, all_edges)
        variant_stats = {
            **base_stats,
            **mv_stats,
            "internal_segments": len(colored),
            "total_cp_segments": len(all_edges),
            "camv_structural_completeness_score": camv_structure[
                "structural_completeness_score"
            ],
            "camv_structure_violation_count": camv_structure[
                "violation_count"
            ],
            "camv_structure": camv_structure,
            "camv_full": camv_full,
            "construction_variant": kind,
            "construction_lines_added": len(selected),
        }
        constructions = [
            {
                "kind": item.kind,
                "label": item.label,
                "expression": item.expression,
                "support": round(item.support, 9),
                "continuous_run": round(item.continuous_run, 9),
                "novel_coverage": round(item.novel_coverage, 9),
                "start_px": [round(float(value), 6) for value in item.start],
                "end_px": [round(float(value), 6) for value in item.end],
            }
            for item in selected
        ]
        variant_warnings = [
            f"本版本在严格 22.5° 结果上新增 {len(selected)} 条“{labels[kind]}”精确构造；"
            "每条线都由残余笔画像素触发，并保留父规则与证据，不是自由角度拟合。"
        ]
        if camv_structure["violation_count"]:
            variant_warnings.append(
                f"新增后仍有 {camv_structure['violation_vertex_count']} 个 cAMV 结构可疑节点；"
                "该指标没有被当作绝对否决条件。"
            )
        variants.append(
            {
                "id": kind.replace("_", "-"),
                "label": labels[kind],
                "cp": edges_to_cp(all_edges, size),
                "stats": variant_stats,
                "warnings": variant_warnings,
                "constructions": constructions,
                "overlay_data_uri": _png_data_uri(overlay),
                "reconstruction_data_uri": _png_data_uri(reconstruction),
            }
        )
        admitted_by_strategy[kind] = len(selected)
    diagnostics["construction_versions_emitted"] = len(variants)
    diagnostics["construction_lines_admitted_by_strategy"] = admitted_by_strategy
    return variants, diagnostics


def _edge_mv_evidence(square: np.ndarray, edge: Edge) -> dict:
    """Measure deterministic red/blue evidence along one planar edge."""

    delta = edge.end - edge.start
    length = float(np.linalg.norm(delta))
    if length <= 1e-9:
        return {
            "red_score": 0.0,
            "blue_score": 0.0,
            "red_probability": 0.5,
            "confidence": 0.0,
            "coverage": 0.0,
            "ambiguous": True,
        }
    direction = delta / length
    normal = np.array([-direction[1], direction[0]], dtype=float)
    margin = min(3.0, length * 0.22)
    sample_count = max(5, int(max(1.0, length - 2.0 * margin) * 1.5) + 1)
    parameters = np.linspace(margin, length - margin, sample_count)
    centers = edge.start + parameters[:, None] * direction
    offsets = np.array([-1.5, -0.75, 0.0, 0.75, 1.5], dtype=float)
    points = centers[:, None, :] + offsets[None, :, None] * normal
    xs = np.clip(
        np.rint(points[:, :, 0]).astype(int), 0, square.shape[1] - 1
    )
    ys = np.clip(
        np.rint(points[:, :, 1]).astype(int), 0, square.shape[0] - 1
    )
    pixels = square[ys, xs].astype(float)
    blue = pixels[:, :, 0]
    green = pixels[:, :, 1]
    red = pixels[:, :, 2]
    red_strength = np.maximum(0.0, red - np.maximum(green, blue))
    blue_strength = np.maximum(0.0, blue - np.maximum(green, red))
    chroma = red_strength + blue_strength
    best = np.argmax(chroma, axis=1)
    rows = np.arange(sample_count)
    selected_red = red_strength[rows, best]
    selected_blue = blue_strength[rows, best]
    selected_chroma = chroma[rows, best]
    colored = selected_chroma >= 18.0
    red_score = float(np.sum(selected_red[colored]))
    blue_score = float(np.sum(selected_blue[colored]))
    total = red_score + blue_score
    coverage = float(np.mean(colored)) if sample_count else 0.0
    red_probability = (red_score + 4.0) / (total + 8.0)
    confidence = abs(red_score - blue_score) / (total + 1.0)
    ambiguous = confidence < 0.48 or coverage < 0.32 or total < 40.0
    return {
        "red_score": round(red_score, 6),
        "blue_score": round(blue_score, 6),
        "red_probability": round(float(red_probability), 9),
        "confidence": round(float(confidence), 9),
        "coverage": round(coverage, 9),
        "ambiguous": ambiguous,
    }


def _assign_and_optimize_mv(
    square: np.ndarray,
    edges: list[Edge],
    mv_mode: str = "auto",
) -> tuple[list[Edge], dict, dict]:
    """Classify red/blue locally, then let full cAMV revise only weak calls."""

    size = square.shape[0]
    evidence = [_edge_mv_evidence(square, edge) for edge in edges]

    def full_report(values: list[Edge], *, include_mv: bool = True) -> dict:
        all_edges = _add_boundaries(values, size)
        segments: list[GeometrySegment] = []
        internal_row = 0
        for edge in all_edges:
            row = None
            if edge.line_type != 1:
                row = internal_row
                internal_row += 1
            segments.append(
                GeometrySegment(
                    edge.line_type,
                    (float(edge.start[0]), float(edge.start[1])),
                    (float(edge.end[0]), float(edge.end[1])),
                    row=row,
                )
            )
        return audit_camv_structure(
            segments,
            folding_types={2, 3},
            include_mv=include_mv,
        )

    color_evidence_segments = sum(
        item["coverage"] >= 0.10
        and item["red_score"] + item["blue_score"] >= 40.0
        for item in evidence
    )
    detected_monochrome = color_evidence_segments == 0
    use_monochrome = mv_mode == "monochrome" or (
        mv_mode == "auto" and detected_monochrome
    )
    if use_monochrome:
        # A black line contains geometry but no M/V observation.  Keep the
        # established safe export convention (mountain) and explicitly skip
        # M/V rules; cAMV must not fabricate a unique colouring from nothing.
        assigned = [
            Edge(edge.start.copy(), edge.end.copy(), 2, edge.support)
            for edge in edges
        ]
        report = full_report(assigned, include_mv=False)
        uncertain = [
            {
                "edge_index": index,
                "start": [round(float(value), 6) for value in edge.start],
                "end": [round(float(value), 6) for value in edge.end],
                "initial_type": 2,
                "final_type": 2,
                **item,
            }
            for index, (edge, item) in enumerate(zip(edges, evidence))
        ]
        return assigned, {
            "mv_input_mode": "monochrome",
            "mv_assignment_source": "all_mountain_safe_default",
            "mv_color_evidence_segments": color_evidence_segments,
            "mv_red_segments": len(assigned),
            "mv_blue_segments": 0,
            "mv_ambiguous_segments": len(assigned),
            "mv_strong_segments": 0,
            "mv_camv_single_flips": 0,
            "mv_camv_pair_flips": 0,
            "mv_camv_changed_segments": 0,
            "mv_camv_changed_edge_indices": [],
            "mv_camv_violations_before": 0,
            "mv_camv_violations_after": 0,
            "camv_full_violations_before": report["violation_count"],
            "camv_full_violations_after": report["violation_count"],
            "camv_full_passes": None,
            "mv_average_confidence": 0.0,
            "mv_optimization_history": [],
            "mv_most_uncertain_segments": uncertain[:40],
        }, report

    assigned = [
        Edge(
            edge.start.copy(),
            edge.end.copy(),
            2 if item["red_probability"] >= 0.5 else 3,
            edge.support,
        )
        for edge, item in zip(edges, evidence)
    ]

    initial_types = [edge.line_type for edge in assigned]
    report = full_report(assigned)
    initial_report = report
    history: list[dict] = []
    single_flips = 0
    pair_flips = 0

    def evidence_cost(index: int, line_type: int) -> float:
        probability = float(evidence[index]["red_probability"])
        selected = probability if line_type == 2 else 1.0 - probability
        return -math.log(max(1e-6, selected))

    def implicated_candidates(current_report: dict) -> list[int]:
        rows = {
            int(row)
            for violation in current_report["violations"]
            if violation["rule"] in {"maekawa", "little_big_little"}
            for row in violation["rows"]
            if isinstance(row, int) and 0 <= row < len(assigned)
        }
        candidates = [
            index
            for index in rows
            if evidence[index]["ambiguous"]
        ]
        return sorted(
            candidates,
            key=lambda index: (
                float(evidence[index]["confidence"]),
                -float(evidence[index]["coverage"]),
                index,
            ),
        )[:32]

    for _ in range(16):
        current_mv = int(report["mv_violation_count"])
        if current_mv == 0:
            break
        candidates = implicated_candidates(report)
        if not candidates:
            break

        best_single: tuple[int, float, int, dict] | None = None
        for index in candidates:
            old_type = assigned[index].line_type
            new_type = 3 if old_type == 2 else 2
            assigned[index].line_type = new_type
            trial_report = full_report(assigned)
            assigned[index].line_type = old_type
            reduction = current_mv - int(trial_report["mv_violation_count"])
            if reduction <= 0:
                continue
            cost_delta = evidence_cost(index, new_type) - evidence_cost(
                index, old_type
            )
            proposal = (reduction, -cost_delta, -index, trial_report)
            if best_single is None or proposal[:3] > best_single[:3]:
                best_single = proposal

        if best_single is not None:
            index = -best_single[2]
            old_type = assigned[index].line_type
            assigned[index].line_type = 3 if old_type == 2 else 2
            before = int(report["mv_violation_count"])
            report = best_single[3]
            history.append(
                {
                    "kind": "single",
                    "edge_indices": [index],
                    "from": [old_type],
                    "to": [assigned[index].line_type],
                    "confidence": [evidence[index]["confidence"]],
                    "mv_violations_before": before,
                    "mv_violations_after": report["mv_violation_count"],
                }
            )
            single_flips += 1
            continue

        # Some little-big-little states need two weak assignments changed
        # together. Keep this search local and small; strong image evidence is
        # never admitted to the candidate set.
        pair_candidates = candidates[:10]
        best_pair: tuple[int, float, int, int, dict] | None = None
        for first_position, first in enumerate(pair_candidates):
            for second in pair_candidates[first_position + 1 :]:
                old_first = assigned[first].line_type
                old_second = assigned[second].line_type
                assigned[first].line_type = 3 if old_first == 2 else 2
                assigned[second].line_type = 3 if old_second == 2 else 2
                trial_report = full_report(assigned)
                assigned[first].line_type = old_first
                assigned[second].line_type = old_second
                reduction = current_mv - int(
                    trial_report["mv_violation_count"]
                )
                if reduction <= 0:
                    continue
                cost_delta = (
                    evidence_cost(first, 3 if old_first == 2 else 2)
                    - evidence_cost(first, old_first)
                    + evidence_cost(second, 3 if old_second == 2 else 2)
                    - evidence_cost(second, old_second)
                )
                proposal = (
                    reduction,
                    -cost_delta,
                    -first,
                    -second,
                    trial_report,
                )
                if best_pair is None or proposal[:4] > best_pair[:4]:
                    best_pair = proposal
        if best_pair is None:
            break
        first, second = -best_pair[2], -best_pair[3]
        old_types = [assigned[first].line_type, assigned[second].line_type]
        assigned[first].line_type = 3 if old_types[0] == 2 else 2
        assigned[second].line_type = 3 if old_types[1] == 2 else 2
        before = int(report["mv_violation_count"])
        report = best_pair[4]
        history.append(
            {
                "kind": "pair",
                "edge_indices": [first, second],
                "from": old_types,
                "to": [assigned[first].line_type, assigned[second].line_type],
                "confidence": [
                    evidence[first]["confidence"],
                    evidence[second]["confidence"],
                ],
                "mv_violations_before": before,
                "mv_violations_after": report["mv_violation_count"],
            }
        )
        pair_flips += 2

    changed = [
        index
        for index, edge in enumerate(assigned)
        if edge.line_type != initial_types[index]
    ]
    ambiguous_indices = [
        index for index, item in enumerate(evidence) if item["ambiguous"]
    ]
    uncertain = sorted(
        (
            {
                "edge_index": index,
                "start": [round(float(value), 6) for value in edges[index].start],
                "end": [round(float(value), 6) for value in edges[index].end],
                "initial_type": initial_types[index],
                "final_type": assigned[index].line_type,
                **evidence[index],
            }
            for index in ambiguous_indices
        ),
        key=lambda item: (item["confidence"], item["edge_index"]),
    )
    stats = {
        "mv_input_mode": "color",
        "mv_assignment_source": "image_then_camv_weak_revision",
        "mv_color_evidence_segments": color_evidence_segments,
        "mv_red_segments": sum(edge.line_type == 2 for edge in assigned),
        "mv_blue_segments": sum(edge.line_type == 3 for edge in assigned),
        "mv_ambiguous_segments": len(ambiguous_indices),
        "mv_strong_segments": len(assigned) - len(ambiguous_indices),
        "mv_camv_single_flips": single_flips,
        "mv_camv_pair_flips": pair_flips,
        "mv_camv_changed_segments": len(changed),
        "mv_camv_changed_edge_indices": changed,
        "mv_camv_violations_before": initial_report["mv_violation_count"],
        "mv_camv_violations_after": report["mv_violation_count"],
        "camv_full_violations_before": initial_report["violation_count"],
        "camv_full_violations_after": report["violation_count"],
        "camv_full_passes": report["passes_camv"],
        "mv_average_confidence": round(
            float(np.mean([item["confidence"] for item in evidence]))
            if evidence
            else 1.0,
            9,
        ),
        "mv_optimization_history": history,
        "mv_most_uncertain_segments": uncertain[:40],
    }
    return assigned, stats, report


def _cp_value(value: float) -> str:
    if abs(value) < 5e-10:
        value = 0.0
    if abs(value - 200.0) < 5e-9:
        value = 200.0
    if abs(value + 200.0) < 5e-9:
        value = -200.0
    return f"{value:.12g}"


def edges_to_cp(edges: Iterable[Edge], size: int) -> str:
    maximum = float(size - 1)
    rows: list[tuple[int, float, float, float, float]] = []
    for edge in edges:
        x1 = -200.0 + 400.0 * float(edge.start[0]) / maximum
        # Oriedita's .cp reader/writer preserves coordinates verbatim.  The
        # supplied/reference CP convention maps the raster top to y=-200 and
        # the raster bottom to y=+200, so export must use the same downward Y
        # direction as the normalized source image.
        y1 = -200.0 + 400.0 * float(edge.start[1]) / maximum
        x2 = -200.0 + 400.0 * float(edge.end[0]) / maximum
        y2 = -200.0 + 400.0 * float(edge.end[1]) / maximum
        # Oriedita cAMV ignores auxiliary creases entirely. Unclassified
        # internal geometry still falls back to mountain, while classified
        # mountain/valley assignments are preserved.
        output_type = (
            1
            if edge.line_type == 1
            else edge.line_type
            if edge.line_type in {2, 3}
            else 2
        )
        rows.append((output_type, x1, y1, x2, y2))
    rows.sort(key=lambda row: (row[0], round(row[2], 8), round(row[1], 8), round(row[4], 8), round(row[3], 8)))
    return "".join(
        f"{line_type} {_cp_value(x1)} {_cp_value(y1)} {_cp_value(x2)} {_cp_value(y2)}\n"
        for line_type, x1, y1, x2, y2 in rows
    )


def _render_images(square: np.ndarray, edges: list[Edge]) -> tuple[np.ndarray, np.ndarray]:
    overlay_lines = square.copy()
    reconstruction = np.full_like(square, 255)
    for edge in edges:
        start = tuple(np.rint(edge.start).astype(int))
        end = tuple(np.rint(edge.end).astype(int))
        if edge.line_type == 1:
            color, width = (20, 20, 20), 2
        elif edge.line_type == 2:
            color, width = (20, 20, 235), 1
        elif edge.line_type == 3:
            color, width = (235, 45, 35), 1
        else:
            color, width = (40, 185, 25), 1
        cv2.line(overlay_lines, start, end, color, width, cv2.LINE_AA)
        cv2.line(reconstruction, start, end, color, width, cv2.LINE_AA)
    overlay = cv2.addWeighted(square, 0.50, overlay_lines, 0.50, 0)
    return overlay, reconstruction


def _png_data_uri(image: np.ndarray) -> str:
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ReconstructionError("生成预览图失败。")
    return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def reconstruct(
    data: bytes,
    settings: Settings | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict:
    def report(percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(percent, message)

    settings = settings or Settings()
    report(2, "读取图片")
    image = _decode_image(data)
    square, (x0, y0, x1, y1), scale_stats = prepare_paper_square(
        image, settings.analysis_size, settings.paper_corners
    )
    report(7, "检查白底线稿格式")
    input_format_stats = validate_white_line_art(square)
    size = square.shape[0]
    report(11, "提取黑线与红蓝线像素")
    ink, _, evidence_stats = _adaptive_geometry_evidence(square)
    diffuse_input = _has_diffuse_color_bleed(square)
    # All later distance tolerances are expressed relative to the estimated
    # stroke width. The public settings object remains unchanged, so repeated
    # calls cannot leak one image's calibration into the next.
    effective_settings = replace(
        settings,
        evidence_distance_px=evidence_stats[
            "adaptive_evidence_distance_px"
        ],
        algebraic_snap_px=float(
            np.clip(
                settings.algebraic_snap_px
                * evidence_stats["adaptive_evidence_distance_px"]
                / 1.75,
                settings.algebraic_snap_px,
                6.0,
            )
        ),
    )

    report(16, "寻找候选直线")
    clusters, hough_stats = _extract_hough_clusters(ink, effective_settings)
    lines, algebraic_rejected = _snap_lines_to_algebraic_anchors(
        clusters, size, effective_settings
    )
    if not lines:
        raise ReconstructionError("没有候选线通过 22.5° 与 a+b√2 约束。")
    # The finite-ray pass is the precision backbone. Two independently built
    # vertex graphs are recall passes; fusion admits only strokes not already
    # represented by the backbone. On diffuse/strongly compressed inputs those
    # recall graphs are deliberately disabled: colour bleed creates plausible
    # but false local vertices, while the exact-direction projection still has
    # enough global evidence to reconstruct the 22.5-degree ray system.
    report(24, "激活角点、边中点与合法射线")
    ray_edges, lsd_lines, ray_stats = _reconstruct_lsd_rays(
        square, ink, effective_settings
    )
    vertex_edges: list[Edge] = []
    hough_vertex_edges: list[Edge] = []
    vertex_stats: dict = {}
    hough_vertex_stats: dict = {}
    recall_policy_stats = {"diffuse_recall_graphs_skipped": int(diffuse_input)}
    report(34, "建立候选交点图")
    if not diffuse_input:
        try:
            vertex_edges, _, vertex_stats = _reconstruct_lsd_vertex_graph(
                square, ink, effective_settings
            )
        except ReconstructionError:
            pass
        try:
            hough_vertex_edges, _, hough_vertex_stats = _reconstruct_vertex_graph(
                ink, effective_settings
            )
        except ReconstructionError:
            pass
    report(43, "融合射线与交点证据")
    internal_edges, fusion_stats = _fuse_edge_sets(
        ray_edges, vertex_edges, hough_vertex_edges, lsd_lines, size
    )
    skeleton_recovery_stats = {
        "skeleton_topology_observations": 0,
        "skeleton_exact_nodes": 0,
        "skeleton_exact_edges_recovered": 0,
        "skeleton_exact_rays_recovered": 0,
        "skeleton_recovery_rounds": [],
    }
    if not diffuse_input:
        internal_edges, skeleton_recovery_stats = (
            _recover_exact_skeleton_node_edges(
                square,
                ink,
                internal_edges,
                lsd_lines,
                effective_settings,
            )
        )
    parallel_identity_stats = {
        "overlapping_parallel_ray_identities_bound": 0
    }
    if (
        not diffuse_input
        and ray_stats.get("lsd_stroke_edges_centered", 0) > 0
    ):
        internal_edges, bound_parallel_identities = (
            _bind_overlapping_parallel_ray_identities(
                internal_edges,
                lsd_lines,
            )
        )
        parallel_identity_stats[
            "overlapping_parallel_ray_identities_bound"
        ] = bound_parallel_identities
    report(52, "推导精确交点并清理线头")
    internal_edges, construction_stats = _snap_and_prune_dangling_edges(
        internal_edges, size, construction_lines=lsd_lines
    )
    # Closure decisions must see every crossing as an explicit existing node;
    # otherwise a long segment hides its interior contacts and node-to-node
    # recovery can repeatedly nominate geometry that is already present.
    internal_edges = _planarize_edges(internal_edges)
    report(61, "闭合有证据支持的折痕路径")
    internal_edges, closure_stats = _close_internal_lineheads(
        internal_edges,
        lsd_lines,
        ink,
        effective_settings,
        conservative_evidence=diffuse_input,
    )
    if (
        not diffuse_input
        and ray_stats.get("lsd_stroke_edges_centered", 0) > 0
    ):
        internal_edges, post_closure_bound = (
            _bind_overlapping_parallel_ray_identities(
                internal_edges,
                lsd_lines,
            )
        )
        parallel_identity_stats[
            "overlapping_parallel_ray_identities_bound"
        ] += post_closure_bound
        if post_closure_bound:
            # Recompute every affected endpoint as an exact intersection of
            # the now-canonical ray identities.  This preserves the attached
            # transverse arms; post-planar pruning is an invariant check, not
            # the mechanism used to finish a merge.
            internal_edges, resnap_stats = _snap_and_prune_dangling_edges(
                internal_edges,
                size,
                construction_lines=lsd_lines,
            )
            closure_stats["post_binding_endpoints_snapped"] = (
                resnap_stats["construction_endpoints_snapped"]
            )
            closure_stats["post_binding_edges_rejected"] = resnap_stats[
                "dangling_edges_rejected"
            ]
    # Closure can add a bridge ending in the middle of an exported segment.
    # Split again so every geometric connection is also an explicit CP node.
    internal_edges = _planarize_edges(internal_edges)
    # Planarization can expose a tiny degree-one arm that was hidden inside an
    # unsplit segment during closure. Audit the actual final graph, not its
    # pre-split representation.
    internal_edges, post_planar_pruned = _prune_post_planar_lineheads(
        internal_edges, size
    )
    closure_stats["post_planar_lineheads_pruned"] = post_planar_pruned
    graph_chord_stats = {
        "supported_graph_chords_recovered": 0,
        "supported_boundary_chords_recovered": 0,
    }
    if not diffuse_input:
        internal_edges, graph_chord_stats = _recover_supported_graph_chords(
            square,
            ink,
            internal_edges,
            effective_settings,
        )
        internal_edges = _planarize_edges(internal_edges)
    one_ended_stats = {"one_ended_exact_rays_recovered": 0}
    if not diffuse_input:
        internal_edges, one_ended_stats = _recover_one_ended_exact_rays(
            square,
            ink,
            internal_edges,
        )
        internal_edges = _planarize_edges(internal_edges)
    internal_edges, local_cycle_stats = _prune_unsupported_local_cycles(
        square,
        ink,
        internal_edges,
    )
    internal_edges, local_cycle_lineheads = _prune_post_planar_lineheads(
        internal_edges, size
    )
    local_cycle_stats["local_cycle_lineheads_pruned"] = local_cycle_lineheads
    pipeline = "fused_22_5_graph"
    if lsd_lines:
        lines = lsd_lines

    # Sparse monochrome patterns can still lack enough endpoint votes. Retain
    # the exact Hough rays as a final, conservative fallback. This decision
    # must happen before cAMV and M/V assignment: replacing the graph later
    # would leave both reports describing geometry that is no longer exported.
    fallback_stats = {
        "sparse_ray_fallback_used": 0,
        "sparse_ray_fallback_segments": 0,
    }
    if len(internal_edges) < max(4, len(lines) // 2):
        fallback_runs, fallback_distance = _supported_runs(
            lines, ink, effective_settings
        )
        fallback_edges = _edges_from_runs(
            lines,
            fallback_runs,
            fallback_distance,
            effective_settings,
        )
        if len(fallback_edges) > len(internal_edges):
            internal_edges = _planarize_edges(
                _remove_boundary_coincident_edges(fallback_edges, size)
            )
            fallback_stats["sparse_ray_fallback_used"] = 1
            fallback_stats["sparse_ray_fallback_segments"] = len(internal_edges)
            fallback_stats["supported_runs"] = len(fallback_runs)
            pipeline = "sparse_ray_fallback"

    report(76, "执行 cAMV 几何复核")
    internal_edges, camv_repair_stats = _repair_near_focus_camv_violations(
        internal_edges,
        lsd_lines,
        ink,
        effective_settings,
    )
    internal_edges = _planarize_edges(internal_edges)
    report(84, "根据 cAMV 反复复核缺失路径")
    internal_edges, camv_path_stats = _recover_camv_supported_paths(
        square,
        ink,
        internal_edges,
        effective_settings,
    )
    internal_edges = _planarize_edges(internal_edges)
    report(90, "判断红蓝峰谷并完成 cAMV 检验")
    internal_edges, mv_stats, camv_full = _assign_and_optimize_mv(
        square,
        internal_edges,
        settings.mv_mode,
    )
    graph_stats = {
        **ray_stats,
        **vertex_stats,
        **hough_vertex_stats,
        **recall_policy_stats,
        **fusion_stats,
        **skeleton_recovery_stats,
        **parallel_identity_stats,
        **construction_stats,
        **closure_stats,
        **graph_chord_stats,
        **one_ended_stats,
        **local_cycle_stats,
        **fallback_stats,
        **camv_repair_stats,
        **camv_path_stats,
        **mv_stats,
    }
    all_edges = _add_boundaries(internal_edges, size)
    camv_structure = audit_camv_structure(
        [
            GeometrySegment(
                edge.line_type,
                (float(edge.start[0]), float(edge.start[1])),
                (float(edge.end[0]), float(edge.end[1])),
            )
            for edge in all_edges
        ]
    )
    report(94, "生成 CP 与结果预览")
    cp_text = edges_to_cp(all_edges, size)
    overlay, reconstruction = _render_images(square, all_edges)

    anchors = []
    for line in sorted(lines, key=lambda item: (item.orientation, item.offset)):
        if line.origin_kind == "intersection":
            source_label = f"第 {line.generation} 代交点"
            expression = "由母射线相交"
            side_label = "内部"
        elif line.origin_kind == "boundary_contact":
            source_label = f"第 {line.generation} 代纸边交点"
            expression = "由已有射线与纸边相交"
            side_label = line.anchor_side
        elif line.origin_kind == "corner":
            source_label = "角点种子"
            expression = "角点"
            side_label = line.anchor_side
        elif line.origin_kind == "midpoint":
            source_label = "边中点种子"
            expression = "1/2"
            side_label = line.anchor_side
        elif line.origin_kind == "algebraic_internal":
            source_label = "唯一内部 a+b√2 种子"
            if line.anchor_coordinates is not None:
                expression = (
                    f"({line.anchor_coordinates[0].expression}, "
                    f"{line.anchor_coordinates[1].expression})"
                )
            else:
                expression = line.anchor_value.expression
            side_label = "内部"
        elif line.origin_kind == "algebraic_boundary":
            source_label = "唯一纸边 a+b√2 种子"
            if line.anchor_coordinates is not None:
                expression = (
                    f"({line.anchor_coordinates[0].expression}, "
                    f"{line.anchor_coordinates[1].expression})"
                )
            else:
                expression = line.anchor_value.expression
            side_label = line.anchor_side
        else:
            source_label = "a+b√2 种子"
            expression = line.anchor_value.expression
            side_label = line.anchor_side
        anchors.append(
            {
                "side": side_label,
                "expression": expression,
                "decimal": round(line.anchor_value.value, 9),
                "coordinate_decimal": (
                    [
                        round(line.anchor_coordinates[0].value, 9),
                        round(line.anchor_coordinates[1].value, 9),
                    ]
                    if line.anchor_coordinates is not None
                    else None
                ),
                "coordinate_expression": (
                    [
                        line.anchor_coordinates[0].expression,
                        line.anchor_coordinates[1].expression,
                    ]
                    if line.anchor_coordinates is not None
                    else None
                ),
                "angle": line.angle_deg,
                "line_offset_px": round(float(line.offset), 6),
                "anchor_point_px": [
                    round(float(line.anchor_point[0]), 6),
                    round(float(line.anchor_point[1]), 6),
                ],
                "snap_error_px": round(line.snap_error_px, 3),
                "generation": line.generation,
                "source": source_label,
                "parents": list(line.parent_lines) if line.parent_lines is not None else None,
            }
        )

    boundary_count = sum(edge.line_type == 1 for edge in all_edges)
    stats = {
        **hough_stats,
        "source_width": int(image.shape[1]),
        "source_height": int(image.shape[0]),
        "paper_bbox": [x0, y0, x1, y1],
        **scale_stats,
        **input_format_stats,
        **evidence_stats,
        "diffuse_input_mode": diffuse_input,
        "algebraic_rejected_rays": algebraic_rejected,
        "exact_rays": len(lines),
        **graph_stats,
        "supported_runs": graph_stats.get(
            "supported_runs",
            graph_stats.get("lsd_evidence_intervals", graph_stats.get("vertex_graph_edges", 0)),
        ),
        "internal_segments": len(internal_edges),
        "boundary_segments": boundary_count,
        "total_cp_segments": len(all_edges),
        "output_internal_line_types": [2, 3],
        "camv_structural_completeness_score": camv_structure[
            "structural_completeness_score"
        ],
        "camv_structure_violation_count": camv_structure["violation_count"],
        "camv_structure": camv_structure,
        "camv_full": camv_full,
        "pipeline": pipeline,
        "settings": asdict(settings),
    }
    warnings = []
    if graph_stats.get("camv_path_recheck_improved"):
        warnings.append(
            "cAMV 结构复核触发强证据补线："
            f"经过 {graph_stats['camv_path_recheck_rounds']} 轮，"
            f"补回 {graph_stats['camv_path_committed_arms']} 条精确节点间射线，"
            f"结构异常由 {graph_stats['camv_path_violations_before']} 降至 "
            f"{graph_stats['camv_path_violations_after']}。"
        )
    if camv_structure["violation_count"]:
        counts = camv_structure["rule_counts"]
        warnings.append(
            "cAMV 结构子集发现 "
            f"{camv_structure['violation_vertex_count']} 个可疑节点"
            f"（奇数折痕 {counts['number_of_folds']}，"
            f"川崎角度 {counts['kawasaki_angles']}，"
            f"边界拓扑 {counts['boundary_topology']}）。"
            "这是高权重完备性信号，但不会单独否决结果。"
        )
    if camv_full["mv_checks"]["enabled"] and camv_full["violation_count"]:
        counts = camv_full["rule_counts"]
        warnings.append(
            "完整 cAMV 仍有 "
            f"{camv_full['violation_vertex_count']} 个异常节点"
            f"（结构 {camv_full['structure_violation_count']}，"
            f"Maekawa {counts['maekawa']}，"
            f"big-little-big {counts['little_big_little']}）。"
            "程序不会翻转强红/强蓝折痕来强行消除这些异常。"
        )
    if graph_stats.get("mv_input_mode") == "monochrome":
        warnings.append(
            "检测为纯黑线 CP：几何照常重建，内部线统一按峰线导出；"
            "由于图片没有红蓝证据，本版本不运行 Maekawa 与 big-little-big 改色，"
            "也不会把 cAMV 推测颜色冒充识别结果。"
        )
    if hough_stats["angle_rejected_segments"]:
        warnings.append(
            f"有 {hough_stats['angle_rejected_segments']} 个图像线段偏离 22.5° 系，已忽略。"
        )
    if len(internal_edges) > 700:
        warnings.append("当前结果切分较细；这是原型阶段的已知问题，可继续合并同射线上的冗余小段。")
    variants: list[dict] = []
    construction_variant_stats = {
        "construction_residual_pixels": 0,
        "construction_residual_observations": 0,
        "construction_candidates": 0,
        "construction_versions_emitted": 0,
    }
    if settings.construction_variants:
        report(97, "生成精确备选构造版本")
        variants, construction_variant_stats = _build_exact_construction_variants(
            square,
            ink,
            internal_edges,
            effective_settings,
            stats,
        )
    stats.update(construction_variant_stats)
    report(100, "重建完成")
    return {
        "id": "strict",
        "label": "严格 22.5°",
        "cp": cp_text,
        "stats": stats,
        "anchors": anchors,
        "warnings": warnings,
        "constructions": [],
        "variants": variants,
        "overlay_data_uri": _png_data_uri(overlay),
        "reconstruction_data_uri": _png_data_uri(reconstruction),
        "overlay_image": overlay,
        "reconstruction_image": reconstruction,
    }


def reconstruct_file(
    image_path: str | Path,
    cp_path: str | Path,
    overlay_path: str | Path | None = None,
    reconstruction_path: str | Path | None = None,
    settings: Settings | None = None,
) -> dict:
    image_path = Path(image_path)
    result = reconstruct(image_path.read_bytes(), settings=settings)
    Path(cp_path).write_text(result["cp"], encoding="utf-8", newline="\n")
    if overlay_path:
        cv2.imwrite(str(overlay_path), result["overlay_image"])
    if reconstruction_path:
        cv2.imwrite(str(reconstruction_path), result["reconstruction_image"])
    return result
