from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class GeometrySegment:
    """Minimal segment representation used by the cAMV structure audit."""

    line_type: int
    start: tuple[float, float]
    end: tuple[float, float]
    row: int | None = None


class _PointRegistry:
    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance
        self.points: list[tuple[float, float]] = []
        self._cells: dict[tuple[int, int], list[int]] = {}

    def add(self, point: Sequence[float]) -> int:
        x, y = float(point[0]), float(point[1])
        cell = (
            int(math.floor(x / self.tolerance)),
            int(math.floor(y / self.tolerance)),
        )
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in self._cells.get((cell[0] + dx, cell[1] + dy), []):
                    px, py = self.points[index]
                    if math.hypot(x - px, y - py) <= self.tolerance:
                        return index
        index = len(self.points)
        self.points.append((x, y))
        self._cells.setdefault(cell, []).append(index)
        return index


def _cross(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _subtract(
    first: tuple[float, float], second: tuple[float, float]
) -> tuple[float, float]:
    return first[0] - second[0], first[1] - second[1]


def _add_scaled(
    point: tuple[float, float],
    delta: tuple[float, float],
    factor: float,
) -> tuple[float, float]:
    return point[0] + factor * delta[0], point[1] + factor * delta[1]


def _inside_little_big_little_passes(
    crease_rays: list[dict], angle_tolerance: float
) -> bool:
    """Port the reduction used by Oriedita Check4 for an interior vertex."""

    items = sorted(
        [(float(ray["angle"]), int(ray["line_type"])) for ray in crease_rays]
    )
    maximum = 2.0 * math.pi
    while len(items) > 2:
        total = len(items)
        sectors = [
            (items[(index + 1) % total][0] - items[index][0]) % maximum
            for index in range(total)
        ]
        minimum = min(sectors)
        reduced: list[tuple[float, int]] | None = None
        for index, sector in enumerate(sectors):
            if abs(sector - minimum) > angle_tolerance:
                continue
            next_index = (index + 1) % total
            if items[index][1] == items[next_index][1]:
                continue
            remaining = [
                items[(next_index + offset) % total]
                for offset in range(1, total - 1)
            ]
            reference = remaining[0][0]
            reduced = sorted(
                [
                    ((angle - reference) % maximum, line_type)
                    for angle, line_type in remaining
                ]
            )
            maximum -= 2.0 * minimum
            break
        if reduced is None or len(reduced) == len(items):
            return False
        items = reduced

    if len(items) != 2:
        return True
    separation = (items[1][0] - items[0][0]) % maximum
    return abs(maximum - 2.0 * separation) <= angle_tolerance


def _side_little_big_little_passes(
    rays: list[dict], angle_tolerance: float
) -> bool:
    """Port the boundary-vertex reduction used by Oriedita Check4."""

    items = sorted(
        [
            (
                float(ray["angle"]),
                None if ray["boundary"] else int(ray["line_type"]),
            )
            for ray in rays
        ],
        key=lambda item: item[0],
    )
    if len(items) == 2:
        return items[0][1] is None and items[1][1] is None

    start_index: int | None = None
    for index in range(len(items)):
        next_index = (index + 1) % len(items)
        if items[index][1] is None and items[next_index][1] is None:
            start_index = next_index
    if start_index is None:
        return False

    items = items[start_index:] + items[:start_index]
    reference = items[0][0]
    items = [
        ((angle - reference) % (2.0 * math.pi), line_type)
        for angle, line_type in items
    ]

    while len(items) > 2:
        sectors = [
            items[index + 1][0] - items[index][0]
            for index in range(len(items) - 1)
        ]
        minimum = min(sectors)
        if abs(sectors[0] - minimum) <= angle_tolerance:
            items = items[1:]
            continue
        if abs(sectors[-1] - minimum) <= angle_tolerance:
            items = items[:-1]
            continue

        reduced: list[tuple[float, int | None]] | None = None
        for index in range(1, len(items) - 2):
            if abs(sectors[index] - minimum) > angle_tolerance:
                continue
            if items[index][1] == items[index + 1][1]:
                continue
            reduced = items[:index] + [
                (angle - 2.0 * minimum, line_type)
                for angle, line_type in items[index + 2 :]
            ]
            break
        if reduced is None or len(reduced) == len(items):
            return False
        items = reduced
    return True


def audit_camv_structure(
    segments: Iterable[GeometrySegment],
    *,
    boundary_type: int = 1,
    folding_types: set[int] | None = None,
    include_mv: bool = False,
    mountain_type: int = 2,
    valley_type: int = 3,
    point_tolerance: float = 1e-5,
    angle_tolerance_deg: float = 1e-5,
) -> dict:
    """Audit the M/V-independent structural subset of Oriedita Check4/cAMV.

    Set ``include_mv`` to also evaluate the Maekawa and little-big-little
    branches of Oriedita Check4. With it disabled, this is the M/V-independent
    structure subset used while geometry is still being reconstructed.
    """

    source = [
        segment
        for segment in segments
        if (
            segment.line_type == boundary_type
            or folding_types is None
            or segment.line_type in folding_types
        )
        if math.dist(segment.start, segment.end) > point_tolerance
    ]
    split_parameters: list[list[float]] = [[0.0, 1.0] for _ in source]

    for first_index, first in enumerate(source):
        first_delta = _subtract(first.end, first.start)
        for second_index in range(first_index + 1, len(source)):
            second = source[second_index]
            second_delta = _subtract(second.end, second.start)
            denominator = _cross(first_delta, second_delta)
            if abs(denominator) <= 1e-12:
                continue
            relative = _subtract(second.start, first.start)
            first_t = _cross(relative, second_delta) / denominator
            second_t = _cross(relative, first_delta) / denominator
            parameter_tolerance = 1e-9
            if (
                -parameter_tolerance <= first_t <= 1.0 + parameter_tolerance
                and -parameter_tolerance <= second_t <= 1.0 + parameter_tolerance
            ):
                split_parameters[first_index].append(
                    min(1.0, max(0.0, first_t))
                )
                split_parameters[second_index].append(
                    min(1.0, max(0.0, second_t))
                )

    registry = _PointRegistry(point_tolerance)
    atomic_edges: dict[tuple[int, int, bool], dict] = {}
    for segment, parameters in zip(source, split_parameters):
        ordered: list[float] = []
        for value in sorted(parameters):
            if not ordered or value - ordered[-1] > 1e-10:
                ordered.append(value)
        delta = _subtract(segment.end, segment.start)
        for first_t, second_t in zip(ordered, ordered[1:]):
            first_point = _add_scaled(segment.start, delta, first_t)
            second_point = _add_scaled(segment.start, delta, second_t)
            if math.dist(first_point, second_point) <= point_tolerance:
                continue
            first_node = registry.add(first_point)
            second_node = registry.add(second_point)
            if first_node == second_node:
                continue
            is_boundary = segment.line_type == boundary_type
            key = (
                min(first_node, second_node),
                max(first_node, second_node),
                is_boundary,
            )
            record = atomic_edges.setdefault(
                key,
                {
                    "first": first_node,
                    "second": second_node,
                    "boundary": is_boundary,
                    "line_type": segment.line_type,
                    "rows": set(),
                },
            )
            if segment.row is not None:
                record["rows"].add(segment.row)

    incident: list[list[dict]] = [[] for _ in registry.points]
    for edge in atomic_edges.values():
        first = registry.points[edge["first"]]
        second = registry.points[edge["second"]]
        forward = math.atan2(second[1] - first[1], second[0] - first[0]) % (
            2.0 * math.pi
        )
        backward = (forward + math.pi) % (2.0 * math.pi)
        common = {
            "boundary": edge["boundary"],
            "line_type": edge["line_type"],
            "rows": sorted(edge["rows"]),
        }
        incident[edge["first"]].append({**common, "angle": forward})
        incident[edge["second"]].append({**common, "angle": backward})

    violations: list[dict] = []
    checked_vertices = 0
    interior_vertices = 0
    boundary_vertices = 0
    rule_counts = {
        "boundary_topology": 0,
        "number_of_folds": 0,
        "kawasaki_angles": 0,
        "maekawa": 0,
        "little_big_little": 0,
    }

    def add_violation(
        node_index: int,
        rule: str,
        crease_degree: int,
        boundary_degree: int,
        **details: float,
    ) -> None:
        rule_counts[rule] += 1
        rows = sorted(
            {
                row
                for ray in incident[node_index]
                for row in ray["rows"]
            }
        )
        violations.append(
            {
                "point": [
                    round(registry.points[node_index][0], 9),
                    round(registry.points[node_index][1], 9),
                ],
                "rule": rule,
                "crease_degree": crease_degree,
                "boundary_degree": boundary_degree,
                "rows": rows,
                **details,
            }
        )

    for node_index, rays in enumerate(incident):
        boundary_rays = [ray for ray in rays if ray["boundary"]]
        crease_rays = [ray for ray in rays if not ray["boundary"]]
        boundary_degree = len(boundary_rays)
        crease_degree = len(crease_rays)
        if crease_degree == 0 and boundary_degree in (0, 2):
            continue
        checked_vertices += 1

        if boundary_degree == 0:
            interior_vertices += 1
        else:
            boundary_vertices += 1
            if boundary_degree != 2:
                add_violation(
                    node_index,
                    "boundary_topology",
                    crease_degree,
                    boundary_degree,
                )
                # Match Check4: malformed boundary incidence is reported before
                # the interior/side cases are considered.
                continue

        if boundary_degree == 2:
            if include_mv and not _side_little_big_little_passes(
                rays, math.radians(angle_tolerance_deg)
            ):
                add_violation(
                    node_index,
                    "little_big_little",
                    crease_degree,
                    boundary_degree,
                )
            continue

        if crease_degree % 2 == 1:
            add_violation(
                node_index,
                "number_of_folds",
                crease_degree,
                boundary_degree,
            )
            continue

        if crease_degree == 2:
            first_angle, second_angle = sorted(
                ray["angle"] for ray in crease_rays
            )
            separation = abs(second_angle - first_angle)
            separation = min(separation, 2.0 * math.pi - separation)
            error = abs(math.pi - separation) * 180.0 / math.pi
            if error > angle_tolerance_deg:
                add_violation(
                    node_index,
                    "kawasaki_angles",
                    crease_degree,
                    boundary_degree,
                    angle_error_deg=round(error, 9),
                )
                continue
            if include_mv:
                mountain_count = sum(
                    ray["line_type"] == mountain_type for ray in crease_rays
                )
                valley_count = sum(
                    ray["line_type"] == valley_type for ray in crease_rays
                )
                if abs(mountain_count - valley_count) != 2:
                    add_violation(
                        node_index,
                        "maekawa",
                        crease_degree,
                        boundary_degree,
                        mountain_count=mountain_count,
                        valley_count=valley_count,
                    )
            continue

        if crease_degree >= 4:
            angles = sorted(ray["angle"] for ray in crease_rays)
            sectors = [
                (angles[(index + 1) % crease_degree] - angles[index])
                % (2.0 * math.pi)
                for index in range(crease_degree)
            ]
            even_sum = sum(sectors[0::2])
            odd_sum = sum(sectors[1::2])
            error = abs(even_sum - odd_sum) * 180.0 / math.pi
            if error > angle_tolerance_deg:
                add_violation(
                    node_index,
                    "kawasaki_angles",
                    crease_degree,
                    boundary_degree,
                    angle_error_deg=round(error, 9),
                    alternating_sum_a_deg=round(even_sum * 180.0 / math.pi, 9),
                    alternating_sum_b_deg=round(odd_sum * 180.0 / math.pi, 9),
                )
                continue
            if include_mv:
                mountain_count = sum(
                    ray["line_type"] == mountain_type for ray in crease_rays
                )
                valley_count = sum(
                    ray["line_type"] == valley_type for ray in crease_rays
                )
                if abs(mountain_count - valley_count) != 2:
                    add_violation(
                        node_index,
                        "maekawa",
                        crease_degree,
                        boundary_degree,
                        mountain_count=mountain_count,
                        valley_count=valley_count,
                    )
                    continue
                if not _inside_little_big_little_passes(
                    crease_rays, math.radians(angle_tolerance_deg)
                ):
                    add_violation(
                        node_index,
                        "little_big_little",
                        crease_degree,
                        boundary_degree,
                        mountain_count=mountain_count,
                        valley_count=valley_count,
                    )

    violating_vertices = len({tuple(item["point"]) for item in violations})
    score = (
        max(0.0, 1.0 - violating_vertices / checked_vertices)
        if checked_vertices
        else 1.0
    )
    structure_violation_count = sum(
        rule_counts[name]
        for name in ("boundary_topology", "number_of_folds", "kawasaki_angles")
    )
    mv_violation_count = sum(
        rule_counts[name] for name in ("maekawa", "little_big_little")
    )
    return {
        "report_kind": "camv_full" if include_mv else "camv_structure_subset",
        "soft_constraint": True,
        "checked_vertex_count": checked_vertices,
        "interior_vertex_count": interior_vertices,
        "boundary_vertex_count": boundary_vertices,
        "violation_vertex_count": violating_vertices,
        "violation_count": len(violations),
        "structure_violation_count": structure_violation_count,
        "mv_violation_count": mv_violation_count,
        "rule_counts": rule_counts,
        "structural_completeness_score": round(score, 9),
        "passes_structure_subset": structure_violation_count == 0,
        "passes_camv": not violations if include_mv else None,
        "violations": violations,
        "mv_checks": {
            "enabled": include_mv,
            "skipped_rules": [] if include_mv else ["maekawa", "little_big_little"],
            "reason": None if include_mv else "mountain/valley assignment is intentionally unknown",
        },
    }
