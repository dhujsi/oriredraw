"""Determine a selected boundary seed from exact observed incidence equations.

Each existing crease has one unknown offset and a known 22.5-degree direction.
Shared observed nodes, paper sides, and the selected division supply equations.
Only uniquely determined coordinates are returned; pixels validate the solution
but never supply a right-hand side or choose values for free variables.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from construction_search import ConstructionGraph
from exact_graph_propagation import _direction_vector, _point_tolerance
from exact_qsqrt2 import Qsqrt2
from qsqrt2_coordinates import qsqrt2_to_mapping


_ZERO = Qsqrt2()
_ONE = Qsqrt2(1)
_Affine = tuple[dict[int, Qsqrt2], Qsqrt2]
_CORNERS = {
    "corner:top_left": (0, 0),
    "corner:top_right": (1, 0),
    "corner:bottom_right": (1, 1),
    "corner:bottom_left": (0, 1),
}


def _combine(*terms: tuple[Qsqrt2, _Affine]) -> _Affine:
    coefficients: dict[int, Qsqrt2] = {}
    constant = _ZERO
    for factor, (row, value) in terms:
        if factor == _ZERO:
            continue
        constant += factor * value
        for key, coefficient in row.items():
            coefficients[key] = coefficients.get(key, _ZERO) + factor * coefficient
    return {k: v for k, v in coefficients.items() if v != _ZERO}, constant


class _ExactSystem:
    def __init__(self) -> None:
        self.basis: dict[int, tuple[dict[int, Qsqrt2], Qsqrt2]] = {}
        self.consistent = True
        self.equation_count = 0

    def add_zero(self, expression: _Affine) -> None:
        row, constant = expression
        row, rhs = dict(row), -constant
        if row or rhs != _ZERO:
            self.equation_count += 1
        while row:
            pivot = min(row)
            factor = row[pivot]
            existing = self.basis.get(pivot)
            if existing is None:
                self.basis[pivot] = (
                    {k: v / factor for k, v in row.items()}, rhs / factor,
                )
                return
            known, value = existing
            row, _ = _combine((_ONE, (row, _ZERO)), (-factor, (known, _ZERO)))
            rhs -= factor * value
        if rhs != _ZERO:
            self.consistent = False

    def unique_value(self, expression: _Affine) -> Qsqrt2 | None:
        row, constant = expression
        row = dict(row)
        for pivot, (known, value) in sorted(self.basis.items()):
            factor = row.get(pivot, _ZERO)
            if factor == _ZERO:
                continue
            row, _ = _combine((_ONE, (row, _ZERO)), (-factor, (known, _ZERO)))
            constant += factor * value
        return constant if not row and self.consistent else None


def resolve_boundary_seed_constraints(
    graph: ConstructionGraph,
    relations: list[Mapping[str, Any]],
    *,
    maximum: float,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "mode": "exact_boundary_seed_incidence_v1",
        "status": "underconstrained",
        "resolved_points": {},
        "relation_ids": [str(r.get("id", "")) for r in relations],
        "pixel_coordinates_used_as_equations": False,
        "free_variables_fitted": False,
    }
    creases = [
        e for e in graph.geometry_entities.values()
        if e.kind == "crease" and e.observed_geometry.get("direction_index") in range(8)
    ]
    if not relations or not creases or not math.isfinite(maximum) or maximum <= 0:
        return report
    if len(creases) > 256:
        report["status"] = "constraint_budget_exceeded"
        return report
    index = {e.id: i for i, e in enumerate(creases)}
    normals = {}
    for crease in creases:
        dx, dy = _direction_vector(int(crease.observed_geometry["direction_index"]))
        normals[crease.id] = (-dy, dx)
    system = _ExactSystem()
    expressions: dict[str, tuple[_Affine, _Affine]] = {}
    points = {str(e.id): e for e in graph.geometry_entities.values() if e.kind == "point"}
    constraint_points: list[str] = []
    for point_id, point in points.items():
        lines = [
            (normals[i], ({index[i]: _ONE}, _ZERO))
            for i in sorted(graph.incidence.get(point.id, ()), key=str)
            if i in index
        ]
        for side in point.observed_geometry.get("boundary_sides", []):
            if side not in ("left", "right", "top", "bottom"):
                continue
            normal = (_ONE, _ZERO) if side in ("left", "right") else (_ZERO, _ONE)
            lines.append((normal, ({}, Qsqrt2(int(side in ("right", "bottom"))))))
        if len(lines) < 2:
            continue
        (ax, ay), first = lines[0]
        other = next(
            (line for line in lines[1:] if ax * line[0][1] - ay * line[0][0] != _ZERO),
            None,
        )
        before = system.equation_count
        if other is None:
            for (nx, ny), offset in lines[1:]:
                factor = nx / ax if ax != _ZERO else ny / ay
                system.add_zero(_combine((_ONE, offset), (-factor, first)))
        else:
            (bx, by), second = other
            determinant = ax * by - ay * bx
            x = _combine((by / determinant, first), (-ay / determinant, second))
            y = _combine((-bx / determinant, first), (ax / determinant, second))
            expressions[point_id] = (x, y)
            for (nx, ny), offset in lines:
                system.add_zero(_combine((nx, x), (ny, y), (-_ONE, offset)))
        if system.equation_count > before:
            constraint_points.append(point_id)

    # A declared paper corner is a fixed reference, even without an entering
    # crease. No measured coordinate is converted to an exact reference.
    for name, (x, y) in _CORNERS.items():
        expressions[name] = (({}, Qsqrt2(x)), ({}, Qsqrt2(y)))
    base_equations = system.equation_count
    for relation in relations:
        by_role = {p.get("relation_role"): expressions.get(str(p.get("id")))
                   for p in relation.get("points", [])}
        if not by_role or any(p is None for p in by_role.values()) or not all(
            role in by_role for role in ("start", "end")
        ):
            return report
        for role, point in by_role.items():
            if role in ("start", "end"):
                continue
            try:
                numerator, denominator = map(int, str(role).split("/"))
                if not 0 < numerator < denominator:
                    return report
                ratio = Qsqrt2(numerator) / Qsqrt2(denominator)
            except (ValueError, ZeroDivisionError):
                return report
            for axis in (0, 1):
                system.add_zero(_combine(
                    (_ONE, point[axis]),
                    (ratio - _ONE, by_role["start"][axis]),
                    (-ratio, by_role["end"][axis]),
                ))
    report.update({
        "crease_variable_count": len(creases), "rank": len(system.basis),
        "incidence_equation_count": base_equations,
        "division_equation_count": system.equation_count - base_equations,
        "constraint_point_ids": constraint_points,
    })
    if not system.consistent:
        report["status"] = "inconsistent_incidence_or_division"
        return report

    resolved = {}
    residuals = []
    for relation in relations:
        for item in relation.get("points", []):
            point_id = str(item["id"])
            coordinate = [system.unique_value(axis) for axis in expressions[point_id]]
            if any(value is None for value in coordinate):
                return report
            projected = [float(value) * maximum for value in coordinate]
            observed = item.get("observed_point_px", item.get("point_px"))
            point = points.get(point_id)
            tolerance = _point_tolerance(graph, point.id) if point is not None else 0.85
            if not isinstance(observed, (list, tuple)) or len(observed) != 2:
                return report
            residual = math.dist(projected, observed)
            if residual > tolerance or any(v < -1e-8 or v > maximum + 1e-8 for v in projected):
                report["status"] = "solution_outside_source_evidence"
                return report
            residuals.append(residual)
            resolved[point_id] = [qsqrt2_to_mapping(value) for value in coordinate]
    # Also reject an algebraically consistent but visibly wrong interpretation
    # elsewhere in the same observed graph. Unsolved offsets stay free.
    for crease in creases:
        value = system.unique_value(({index[crease.id]: _ONE}, _ZERO))
        if value is None:
            continue
        norm = math.hypot(*(float(n) for n in normals[crease.id]))
        residual = abs(float(value) * maximum / norm - crease.observed_geometry["line_offset_px"])
        tolerance = float(crease.observed_geometry.get("match_tolerance_px", 0.85))
        if residual > tolerance:
            report["status"] = "solution_outside_source_evidence"
            return report
    # A later frontier may stall at a node with only one already exact parent
    # crease. Keep uniquely proved coordinates available there as well: they
    # must not be replaced by a new one-dimensional pixel fit. This does not
    # activate any point or crease; the normal frontier still chooses the node.
    determined_points = {}
    for point_id, point in points.items():
        expression = expressions.get(point_id)
        observed = point.observed_geometry.get("point_px")
        if expression is None or not isinstance(observed, (list, tuple)) or len(observed) != 2:
            continue
        coordinate = [system.unique_value(axis) for axis in expression]
        if any(value is None for value in coordinate):
            continue
        projected = [float(value) * maximum for value in coordinate]
        if any(v < -1e-8 or v > maximum + 1e-8 for v in projected):
            continue
        if math.dist(projected, observed) > _point_tolerance(graph, point.id):
            continue
        determined_points[point_id] = [qsqrt2_to_mapping(value) for value in coordinate]
    report.update({
        "status": "resolved", "resolved_points": resolved,
        "determined_point_coordinates": determined_points,
        "max_seed_residual_px": round(max(residuals, default=0.0), 6),
    })
    return report
