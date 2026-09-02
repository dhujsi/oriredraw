"""User-selected boundary relations for a constrained construction pass.

The strict reconstruction remains the production result.  This module adds a
separate, deliberately small search that starts from one boundary relation the
user chose from the result.  It never invents a missing divider and it does not
write new CP geometry yet; its output is a transparent construction route that
can later be handed to a geometry-fitting pass.
"""

from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
import math
from typing import Any, Hashable, Iterable, Mapping

from boundary_relations import detect_boundary_ratio_relations
from constrained_angle_candidates import build_constrained_angle_candidates
from construction_search import (
    ConstructionGraph,
    ConstructionOperation,
    GeometryEntity,
)
from exact_graph_propagation import propagate_exact_geometry
from finite_endpoint_closure import build_finite_endpoint_closed_topology
from guided_cp_output import build_guided_cp_output_contract
from exact_qsqrt2 import Qsqrt2
from qsqrt2_coordinates import (
    infer_boundary_coordinate_gauges,
    qsqrt2_canonical_coefficients,
    qsqrt2_complexity,
    qsqrt2_expression,
    qsqrt2_from_coefficients,
    qsqrt2_from_mapping,
    qsqrt2_to_mapping,
)
from raw_crease_topology import build_raw_crease_topology_graph
from transactional_angle_repair import build_transactional_angle_repair
from shadow_search import (
    _algebraic_coefficients,
    _anchor_point,
    _generation,
    _line_geometry,
    _operation_summary,
    _parent_ids,
    _point_tolerance,
    _snap_residual,
    _source_kind,
    _trace_id,
)


_SIDE_LABELS = {
    "top": "上",
    "right": "右",
    "bottom": "下",
    "left": "左",
}
_SIDE_ORDER = {"top": 0, "right": 1, "bottom": 2, "left": 3}
_CORNER_POINTS = (
    ("corner:top_left", "左上角", (0.0, 0.0)),
    ("corner:top_right", "右上角", (1.0, 0.0)),
    ("corner:bottom_right", "右下角", (1.0, 1.0)),
    ("corner:bottom_left", "左下角", (0.0, 1.0)),
)
_RADICAL_DENOMINATORS = (1, 2, 3, 4, 6, 8, 12)
_MAX_FREE_POINT_COMPLEXITY = 28
_MAX_LINE_CONSTRAINED_POINT_COMPLEXITY = 48


def _reduce_radical(a: int, b: int, denominator: int) -> tuple[int, int, int]:
    common = math.gcd(math.gcd(abs(a), abs(b)), max(1, abs(denominator)))
    if common > 1:
        a //= common
        b //= common
        denominator //= common
    if denominator < 0:
        a, b, denominator = -a, -b, -denominator
    return a, b, denominator


def _format_radical(a: int, b: int, denominator: int) -> str:
    """Format ``(a + b√2) / denominator`` without floating-point text."""

    a, b, denominator = _reduce_radical(a, b, denominator)
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
    if len(pieces) > 1:
        return f"({numerator})/{denominator}"
    return f"{numerator}/{denominator}"


def _radical_coordinate(value: float) -> dict[str, Any]:
    """Find a compact rational ``a+b√2`` form for a normalized coordinate."""

    best: tuple[float, int, int, int, float] | None = None
    for denominator in _RADICAL_DENOMINATORS:
        for b in range(-12, 13):
            a = int(round(denominator * value - b * math.sqrt(2.0)))
            if abs(a) > 24:
                continue
            a, b_reduced, denominator_reduced = _reduce_radical(a, b, denominator)
            candidate = (a + b_reduced * math.sqrt(2.0)) / denominator_reduced
            error = abs(candidate - value)
            complexity = abs(a) + abs(b_reduced) + denominator_reduced - 1
            rank = (error, complexity, denominator_reduced, abs(b_reduced), abs(a))
            if best is None or rank < (best[0], best[1], best[2], abs(best[3]), abs(best[4])):
                best = (error, complexity, denominator_reduced, b_reduced, a)
    assert best is not None
    error, complexity, denominator, b, a = best
    return {
        "expression": _format_radical(a, b, denominator),
        "coefficients": [int(a), int(b), int(denominator)],
        "value": round((a + b * math.sqrt(2.0)) / denominator, 12),
        "residual_normalized": round(float(error), 12),
        "complexity": int(complexity),
    }


def _regularized_radical_coordinate(value: float, maximum: float) -> dict[str, Any]:
    """Fit noisy image evidence to a compact Q(sqrt(2)) coordinate.

    Raw raster contacts are not exact.  Minimizing residual alone therefore
    overfits them with large coefficients.  A small pixel-denominated
    complexity penalty prefers a nearby simple expression; denominator growth
    costs more than numerator growth so ``(4-3sqrt(2))/2`` is not displaced by
    an accidental high-denominator rational approximation.
    """

    best: tuple[float, float, int, int, int, int, float] | None = None
    for denominator in _RADICAL_DENOMINATORS:
        for b in range(-12, 13):
            a = int(round(denominator * value - b * math.sqrt(2.0)))
            if abs(a) > 24:
                continue
            a, b_reduced, denominator_reduced = _reduce_radical(a, b, denominator)
            candidate = (a + b_reduced * math.sqrt(2.0)) / denominator_reduced
            residual_px = abs(candidate - value) * maximum / 2.0
            complexity = abs(a) + abs(b_reduced) + 2 * (denominator_reduced - 1)
            rank = residual_px + 0.15 * complexity
            item = (
                rank,
                residual_px,
                complexity,
                denominator_reduced,
                b_reduced,
                a,
                candidate,
            )
            if best is None or item < best:
                best = item
    assert best is not None
    _, residual_px, complexity, denominator, b, a, candidate = best
    return {
        "expression": _format_radical(a, b, denominator),
        "coefficients": [int(a), int(b), int(denominator)],
        "value": round(float(candidate), 12),
        "residual_px": round(float(residual_px), 6),
        "complexity": int(complexity),
    }


def _analysis_size(result: Mapping[str, Any]) -> int:
    try:
        raw = result.get("raw_crease_evidence")
        raw_size = raw.get("analysis_size") if isinstance(raw, Mapping) else None
        return max(
            2,
            int(
                result.get("stats", {}).get("analysis_size_used")
                or raw_size
                or 512
            ),
        )
    except (AttributeError, TypeError, ValueError):
        return 512


def _observed_boundary_points(
    trace: list[Mapping[str, Any]],
    maximum: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    points: list[dict[str, Any]] = []
    point_index: dict[str, dict[str, Any]] = {}
    for point_id, label, normalized in _CORNER_POINTS:
        point = [normalized[0] * maximum, normalized[1] * maximum]
        item = {
            "id": point_id,
            "label": label,
            "point": point,
            "source": "paper_corner",
            "trace_id": None,
        }
        points.append(item)
        point_index[point_id] = item
    for fallback, anchor in enumerate(trace):
        point = _anchor_point(anchor)
        if point is None:
            continue
        trace_id = _trace_id(anchor, fallback)
        point_id = f"trace:{trace_id}"
        item = {
            "id": point_id,
            "label": f"轨迹 {trace_id}",
            "point": [round(point[0], 6), round(point[1], 6)],
            "source": "playback_trace",
            "trace_id": trace_id,
            "trace_source": str(anchor.get("source") or ""),
        }
        points.append(item)
        point_index[point_id] = item
    return points, point_index


def _describe_point_geometry(item: Mapping[str, Any], maximum: float) -> dict[str, Any] | None:
    raw = item.get("point")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2 or maximum <= 0:
        return None
    try:
        x, y = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    normalized = (-1.0 + 2.0 * x / maximum, -1.0 + 2.0 * y / maximum)
    return {
        "id": str(item["id"]),
        "label": str(item.get("label") or item["id"]),
        "trace_id": item.get("trace_id"),
        "source": str(item.get("source") or ""),
        "point_px": [round(x, 6), round(y, 6)],
        "normalized_coordinate": [round(normalized[0], 12), round(normalized[1], 12)],
    }


def _describe_point(item: Mapping[str, Any], maximum: float) -> dict[str, Any] | None:
    described = _describe_point_geometry(item, maximum)
    if described is None:
        return None
    normalized = described["normalized_coordinate"]
    x_form = _radical_coordinate(float(normalized[0]))
    y_form = _radical_coordinate(float(normalized[1]))
    described.update({
        "coordinate_expression": [x_form["expression"], y_form["expression"]],
        "algebraic_residual_px": round(
            max(x_form["residual_normalized"], y_form["residual_normalized"]) * maximum / 2.0,
            6,
        ),
        "algebraic_complexity": int(x_form["complexity"] + y_form["complexity"]),
    })
    return described


def _relation_id(relation: Mapping[str, Any]) -> str:
    divider_key = ",".join(
        f"{item.get('ratio')}:{float(item.get('coordinate', 0.0)):.4f}"
        for item in relation.get("dividers", [])
        if isinstance(item, Mapping)
    )
    endpoint_key = ",".join(f"{float(value):.4f}" for value in relation.get("endpoint_coordinates", []))
    raw = f"{relation.get('kind')}|{relation.get('side')}|{endpoint_key}|{divider_key}"
    return f"boundary-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def _build_playback_boundary_relation_catalog_legacy(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List selectable boundary hypotheses from the strict result's trace.

    The source is intentionally named in every result: current candidates come
    from the strict output's boundary contacts, not yet from a separate raw
    image endpoint detector.  This keeps the UI honest until that evidence
    source is implemented.
    """

    trace = [item for item in list(result.get("playback_trace") or []) if isinstance(item, Mapping)]
    size = _analysis_size(result)
    maximum = float(size - 1)
    if not trace or maximum <= 0:
        return []
    observed, point_index = _observed_boundary_points(trace, maximum)
    relations = detect_boundary_ratio_relations(observed, maximum)
    catalog: list[dict[str, Any]] = []
    for relation in relations:
        raw_ids = list(relation.get("endpoint_ids") or []) + [
            item.get("point_id")
            for item in relation.get("dividers", [])
            if isinstance(item, Mapping)
        ]
        points: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_id in raw_ids:
            point_id = str(raw_id)
            if point_id in seen:
                continue
            seen.add(point_id)
            described = _describe_point(point_index.get(point_id, {}), maximum)
            if described is not None:
                points.append(described)
        if len(points) < 3:
            continue
        relation_copy = dict(relation)
        relation_copy["id"] = _relation_id(relation)
        relation_copy["label"] = (
            f"{_SIDE_LABELS.get(str(relation.get('side')), str(relation.get('side')))}边"
            + ("完整三等分" if relation.get("kind") == "trisection" else "二等分")
        )
        relation_copy["points"] = points
        relation_copy["evidence_source"] = "strict_playback_trace_boundary_contacts"
        relation_copy["supporting_trace_ids"] = sorted(
            int(point["trace_id"])
            for point in points
            if point.get("trace_id") is not None
        )
        relation_copy["algebraic_complexity"] = sum(
            int(point.get("algebraic_complexity", 0)) for point in points
        )
        relation_copy["algebraic_max_residual_px"] = round(
            max(float(point.get("algebraic_residual_px", 0.0)) for point in points),
            6,
        )
        catalog.append(relation_copy)

    catalog.sort(
        key=lambda item: (
            0 if item.get("kind") == "trisection" else 1,
            0 if any(str(point.get("id", "")).startswith("corner:") for point in item["points"][:2]) else 1,
            float(item.get("max_residual_px", 0.0)),
            float(item.get("algebraic_max_residual_px", 0.0)),
            int(item.get("algebraic_complexity", 0)),
            -float(item.get("span_px", 0.0)),
            _SIDE_ORDER.get(str(item.get("side")), 4),
            str(item.get("id")),
        )
    )
    for priority, item in enumerate(catalog, start=1):
        item["priority"] = priority
    return catalog


def _point_from_side_coordinate(side: str, coordinate: float, maximum: float) -> list[float]:
    if side == "top":
        return [coordinate, 0.0]
    if side == "right":
        return [maximum, coordinate]
    if side == "bottom":
        return [maximum - coordinate, maximum]
    if side == "left":
        return [0.0, maximum - coordinate]
    raise ValueError(f"unknown paper side: {side}")


def _catalog_observations(
    observed_points: Iterable[Mapping[str, Any]],
    maximum: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    observed: list[dict[str, Any]] = []
    point_index: dict[str, dict[str, Any]] = {}
    for item in observed_points:
        if not isinstance(item, Mapping) or item.get("id") is None:
            continue
        raw = item.get("point")
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        try:
            point = [float(raw[0]), float(raw[1])]
        except (TypeError, ValueError):
            continue
        copied = dict(item)
        copied["id"] = str(item["id"])
        copied["point"] = point
        observed.append(copied)
        point_index[copied["id"]] = copied
    for point_id, label, normalized in _CORNER_POINTS:
        if point_id in point_index:
            continue
        item = {
            "id": point_id,
            "label": label,
            "point": [normalized[0] * maximum, normalized[1] * maximum],
            "source": "paper_corner",
            "trace_id": None,
        }
        observed.append(item)
        point_index[point_id] = item
    return observed, point_index


def _relation_members(relation: Mapping[str, Any]) -> list[tuple[str, str, float]]:
    endpoints = list(relation.get("endpoint_ids") or [])
    coordinates = list(relation.get("endpoint_coordinates") or [])
    if len(endpoints) != 2 or len(coordinates) != 2:
        return []
    members: list[tuple[str, str, float]] = [
        (str(endpoints[0]), "start", float(coordinates[0]))
    ]
    wanted = {"1/3", "2/3"} if relation.get("kind") == "trisection" else {"1/2"}
    dividers = [
        item
        for item in relation.get("dividers", [])
        if isinstance(item, Mapping) and str(item.get("ratio")) in wanted
    ]
    dividers.sort(
        key=lambda item: (
            int(str(item["ratio"]).split("/", 1)[0])
            / int(str(item["ratio"]).split("/", 1)[1])
        )
    )
    members.extend(
        (str(item["point_id"]), str(item["ratio"]), float(item["coordinate"]))
        for item in dividers
    )
    members.append((str(endpoints[1]), "end", float(coordinates[1])))
    return members


def _relation_edge_parameter_values(
    members: Iterable[tuple[str, str, float]],
    start_fit: Mapping[str, Any],
    end_fit: Mapping[str, Any],
) -> list[tuple[str, str, Qsqrt2]]:
    """Return exact clockwise edge parameters in [0, 1]."""

    start_centered = qsqrt2_from_mapping(start_fit)
    end_centered = qsqrt2_from_mapping(end_fit)
    start = (start_centered + 1) / 2
    end = (end_centered + 1) / 2
    output: list[tuple[str, str, Qsqrt2]] = []
    for point_id, role, _ in members:
        if role == "start":
            parameter = start
        elif role == "end":
            parameter = end
        else:
            numerator, denominator = (int(value) for value in role.split("/", 1))
            parameter = start + (end - start) * qsqrt2_from_coefficients(
                numerator,
                0,
                denominator,
            )
        output.append((point_id, role, parameter))
    return output


def _normalized_boundary_point(side: str, parameter: Qsqrt2) -> tuple[Qsqrt2, Qsqrt2]:
    """Map a clockwise edge parameter to centered exact paper coordinates."""

    one = Qsqrt2(1)
    centered = 2 * parameter - 1
    if side == "top":
        return centered, -one
    if side == "right":
        return one, centered
    if side == "bottom":
        return -centered, one
    if side == "left":
        return -one, -centered
    raise ValueError(f"unknown paper side: {side}")


def _serialize_relation_coordinates(
    side: str,
    parameter_values: Iterable[tuple[str, str, Qsqrt2]],
    points: Iterable[dict[str, Any]],
    maximum: float,
) -> list[dict[str, Any]]:
    """Attach one grouped exact relation without refitting each point independently."""

    exact_by_id: dict[str, tuple[Qsqrt2, Qsqrt2]] = {}
    edge_parameters: list[dict[str, Any]] = []
    for point_id, role, parameter in parameter_values:
        exact_by_id[point_id] = _normalized_boundary_point(side, parameter)
        edge_parameters.append(
            {
                "id": point_id,
                "role": role,
                "parameter": qsqrt2_to_mapping(parameter),
            }
        )
    for point in points:
        exact = exact_by_id.get(str(point.get("id")))
        normalized = point.get("normalized_coordinate")
        if exact is None or not isinstance(normalized, (list, tuple)) or len(normalized) < 2:
            continue
        x, y = exact
        point["coordinate_expression"] = [qsqrt2_expression(x), qsqrt2_expression(y)]
        point["algebraic_residual_px"] = round(
            max(abs(float(x) - float(normalized[0])), abs(float(y) - float(normalized[1])))
            * maximum
            / 2.0,
            6,
        )
        point["algebraic_complexity"] = qsqrt2_complexity(x) + qsqrt2_complexity(y)
    return edge_parameters


def build_boundary_relation_catalog_from_points(
    observed_points: Iterable[Mapping[str, Any]],
    maximum: float,
    *,
    evidence_source: str,
    tolerance_px: float = 1.5,
    fit_algebraic_geometry: bool = False,
    maximum_candidates: int = 64,
) -> list[dict[str, Any]]:
    """Build selectable grouped relations from one declared evidence source."""

    if maximum <= 0:
        return []
    observed, point_index = _catalog_observations(observed_points, maximum)
    relations = detect_boundary_ratio_relations(
        observed,
        maximum,
        tolerance_px=max(0.0, float(tolerance_px)),
    )
    # A noisy edge can support many overlapping ratios.  The detector already
    # orders complete relations by observation residual; fitting every weak
    # overlap to hundreds of radical forms is wasted work in Pyodide.  Keep a
    # generous preselection so lower-residual alternatives and the霸王龙 top
    # relation both survive, then apply the richer priority sort below.
    relations = relations[: max(128, int(maximum_candidates) * 8)]
    catalog: list[dict[str, Any]] = []
    endpoint_fit_cache: dict[float, dict[str, Any]] = {}
    for relation in relations:
        members = _relation_members(relation)
        if len(members) < 3:
            continue
        side = str(relation.get("side") or "")
        observed_start = float(members[0][2])
        observed_end = float(members[-1][2])
        def endpoint_fit(coordinate: float) -> dict[str, Any]:
            key = round(float(coordinate), 6)
            if key not in endpoint_fit_cache:
                endpoint_fit_cache[key] = _regularized_radical_coordinate(
                    -1.0 + 2.0 * coordinate / maximum,
                    maximum,
                )
            return endpoint_fit_cache[key]

        start_fit = endpoint_fit(observed_start)
        end_fit = endpoint_fit(observed_end)
        edge_parameter_values = _relation_edge_parameter_values(
            members,
            start_fit,
            end_fit,
        )
        if fit_algebraic_geometry:
            exact_start = (float(start_fit["value"]) + 1.0) * maximum / 2.0
            exact_end = (float(end_fit["value"]) + 1.0) * maximum / 2.0
        else:
            exact_start = observed_start
            exact_end = observed_end

        points: list[dict[str, Any]] = []
        image_residuals: list[float] = []
        evidence_scores: list[float] = []
        for point_id, role, observed_coordinate in members:
            if role == "start":
                exact_coordinate = exact_start
            elif role == "end":
                exact_coordinate = exact_end
            else:
                numerator, denominator = (int(value) for value in role.split("/", 1))
                exact_coordinate = exact_start + (exact_end - exact_start) * numerator / denominator
            source_item = point_index.get(point_id, {})
            exact_item = dict(source_item)
            exact_item["id"] = point_id
            exact_item["point"] = _point_from_side_coordinate(side, exact_coordinate, maximum)
            described = _describe_point_geometry(exact_item, maximum)
            if described is None:
                continue
            raw_point = source_item.get("point")
            if isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                described["observed_point_px"] = [
                    round(float(raw_point[0]), 6),
                    round(float(raw_point[1]), 6),
                ]
            residual = abs(float(observed_coordinate) - exact_coordinate)
            described["relation_role"] = role
            described["observed_side_coordinate_px"] = round(float(observed_coordinate), 6)
            described["fitted_side_coordinate_px"] = round(float(exact_coordinate), 6)
            described["image_residual_px"] = round(residual, 6)
            if source_item.get("directional_score") is not None:
                score = float(source_item["directional_score"])
                described["directional_score"] = round(score, 6)
                evidence_scores.append(score)
            image_residuals.append(residual)
            points.append(described)
        if len(points) != len(members):
            continue

        relation_copy = dict(relation)
        relation_copy["id"] = _relation_id(relation)
        relation_copy["label"] = (
            f"{_SIDE_LABELS.get(side, side)}边"
            + ("完整三等分" if relation.get("kind") == "trisection" else "二等分")
        )
        relation_copy["points"] = points
        relation_copy["_edge_parameter_values"] = edge_parameter_values
        relation_copy["evidence_source"] = evidence_source
        relation_copy["geometry_mode"] = (
            "fitted_qsqrt2_boundary_relation"
            if fit_algebraic_geometry
            else "observed_exact_boundary_relation"
        )
        relation_copy["observed_endpoint_coordinates"] = [
            round(observed_start, 6),
            round(observed_end, 6),
        ]
        relation_copy["fitted_endpoint_coordinates"] = [
            round(exact_start, 6),
            round(exact_end, 6),
        ]
        relation_copy["fitted_geometry_max_residual_px"] = round(
            max(image_residuals, default=0.0),
            6,
        )
        relation_copy["endpoint_algebraic_snap_residual_px"] = round(
            max(float(start_fit["residual_px"]), float(end_fit["residual_px"])),
            6,
        )
        relation_copy["endpoint_algebraic_complexity"] = (
            int(start_fit["complexity"]) + int(end_fit["complexity"])
        )
        relation_copy["evidence_score"] = round(
            sum(evidence_scores) / len(evidence_scores) if evidence_scores else 0.0,
            6,
        )
        relation_copy["supporting_trace_ids"] = sorted(
            int(point["trace_id"])
            for point in points
            if point.get("trace_id") is not None
        )
        relation_copy["algebraic_complexity"] = sum(
            qsqrt2_complexity(value)
            for _, _, parameter in edge_parameter_values
            for value in _normalized_boundary_point(side, parameter)
        )
        catalog.append(relation_copy)

    catalog.sort(
        key=lambda item: (
            0 if item.get("kind") == "trisection" else 1,
            0
            if any(
                str(point.get("id", "")).startswith("corner:")
                for point in item["points"][:2]
            )
            else 1,
            float(
                item.get(
                    "fitted_geometry_max_residual_px",
                    item.get("max_residual_px", 0.0),
                )
            ),
            -float(item.get("evidence_score", 0.0)),
            float(item.get("endpoint_algebraic_snap_residual_px", 0.0)),
            int(item.get("endpoint_algebraic_complexity", 0)),
            int(item.get("algebraic_complexity", 0)),
            -float(item.get("span_px", 0.0)),
            _SIDE_ORDER.get(str(item.get("side")), 4),
            str(item.get("id")),
        )
    )
    catalog = catalog[: max(1, int(maximum_candidates))]
    for priority, item in enumerate(catalog, start=1):
        item["priority"] = priority
        edge_parameter_values = item.pop("_edge_parameter_values", [])
        item["edge_parameters"] = _serialize_relation_coordinates(
            str(item.get("side") or ""),
            edge_parameter_values,
            item.get("points") or [],
            maximum,
        )
        item["algebraic_max_residual_px"] = round(
            max(
                (
                    float(point.get("algebraic_residual_px", 0.0))
                    for point in item.get("points", [])
                    if isinstance(point, Mapping)
                ),
                default=0.0,
            ),
            6,
        )
        coordinate_gauges = infer_boundary_coordinate_gauges(
            str(item.get("side") or ""),
            item.get("edge_parameters") or [],
        )
        item["coordinate_gauge_candidates"] = coordinate_gauges
        if not coordinate_gauges:
            continue
        recommended_gauge = coordinate_gauges[0]
        item["recommended_coordinate_gauge"] = recommended_gauge
        project_points = {
            str(point.get("id")): point
            for point in recommended_gauge.get("points", [])
            if isinstance(point, Mapping)
        }
        for point in item.get("points", []):
            if not isinstance(point, dict):
                continue
            project_point = project_points.get(str(point.get("id")))
            if project_point is not None:
                point["project_coordinate"] = project_point.get("coordinate")
                point["edge_distance"] = project_point.get("edge_distance")
                point["edge_parameter"] = project_point.get("edge_parameter")
    return catalog


def build_boundary_relation_catalog(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List selectable boundary hypotheses from supplied or legacy evidence."""

    supplied = result.get("boundary_relation_candidates")
    if isinstance(supplied, list):
        return [dict(item) for item in supplied if isinstance(item, Mapping)]
    size = _analysis_size(result)
    maximum = float(size - 1)
    raw_points = result.get("raw_boundary_contacts")
    if isinstance(raw_points, list) and raw_points:
        return build_boundary_relation_catalog_from_points(
            raw_points,
            maximum,
            evidence_source="raw_image_directional_scan",
            tolerance_px=max(5.0, maximum * 0.012),
            fit_algebraic_geometry=True,
        )
    trace = [
        item
        for item in list(result.get("playback_trace") or [])
        if isinstance(item, Mapping)
    ]
    if not trace:
        return []
    observed, _ = _observed_boundary_points(trace, maximum)
    return build_boundary_relation_catalog_from_points(
        observed,
        maximum,
        evidence_source="strict_playback_trace_boundary_contacts",
    )


def _legal_direction_index(anchor: Mapping[str, Any]) -> int | None:
    """Classify one observed crease direction without generating alternatives."""

    try:
        angle = float(anchor["angle"]) % 180.0
    except (KeyError, TypeError, ValueError):
        return None
    index = int(round(angle / 22.5)) % 8
    expected = index * 22.5
    residual = abs((angle - expected + 90.0) % 180.0 - 90.0)
    return index if residual <= 1.5 else None


def _register_observed_geometry(
    graph: ConstructionGraph,
    indexed: Iterable[tuple[int, Mapping[str, Any]]],
    *,
    point_merge_tolerance_px: float = 0.8,
) -> dict[int, Hashable]:
    """Register trace creases and merge repeated observed anchor points."""

    items = list(indexed)
    clusters: list[dict[str, Any]] = []
    anchor_nodes: dict[int, Hashable] = {}
    for trace_id, anchor in items:
        point = _anchor_point(anchor)
        if point is None:
            continue
        best_index: int | None = None
        best_distance = math.inf
        for cluster_index, cluster in enumerate(clusters):
            center = cluster["center"]
            distance = math.hypot(point[0] - center[0], point[1] - center[1])
            if distance <= point_merge_tolerance_px and distance < best_distance:
                best_index = cluster_index
                best_distance = distance
        if best_index is None:
            best_index = len(clusters)
            clusters.append(
                {
                    "center": [point[0], point[1]],
                    "samples": [],
                }
            )
        cluster = clusters[best_index]
        cluster["samples"].append(
            {
                "source": "playback_trace_anchor",
                "trace_id": trace_id,
                "point_px": [round(point[0], 6), round(point[1], 6)],
            }
        )
        sample_count = len(cluster["samples"])
        old_center = cluster["center"]
        cluster["center"] = [
            old_center[0] + (point[0] - old_center[0]) / sample_count,
            old_center[1] + (point[1] - old_center[1]) / sample_count,
        ]

    for cluster_index, cluster in enumerate(clusters):
        point_id = ("point", "observed", cluster_index)
        trace_ids = sorted(int(item["trace_id"]) for item in cluster["samples"])
        graph.add_geometry_entity(
            GeometryEntity(
                id=point_id,
                kind="point",
                observed_geometry={
                    "point_px": [round(float(value), 6) for value in cluster["center"]],
                    "observations": list(cluster["samples"]),
                },
                evidence_sources={"playback_trace"},
                metadata={"anchor_trace_ids": trace_ids},
            )
        )
        for trace_id in trace_ids:
            anchor_nodes[trace_id] = point_id

    for trace_id, anchor in items:
        crease_id = ("ray", trace_id)
        point = _anchor_point(anchor)
        try:
            observed_angle = round(float(anchor["angle"]), 6)
        except (KeyError, TypeError, ValueError):
            observed_angle = None
        observed_geometry: dict[str, Any] = {
            "trace_id": trace_id,
            "angle_deg": observed_angle,
            "direction_index": _legal_direction_index(anchor),
            "line_offset_px": round(_observed_offset(anchor), 9),
            "match_tolerance_px": round(
                min(3.2, max(0.85, _point_tolerance(anchor) + 0.5)),
                6,
            ),
            "snap_residual_px": round(_snap_residual(anchor), 6),
        }
        if point is not None:
            observed_geometry["anchor_point_px"] = [
                round(point[0], 6),
                round(point[1], 6),
            ]
        graph.add_geometry_entity(
            GeometryEntity(
                id=crease_id,
                kind="crease",
                observed_geometry=observed_geometry,
                evidence_sources={"playback_trace"},
                metadata={
                    "source": str(anchor.get("source") or ""),
                    "generation": max(0, _generation(anchor)),
                    "parent_trace_ids": list(anchor.get("trace_parent_ids") or []),
                },
            )
        )
    crease_ids = {
        int(entity.observed_geometry["trace_id"]): entity.id
        for entity in graph.geometry_entities.values()
        if entity.kind == "crease" and entity.observed_geometry.get("trace_id") is not None
    }
    for trace_id, anchor in items:
        anchor_node = anchor_nodes.get(trace_id)
        if anchor_node is None:
            continue
        own_crease = crease_ids.get(trace_id)
        if own_crease is not None:
            graph.connect_incidence(anchor_node, own_crease)
        for parent_id in _parent_ids(anchor, set(crease_ids)):
            parent_crease = crease_ids.get(parent_id)
            if parent_crease is not None:
                graph.connect_incidence(anchor_node, parent_crease)
    return anchor_nodes


def _legacy_trace_graph(
    trace: list[Mapping[str, Any]],
) -> tuple[ConstructionGraph, frozenset[Hashable], dict[int, Mapping[str, Any]], dict[Hashable, dict[str, Any]]]:
    indexed = [(_trace_id(anchor, fallback), anchor) for fallback, anchor in enumerate(trace)]
    anchors = {trace_id: anchor for trace_id, anchor in indexed}
    valid_ids = set(anchors)
    graph = ConstructionGraph()
    _register_observed_geometry(graph, indexed)
    observations: set[Hashable] = set()
    details: dict[Hashable, dict[str, Any]] = {}
    for trace_id, anchor in indexed:
        kind = _source_kind(anchor)
        operation_id = ("legacy", trace_id)
        graph.add_operation(
            ConstructionOperation(
                id=operation_id,
                kind=kind,
                parents=tuple(("ray", parent) for parent in _parent_ids(anchor, valid_ids)),
                outputs=(("ray", trace_id),),
                explains=frozenset({("required_ray", trace_id)}),
                residual=_snap_residual(anchor),
                generation=max(0, _generation(anchor)),
                independent_parameters=1 if kind == "algebraic_seed" else 0,
                algebraic_coefficients=_algebraic_coefficients(anchor) if kind == "algebraic_seed" else (),
            )
        )
        details[operation_id] = {"provenance": "legacy", "target_trace_id": trace_id}
        observations.add(("required_ray", trace_id))
    return graph, frozenset(observations), anchors, details


def _constrain_initial_sources(
    graph: ConstructionGraph,
    details: Mapping[Hashable, Mapping[str, Any]],
) -> tuple[ConstructionGraph, int]:
    """Keep legacy propagation but remove unchosen parentless source rays."""

    constrained = ConstructionGraph()
    # The operation filter changes provenance availability, not the underlying
    # observed/exact geometry.  Keep the same incidence graph.
    constrained.geometry_entities = graph.geometry_entities
    constrained.incidence = graph.incidence
    removed = 0
    for operation in graph.operations.values():
        provenance = str(details.get(operation.id, {}).get("provenance") or "")
        is_unselected_legacy_root = (
            provenance == "legacy"
            and not operation.parents
            and operation.kind not in {"corner_seed", "midpoint_seed"}
        )
        if is_unselected_legacy_root:
            removed += 1
            continue
        constrained.add_operation(operation)
    return constrained, removed


def _observed_offset(anchor: Mapping[str, Any]) -> float:
    try:
        return float(anchor.get("observed_offset_px", anchor["line_offset_px"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _qsqrt2_mapping_key(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return qsqrt2_canonical_coefficients(qsqrt2_from_mapping(value))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _project_coordinate_key(value: Any) -> tuple[tuple[int, int, int], ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    keys = tuple(_qsqrt2_mapping_key(item) for item in value)
    if any(item is None for item in keys):
        return None
    return keys  # type: ignore[return-value]


def _relation_side_length_key(
    relation: Mapping[str, Any],
) -> tuple[int, int, int] | None:
    gauge = relation.get("recommended_coordinate_gauge")
    if not isinstance(gauge, Mapping):
        return None
    return _qsqrt2_mapping_key(gauge.get("side_length"))


def _point_exact_geometry_is_compatible(
    entity: GeometryEntity,
    project_coordinate: Any,
    side_length: Any,
) -> bool:
    if not entity.exact_geometry:
        return True
    existing_coordinate = _project_coordinate_key(
        entity.exact_geometry.get("project_coordinate")
    )
    candidate_coordinate = _project_coordinate_key(project_coordinate)
    if (
        existing_coordinate is not None
        and candidate_coordinate is not None
        and existing_coordinate != candidate_coordinate
    ):
        return False
    existing_side = _qsqrt2_mapping_key(entity.exact_geometry.get("side_length"))
    candidate_side = _qsqrt2_mapping_key(side_length)
    return not (
        existing_side is not None
        and candidate_side is not None
        and existing_side != candidate_side
    )


def _add_guided_relation_operations(
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: dict[Hashable, dict[str, Any]],
    relation: Mapping[str, Any],
    *,
    require_existing_incidence: bool = False,
    selection_round: int = 1,
) -> tuple[ConstructionOperation, int]:
    relation_id = str(relation["id"])
    points = [item for item in relation.get("points", []) if isinstance(item, Mapping)]
    evidence_source = str(relation.get("evidence_source") or "selected_boundary_relation")
    gauge = relation.get("recommended_coordinate_gauge")
    side_length = (
        gauge.get("side_length")
        if isinstance(gauge, Mapping) and isinstance(gauge.get("side_length"), Mapping)
        else None
    )
    point_bindings: list[dict[str, Any]] = []
    claimed_nodes: set[Hashable] = set()
    for order, point in enumerate(points):
        fitted_raw = point.get("point_px")
        observed_raw = point.get("observed_point_px", fitted_raw)
        if not isinstance(fitted_raw, (list, tuple)) or len(fitted_raw) < 2:
            continue
        if not isinstance(observed_raw, (list, tuple)) or len(observed_raw) < 2:
            observed_raw = fitted_raw
        try:
            fitted = (float(fitted_raw[0]), float(fitted_raw[1]))
            observed = (float(observed_raw[0]), float(observed_raw[1]))
        except (TypeError, ValueError):
            continue

        node: Hashable | None = None
        nearest_distance = math.inf
        try:
            relation_residual = float(point.get("image_residual_px", 0.0) or 0.0)
        except (TypeError, ValueError):
            relation_residual = 0.0
        point_match_tolerance = min(
            5.5,
            max(
                0.9,
                relation_residual + 1.0,
                math.hypot(fitted[0] - observed[0], fitted[1] - observed[1]) + 1.0,
            ),
        )
        relation_side = str(relation.get("side") or "")
        for entity_id, entity in graph.geometry_entities.items():
            if entity.kind != "point" or entity_id in claimed_nodes:
                continue
            if require_existing_incidence:
                boundary_sides = set(entity.observed_geometry.get("boundary_sides") or ())
                if relation_side and relation_side not in boundary_sides:
                    continue
            candidate = entity.observed_geometry.get("point_px")
            if not isinstance(candidate, (list, tuple)) or len(candidate) < 2:
                continue
            distance = math.hypot(
                observed[0] - float(candidate[0]),
                observed[1] - float(candidate[1]),
            )
            if distance <= point_match_tolerance and distance < nearest_distance:
                node = entity_id
                nearest_distance = distance
        preexisting_node = node is not None
        if node is None:
            node = ("guided_boundary_point", relation_id, str(point["id"]))
            graph.add_geometry_entity(
                GeometryEntity(
                    id=node,
                    kind="point",
                    observed_geometry={
                        "point_px": [round(observed[0], 6), round(observed[1], 6)],
                        "observations": [],
                    },
                    evidence_sources={evidence_source},
                )
            )
        claimed_nodes.add(node)
        entity = graph.geometry_entity(node)
        entity.evidence_sources.add(evidence_source)
        observations = entity.observed_geometry.setdefault("observations", [])
        observations.append(
            {
                "source": evidence_source,
                "relation_id": relation_id,
                "relation_point_id": str(point["id"]),
                "point_px": [round(observed[0], 6), round(observed[1], 6)],
            }
        )
        entity.observed_geometry["relation_fitted_point_px"] = [
            round(fitted[0], 6),
            round(fitted[1], 6),
        ]
        entity.metadata.setdefault("selected_relation_points", []).append(
            {
                "relation_id": relation_id,
                "selection_round": int(selection_round),
                "point_id": str(point["id"]),
                "role": str(point.get("relation_role") or ""),
            }
        )
        entity.metadata.setdefault("selected_relation_ids", []).append(relation_id)
        project_coordinate = point.get("project_coordinate")
        if not _point_exact_geometry_is_compatible(
            entity,
            project_coordinate,
            side_length,
        ):
            raise ValueError(
                f"selected relation {relation_id!r} conflicts at point {node!r}"
            )
        exact_geometry: dict[str, Any] = {
            "source_relation_id": relation_id,
            "origin": "top_left",
            "side": relation.get("side"),
        }
        if isinstance(project_coordinate, (list, tuple)) and len(project_coordinate) == 2:
            exact_geometry["project_coordinate"] = list(project_coordinate)
        if isinstance(point.get("edge_parameter"), Mapping):
            exact_geometry["edge_parameter"] = dict(point["edge_parameter"])
        if isinstance(point.get("edge_distance"), Mapping):
            exact_geometry["edge_distance"] = dict(point["edge_distance"])
        if side_length is not None:
            exact_geometry["side_length"] = dict(side_length)
        if not entity.exact_geometry:
            graph.exactify_geometry(node, exact_geometry)
        point_bindings.append(
            {
                "point": point,
                "node": node,
                "order": order,
                "fitted": fitted,
                "observed": observed,
                "preexisting_node": preexisting_node,
            }
        )

    nodes = tuple(item["node"] for item in point_bindings)
    relation_operation = ConstructionOperation(
        id=("guided_boundary_relation", relation_id),
        kind="guided_boundary_relation",
        parents=(),
        outputs=nodes,
        generation=0,
    )
    graph.add_operation(relation_operation)
    details[relation_operation.id] = {
        "provenance": "guided_boundary_relation",
        "guided_relation_id": relation_id,
        "kind": relation.get("kind"),
        "side": relation.get("side"),
        "point_count": len(points),
        "selection_round": int(selection_round),
    }

    side = str(relation.get("side") or "")
    tangent_direction = (
        0
        if side in {"top", "bottom"}
        else 4 if side in {"left", "right"} else None
    )
    matches_by_crease: dict[int, dict[str, Any]] = {}
    for binding in point_bindings:
        point = binding["point"]
        node = binding["node"]
        fitted = binding["fitted"]
        observed = binding["observed"]
        point_uncertainty = max(
            float(point.get("image_residual_px", 0.0) or 0.0),
            math.hypot(fitted[0] - observed[0], fitted[1] - observed[1]),
        )
        for target_id, anchor in anchors.items():
            crease_node = anchor.get("_geometry_entity_id", ("ray", target_id))
            if crease_node not in graph.geometry_entities:
                continue
            if require_existing_incidence and (
                not binding["preexisting_node"]
                or crease_node not in graph.incidence.get(node, set())
            ):
                # Raw-image mode may only exactify a crease already incident
                # to this observed boundary node.  An infinite line extension
                # passing through the selected coordinate is not evidence.
                continue
            direction_index = _legal_direction_index(anchor)
            if direction_index is None or (
                tangent_direction is not None and direction_index == tangent_direction
            ):
                continue
            geometry = _line_geometry(anchor)
            if geometry is None:
                continue
            _, normal, _ = geometry
            fitted_offset = normal[0] * fitted[0] + normal[1] * fitted[1]
            observed_offset = normal[0] * observed[0] + normal[1] * observed[1]
            line_offset = _observed_offset(anchor)
            fitted_residual = abs(fitted_offset - line_offset)
            observed_residual = abs(observed_offset - line_offset)
            tolerance = min(3.2, max(0.85, _point_tolerance(anchor) + 0.5))
            if fitted_residual > tolerance or observed_residual > tolerance + point_uncertainty:
                continue
            candidate = {
                "point": point,
                "node": node,
                "target_id": target_id,
                "crease_node": crease_node,
                "direction_source": str(
                    anchor.get("source") or "existing_observed_crease"
                ),
                "direction_index": direction_index,
                "fitted": fitted,
                "observed": observed,
                "fitted_offset": fitted_offset,
                "fitted_residual": fitted_residual,
                "observed_residual": observed_residual,
                "rank": (fitted_residual, observed_residual, int(binding["order"])),
            }
            old = matches_by_crease.get(target_id)
            if old is None or candidate["rank"] < old["rank"]:
                matches_by_crease[target_id] = candidate

    added = 0
    for target_id, match in sorted(matches_by_crease.items()):
        point = match["point"]
        node = match["node"]
        project_coordinate = point.get("project_coordinate")
        if not isinstance(project_coordinate, (list, tuple)) or len(project_coordinate) != 2:
            continue
        crease_node = match["crease_node"]
        if crease_node not in graph.geometry_entities:
            continue
        graph.connect_incidence(node, crease_node)
        crease_entity = graph.geometry_entity(crease_node)
        crease_entity.metadata.setdefault("selected_relation_ids", []).append(
            relation_id
        )
        if crease_entity.exact_geometry:
            # A prior selected relation or the deterministic frontier already
            # explained this same observed crease.  Keep the extra relation as
            # provenance, but do not overwrite the single exact geometry.
            continue
        exact_crease_geometry = {
            "source_relation_id": relation_id,
            "direction_index": int(match["direction_index"]),
            "direction_deg": round(int(match["direction_index"]) * 22.5, 6),
            "through_point_id": str(node),
            "through_point_project": list(project_coordinate),
        }
        if side_length is not None:
            exact_crease_geometry["side_length"] = dict(side_length)
        graph.exactify_geometry(crease_node, exact_crease_geometry)
        crease_entity.metadata["exactified_by_selected_relation"] = True
        crease_entity.metadata["selected_relation_round"] = int(selection_round)
        operation_id = (
            "guided_boundary_relation_ray",
            relation_id,
            str(point["id"]),
            target_id,
        )
        graph.add_operation(
            ConstructionOperation(
                id=operation_id,
                kind="guided_boundary_relation_ray",
                parents=(node,),
                outputs=(crease_node,),
                explains=frozenset({("required_ray", target_id)}),
                residual=float(match["fitted_residual"]),
                generation=1,
            )
        )
        details[operation_id] = {
            "provenance": "guided_boundary_relation_ray",
            "guided_relation_id": relation_id,
            "selection_round": int(selection_round),
            "target_trace_id": target_id,
            "source_boundary_point_id": point["id"],
            "source_boundary_point_px": [
                round(match["observed"][0], 6),
                round(match["observed"][1], 6),
            ],
            "fitted_boundary_point_px": [
                round(match["fitted"][0], 6),
                round(match["fitted"][1], 6),
            ],
            "candidate_offset_px": round(float(match["fitted_offset"]), 9),
            "observed_line_residual_px": round(float(match["observed_residual"]), 6),
            "exact_fit_line_residual_px": round(float(match["fitted_residual"]), 6),
            "direction_index": int(match["direction_index"]),
            "direction_source": match["direction_source"],
            "relation_kind": relation.get("kind"),
            "relation_side": relation.get("side"),
        }
        added += 1
    return relation_operation, added


def _selection_steps(
    selection: Mapping[str, Any] | None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Normalize old relation-only input and the ordered mixed-step format."""

    source = selection or {}
    raw_steps = source.get("selection_steps")
    steps: list[dict[str, str]] = []
    invalid: list[str] = []
    if isinstance(raw_steps, (list, tuple)):
        for index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, Mapping):
                invalid.append(f"step:{index}")
                continue
            kind = str(raw_step.get("kind") or "")
            step_id = str(raw_step.get("id") or "")
            if kind not in {"boundary_relation", "topology_point"} or not step_id:
                invalid.append(f"step:{index}")
                continue
            steps.append({"kind": kind, "id": step_id})
    else:
        raw_ids = source.get("relation_ids")
        if not isinstance(raw_ids, (list, tuple)):
            raw_ids = [source.get("id")]
        steps = [
            {"kind": "boundary_relation", "id": str(raw_id)}
            for raw_id in raw_ids
            if str(raw_id or "")
        ]

    seen: set[tuple[str, str]] = set()
    unique_steps: list[dict[str, str]] = []
    for index, step in enumerate(steps):
        key = (step["kind"], step["id"])
        if key in seen:
            invalid.append(f"duplicate:{index}:{step['kind']}:{step['id']}")
            continue
        seen.add(key)
        unique_steps.append(step)
    if unique_steps and unique_steps[0]["kind"] != "boundary_relation":
        invalid.append("first_step_must_be_boundary_relation")
    return unique_steps, invalid


def _selection_ids(selection: Mapping[str, Any] | None) -> list[str]:
    steps, _ = _selection_steps(selection)
    return [step["id"] for step in steps if step["kind"] == "boundary_relation"]


def _selected_relations(
    catalog: list[dict[str, Any]],
    selection: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected_ids = _selection_ids(selection)
    index = {str(item.get("id")): item for item in catalog}
    missing = [relation_id for relation_id in selected_ids if relation_id not in index]
    return [index[relation_id] for relation_id in selected_ids if relation_id in index], missing


def _fit_project_axis(
    pixel: float,
    side_length: Qsqrt2,
    maximum: float,
) -> dict[str, Any]:
    """Regularize one noisy pixel coordinate to a compact project Q(sqrt(2))."""

    target = float(pixel) / maximum * float(side_length)
    best: tuple[float, float, int, int, int, int, Qsqrt2] | None = None
    for denominator in _RADICAL_DENOMINATORS:
        for b in range(-12, 13):
            a = int(round(denominator * target - b * math.sqrt(2.0)))
            if abs(a) > 24:
                continue
            candidate = qsqrt2_from_coefficients(a, b, denominator)
            canonical_a, canonical_b, canonical_denominator = (
                qsqrt2_canonical_coefficients(candidate)
            )
            fitted_pixel = float(candidate / side_length) * maximum
            residual_px = abs(fitted_pixel - pixel)
            complexity = qsqrt2_complexity(candidate)
            score = (
                residual_px
                + 0.15 * complexity
                + 0.2 * max(0, -canonical_b)
            )
            item = (
                score,
                residual_px,
                complexity,
                canonical_denominator,
                abs(canonical_b),
                abs(canonical_a),
                candidate,
            )
            if best is None or item[:-1] < best[:-1]:
                best = item
    assert best is not None
    _, residual_px, complexity, _, _, _, candidate = best
    mapping = qsqrt2_to_mapping(candidate)
    return {
        **mapping,
        "fitted_pixel": round(float(candidate / side_length) * maximum, 6),
        "residual_px": round(float(residual_px), 6),
        "complexity": int(complexity),
    }


def _guided_direction_vector(direction_index: int) -> tuple[Qsqrt2, Qsqrt2]:
    """Return the same exact direction basis used by graph propagation."""

    zero = Qsqrt2()
    one = Qsqrt2(1)
    diagonal = Qsqrt2(1, 1)
    vectors = (
        (one, zero),
        (diagonal, one),
        (one, one),
        (one, diagonal),
        (zero, one),
        (-one, diagonal),
        (-one, one),
        (-diagonal, one),
    )
    if not 0 <= direction_index < len(vectors):
        raise ValueError(f"invalid 22.5-degree direction index: {direction_index}")
    return vectors[direction_index]


def _line_constrained_topology_point_fit(
    entity: GeometryEntity,
    graph: ConstructionGraph,
    side_length: Qsqrt2,
    maximum: float,
    observed: tuple[float, float],
    tolerance: float,
) -> dict[str, Any] | None:
    """Fit one scalar on an already exact incident crease.

    A free x/y fit can place a selected topology point slightly off a crease
    that is already exact.  With exactly one exact incident crease, the source
    image only needs to determine the point's position along that crease.  The
    resulting point is exact and incident by construction; a later copied-graph
    trial still decides whether committing it resolves existing geometry.
    """

    exact_incident: list[tuple[GeometryEntity, tuple[Qsqrt2, Qsqrt2], int]] = []
    for crease in graph.incident_entities(entity.id):
        if crease.kind != "crease":
            continue
        through_raw = crease.exact_geometry.get("through_point_project")
        if not isinstance(through_raw, (list, tuple)) or len(through_raw) != 2:
            continue
        try:
            through = (
                qsqrt2_from_mapping(through_raw[0]),
                qsqrt2_from_mapping(through_raw[1]),
            )
            direction_index = int(crease.exact_geometry.get("direction_index"))
            _guided_direction_vector(direction_index)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        exact_incident.append((crease, through, direction_index))

    # Two exact incident creases belong to deterministic propagation.  If that
    # propagation rejected their intersection, do not override the conflict by
    # fitting a third, unrelated point.
    if len(exact_incident) != 1:
        return None

    crease, through, direction_index = exact_incident[0]
    direction = _guided_direction_vector(direction_index)
    observed_project = (
        observed[0] / maximum * float(side_length),
        observed[1] / maximum * float(side_length),
    )
    through_float = (float(through[0]), float(through[1]))
    direction_float = (float(direction[0]), float(direction[1]))
    direction_norm_squared = (
        direction_float[0] * direction_float[0]
        + direction_float[1] * direction_float[1]
    )
    if direction_norm_squared <= 1e-12:
        return None
    target_parameter = (
        (observed_project[0] - through_float[0]) * direction_float[0]
        + (observed_project[1] - through_float[1]) * direction_float[1]
    ) / direction_norm_squared

    candidates: dict[tuple[int, int, int], dict[str, Any]] = {}
    for denominator in _RADICAL_DENOMINATORS:
        for b in range(-12, 13):
            a = int(round(denominator * target_parameter - b * math.sqrt(2.0)))
            if abs(a) > 24:
                continue
            parameter = qsqrt2_from_coefficients(a, b, denominator)
            parameter_key = qsqrt2_canonical_coefficients(parameter)
            coordinate = (
                through[0] + parameter * direction[0],
                through[1] + parameter * direction[1],
            )
            if not all(
                -1e-9 <= float(value) <= float(side_length) + 1e-9
                for value in coordinate
            ):
                continue
            fitted_pixel = (
                float(coordinate[0] / side_length) * maximum,
                float(coordinate[1] / side_length) * maximum,
            )
            residual = math.hypot(
                fitted_pixel[0] - observed[0],
                fitted_pixel[1] - observed[1],
            )
            complexity = (
                qsqrt2_complexity(coordinate[0])
                + qsqrt2_complexity(coordinate[1])
            )
            candidate = {
                "coordinate": coordinate,
                "fitted_pixel": fitted_pixel,
                "residual_px": residual,
                "complexity": complexity,
                "parameter": parameter,
                "parameter_complexity": qsqrt2_complexity(parameter),
                "score": residual + 0.15 * complexity,
            }
            previous = candidates.get(parameter_key)
            if previous is None or (
                candidate["score"],
                candidate["residual_px"],
                candidate["complexity"],
            ) < (
                previous["score"],
                previous["residual_px"],
                previous["complexity"],
            ):
                candidates[parameter_key] = candidate

    within_tolerance = [
        candidate
        for candidate in candidates.values()
        if candidate["residual_px"] <= tolerance
    ]
    if not within_tolerance:
        return None
    within_complexity = [
        candidate
        for candidate in within_tolerance
        if candidate["complexity"] <= _MAX_LINE_CONSTRAINED_POINT_COMPLEXITY
    ]
    candidate = min(
        within_complexity or within_tolerance,
        key=lambda item: (
            item["score"],
            item["residual_px"],
            item["complexity"],
            qsqrt2_canonical_coefficients(item["parameter"]),
        ),
    )
    coordinate = candidate["coordinate"]
    fit_is_selectable = (
        candidate["complexity"] <= _MAX_LINE_CONSTRAINED_POINT_COMPLEXITY
    )
    return {
        "observed_point_px": [round(observed[0], 6), round(observed[1], 6)],
        "fitted_point_px": [
            round(float(candidate["fitted_pixel"][0]), 6),
            round(float(candidate["fitted_pixel"][1]), 6),
        ],
        "project_coordinate": [
            qsqrt2_to_mapping(coordinate[0]),
            qsqrt2_to_mapping(coordinate[1]),
        ],
        "coordinate_expression": [
            qsqrt2_expression(coordinate[0]),
            qsqrt2_expression(coordinate[1]),
        ],
        "fit_residual_px": round(float(candidate["residual_px"]), 6),
        "fit_tolerance_px": round(float(tolerance), 6),
        "algebraic_complexity": int(candidate["complexity"]),
        "fit_is_selectable": fit_is_selectable,
        "fit_block_reason": (
            None if fit_is_selectable else "algebraic_complexity_too_high"
        ),
        "fit_constraint": "existing_exact_incident_crease",
        "fit_constraint_crease_id": str(crease.id),
        "fit_parameter": qsqrt2_to_mapping(candidate["parameter"]),
        "fit_parameter_complexity": int(candidate["parameter_complexity"]),
    }


def _fit_topology_point(
    entity: GeometryEntity,
    side_length: Qsqrt2,
    maximum: float,
    *,
    graph: ConstructionGraph | None = None,
) -> dict[str, Any] | None:
    raw_point = entity.observed_geometry.get("point_px")
    if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
        return None
    try:
        observed = (float(raw_point[0]), float(raw_point[1]))
    except (TypeError, ValueError):
        return None
    x_fit = _fit_project_axis(observed[0], side_length, maximum)
    y_fit = _fit_project_axis(observed[1], side_length, maximum)
    residual = math.hypot(
        float(x_fit["fitted_pixel"]) - observed[0],
        float(y_fit["fitted_pixel"]) - observed[1],
    )
    try:
        observed_tolerance = float(
            entity.observed_geometry.get("match_tolerance_px", 1.75)
        )
    except (TypeError, ValueError):
        observed_tolerance = 1.75
    tolerance = min(3.2, max(1.25, observed_tolerance))
    if graph is not None:
        constrained = _line_constrained_topology_point_fit(
            entity,
            graph,
            side_length,
            maximum,
            observed,
            tolerance,
        )
        if constrained is not None:
            return constrained
    complexity = int(x_fit["complexity"]) + int(y_fit["complexity"])
    coordinates = [
        {key: value for key, value in x_fit.items() if key not in {"fitted_pixel", "residual_px"}},
        {key: value for key, value in y_fit.items() if key not in {"fitted_pixel", "residual_px"}},
    ]
    within_paper = all(
        -1e-9 <= float(qsqrt2_from_mapping(value)) <= float(side_length) + 1e-9
        for value in coordinates
    )
    fit_is_selectable = (
        within_paper
        and residual <= tolerance
        and complexity <= _MAX_FREE_POINT_COMPLEXITY
    )
    return {
        "observed_point_px": [round(observed[0], 6), round(observed[1], 6)],
        "fitted_point_px": [x_fit["fitted_pixel"], y_fit["fitted_pixel"]],
        "project_coordinate": coordinates,
        "coordinate_expression": [x_fit["expression"], y_fit["expression"]],
        "fit_residual_px": round(float(residual), 6),
        "fit_tolerance_px": round(float(tolerance), 6),
        "algebraic_complexity": complexity,
        "fit_is_selectable": fit_is_selectable,
        "fit_block_reason": (
            None
            if fit_is_selectable
            else "outside_paper"
            if not within_paper
            else "algebraic_complexity_too_high"
            if complexity > _MAX_FREE_POINT_COMPLEXITY
            else "coordinate_residual_too_large"
        ),
    }


def _rank_next_topology_point_candidates(
    graph: ConstructionGraph,
    propagation: Mapping[str, Any],
    side_length: Qsqrt2,
    *,
    maximum: float,
) -> list[dict[str, Any]]:
    """Expose only existing internal points touching unresolved creases.

    A compact coordinate is displayed for every retained candidate.  Selection
    is enabled only when a trial on a copied graph reduces the unresolved
    crease count; the trial cannot add a point, crease, or direction.
    """

    unresolved_ids = {
        str(item)
        for item in propagation.get("unresolved_crease_entity_ids", [])
    }
    baseline = int(propagation.get("unresolved_crease_count", 0) or 0)
    if baseline <= 0 or not unresolved_ids:
        return []
    candidates: list[dict[str, Any]] = []
    for entity in graph.geometry_entities.values():
        if entity.kind != "point" or entity.is_exact:
            continue
        if entity.observed_geometry.get("boundary_sides"):
            continue
        incident_unresolved = [
            crease
            for crease in graph.incident_entities(entity.id)
            if crease.kind == "crease" and str(crease.id) in unresolved_ids
        ]
        if not incident_unresolved:
            continue
        fitted = _fit_topology_point(
            entity,
            side_length,
            maximum,
            graph=graph,
        )
        if fitted is None:
            continue
        projected_gain = 0
        projected_unresolved = baseline
        block_reason = fitted.get("fit_block_reason")
        if fitted.get("fit_is_selectable"):
            trial_graph = copy.deepcopy(graph)
            trial_graph.exactify_geometry(
                entity.id,
                {
                    "source": "guided_internal_topology_point_trial",
                    "project_coordinate": list(fitted["project_coordinate"]),
                    "side_length": qsqrt2_to_mapping(side_length),
                    "exact_generation": 0,
                    "observed_residual_px": fitted["fit_residual_px"],
                },
            )
            trial = propagate_exact_geometry(trial_graph, maximum=maximum)
            projected_unresolved = int(
                trial.get("unresolved_crease_count", baseline) or 0
            )
            projected_gain = max(0, baseline - projected_unresolved)
            if projected_gain <= 0:
                block_reason = "no_unresolved_crease_gain"
        selectable = bool(fitted.get("fit_is_selectable") and projected_gain > 0)
        candidates.append(
            {
                "id": str(entity.id),
                "label": (
                    "原图折痕交点"
                    if entity.observed_geometry.get("point_kind") == "line_intersection"
                    else "原图有限线端点"
                ),
                "source": "existing_raw_topology_point",
                "point_kind": entity.observed_geometry.get("point_kind"),
                "selection_basis": "incident_unresolved_existing_creases",
                "incident_unresolved_crease_count": len(incident_unresolved),
                "incident_unresolved_crease_ids": [
                    str(crease.id) for crease in incident_unresolved
                ],
                **fitted,
                "selectable": selectable,
                "selection_block_reason": None if selectable else block_reason,
                "projected_new_crease_count": projected_gain,
                "projected_unexplained_observations": projected_unresolved,
            }
        )
    point_kind_order = {"line_intersection": 0, "finite_endpoint": 1}
    candidates.sort(
        key=lambda item: (
            not bool(item.get("selectable")),
            -int(item.get("projected_new_crease_count", 0)),
            -int(item.get("incident_unresolved_crease_count", 0)),
            point_kind_order.get(str(item.get("point_kind")), 2),
            float(item.get("fit_residual_px", math.inf)),
            int(item.get("algebraic_complexity", 10**9)),
            str(item.get("id") or ""),
        )
    )
    for priority, candidate in enumerate(candidates[:64], start=1):
        candidate["next_priority"] = priority
    return candidates[:64]


def _add_guided_topology_point_operation(
    graph: ConstructionGraph,
    details: dict[Hashable, dict[str, Any]],
    candidate: Mapping[str, Any],
    side_length: Qsqrt2,
    *,
    selection_round: int,
    automatic: bool = False,
) -> ConstructionOperation:
    point_id = str(candidate["id"])
    source = (
        "guided_automatic_topology_point"
        if automatic
        else "guided_internal_topology_point"
    )
    round_key = "automatic_fit_order" if automatic else "selection_round"
    entity = next(
        item
        for item in graph.geometry_entities.values()
        if item.kind == "point" and str(item.id) == point_id
    )
    graph.exactify_geometry(
        entity.id,
        {
            "source": source,
            "project_coordinate": list(candidate["project_coordinate"]),
            "side_length": qsqrt2_to_mapping(side_length),
            "exact_generation": 0,
            "observed_residual_px": candidate["fit_residual_px"],
            f"guided_{round_key}": int(selection_round),
        },
    )
    metadata_key = (
        "guided_automatic_point_fits"
        if automatic
        else "guided_internal_point_selections"
    )
    entity.metadata.setdefault(metadata_key, []).append(
        {
            round_key: int(selection_round),
            "fit_residual_px": candidate["fit_residual_px"],
        }
    )
    operation = ConstructionOperation(
        id=(source, point_id, int(selection_round)),
        kind=source,
        parents=(),
        outputs=(entity.id,),
        generation=0,
        independent_parameters=0,
    )
    graph.add_operation(operation)
    details[operation.id] = {
        "provenance": source,
        round_key: int(selection_round),
        "automatic": bool(automatic),
        "topology_point_id": point_id,
        "observed_point_px": list(candidate["observed_point_px"]),
        "fitted_point_px": list(candidate["fitted_point_px"]),
        "coordinate_expression": list(candidate["coordinate_expression"]),
        "fit_residual_px": candidate["fit_residual_px"],
        "projected_new_crease_count": candidate["projected_new_crease_count"],
    }
    return operation


def _automatically_fit_topology_points(
    graph: ConstructionGraph,
    details: dict[Hashable, dict[str, Any]],
    side_length: Qsqrt2,
    *,
    maximum: float,
) -> tuple[
    ConstructionGraph,
    dict[Hashable, dict[str, Any]],
    list[ConstructionOperation],
    list[dict[str, Any]],
    list[Mapping[str, Any]],
]:
    """Greedily exactify only observed points that prove useful on a trial graph.

    Candidate ranking already exactifies each existing topology point on a copy
    and keeps it selectable only when deterministic propagation resolves at
    least one additional observed crease.  This loop commits the best such
    trial, recomputes the frontier, and stops when no verified gain remains.
    It cannot add a point, crease, or direction that was absent from the raw
    finite topology.
    """

    operations: list[ConstructionOperation] = []
    history: list[dict[str, Any]] = []
    reports: list[Mapping[str, Any]] = []
    current_report = propagate_exact_geometry(graph, maximum=maximum)
    reports.append(current_report)
    fit_order = 0

    while int(current_report.get("unresolved_crease_count", 0) or 0) > 0:
        baseline = int(current_report.get("unresolved_crease_count", 0) or 0)
        candidates = _rank_next_topology_point_candidates(
            graph,
            current_report,
            side_length,
            maximum=maximum,
        )
        committed = False
        for candidate in candidates:
            if not candidate.get("selectable"):
                continue
            trial_graph = copy.deepcopy(graph)
            trial_details = copy.deepcopy(details)
            trial_order = fit_order + 1
            operation = _add_guided_topology_point_operation(
                trial_graph,
                trial_details,
                candidate,
                side_length,
                selection_round=trial_order,
                automatic=True,
            )
            trial_report = propagate_exact_geometry(
                trial_graph,
                maximum=maximum,
            )
            remaining = int(
                trial_report.get("unresolved_crease_count", baseline) or 0
            )
            if remaining >= baseline:
                continue

            graph = trial_graph
            details = trial_details
            current_report = trial_report
            fit_order = trial_order
            operations.append(operation)
            history.append(
                {
                    "automatic_fit_order": fit_order,
                    "id": str(candidate["id"]),
                    "label": str(
                        candidate.get("label") or "observed topology point"
                    ),
                    "kind": "topology_point",
                    "point_kind": candidate.get("point_kind"),
                    "observed_point_px": list(candidate["observed_point_px"]),
                    "fitted_point_px": list(candidate["fitted_point_px"]),
                    "coordinate_expression": list(
                        candidate["coordinate_expression"]
                    ),
                    "fit_residual_px": candidate["fit_residual_px"],
                    "resolved_crease_count": baseline - remaining,
                    "remaining_unresolved_crease_count": remaining,
                }
            )
            reports.append(trial_report)
            committed = True
            break
        if not committed:
            break

    return graph, details, operations, history, reports


def _combine_propagation_reports(
    reports: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if not reports:
        return {"enabled": False, "reason": "propagation_not_run"}
    combined = dict(reports[-1])
    events = [
        dict(event)
        for report in reports
        for event in report.get("events", [])
        if isinstance(event, Mapping)
    ]
    rejections: Counter[str] = Counter()
    for report in reports:
        raw = report.get("rejection_counts")
        if isinstance(raw, Mapping):
            rejections.update({str(key): int(value) for key, value in raw.items()})
    propagated_points = len(
        {event.get("entity_id") for event in events if event.get("kind") == "exactify_existing_point"}
    )
    propagated_creases = len(
        {event.get("entity_id") for event in events if event.get("kind") == "exactify_existing_crease"}
    )
    combined.update(
        {
            "initial_exact_point_count": max(
                0, int(combined.get("final_exact_point_count", 0)) - propagated_points
            ),
            "initial_exact_crease_count": max(
                0, int(combined.get("final_exact_crease_count", 0)) - propagated_creases
            ),
            "propagated_point_count": propagated_points,
            "propagated_crease_count": propagated_creases,
            "frontier_pop_count": sum(
                int(report.get("frontier_pop_count", 0) or 0) for report in reports
            ),
            "duration_ms": round(
                sum(float(report.get("duration_ms", 0.0) or 0.0) for report in reports),
                3,
            ),
            "rejection_counts": dict(sorted(rejections.items())),
            "events": events,
            "propagation_pass_count": len(reports),
        }
    )
    return combined


def _rank_next_relation_candidates(
    catalog: list[dict[str, Any]],
    selected_ids: list[str],
    graph: ConstructionGraph,
    anchors: Mapping[int, Mapping[str, Any]],
    details: Mapping[Hashable, Mapping[str, Any]],
    *,
    maximum: float,
    baseline_unresolved: int,
    require_existing_incidence: bool,
) -> list[dict[str, Any]]:
    """Rank unselected relations only by their gain on unresolved creases.

    Each trial reuses the already exactified incidence graph, adds one declared
    boundary relation, and runs the same deterministic frontier.  It never
    creates a direction or crease; the trial only estimates which human choice
    could explain more of the finite observations already present.
    """

    if baseline_unresolved <= 0 or not selected_ids:
        return []
    selected_set = set(selected_ids)
    selected_index = {str(item.get("id")): item for item in catalog}
    gauge_key = _relation_side_length_key(selected_index[selected_ids[0]])
    ranked: list[dict[str, Any]] = []
    for relation in catalog:
        relation_id = str(relation.get("id") or "")
        if not relation_id or relation_id in selected_set:
            continue
        if _relation_side_length_key(relation) != gauge_key:
            continue
        trial_graph = copy.deepcopy(graph)
        trial_details = {key: dict(value) for key, value in details.items()}
        try:
            _, direct_seed_count = _add_guided_relation_operations(
                trial_graph,
                anchors,
                trial_details,
                relation,
                require_existing_incidence=require_existing_incidence,
                selection_round=len(selected_ids) + 1,
            )
        except ValueError:
            continue
        if direct_seed_count <= 0:
            continue
        trial_propagation = propagate_exact_geometry(
            trial_graph,
            maximum=maximum,
        )
        if not trial_propagation.get("enabled"):
            continue
        projected_unresolved = int(
            trial_propagation.get("unresolved_crease_count", baseline_unresolved)
            or 0
        )
        projected_gain = max(0, baseline_unresolved - projected_unresolved)
        if projected_gain <= 0:
            continue
        candidate = dict(relation)
        candidate.update(
            {
                "selection_basis": "unresolved_existing_creases",
                "direct_unresolved_seed_count": int(direct_seed_count),
                "projected_new_crease_count": int(projected_gain),
                "projected_unexplained_observations": projected_unresolved,
                "compatible_global_side_length": True,
            }
        )
        ranked.append(candidate)
    ranked.sort(key=_next_relation_rank_key)
    ranked = ranked[:24]
    for next_priority, item in enumerate(ranked, start=1):
        item["next_priority"] = next_priority
    return ranked


def _finite_rank_number(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) else fallback


def _next_relation_rank_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """Prefer simple, well-fitted gauges after equal projected total gain.

    Projected gain already measures the whole deterministic propagation trial.
    A larger number of direct seeds therefore must not outrank a dramatically
    simpler global Q(sqrt(2)) gauge that closes the same number of creases.
    """

    gauge = item.get("recommended_coordinate_gauge")
    gauge_score = (
        _finite_rank_number(gauge.get("score"), math.inf)
        if isinstance(gauge, Mapping)
        else math.inf
    )
    residual = _finite_rank_number(
        item.get(
            "algebraic_max_residual_px",
            item.get(
                "fitted_geometry_max_residual_px",
                item.get("max_residual_px", math.inf),
            ),
        ),
        math.inf,
    )
    return (
        -int(item.get("projected_new_crease_count", 0) or 0),
        gauge_score,
        residual,
        -int(item.get("direct_unresolved_seed_count", 0) or 0),
        int(item.get("priority", 10**9) or 10**9),
        str(item.get("id") or ""),
    )


def build_guided_boundary_report(
    result: Mapping[str, Any],
    selection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Propagate an ordered human selection history on one observed graph.

    Every request rebuilds the same incidence graph from immutable observations,
    replays one ordered list of boundary relations and confirmed topology points,
    and runs the same deterministic exact frontier.  Undo is therefore just a
    shorter step list; no parallel reconstruction state is retained.
    """

    raw_report = result.get("raw_crease_evidence")
    raw_available = (
        isinstance(raw_report, Mapping)
        and bool(raw_report.get("enabled", True))
        and bool(raw_report.get("lines"))
    )
    trace = [
        item
        for item in list(result.get("playback_trace") or [])
        if isinstance(item, Mapping)
    ]
    mode = "guided_boundary_relation_v5" if raw_available else "guided_boundary_relation_v4"
    if not raw_available and not trace:
        return {
            "enabled": False,
            "mode": mode,
            "reason": "no_observed_crease_evidence",
        }
    selection_steps, invalid_selection_steps = _selection_steps(selection)
    catalog = build_boundary_relation_catalog(result)
    selected_relations, missing_relation_ids = _selected_relations(catalog, selection)
    if (
        invalid_selection_steps
        or not selected_relations
        or missing_relation_ids
    ):
        return {
            "enabled": False,
            "mode": mode,
            "reason": "invalid_guided_boundary_relation",
            "invalid_selection_steps": invalid_selection_steps,
            "invalid_relation_ids": missing_relation_ids,
            "available_relation_ids": [item["id"] for item in catalog],
        }
    selected_relation_ids = [str(item["id"]) for item in selected_relations]
    global_side_length_key = _relation_side_length_key(selected_relations[0])
    incompatible_relation_ids = [
        str(item["id"])
        for item in selected_relations[1:]
        if _relation_side_length_key(item) != global_side_length_key
    ]
    if incompatible_relation_ids:
        return {
            "enabled": False,
            "mode": mode,
            "reason": "incompatible_relation_coordinate_gauge",
            "selected_relation_ids": selected_relation_ids,
            "incompatible_relation_ids": incompatible_relation_ids,
        }

    first_gauge = selected_relations[0].get("recommended_coordinate_gauge")
    global_side_length = (
        dict(first_gauge.get("side_length"))
        if isinstance(first_gauge, Mapping)
        and isinstance(first_gauge.get("side_length"), Mapping)
        else None
    )
    try:
        exact_side_length = (
            qsqrt2_from_mapping(global_side_length)
            if isinstance(global_side_length, Mapping)
            else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        exact_side_length = None
    if (
        any(step["kind"] == "topology_point" for step in selection_steps)
        and (not raw_available or exact_side_length is None)
    ):
        return {
            "enabled": False,
            "mode": mode,
            "reason": "guided_topology_point_requires_raw_graph_and_global_scale",
            "selected_relation_ids": selected_relation_ids,
        }

    topology_report: dict[str, Any] | None = None
    require_existing_incidence = False
    if raw_available:
        assert isinstance(raw_report, Mapping)
        graph, anchors, topology_report = build_raw_crease_topology_graph(raw_report)
        if not topology_report.get("enabled"):
            return {
                "enabled": False,
                "mode": mode,
                "reason": str(
                    topology_report.get("reason") or "raw_topology_unavailable"
                ),
                "raw_topology": topology_report,
            }
        details: dict[Hashable, dict[str, Any]] = {}
        suppressed_roots = 0
        require_existing_incidence = True
        observed_graph_source = "raw_image_finite_line_evidence"
    else:
        graph, _, anchors, details = _legacy_trace_graph(trace)
        graph, suppressed_roots = _constrain_initial_sources(graph, details)
        observed_graph_source = "playback_trace"
    maximum = float(_analysis_size(result) - 1)
    relation_index = {str(item.get("id") or ""): item for item in catalog}
    relation_operations: list[ConstructionOperation] = []
    relation_history_records: list[dict[str, Any]] = []
    guided_candidate_counts: list[int] = []
    selected_point_operations: list[ConstructionOperation] = []
    selected_point_history: list[dict[str, Any]] = []
    propagation_reports: list[Mapping[str, Any]] = []
    for selection_round, step in enumerate(selection_steps, start=1):
        if step["kind"] == "boundary_relation":
            relation = relation_index[step["id"]]
            try:
                relation_operation, guided_candidates = _add_guided_relation_operations(
                    graph,
                    anchors,
                    details,
                    relation,
                    require_existing_incidence=require_existing_incidence,
                    selection_round=selection_round,
                )
            except ValueError as error:
                return {
                    "enabled": False,
                    "mode": mode,
                    "reason": "conflicting_selected_relation_geometry",
                    "selected_relation_ids": selected_relation_ids,
                    "conflicting_relation_id": str(relation.get("id") or ""),
                    "detail": str(error),
                }
            relation_operations.append(relation_operation)
            guided_candidate_counts.append(guided_candidates)
            relation_history_records.append(
                {
                    "selection_round": selection_round,
                    "id": str(relation.get("id") or ""),
                    "label": str(relation.get("label") or relation.get("id") or ""),
                    "kind": relation.get("kind"),
                    "side": relation.get("side"),
                    "direct_seed_crease_count": int(guided_candidates),
                }
            )
            continue

        assert exact_side_length is not None
        prefix_propagation = propagate_exact_geometry(graph, maximum=maximum)
        propagation_reports.append(prefix_propagation)
        prefix_unexplained = int(
            prefix_propagation.get("unresolved_crease_count", 0) or 0
        )
        prefix_relation_ids = [
            str(item["id"]) for item in relation_history_records
        ]
        higher_priority_relations = _rank_next_relation_candidates(
            catalog,
            prefix_relation_ids,
            graph,
            anchors,
            details,
            maximum=maximum,
            baseline_unresolved=prefix_unexplained,
            require_existing_incidence=require_existing_incidence,
        )
        if higher_priority_relations:
            return {
                "enabled": False,
                "mode": mode,
                "reason": "boundary_relation_has_priority",
                "invalid_topology_point_id": step["id"],
                "selection_round": selection_round,
                "available_relation_ids": [
                    str(candidate["id"])
                    for candidate in higher_priority_relations
                ],
            }
        available_points = _rank_next_topology_point_candidates(
            graph,
            prefix_propagation,
            exact_side_length,
            maximum=maximum,
        )
        selected_point = next(
            (
                candidate
                for candidate in available_points
                if candidate.get("id") == step["id"]
                and candidate.get("selectable")
            ),
            None,
        )
        if selected_point is None:
            return {
                "enabled": False,
                "mode": mode,
                "reason": "invalid_guided_topology_point",
                "invalid_topology_point_id": step["id"],
                "selection_round": selection_round,
                "available_topology_point_ids": [
                    str(candidate["id"])
                    for candidate in available_points
                    if candidate.get("selectable")
                ],
            }
        point_operation = _add_guided_topology_point_operation(
            graph,
            details,
            selected_point,
            exact_side_length,
            selection_round=selection_round,
        )
        selected_point_operations.append(point_operation)
        selected_point_history.append(
            {
                "selection_round": selection_round,
                "id": str(selected_point["id"]),
                "label": str(selected_point.get("label") or "原图拓扑点"),
                "kind": "topology_point",
                "point_kind": selected_point.get("point_kind"),
                "observed_point_px": list(selected_point["observed_point_px"]),
                "fitted_point_px": list(selected_point["fitted_point_px"]),
                "coordinate_expression": list(selected_point["coordinate_expression"]),
                "fit_residual_px": selected_point["fit_residual_px"],
                "direct_seed_crease_count": int(
                    selected_point.get("projected_new_crease_count", 0)
                ),
            }
        )

    automatic_point_operations: list[ConstructionOperation] = []
    automatic_point_history: list[dict[str, Any]] = []
    if (
        raw_available
        and exact_side_length is not None
        and sum(guided_candidate_counts) > 0
    ):
        (
            graph,
            details,
            automatic_point_operations,
            automatic_point_history,
            automatic_reports,
        ) = _automatically_fit_topology_points(
            graph,
            details,
            exact_side_length,
            maximum=maximum,
        )
        propagation_reports.extend(automatic_reports)
    else:
        propagation_reports.append(
            propagate_exact_geometry(graph, maximum=maximum)
        )
    geometry_propagation = _combine_propagation_reports(propagation_reports)
    relation_summaries = [
        _operation_summary(operation, details)
        for operation in relation_operations
    ]
    selected_point_summaries = [
        _operation_summary(operation, details)
        for operation in selected_point_operations
    ]
    automatic_point_summaries = [
        _operation_summary(operation, details)
        for operation in automatic_point_operations
    ]
    guided_operations = [
        _operation_summary(operation, details)
        for operation in graph.operations.values()
        if details.get(operation.id, {}).get("provenance")
        == "guided_boundary_relation_ray"
    ]
    guided_operations.sort(
        key=lambda item: (
            int(item.get("selection_round", 0) or 0),
            int(item.get("target_trace_id", -1)),
        )
    )
    propagation_operations = [
        {"provenance": "deterministic_exact_frontier", **dict(item)}
        for item in geometry_propagation.get("events", [])
        if isinstance(item, Mapping)
    ]
    operations = [
        *relation_summaries,
        *selected_point_summaries,
        *automatic_point_summaries,
        *guided_operations,
        *propagation_operations,
    ]
    unexplained = int(geometry_propagation.get("unresolved_crease_count", 0) or 0)
    guided_candidates = sum(guided_candidate_counts)
    if not guided_candidates:
        status = "no_matching_observed_creases"
    elif not geometry_propagation.get("enabled"):
        status = "exact_propagation_unavailable"
    elif unexplained:
        status = "partial_propagation"
    else:
        status = "complete_propagation"
    evidence_sources = {
        str(relation.get("evidence_source") or "")
        for relation in selected_relations
    }
    evidence_note = (
        "候选关系来自原图有限折痕拓扑或独立边界扫描；像素接触只作证据，所选坐标已整体拟合到 Q(√2) 精确关系。"
        if evidence_sources
        & {"raw_image_directional_scan", "raw_image_finite_topology"}
        else "候选关系来自严格结果的纸边接触轨迹，属于旧结果兼容路径。"
    )
    crease_entities = [
        entity
        for entity in graph.geometry_entities.values()
        if entity.kind == "crease"
    ]
    unresolved_crease_entity_ids = [
        str(item)
        for item in geometry_propagation.get("unresolved_crease_entity_ids", [])
    ]
    unresolved_crease_set = set(unresolved_crease_entity_ids)
    unresolved_crease_summaries = [
        {
            "id": str(entity.id),
            "raw_line_id": entity.observed_geometry.get("raw_line_id"),
            "trace_id": entity.observed_geometry.get("trace_id"),
            "direction_index": entity.observed_geometry.get("direction_index"),
            "angle_deg": entity.observed_geometry.get("angle_deg"),
        }
        for entity in crease_entities
        if str(entity.id) in unresolved_crease_set
    ]
    next_relation_candidates = _rank_next_relation_candidates(
        catalog,
        selected_relation_ids,
        graph,
        anchors,
        details,
        maximum=maximum,
        baseline_unresolved=unexplained,
        require_existing_incidence=require_existing_incidence,
    )
    next_topology_point_candidates: list[dict[str, Any]] = []
    if (
        unexplained > 0
        and not next_relation_candidates
        and raw_available
        and exact_side_length is not None
    ):
        next_topology_point_candidates = _rank_next_topology_point_candidates(
            graph,
            geometry_propagation,
            exact_side_length,
            maximum=maximum,
        )
    selectable_topology_point_count = sum(
        bool(candidate.get("selectable"))
        for candidate in next_topology_point_candidates
    )
    phase = (
        "complete_existing_creases"
        if unexplained == 0
        else "awaiting_additional_relation"
        if next_relation_candidates
        else "awaiting_topology_point"
        if selectable_topology_point_count
        else "manual_point_unavailable"
    )
    geometry_snapshot = graph.geometry_snapshot()
    direction_mismatches = [
        str(entity.id)
        for entity in crease_entities
        if entity.exact_geometry
        and entity.exact_geometry.get("direction_index")
        != entity.observed_geometry.get("direction_index")
    ]
    geometry_snapshot["invariants"] = {
        "observed_crease_count": sum(
            observed_graph_source in entity.evidence_sources for entity in crease_entities
        ),
        "exactified_existing_crease_count": sum(
            bool(entity.exact_geometry)
            and observed_graph_source in entity.evidence_sources
            for entity in crease_entities
        ),
        "invented_crease_count": sum(
            observed_graph_source not in entity.evidence_sources
            for entity in crease_entities
        ),
        "invented_direction_count": len(direction_mismatches),
        "direction_mismatch_entity_ids": direction_mismatches,
    }
    output_note = (
        "本阶段只解释原图已有有限折痕，尚未生成 CP；不会把缺少的三等分点或折痕自动补造出来。"
        if raw_available
        else "本阶段只重排已有严格结果的构造解释，不改 strict CP，也不会把缺少的三等分点补造出来。"
    )
    selected_relation_history = relation_history_records
    selected_topology_point_ids = [
        str(item["id"]) for item in selected_point_history
    ]
    automatic_topology_point_ids = [
        str(item["id"]) for item in automatic_point_history
    ]
    selection_history = sorted(
        [
            *(
                {
                    **item,
                    "step_kind": "boundary_relation",
                }
                for item in selected_relation_history
            ),
            *(
                {
                    **item,
                    "step_kind": "topology_point",
                }
                for item in selected_point_history
            ),
        ],
        key=lambda item: int(item.get("selection_round", 0) or 0),
    )
    report = {
        "enabled": True,
        "mode": mode,
        "output_unchanged": True,
        "status": status,
        "phase": phase,
        "observed_graph_source": observed_graph_source,
        "raw_topology": topology_report,
        "selected_relation": selected_relations[-1],
        "selected_relations": selected_relations,
        "selected_relation_ids": selected_relation_ids,
        "selected_relation_history": selected_relation_history,
        "selected_topology_point_ids": selected_topology_point_ids,
        "selected_topology_point_history": selected_point_history,
        "automatic_topology_point_ids": automatic_topology_point_ids,
        "automatic_topology_point_history": automatic_point_history,
        "automatic_topology_point_count": len(automatic_point_history),
        "selection_steps": [dict(step) for step in selection_steps],
        "selection_history": selection_history,
        "selection_round": len(selection_steps),
        "can_undo": bool(selection_steps),
        "global_side_length": global_side_length,
        "required_observations": max(
            0,
            len(crease_entities)
            - int(
                geometry_propagation.get(
                    "pruned_paper_boundary_tangent_count",
                    0,
                )
                or 0
            ),
        ),
        "unexplained_observations": unexplained,
        "unresolved_crease_entity_ids": unresolved_crease_entity_ids,
        "unresolved_crease_summaries": unresolved_crease_summaries,
        "next_relation_candidates": next_relation_candidates,
        "next_relation_candidate_count": len(next_relation_candidates),
        "next_topology_point_candidates": next_topology_point_candidates,
        "next_topology_point_candidate_count": len(next_topology_point_candidates),
        "selectable_topology_point_candidate_count": selectable_topology_point_count,
        "next_selection_required": unexplained > 0,
        "manual_point_selection_required": (
            unexplained > 0 and not next_relation_candidates
        ),
        "suppressed_unselected_root_operations": suppressed_roots,
        "legacy_search_executed": False,
        "guided_candidate_ray_count": guided_candidates,
        "guided_seed_ray_count": len(guided_operations),
        "guided_selected_ray_count": int(
            geometry_propagation.get("final_exact_crease_count", 0) or 0
        ),
        "propagated_existing_crease_count": int(
            geometry_propagation.get("propagated_crease_count", 0) or 0
        ),
        "relation_used_by_selected_route": bool(guided_operations),
        "all_selected_relations_used": all(
            count > 0 for count in guided_candidate_counts
        ),
        "selected_operations": operations,
        "selected_guided_operations": guided_operations,
        "selected_guided_point_operations": selected_point_summaries,
        "automatic_guided_point_operations": automatic_point_summaries,
        "geometry_propagation": geometry_propagation,
        "geometry_graph": geometry_snapshot,
        "notes": [
            "观测点、观测折痕及其精确几何现在位于同一张点—折痕关联图；精确化不会复制另一套图。",
            "旧 beam/组合搜索不再执行；旧操作只作为来源记录保留。",
            "全部人工选择按边界关系或内部拓扑点组成一条有序步骤链；撤销后以缩短的步骤列表确定性重算，不保存平行结果。",
            "系统先列出仍能减少未解释折痕的同尺度边界关系；只有边界续选耗尽后，才显示连接未解释折痕的既有内部拓扑点。",
            "内部点的 Q(√2) 坐标是对像素观测的带复杂度约束拟合；界面显示残差，只有复制图试算确实减少已有未解释折痕时才允许人工确认。",
            "关系点只绑定穿过该点的已有折痕，精确方向沿用对应观测轨迹，不枚举或生成八个方向。",
            "原图拓扑模式还要求关系点与折痕已经存在有限区间入射；无限延长线恰好穿点不会被激活。",
            "精确传播使用有界前沿：精确点只激活已有入射折痕，精确折痕只求已有交点或纸边接触；残差过大或候选冲突即停止该分支。",
            output_note,
            evidence_note,
        ],
    }
    line_type_assignments = (
        selection.get("segment_line_types")
        if isinstance(selection, Mapping)
        and isinstance(selection.get("segment_line_types"), Mapping)
        else None
    )
    finite_topology = build_finite_endpoint_closed_topology(report)
    if finite_topology.get("enabled", False):
        report["finite_topology"] = finite_topology
        report["finite_endpoint_closure"] = finite_topology.get(
            "endpoint_closure", {}
        )
    else:
        report["finite_endpoint_closure"] = finite_topology
    base_output_contract = build_guided_cp_output_contract(
        report,
        segment_line_types=line_type_assignments,
    )
    angle_candidates = build_constrained_angle_candidates(
        raw_report if isinstance(raw_report, Mapping) else None,
        base_output_contract,
    )
    angle_repair, repaired_output_contract = build_transactional_angle_repair(
        base_output_contract,
        angle_candidates,
    )
    output_contract = repaired_output_contract or base_output_contract
    report["cp_output_contract"] = output_contract
    report["construction_angle_candidates"] = angle_candidates
    report["construction_angle_repair"] = angle_repair
    # Persist only assignments belonging to the observed topology. Generated
    # repair/split IDs exist solely in the effective output contract and must
    # not be sent back as unknown user assignments on the next guided replay.
    report["segment_line_type_assignments"] = base_output_contract[
        "segment_line_type_assignments"
    ]
    report["output_ready"] = bool(output_contract["output_ready"])
    report["checks_passed"] = bool(output_contract["checks_passed"])
    report["cp_available"] = bool(output_contract["cp_available"])
    report["cp"] = output_contract["cp"]
    report["output_unchanged"] = not report["cp_available"]
    return report


def build_guided_boundary_report_json(result_json: str, selection_json: str) -> str:
    result = json.loads(result_json or "{}")
    selection = json.loads(selection_json or "{}")
    return json.dumps(
        build_guided_boundary_report(result, selection),
        ensure_ascii=False,
        separators=(",", ":"),
    )


__all__ = [
    "build_boundary_relation_catalog",
    "build_boundary_relation_catalog_from_points",
    "build_guided_boundary_report",
    "build_guided_boundary_report_json",
]
