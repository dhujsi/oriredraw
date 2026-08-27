"""Exact project coordinates in Q(sqrt(2)) with a semantic paper scale.

The paper uses a top-left origin and one global side length ``L=c+d*sqrt(2)``.
Boundary point coordinates are distances in that project scale; the normalized
edge parameter is retained separately as evidence-independent geometry.
"""

from __future__ import annotations

from fractions import Fraction
import math
from typing import Any, Iterable, Mapping

from exact_qsqrt2 import Qsqrt2

def qsqrt2_from_coefficients(a: int, b: int = 0, denominator: int = 1) -> Qsqrt2:
    """Build the repository's canonical exact value from ``(a+b√2)/denominator``."""

    if denominator == 0:
        raise ZeroDivisionError("Q(sqrt(2)) denominator cannot be zero")
    return Qsqrt2(Fraction(int(a), int(denominator)), Fraction(int(b), int(denominator)))


def qsqrt2_from_mapping(value: Mapping[str, Any]) -> Qsqrt2:
    coefficients = value.get("coefficients")
    if not isinstance(coefficients, (list, tuple)) or len(coefficients) != 3:
        raise ValueError("Q(sqrt(2)) mapping requires [a, b, denominator]")
    return qsqrt2_from_coefficients(
        int(coefficients[0]),
        int(coefficients[1]),
        int(coefficients[2]),
    )


def qsqrt2_canonical_coefficients(value: Qsqrt2) -> tuple[int, int, int]:
    denominator = math.lcm(value.p.denominator, value.q.denominator)
    a = value.p.numerator * (denominator // value.p.denominator)
    b = value.q.numerator * (denominator // value.q.denominator)
    common = math.gcd(math.gcd(abs(a), abs(b)), denominator)
    if common > 1:
        a //= common
        b //= common
        denominator //= common
    return int(a), int(b), int(denominator)


def qsqrt2_expression(value: Qsqrt2) -> str:
    """Use one semantic denominator so UI can retain ``(a+b√2)/c`` forms."""

    a, b, denominator = qsqrt2_canonical_coefficients(value)
    pieces: list[str] = []
    if a:
        pieces.append(str(a))
    if b:
        magnitude = "√2" if abs(b) == 1 else f"{abs(b)}√2"
        if not pieces:
            pieces.append(magnitude if b > 0 else f"-{magnitude}")
        else:
            pieces.append(("+" if b > 0 else "-") + magnitude)
    numerator = "".join(pieces) or "0"
    if denominator == 1:
        return numerator
    if a and b:
        return f"({numerator})/{denominator}"
    return f"{numerator}/{denominator}"


def qsqrt2_complexity(value: Qsqrt2) -> int:
    a, b, denominator = qsqrt2_canonical_coefficients(value)
    return abs(a) + abs(b) + 2 * (denominator - 1)


def qsqrt2_to_mapping(value: Qsqrt2) -> dict[str, Any]:
    a, b, denominator = qsqrt2_canonical_coefficients(value)
    return {
        "expression": qsqrt2_expression(value),
        "coefficients": [a, b, denominator],
        "value": round(float(value), 12),
        "complexity": qsqrt2_complexity(value),
    }


def boundary_project_point(side: str, distance: Qsqrt2, side_length: Qsqrt2) -> tuple[Qsqrt2, Qsqrt2]:
    """Return a top-left-origin project coordinate for a clockwise side distance."""

    zero = Qsqrt2()
    if side == "top":
        return distance, zero
    if side == "right":
        return side_length, distance
    if side == "bottom":
        return side_length - distance, side_length
    if side == "left":
        return zero, side_length - distance
    raise ValueError(f"unknown paper side: {side}")


def _scale_candidates(
    coefficient_limit: int,
    *,
    minimum_value: float,
    maximum_value: float,
) -> Iterable[Qsqrt2]:
    seen: set[tuple[int, int, int]] = set()
    for a in range(-coefficient_limit, coefficient_limit + 1):
        for b in range(-coefficient_limit, coefficient_limit + 1):
            if not (a or b) or math.gcd(abs(a), abs(b)) != 1:
                continue
            value = qsqrt2_from_coefficients(a, b)
            decimal = float(value)
            if not minimum_value <= decimal <= maximum_value:
                continue
            key = qsqrt2_canonical_coefficients(value)
            if key in seen:
                continue
            seen.add(key)
            yield value


def infer_boundary_coordinate_gauges(
    side: str,
    parameters: Iterable[Mapping[str, Any]],
    *,
    coefficient_limit: int = 8,
    maximum_candidates: int = 3,
) -> list[dict[str, Any]]:
    """Rank global ``L=c+d*sqrt(2)`` gauges for exact edge parameters.

    Each parameter mapping contains ``id`` and ``parameter``.  One scale is
    shared by every point; scales are never fitted independently per point.
    """

    items: list[tuple[str, str, Qsqrt2]] = []
    for item in parameters:
        point_id = str(item.get("id") or "")
        if not point_id:
            continue
        raw_parameter = item.get("parameter")
        try:
            parameter = (
                raw_parameter
                if isinstance(raw_parameter, Qsqrt2)
                else qsqrt2_from_mapping(raw_parameter)
            )
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        items.append((point_id, str(item.get("role") or ""), parameter))
    if not items:
        return []

    ranked: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    for side_length in _scale_candidates(
        max(1, int(coefficient_limit)),
        minimum_value=0.75,
        maximum_value=8.0,
    ):
        coordinates: list[dict[str, Any]] = []
        point_complexity = 0
        for point_id, role, parameter in items:
            distance = parameter * side_length
            x, y = boundary_project_point(side, distance, side_length)
            point_complexity += qsqrt2_complexity(distance)
            coordinates.append(
                {
                    "id": point_id,
                    "role": role,
                    "edge_parameter": qsqrt2_to_mapping(parameter),
                    "edge_distance": qsqrt2_to_mapping(distance),
                    "coordinate": [qsqrt2_to_mapping(x), qsqrt2_to_mapping(y)],
                }
            )
        a, b, _ = qsqrt2_canonical_coefficients(side_length)
        scale_complexity = qsqrt2_complexity(side_length)
        negative_radical_penalty = 2 * max(0, -b)
        score = point_complexity + 0.35 * scale_complexity + negative_radical_penalty
        mapping = {
            "origin": "top_left",
            "axes": "x_right_y_down",
            "side": side,
            "side_length": qsqrt2_to_mapping(side_length),
            "points": coordinates,
            "point_complexity": point_complexity,
            "scale_complexity": scale_complexity,
            "score": round(float(score), 6),
        }
        rank = (
            float(score),
            float(point_complexity),
            float(scale_complexity),
            float(negative_radical_penalty),
            abs(b),
            abs(a),
        )
        ranked.append((rank, mapping))
    ranked.sort(key=lambda item: item[0])
    return [mapping for _, mapping in ranked[: max(1, int(maximum_candidates))]]


__all__ = [
    "Qsqrt2",
    "boundary_project_point",
    "infer_boundary_coordinate_gauges",
    "qsqrt2_canonical_coefficients",
    "qsqrt2_complexity",
    "qsqrt2_expression",
    "qsqrt2_from_coefficients",
    "qsqrt2_from_mapping",
    "qsqrt2_to_mapping",
]
