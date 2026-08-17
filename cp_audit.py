from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from foldability import GeometrySegment, audit_camv_structure

from reconstructor import (
    ALLOWED_ANGLES,
    _adaptive_geometry_evidence,
    _decode_image,
    _paper_bbox,
    prepare_paper_square,
)


@dataclass(frozen=True)
class CpSegment:
    line_type: int
    start: tuple[float, float]
    end: tuple[float, float]
    row: int


@dataclass
class RayGroup:
    orientation: int
    offset: float
    intervals: list[list[float]]
    rows: list[int]

    @property
    def length(self) -> float:
        return sum(end - start for start, end in self.intervals)


def parse_cp(text: str) -> tuple[list[CpSegment], list[dict]]:
    segments: list[CpSegment] = []
    issues: list[dict] = []
    for row_number, raw_row in enumerate(text.splitlines(), 1):
        row = raw_row.strip()
        if not row:
            continue
        values = row.split()
        if len(values) != 5:
            issues.append(
                {"row": row_number, "code": "field_count", "text": raw_row}
            )
            continue
        try:
            line_type = int(values[0])
            coordinates = [float(value) for value in values[1:]]
        except ValueError:
            issues.append(
                {"row": row_number, "code": "invalid_number", "text": raw_row}
            )
            continue
        if not all(math.isfinite(value) for value in coordinates):
            issues.append(
                {"row": row_number, "code": "non_finite", "text": raw_row}
            )
            continue
        start = (coordinates[0], coordinates[1])
        end = (coordinates[2], coordinates[3])
        if math.dist(start, end) <= 1e-9:
            issues.append(
                {"row": row_number, "code": "zero_length", "text": raw_row}
            )
            continue
        segments.append(CpSegment(line_type, start, end, row_number))
    return segments, issues


def read_cp(path: str | Path) -> tuple[list[CpSegment], list[dict]]:
    return parse_cp(Path(path).read_text(encoding="utf-8"))


def _ray_values(segment: CpSegment) -> tuple[int, float, float, float, float]:
    start = np.array(segment.start, dtype=float)
    end = np.array(segment.end, dtype=float)
    delta = end - start
    raw_angle = math.atan2(float(delta[1]), float(delta[0])) % math.pi
    errors = [
        min(abs(raw_angle - angle), math.pi - abs(raw_angle - angle))
        for angle in ALLOWED_ANGLES
    ]
    orientation = int(np.argmin(errors))
    theta = ALLOWED_ANGLES[orientation]
    direction = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    normal = np.array([-direction[1], direction[0]], dtype=float)
    offset = float(normal @ ((start + end) / 2.0))
    first_t, second_t = sorted(
        (float(direction @ start), float(direction @ end))
    )
    return orientation, offset, first_t, second_t, math.degrees(errors[orientation])


def _merge_intervals(
    intervals: Iterable[Iterable[float]], gap_tolerance: float = 1e-7
) -> list[list[float]]:
    merged: list[list[float]] = []
    for first, second in sorted(
        (sorted((float(a), float(b))) for a, b in intervals),
        key=lambda value: value[0],
    ):
        if not merged or first - merged[-1][1] > gap_tolerance:
            merged.append([first, second])
        else:
            merged[-1][1] = max(merged[-1][1], second)
    return merged


def group_rays(
    segments: Iterable[CpSegment],
    *,
    legal_angle_tolerance_deg: float = 1e-5,
    collinear_tolerance: float = 1e-5,
) -> tuple[list[RayGroup], list[dict]]:
    records: list[tuple[int, float, float, float, int]] = []
    illegal: list[dict] = []
    for segment in segments:
        orientation, offset, first_t, second_t, error = _ray_values(segment)
        if error > legal_angle_tolerance_deg:
            illegal.append(
                {
                    "row": segment.row,
                    "angle_error_deg": round(error, 9),
                    "start": list(segment.start),
                    "end": list(segment.end),
                }
            )
            continue
        records.append((orientation, offset, first_t, second_t, segment.row))

    groups: list[RayGroup] = []
    for orientation in range(8):
        current: RayGroup | None = None
        for _, offset, first_t, second_t, row in sorted(
            (item for item in records if item[0] == orientation),
            key=lambda item: item[1],
        ):
            if current is None or abs(offset - current.offset) > collinear_tolerance:
                current = RayGroup(
                    orientation, offset, [[first_t, second_t]], [row]
                )
                groups.append(current)
            else:
                count = len(current.rows)
                current.offset = (current.offset * count + offset) / (count + 1)
                current.intervals.append([first_t, second_t])
                current.rows.append(row)
        for group in (value for value in groups if value.orientation == orientation):
            group.intervals = _merge_intervals(group.intervals)
    return groups, illegal


def _subtract_intervals(
    source: list[list[float]], covered: list[list[float]], tolerance: float
) -> list[list[float]]:
    result: list[list[float]] = []
    for first, second in source:
        pieces = [[first, second]]
        for cover_first, cover_second in covered:
            next_pieces: list[list[float]] = []
            for piece_first, piece_second in pieces:
                if (
                    cover_second <= piece_first + tolerance
                    or cover_first >= piece_second - tolerance
                ):
                    next_pieces.append([piece_first, piece_second])
                    continue
                if cover_first > piece_first + tolerance:
                    next_pieces.append(
                        [piece_first, min(piece_second, cover_first)]
                    )
                if cover_second < piece_second - tolerance:
                    next_pieces.append(
                        [max(piece_first, cover_second), piece_second]
                    )
            pieces = next_pieces
        result.extend(
            piece
            for piece in pieces
            if piece[1] - piece[0] > tolerance
        )
    return result


def _interval_overlap(
    first: list[list[float]], second: list[list[float]]
) -> float:
    return sum(
        max(0.0, min(a1, b1) - max(a0, b0))
        for a0, a1 in first
        for b0, b1 in second
    )


def _intersect_intervals(
    first: list[list[float]], second: list[list[float]]
) -> list[list[float]]:
    return _merge_intervals(
        [
            [max(a0, b0), min(a1, b1)]
            for a0, a1 in first
            for b0, b1 in second
            if min(a1, b1) > max(a0, b0)
        ]
    )


def _match_ray_groups(
    reference: list[RayGroup], prediction: list[RayGroup], tolerance: float
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    candidates = sorted(
        (
            (abs(reference[r].offset - prediction[p].offset), r, p)
            for r in range(len(reference))
            for p in range(len(prediction))
            if reference[r].orientation == prediction[p].orientation
            and abs(reference[r].offset - prediction[p].offset) <= tolerance
        ),
        key=lambda value: value[0],
    )
    used_reference: set[int] = set()
    used_prediction: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for error, reference_index, prediction_index in candidates:
        if (
            reference_index in used_reference
            or prediction_index in used_prediction
        ):
            continue
        used_reference.add(reference_index)
        used_prediction.add(prediction_index)
        matches.append((reference_index, prediction_index, error))
    return (
        matches,
        [index for index in range(len(reference)) if index not in used_reference],
        [index for index in range(len(prediction)) if index not in used_prediction],
    )


def _point_on_segment(
    point: np.ndarray, segment: CpSegment, tolerance: float
) -> tuple[bool, bool]:
    start = np.array(segment.start, dtype=float)
    end = np.array(segment.end, dtype=float)
    delta = end - start
    denominator = float(delta @ delta)
    factor = float(((point - start) @ delta) / denominator)
    projection = start + np.clip(factor, 0.0, 1.0) * delta
    on_segment = (
        -tolerance <= factor <= 1.0 + tolerance
        and float(np.linalg.norm(point - projection)) <= tolerance
    )
    at_endpoint = min(
        float(np.linalg.norm(point - start)),
        float(np.linalg.norm(point - end)),
    ) <= tolerance
    return on_segment, at_endpoint


def _canonical_graph_nodes(
    segments: list[CpSegment], tolerance: float
) -> list[dict]:
    points = [
        np.array(point, dtype=float)
        for segment in segments
        for point in (segment.start, segment.end)
    ]
    for first_index, first in enumerate(segments):
        first_start = np.array(first.start, dtype=float)
        first_end = np.array(first.end, dtype=float)
        first_delta = first_end - first_start
        for second in segments[first_index + 1 :]:
            second_start = np.array(second.start, dtype=float)
            second_end = np.array(second.end, dtype=float)
            second_delta = second_end - second_start
            matrix = np.column_stack((first_delta, -second_delta))
            determinant = float(np.linalg.det(matrix))
            if abs(determinant) <= 1e-12:
                continue
            factors = np.linalg.solve(matrix, second_start - first_start)
            if (
                -1e-8 <= factors[0] <= 1.0 + 1e-8
                and -1e-8 <= factors[1] <= 1.0 + 1e-8
            ):
                points.append(first_start + factors[0] * first_delta)

    clusters: list[list[np.ndarray]] = []
    for point in points:
        existing = next(
            (
                cluster
                for cluster in clusters
                if np.linalg.norm(point - np.mean(cluster, axis=0)) <= tolerance
            ),
            None,
        )
        if existing is None:
            clusters.append([point])
        else:
            existing.append(point)

    nodes: list[dict] = []
    for cluster in clusters:
        point = np.mean(cluster, axis=0)
        degree = 0
        incident_rows: list[int] = []
        for segment in segments:
            on_segment, at_endpoint = _point_on_segment(point, segment, tolerance)
            if not on_segment:
                continue
            degree += 1 if at_endpoint else 2
            incident_rows.append(segment.row)
        nodes.append(
            {
                "point": [float(point[0]), float(point[1])],
                "degree": degree,
                "rows": sorted(set(incident_rows)),
            }
        )
    return nodes


def _match_points(
    reference: list[dict], prediction: list[dict], tolerance: float
) -> tuple[int, list[dict], list[dict]]:
    candidates = sorted(
        (
            (
                math.dist(reference[r]["point"], prediction[p]["point"]),
                r,
                p,
            )
            for r in range(len(reference))
            for p in range(len(prediction))
            if math.dist(reference[r]["point"], prediction[p]["point"])
            <= tolerance
        ),
        key=lambda value: value[0],
    )
    used_reference: set[int] = set()
    used_prediction: set[int] = set()
    for _, reference_index, prediction_index in candidates:
        if (
            reference_index in used_reference
            or prediction_index in used_prediction
        ):
            continue
        used_reference.add(reference_index)
        used_prediction.add(prediction_index)
    return (
        len(used_reference),
        [value for index, value in enumerate(reference) if index not in used_reference],
        [value for index, value in enumerate(prediction) if index not in used_prediction],
    )


def _interval_geometry(
    orientation: int, offset: float, interval: list[float]
) -> dict:
    theta = ALLOWED_ANGLES[orientation]
    direction = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    normal = np.array([-direction[1], direction[0]], dtype=float)
    start = normal * offset + direction * interval[0]
    end = normal * offset + direction * interval[1]
    return {
        "orientation": orientation,
        "angle_deg": orientation * 22.5,
        "offset": round(offset, 9),
        "start": [round(float(value), 9) for value in start],
        "end": [round(float(value), 9) for value in end],
        "length": round(interval[1] - interval[0], 9),
    }


def compare_cp_data(
    prediction_text: str,
    reference_text: str,
    *,
    prediction_types: set[int] | None = None,
    reference_types: set[int] | None = None,
    ray_tolerance: float = 0.5,
    interval_tolerance: float = 0.05,
    node_tolerance: float = 0.5,
) -> dict:
    """Development evaluation: compare vector CP data against labelled CP data."""
    prediction_types = prediction_types or {2, 3, 4}
    reference_types = reference_types or {2, 3}
    prediction_all, prediction_parse_issues = parse_cp(prediction_text)
    reference_all, reference_parse_issues = parse_cp(reference_text)
    prediction = [
        value for value in prediction_all if value.line_type in prediction_types
    ]
    reference = [
        value for value in reference_all if value.line_type in reference_types
    ]
    prediction_rays, prediction_illegal = group_rays(prediction)
    reference_rays, reference_illegal = group_rays(reference)
    matches, missing_ray_indices, extra_ray_indices = _match_ray_groups(
        reference_rays, prediction_rays, ray_tolerance
    )

    true_length = 0.0
    mv_correct_length = 0.0
    mv_incorrect_length = 0.0
    mv_mismatch_intervals: list[dict] = []
    missing_intervals: list[dict] = []
    extra_intervals: list[dict] = []
    matched_details: list[dict] = []
    for reference_index, prediction_index, error in matches:
        expected = reference_rays[reference_index]
        actual = prediction_rays[prediction_index]
        true_length += _interval_overlap(expected.intervals, actual.intervals)

        def typed_intervals(
            source_segments: list[CpSegment], rows: list[int], line_type: int
        ) -> list[list[float]]:
            row_set = set(rows)
            return _merge_intervals(
                [
                    list(_ray_values(segment)[2:4])
                    for segment in source_segments
                    if segment.row in row_set and segment.line_type == line_type
                ]
            )

        reference_mountain = typed_intervals(reference, expected.rows, 2)
        reference_valley = typed_intervals(reference, expected.rows, 3)
        prediction_mountain = typed_intervals(prediction, actual.rows, 2)
        prediction_valley = typed_intervals(prediction, actual.rows, 3)
        correct_intervals = _merge_intervals(
            _intersect_intervals(reference_mountain, prediction_mountain)
            + _intersect_intervals(reference_valley, prediction_valley)
        )
        incorrect_intervals = _merge_intervals(
            _intersect_intervals(reference_mountain, prediction_valley)
            + _intersect_intervals(reference_valley, prediction_mountain)
        )
        mv_correct_length += sum(end - start for start, end in correct_intervals)
        mv_incorrect_length += sum(
            end - start for start, end in incorrect_intervals
        )
        mv_mismatch_intervals.extend(
            {
                **_interval_geometry(
                    expected.orientation,
                    expected.offset,
                    interval,
                ),
                "reference_type": reference_type,
                "prediction_type": prediction_type,
            }
            for reference_type, prediction_type, reference_values, prediction_values in (
                (2, 3, reference_mountain, prediction_valley),
                (3, 2, reference_valley, prediction_mountain),
            )
            for interval in _intersect_intervals(
                reference_values, prediction_values
            )
        )
        missing = _subtract_intervals(
            expected.intervals, actual.intervals, interval_tolerance
        )
        extra = _subtract_intervals(
            actual.intervals, expected.intervals, interval_tolerance
        )
        missing_intervals.extend(
            _interval_geometry(expected.orientation, expected.offset, interval)
            for interval in missing
        )
        extra_intervals.extend(
            _interval_geometry(actual.orientation, actual.offset, interval)
            for interval in extra
        )
        matched_details.append(
            {
                "angle_deg": expected.orientation * 22.5,
                "reference_offset": round(expected.offset, 9),
                "prediction_offset": round(actual.offset, 9),
                "normal_error": round(error, 9),
                "reference_rows": expected.rows,
                "prediction_rows": actual.rows,
            }
        )
    for index in missing_ray_indices:
        ray = reference_rays[index]
        missing_intervals.extend(
            _interval_geometry(ray.orientation, ray.offset, interval)
            for interval in ray.intervals
        )
    for index in extra_ray_indices:
        ray = prediction_rays[index]
        extra_intervals.extend(
            _interval_geometry(ray.orientation, ray.offset, interval)
            for interval in ray.intervals
        )

    reference_length = sum(ray.length for ray in reference_rays)
    prediction_length = sum(ray.length for ray in prediction_rays)
    reference_nodes = _canonical_graph_nodes(reference, node_tolerance * 0.1)
    prediction_nodes = _canonical_graph_nodes(prediction, node_tolerance * 0.1)
    matched_node_count, missing_nodes, extra_nodes = _match_points(
        reference_nodes, prediction_nodes, node_tolerance
    )

    def ratio(numerator: float, denominator: float) -> float:
        return round(numerator / denominator, 9) if denominator else 1.0

    report = {
        "report_kind": "development_ground_truth_comparison",
        "evaluation_basis": "prediction_cp_vs_reference_cp_vector_geometry",
        "coordinate_transform_applied": "none",
        "assignment_policy": "geometry_and_mv_reported_separately",
        "scope": {
            "prediction_types": sorted(prediction_types),
            "reference_types": sorted(reference_types),
            "cp_type_meaning": {
                "1": "boundary",
                "2": "mountain",
                "3": "valley",
                "4": "flat_or_unassigned",
            },
            "ray_tolerance_cp_units": ray_tolerance,
            "interval_tolerance_cp_units": interval_tolerance,
            "node_tolerance_cp_units": node_tolerance,
        },
        "ray_metrics": {
            "reference": len(reference_rays),
            "prediction": len(prediction_rays),
            "matched": len(matches),
            "missing": len(missing_ray_indices),
            "extra": len(extra_ray_indices),
            "precision": ratio(len(matches), len(prediction_rays)),
            "recall": ratio(len(matches), len(reference_rays)),
        },
        "finite_geometry_metrics": {
            "reference_length": round(reference_length, 9),
            "prediction_length": round(prediction_length, 9),
            "matched_length": round(true_length, 9),
            "missing_length": round(reference_length - true_length, 9),
            "extra_length": round(prediction_length - true_length, 9),
            "precision": ratio(true_length, prediction_length),
            "recall": ratio(true_length, reference_length),
        },
        "mv_assignment_metrics": {
            "comparable_length": round(
                mv_correct_length + mv_incorrect_length, 9
            ),
            "correct_length": round(mv_correct_length, 9),
            "incorrect_length": round(mv_incorrect_length, 9),
            "accuracy_on_comparable_geometry": ratio(
                mv_correct_length,
                mv_correct_length + mv_incorrect_length,
            ),
            "mismatch_interval_count": len(mv_mismatch_intervals),
        },
        "node_metrics": {
            "reference": len(reference_nodes),
            "prediction": len(prediction_nodes),
            "matched": matched_node_count,
            "missing": len(missing_nodes),
            "extra": len(extra_nodes),
            "precision": ratio(matched_node_count, len(prediction_nodes)),
            "recall": ratio(matched_node_count, len(reference_nodes)),
        },
        "topology": {
            "prediction_internal_degree_one_nodes": [
                node
                for node in prediction_nodes
                if node["degree"] == 1
                and max(abs(value) for value in node["point"]) < 199.999
            ],
            "prediction_illegal_angle_segments": prediction_illegal,
            "reference_illegal_angle_segments": reference_illegal,
        },
        "differences": {
            "missing_whole_ray_indices": missing_ray_indices,
            "extra_whole_ray_indices": extra_ray_indices,
            "missing_intervals": missing_intervals,
            "extra_intervals": extra_intervals,
            "missing_nodes": missing_nodes,
            "extra_nodes": extra_nodes,
            "matched_rays": matched_details,
            "mv_mismatch_intervals": mv_mismatch_intervals,
        },
        "parse_issues": {
            "prediction": prediction_parse_issues,
            "reference": reference_parse_issues,
        },
    }
    report["exact_geometry_match"] = (
        not missing_intervals
        and not extra_intervals
        and not missing_nodes
        and not extra_nodes
        and not prediction_illegal
        and not prediction_parse_issues
    )
    report["exact_mv_match_on_comparable_geometry"] = (
        mv_incorrect_length <= interval_tolerance
    )
    return report


def _svg_line(item: dict, color: str, width: float) -> str:
    start, end = item["start"], item["end"]
    return (
        f'<line x1="{start[0]}" y1="{start[1]}" '
        f'x2="{end[0]}" y2="{end[1]}" '
        f'stroke="{color}" stroke-width="{width}" '
        'vector-effect="non-scaling-stroke" />'
    )


def difference_svg(reference_text: str, report: dict) -> str:
    reference, _ = parse_cp(reference_text)
    base = []
    for segment in reference:
        if segment.line_type not in set(report["scope"]["reference_types"]):
            continue
        base.append(
            _svg_line(
                {"start": list(segment.start), "end": list(segment.end)},
                "#202020",
                0.7,
            )
        )
    missing = [
        _svg_line(item, "#e2231a", 2.2)
        for item in report["differences"]["missing_intervals"]
    ]
    extra = [
        _svg_line(item, "#00a651", 2.2)
        for item in report["differences"]["extra_intervals"]
    ]
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-205 -205 410 410">',
            '<rect x="-205" y="-205" width="410" height="410" fill="white"/>',
            '<g id="reference">',
            *base,
            '</g><g id="missing" aria-label="missing red">',
            *missing,
            '</g><g id="extra" aria-label="extra green">',
            *extra,
            "</g></svg>\n",
        ]
    )


def audit_runtime_reliability(
    cp_text: str,
    image_data: bytes,
    *,
    evidence_support_threshold: float = 0.72,
) -> dict:
    """Runtime evaluation: test exported geometry against source evidence.

    This intentionally has no recall or correctness field.  Without labelled
    reference CP data, missing creases cannot be measured.
    """
    segments, parse_issues = parse_cp(cp_text)
    internal = [segment for segment in segments if segment.line_type != 1]
    image = _decode_image(image_data)
    square, (x0, y0, x1, y1), scale_stats = prepare_paper_square(
        image, 512
    )
    analysis_size = square.shape[0]
    ink, _, evidence_stats = _adaptive_geometry_evidence(square)
    distance = cv2.distanceTransform(
        np.where(ink > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3
    )
    evidence_tolerance = evidence_stats["adaptive_evidence_distance_px"] + 0.4

    edge_reports: list[dict] = []
    weighted_support = 0.0
    total_length = 0.0
    for segment in internal:
        start_cp = np.array(segment.start, dtype=float)
        end_cp = np.array(segment.end, dtype=float)
        maximum = float(analysis_size - 1)
        start = (start_cp + 200.0) * maximum / 400.0
        end = (end_cp + 200.0) * maximum / 400.0
        length = float(np.linalg.norm(end - start))
        samples = np.linspace(start, end, max(5, int(length * 2.0) + 1))
        xs = np.clip(
            np.rint(samples[:, 0]).astype(int), 0, analysis_size - 1
        )
        ys = np.clip(
            np.rint(samples[:, 1]).astype(int), 0, analysis_size - 1
        )
        support = float(np.mean(distance[ys, xs] <= evidence_tolerance))
        weighted_support += support * length
        total_length += length
        _, _, _, _, angle_error = _ray_values(segment)
        edge_reports.append(
            {
                "row": segment.row,
                "line_type": segment.line_type,
                "start": list(segment.start),
                "end": list(segment.end),
                "length_px": round(length, 6),
                "image_support": round(support, 9),
                "supported": support >= evidence_support_threshold,
                "angle_error_deg": round(angle_error, 9),
            }
        )

    nodes = _canonical_graph_nodes(internal, 0.05)
    lineheads = [
        node
        for node in nodes
        if node["degree"] == 1
        and max(abs(value) for value in node["point"]) < 199.999
    ]
    illegal = [item for item in edge_reports if item["angle_error_deg"] > 1e-5]
    unsupported = [item for item in edge_reports if not item["supported"]]
    camv_structure = audit_camv_structure(
        [
            GeometrySegment(
                segment.line_type,
                segment.start,
                segment.end,
                row=segment.row,
            )
            for segment in segments
        ],
        folding_types={2, 3},
        point_tolerance=1e-5,
    )
    camv_full = audit_camv_structure(
        [
            GeometrySegment(
                segment.line_type,
                segment.start,
                segment.end,
                row=segment.row,
            )
            for segment in segments
        ],
        folding_types={2, 3},
        include_mv=True,
        point_tolerance=1e-5,
    )
    report = {
        "report_kind": "runtime_source_reliability",
        "evaluation_basis": "prediction_cp_vs_source_image_and_internal_constraints",
        "does_not_measure": [
            "missing_lines",
            "recall",
            "ground_truth_correctness",
        ],
        "image": {
            "source_width": int(image.shape[1]),
            "source_height": int(image.shape[0]),
            "paper_bbox": [x0, y0, x1, y1],
            **scale_stats,
            **evidence_stats,
        },
        "evidence": {
            "edge_count": len(edge_reports),
            "supported_edge_count": len(edge_reports) - len(unsupported),
            "unsupported_edge_count": len(unsupported),
            "support_threshold": evidence_support_threshold,
            "length_weighted_support": round(
                weighted_support / total_length if total_length else 1.0, 9
            ),
            "unsupported_edges": unsupported,
        },
        "constraints": {
            "illegal_angle_edge_count": len(illegal),
            "illegal_angle_edges": illegal,
            "internal_degree_one_node_count": len(lineheads),
            "internal_degree_one_nodes": lineheads,
            "camv_structure": camv_structure,
            "camv_full": camv_full,
        },
        "parse_issues": parse_issues,
        "per_edge": edge_reports,
    }
    report["structurally_valid"] = (
        not parse_issues
        and not illegal
        and not lineheads
        and camv_structure["passes_structure_subset"]
    )
    report["all_output_edges_have_image_evidence"] = not unsupported
    report["camv_valid"] = camv_full["passes_camv"]
    return report


def write_json(path: str | Path, report: dict) -> None:
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
