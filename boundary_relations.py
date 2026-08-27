"""Observation-level relations between points on the paper boundary.

This module deliberately does not create construction operations.  A boundary
ratio is first treated as a grouped hypothesis about already observed points;
the caller may later let a user select that hypothesis before using it to seed
construction search.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any, Iterable, Mapping


def _boundary_coordinates(
    point: tuple[float, float],
    maximum: float,
    *,
    epsilon: float,
) -> list[tuple[str, float]]:
    x, y = point
    result: list[tuple[str, float]] = []
    if abs(y) <= epsilon:
        result.append(("top", float(x)))
    if abs(x - maximum) <= epsilon:
        result.append(("right", float(y)))
    if abs(y - maximum) <= epsilon:
        result.append(("bottom", float(maximum - x)))
    if abs(x) <= epsilon:
        result.append(("left", float(maximum - y)))
    return result


def _nearest_observed(
    values: list[tuple[float, Any]],
    target: float,
    *,
    excluded: set[Any],
) -> tuple[float, Any] | None:
    if not values:
        return None
    coordinates = [item[0] for item in values]
    index = bisect_left(coordinates, target)
    candidates = []
    for candidate_index in (index - 1, index, index + 1):
        if 0 <= candidate_index < len(values):
            coordinate, point_id = values[candidate_index]
            if point_id not in excluded:
                candidates.append((abs(coordinate - target), coordinate, point_id))
    if not candidates:
        return None
    _, coordinate, point_id = min(candidates)
    return coordinate, point_id


def detect_boundary_ratio_relations(
    observed_points: Iterable[Mapping[str, Any]],
    maximum: float,
    *,
    tolerance_px: float = 1.5,
    boundary_epsilon_px: float = 2.5,
    minimum_span_px: float = 12.0,
) -> list[dict[str, Any]]:
    """Return grouped ratio hypotheses supported by observed boundary points.

    ``observed_points`` contains mappings with an ``id`` and a two-element
    ``point``.  The function only recognizes relations whose endpoints and
    divider points are all observed.  It never invents a missing point and
    never adds a construction operation.

    The returned relations are ordered by residual, then by simplicity.  A
    complete 1/3 + 2/3 relation is one bundle, rather than two unrelated
    point candidates.
    """

    if maximum <= 0 or tolerance_px < 0 or minimum_span_px <= 0:
        return []

    by_side: dict[str, list[tuple[float, Any]]] = {
        "top": [],
        "right": [],
        "bottom": [],
        "left": [],
    }
    seen: set[tuple[str, Any]] = set()
    for item in observed_points:
        point_id = item.get("id")
        raw_point = item.get("point")
        if point_id is None or not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
            continue
        try:
            point = (float(raw_point[0]), float(raw_point[1]))
        except (TypeError, ValueError):
            continue
        boundaries = _boundary_coordinates(
            point,
            float(maximum),
            epsilon=boundary_epsilon_px,
        )
        for side, coordinate in boundaries:
            key = (side, point_id)
            if key in seen:
                continue
            seen.add(key)
            by_side[side].append((coordinate, point_id))

    relations: list[dict[str, Any]] = []
    for side, values in by_side.items():
        values.sort(key=lambda value: (value[0], str(value[1])))
        if len(values) < 3:
            continue
        for start_index, (start, start_id) in enumerate(values):
            for end, end_id in values[start_index + 1 :]:
                span = end - start
                if span < minimum_span_px:
                    continue
                excluded = {start_id, end_id}
                dividers: list[dict[str, Any]] = []
                for numerator, denominator in ((1, 2), (1, 3), (2, 3)):
                    expected = start + span * numerator / denominator
                    nearest = _nearest_observed(values, expected, excluded=excluded)
                    if nearest is None:
                        continue
                    coordinate, point_id = nearest
                    error = abs(coordinate - expected)
                    if error <= tolerance_px:
                        dividers.append(
                            {
                                "ratio": f"{numerator}/{denominator}",
                                "point_id": point_id,
                                "coordinate": round(coordinate, 6),
                                "expected_coordinate": round(expected, 6),
                                "residual_px": round(error, 6),
                            }
                        )
                        # One observed point cannot explain two different
                        # ratios in the same grouped relation.
                        excluded.add(point_id)
                thirds = [item for item in dividers if item["ratio"] in {"1/3", "2/3"}]
                if len(thirds) == 2:
                    kind = "trisection"
                elif any(item["ratio"] == "1/2" for item in dividers):
                    kind = "bisection"
                else:
                    continue
                residual = max(item["residual_px"] for item in dividers)
                relations.append(
                    {
                        "kind": kind,
                        "side": side,
                        "endpoint_ids": [start_id, end_id],
                        "endpoint_coordinates": [round(start, 6), round(end, 6)],
                        "dividers": dividers,
                        "span_px": round(span, 6),
                        "max_residual_px": residual,
                        "observed_point_count": 2 + len(dividers),
                        "is_complete": kind == "trisection",
                        "source": "observed_boundary_relation",
                    }
                )

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for relation in relations:
        divider_key = tuple(
            sorted(
                (item["ratio"], round(float(item["coordinate"]), 3))
                for item in relation["dividers"]
            )
        )
        key = (
            relation["side"],
            round(float(relation["endpoint_coordinates"][0]), 3),
            round(float(relation["endpoint_coordinates"][1]), 3),
            divider_key,
        )
        previous = unique.get(key)
        if previous is None or relation["max_residual_px"] < previous["max_residual_px"]:
            unique[key] = relation
    candidates = list(unique.values())
    complete_point_sets = [
        {
            *relation["endpoint_ids"],
            *(item["point_id"] for item in relation["dividers"]),
        }
        for relation in candidates
        if relation["kind"] == "trisection"
    ]
    # Do not expose the incidental bisections contained inside a complete
    # trisection bundle.  The user should see the larger relation as one
    # hypothesis, not several mechanically overlapping sub-candidates.
    candidates = [
        relation
        for relation in candidates
        if relation["kind"] == "trisection"
        or not any(
            {
                *relation["endpoint_ids"],
                *(item["point_id"] for item in relation["dividers"]),
            }.issubset(point_ids)
            for point_ids in complete_point_sets
        )
    ]
    return sorted(
        candidates,
        key=lambda item: (
            0 if item["kind"] == "trisection" else 1,
            item["max_residual_px"],
            -item["span_px"],
            item["side"],
        ),
    )


__all__ = ["detect_boundary_ratio_relations"]
