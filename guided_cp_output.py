"""CP serialization and diagnostics for the user-guided finite crease graph.

This module never extends a line or creates an internal segment. It serializes
the currently observed finite topology as a draft while independently checking
geometry, construction proof, line-type, boundary, and cAMV verification gates.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Mapping

from construction_proof_topology import build_construction_proof_topology
from exact_qsqrt2 import Qsqrt2
from foldability import GeometrySegment, audit_camv_structure
from qsqrt2_coordinates import (
    qsqrt2_canonical_coefficients,
    qsqrt2_from_mapping,
)


ExactPoint = tuple[Qsqrt2, Qsqrt2]

_RAW_SEGMENT_SOURCE = "raw_image_finite_line_evidence"
_TRUSTED_LINE_TYPE_SOURCES = {
    "explicit_segment_assignment",
    "source_image_color_evidence",
    "source_image_default_mountain",
    "user_confirmed",
}


def _exact_point(raw: Any) -> ExactPoint | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return qsqrt2_from_mapping(raw[0]), qsqrt2_from_mapping(raw[1])
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _point_key(point: ExactPoint) -> tuple[tuple[int, int, int], ...]:
    return (
        qsqrt2_canonical_coefficients(point[0]),
        qsqrt2_canonical_coefficients(point[1]),
    )


def _direction_vector(direction_index: int) -> ExactPoint:
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


def _cross(first: ExactPoint, second: ExactPoint) -> Qsqrt2:
    return first[0] * second[1] - first[1] * second[0]


def _on_exact_line(point: ExactPoint, through: ExactPoint, direction_index: int) -> bool:
    delta = (point[0] - through[0], point[1] - through[1])
    return _cross(delta, _direction_vector(direction_index)) == Qsqrt2()


def _inside_paper(point: ExactPoint, side_length: Qsqrt2) -> bool:
    epsilon = 1e-9
    return all(
        -epsilon <= float(value) <= float(side_length) + epsilon
        for value in point
    )


def _on_boundary(point: ExactPoint, side_length: Qsqrt2) -> bool:
    zero = Qsqrt2()
    return point[0] in {zero, side_length} or point[1] in {zero, side_length}


def _project_to_pixel(
    point: ExactPoint,
    side_length: Qsqrt2,
    maximum: float,
) -> tuple[float, float]:
    return (
        float(point[0] / side_length) * maximum,
        float(point[1] / side_length) * maximum,
    )


def _project_to_cp(point: ExactPoint, side_length: Qsqrt2) -> tuple[float, float]:
    return (
        -200.0 + 400.0 * float(point[0] / side_length),
        -200.0 + 400.0 * float(point[1] / side_length),
    )


def _cp_value(value: float) -> str:
    if abs(value) < 5e-10:
        value = 0.0
    if abs(value - 200.0) < 5e-9:
        value = 200.0
    if abs(value + 200.0) < 5e-9:
        value = -200.0
    return f"{value:.12g}"


def _serialize_cp(rows: list[tuple[int, float, float, float, float]]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (
            row[0],
            round(row[2], 9),
            round(row[1], 9),
            round(row[4], 9),
            round(row[3], 9),
        ),
    )
    return "".join(
        f"{line_type} {_cp_value(x1)} {_cp_value(y1)} "
        f"{_cp_value(x2)} {_cp_value(y2)}\n"
        for line_type, x1, y1, x2, y2 in ordered
    )


def _pixel_point_to_cp(
    raw: Any,
    maximum: float,
    boundary_sides: Any = (),
) -> tuple[float, float] | None:
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) < 2
        or not math.isfinite(maximum)
        or maximum <= 0
    ):
        return None
    try:
        x = -200.0 + 400.0 * float(raw[0]) / maximum
        y = -200.0 + 400.0 * float(raw[1]) / maximum
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    sides = {str(item) for item in boundary_sides or ()}
    if "left" in sides:
        x = -200.0
    if "right" in sides:
        x = 200.0
    if "top" in sides:
        y = -200.0
    if "bottom" in sides:
        y = 200.0
    return x, y


def _draft_boundary_rows(
    endpoint_points: list[tuple[float, float]],
) -> tuple[list[tuple[int, float, float, float, float]], dict[str, int]]:
    side_values: dict[str, set[float]] = {
        side: {-200.0, 200.0} for side in ("top", "right", "bottom", "left")
    }

    def remember(side: str, value: float) -> None:
        side_values[side].add(round(float(value), 12))

    for x, y in endpoint_points:
        if abs(y + 200.0) <= 1e-7:
            remember("top", x)
        if abs(x - 200.0) <= 1e-7:
            remember("right", y)
        if abs(y - 200.0) <= 1e-7:
            remember("bottom", x)
        if abs(x + 200.0) <= 1e-7:
            remember("left", y)

    rows: list[tuple[int, float, float, float, float]] = []
    counts: dict[str, int] = {}
    for side in ("top", "right", "bottom", "left"):
        values = sorted(side_values[side])
        if side in {"bottom", "left"}:
            values.reverse()
        counts[side] = max(0, len(values) - 1)
        for first, second in zip(values, values[1:]):
            if side == "top":
                row = (1, first, -200.0, second, -200.0)
            elif side == "right":
                row = (1, 200.0, first, 200.0, second)
            elif side == "bottom":
                row = (1, first, 200.0, second, 200.0)
            else:
                row = (1, -200.0, first, -200.0, second)
            rows.append(row)
    return rows, counts


def _segment_line_type(
    segment: Mapping[str, Any],
    assignments: Mapping[str, Any],
) -> tuple[int | None, str | None, str | None]:
    """Return line type, provenance, and an optional rejection code."""

    segment_id = str(segment.get("id") or "")
    explicitly_assigned = segment_id in assignments
    raw = assignments.get(segment_id) if explicitly_assigned else segment.get("line_type")
    source: str | None = None
    if isinstance(raw, Mapping):
        source = str(raw.get("source") or "") or None
        raw = raw.get("line_type")
    elif explicitly_assigned:
        source = "explicit_segment_assignment"
    else:
        raw_source = segment.get("line_type_source")
        source = str(raw_source) if raw_source else None
    if raw is None and not explicitly_assigned:
        return 2, "source_image_default_mountain", None
    try:
        line_type = int(raw)
    except (TypeError, ValueError):
        return None, source, "missing_segment_line_type"
    if line_type not in {2, 3}:
        return None, source, "invalid_segment_line_type"
    if source not in _TRUSTED_LINE_TYPE_SOURCES:
        return line_type, source, "untrusted_segment_line_type_source"
    return line_type, source, None


def _build_topology_draft_cp(
    topology: Mapping[str, Any],
    assignments: Mapping[str, Any],
    *,
    endpoint_cp_overrides: Mapping[tuple[str, str], tuple[float, float]] | None = None,
    observed_cp_points: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Serialize every currently representable raw segment without gating it."""

    if not isinstance(topology, Mapping) or not topology.get("enabled", False):
        return {
            "cp": None,
            "cp_available": False,
            "internal_segment_count": 0,
            "boundary_segment_count": 0,
            "boundary_segment_counts_by_side": {},
            "observed_endpoint_fallback_count": 0,
            "red_fallback_segment_count": 0,
            "skipped_internal_segment_ids": [],
            "_rows": [],
        }

    try:
        maximum = float(topology.get("maximum_coordinate_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        maximum = 0.0
    if not math.isfinite(maximum) or maximum <= 0:
        maximum = 0.0

    point_records = {
        str(item.get("id") or ""): item
        for item in topology.get("points", [])
        if isinstance(item, Mapping) and str(item.get("id") or "")
    }
    exact_overrides = endpoint_cp_overrides or {}
    observed_overrides = observed_cp_points or {}
    internal_rows: list[tuple[int, float, float, float, float]] = []
    endpoint_points: list[tuple[float, float]] = []
    skipped_ids: list[str] = []
    observed_endpoint_fallback_count = 0
    red_fallback_segment_count = 0

    def endpoint_cp(
        segment: Mapping[str, Any],
        segment_id: str,
        endpoint_name: str,
    ) -> tuple[tuple[float, float] | None, bool]:
        override = exact_overrides.get((segment_id, endpoint_name))
        if override is not None:
            try:
                point = float(override[0]), float(override[1])
            except (TypeError, ValueError, IndexError):
                point = (math.nan, math.nan)
            if all(math.isfinite(value) for value in point):
                return point, False

        current_id = str(segment.get(f"{endpoint_name}_point_id") or "")
        observed_id = str(
            segment.get(f"observed_{endpoint_name}_point_id") or current_id
        )
        for point_id in dict.fromkeys((observed_id, current_id)):
            record = point_records.get(point_id)
            if record is not None:
                point = _pixel_point_to_cp(
                    record.get("point"),
                    maximum,
                    record.get("boundary_sides"),
                )
                if point is not None:
                    return point, True
            point = observed_overrides.get(point_id)
            if point is not None:
                return point, True
        return None, False

    raw_segments = [
        item for item in topology.get("segments", []) if isinstance(item, Mapping)
    ]
    for segment in raw_segments:
        segment_id = str(segment.get("id") or "")
        start_cp, start_observed = endpoint_cp(segment, segment_id, "start")
        end_cp, end_observed = endpoint_cp(segment, segment_id, "end")
        if start_cp is None or end_cp is None:
            skipped_ids.append(segment_id)
            continue

        line_type, _, type_error = _segment_line_type(segment, assignments)
        if type_error is not None or line_type not in {2, 3}:
            line_type = 2
            red_fallback_segment_count += 1
        if start_cp == end_cp:
            skipped_ids.append(segment_id)
            continue
        internal_rows.append((line_type, *start_cp, *end_cp))
        endpoint_points.extend((start_cp, end_cp))
        observed_endpoint_fallback_count += int(start_observed) + int(end_observed)

    boundary_rows, boundary_counts = _draft_boundary_rows(endpoint_points)
    rows = [*boundary_rows, *internal_rows]
    cp = _serialize_cp(rows) if rows else None
    return {
        "cp": cp,
        "cp_available": bool(cp),
        "internal_segment_count": len(internal_rows),
        "boundary_segment_count": len(boundary_rows),
        "boundary_segment_counts_by_side": boundary_counts,
        "observed_endpoint_fallback_count": observed_endpoint_fallback_count,
        "red_fallback_segment_count": red_fallback_segment_count,
        "skipped_internal_segment_ids": sorted(skipped_ids),
        "_rows": rows,
    }


def build_raw_topology_draft_cp(
    topology: Mapping[str, Any],
    *,
    segment_line_types: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a downloadable CP directly from the currently observed topology."""

    draft = _build_topology_draft_cp(
        topology,
        segment_line_types if isinstance(segment_line_types, Mapping) else {},
    )
    return {key: value for key, value in draft.items() if key != "_rows"}


def _boundary_rows(
    exact_points: list[ExactPoint],
    side_length: Qsqrt2,
) -> tuple[list[tuple[int, float, float, float, float]], dict[str, int]]:
    zero = Qsqrt2()
    side_values: dict[str, dict[tuple[int, int, int], Qsqrt2]] = {
        side: {} for side in ("top", "right", "bottom", "left")
    }

    def remember(side: str, value: Qsqrt2) -> None:
        side_values[side][qsqrt2_canonical_coefficients(value)] = value

    for side in side_values:
        remember(side, zero)
        remember(side, side_length)
    for x, y in exact_points:
        if y == zero:
            remember("top", x)
        if x == side_length:
            remember("right", y)
        if y == side_length:
            remember("bottom", x)
        if x == zero:
            remember("left", y)

    rows: list[tuple[int, float, float, float, float]] = []
    counts: dict[str, int] = {}
    for side in ("top", "right", "bottom", "left"):
        values = sorted(side_values[side].values(), key=float)
        if side in {"bottom", "left"}:
            values.reverse()
        counts[side] = max(0, len(values) - 1)
        for first, second in zip(values, values[1:]):
            if side == "top":
                start, end = (first, zero), (second, zero)
            elif side == "right":
                start, end = (side_length, first), (side_length, second)
            elif side == "bottom":
                start, end = (first, side_length), (second, side_length)
            else:
                start, end = (zero, first), (zero, second)
            x1, y1 = _project_to_cp(start, side_length)
            x2, y2 = _project_to_cp(end, side_length)
            rows.append((1, x1, y1, x2, y2))
    return rows, counts


def build_guided_cp_output_contract(
    guided_report: Mapping[str, Any],
    *,
    segment_line_types: Mapping[str, Any] | None = None,
    max_endpoint_residual_px: float = 3.2,
) -> dict[str, Any]:
    """Materialize finite exact segments and emit CP only if every gate passes."""

    blockers: list[dict[str, Any]] = []

    def block(code: str, **detail: Any) -> None:
        blockers.append({"code": code, **detail})

    assignments = (
        segment_line_types if isinstance(segment_line_types, Mapping) else {}
    )
    graph = guided_report.get("geometry_graph")
    finite_topology = guided_report.get("finite_topology")
    topology = (
        finite_topology
        if isinstance(finite_topology, Mapping)
        and finite_topology.get("enabled", False)
        else guided_report.get("raw_topology")
    )
    if not guided_report.get("enabled", False):
        block("guided_report_disabled")
    if not isinstance(graph, Mapping):
        block("missing_geometry_graph")
        graph = {}
    if not isinstance(topology, Mapping) or not topology.get("enabled", False):
        block("missing_raw_finite_topology")
        topology = {}

    construction_proof = guided_report.get("construction_proof_topology")
    if not isinstance(construction_proof, Mapping) or not construction_proof.get(
        "enabled", False
    ):
        construction_proof = build_construction_proof_topology(graph, topology)
    observed_only_segment_ids = sorted(
        str(item)
        for item in construction_proof.get("observed_only_segment_ids", [])
        if str(item)
    )
    if not construction_proof.get("enabled", False):
        block(
            "missing_construction_proof_topology",
            reason=construction_proof.get("reason"),
        )
    elif observed_only_segment_ids:
        block(
            "unproved_observed_segments",
            count=len(observed_only_segment_ids),
            segment_ids=observed_only_segment_ids,
            reason_counts=dict(
                construction_proof.get("unproved_segment_reason_counts") or {}
            ),
        )

    raw_side_length = guided_report.get("global_side_length")
    try:
        side_length = (
            qsqrt2_from_mapping(raw_side_length)
            if isinstance(raw_side_length, Mapping)
            else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        side_length = None
    if side_length is None or float(side_length) <= 0:
        block("missing_or_invalid_side_length")

    entities = {
        str(item.get("id")): item
        for item in graph.get("entities", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    crease_entities = [
        item for item in entities.values() if item.get("kind") == "crease"
    ]
    point_entities = [
        item for item in entities.values() if item.get("kind") == "point"
    ]
    propagation = guided_report.get("geometry_propagation")
    unresolved_crease_count = (
        int(propagation.get("unresolved_crease_count", 0) or 0)
        if isinstance(propagation, Mapping)
        else len(crease_entities)
    )
    if unresolved_crease_count or guided_report.get("phase") != "complete_existing_creases":
        block(
            "unresolved_existing_creases",
            count=unresolved_crease_count,
            phase=guided_report.get("phase"),
        )

    exact_points_by_id: dict[str, ExactPoint] = {}
    for entity_id, entity in entities.items():
        if entity.get("kind") != "point":
            continue
        exact = entity.get("exact_geometry")
        point = _exact_point(
            exact.get("project_coordinate") if isinstance(exact, Mapping) else None
        )
        if point is not None:
            exact_points_by_id[entity_id] = point

    crease_exact_overrides = (
        topology.get("crease_exact_overrides")
        if isinstance(topology.get("crease_exact_overrides"), Mapping)
        else {}
    )

    def effective_exact_crease(
        entity_id: str,
        entity: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        exact = entity.get("exact_geometry")
        return exact if isinstance(exact, Mapping) else {}

    missing_exact_creases: set[str] = set()
    invented_crease_ids: set[str] = set()
    direction_mismatch_ids: set[str] = set()
    crease_residual_failures: list[dict[str, Any]] = []
    invalid_crease_override_ids = {str(item) for item in crease_exact_overrides}
    maximum = float(topology.get("maximum_coordinate_px", 0.0) or 0.0)
    for entity in crease_entities:
        entity_id = str(entity.get("id"))
        exact = effective_exact_crease(entity_id, entity)
        observed = entity.get("observed_geometry")
        if not isinstance(exact, Mapping):
            exact = {}
        if not isinstance(observed, Mapping):
            observed = {}
        through = _exact_point(exact.get("through_point_project"))
        try:
            exact_direction = int(exact.get("direction_index"))
            observed_direction = int(observed.get("direction_index"))
        except (TypeError, ValueError):
            exact_direction = observed_direction = -1
        if through is None or not 0 <= exact_direction < 8:
            missing_exact_creases.add(entity_id)
            continue
        if _RAW_SEGMENT_SOURCE not in set(entity.get("evidence_sources") or []):
            invented_crease_ids.add(entity_id)
        if exact_direction != observed_direction:
            direction_mismatch_ids.add(entity_id)
        if side_length is not None and maximum > 0:
            try:
                point_px = _project_to_pixel(through, side_length, maximum)
                angle = math.radians(float(observed.get("angle_deg")))
                offset = float(observed.get("line_offset_px"))
                tolerance = min(
                    3.2,
                    max(0.0, float(observed.get("match_tolerance_px", 0.85))),
                )
                residual = abs(
                    -math.sin(angle) * point_px[0]
                    + math.cos(angle) * point_px[1]
                    - offset
                )
                if residual > tolerance + 1e-9:
                    crease_residual_failures.append(
                        {
                            "id": entity_id,
                            "residual_px": round(residual, 6),
                            "tolerance_px": round(tolerance, 6),
                        }
                    )
            except (TypeError, ValueError, ZeroDivisionError):
                crease_residual_failures.append({"id": entity_id, "reason": "invalid_observation"})

    if missing_exact_creases:
        block(
            "missing_exact_creases",
            count=len(missing_exact_creases),
            crease_entity_ids=sorted(missing_exact_creases),
        )
    if invented_crease_ids:
        block(
            "crease_without_raw_image_provenance",
            count=len(invented_crease_ids),
            crease_entity_ids=sorted(invented_crease_ids),
        )
    if direction_mismatch_ids:
        block(
            "exact_observed_direction_mismatch",
            count=len(direction_mismatch_ids),
            crease_entity_ids=sorted(direction_mismatch_ids),
        )
    if crease_residual_failures:
        block(
            "crease_residual_exceeds_tolerance",
            count=len(crease_residual_failures),
            creases=crease_residual_failures,
        )
    if invalid_crease_override_ids:
        block(
            "invalid_crease_exact_overrides",
            count=len(invalid_crease_override_ids),
            crease_entity_ids=sorted(invalid_crease_override_ids),
        )

    topology_invariants = topology.get("invariants")
    topology_mode = topology.get("mode")
    trusted_raw_topology = topology_mode == "raw_finite_crease_topology_v1"
    endpoint_closure = topology.get("endpoint_closure")
    trusted_closed_topology = (
        topology_mode == "finite_endpoint_closed_topology_v1"
        and topology.get("source_topology_mode") == "raw_finite_crease_topology_v1"
        and isinstance(endpoint_closure, Mapping)
        and endpoint_closure.get("enabled", False)
    )
    trusted_endpoint_override_point_ids = {
        str(item.get("observed_point_id") or "")
        for item in (
            endpoint_closure.get("bindings", [])
            if trusted_closed_topology and isinstance(endpoint_closure, Mapping)
            else []
        )
        if (
            isinstance(item, Mapping)
            and (
                (
                    item.get("target_kind")
                    == "known_paper_boundary_intersection"
                    and item.get("source")
                    in {
                        "selected_exact_crease_known_paper_boundary_intersection",
                        "selected_exact_crease_source_verified_near_paper_boundary",
                    }
                )
                or (
                    item.get("target_kind")
                    == "proved_exact_crease_intersection"
                    and item.get("source")
                    == "proved_exact_crease_intersection_near_observed_endpoint"
                )
            )
            and str(item.get("observed_point_id") or "")
        )
    }
    if (
        not (trusted_raw_topology or trusted_closed_topology)
        or not isinstance(topology_invariants, Mapping)
        or int(topology_invariants.get("generated_point_count", 0)) != 0
        or int(topology_invariants.get("generated_crease_count", -1)) != 0
        or int(topology_invariants.get("generated_direction_count", -1)) != 0
        or int(topology_invariants.get("generated_segment_count", 0)) != 0
    ):
        block("untrusted_topology_provenance")

    raw_segments = [
        item
        for item in topology.get("segments", [])
        if isinstance(item, Mapping)
    ]
    if not raw_segments:
        block("no_raw_finite_segments")
    raw_segment_ids = {str(item.get("id") or "") for item in raw_segments}
    collapsed_segment_ids = {
        str(item.get("id") or "")
        for item in (
            endpoint_closure.get("collapsed_segments", [])
            if isinstance(endpoint_closure, Mapping)
            else []
        )
        if isinstance(item, Mapping)
    }
    suppressed_unanchored_segment_ids = {
        str(item.get("id") or "")
        for item in (
            endpoint_closure.get("suppressed_unanchored_segments", [])
            if isinstance(endpoint_closure, Mapping)
            else []
        )
        if isinstance(item, Mapping)
    }
    unknown_assignment_ids = sorted(
        str(item)
        for item in assignments
        if str(item)
        not in (
            raw_segment_ids
            | collapsed_segment_ids
            | suppressed_unanchored_segment_ids
        )
    )
    if unknown_assignment_ids:
        block(
            "unknown_segment_line_type_assignments",
            count=len(unknown_assignment_ids),
            segment_ids=unknown_assignment_ids,
        )

    unresolved_endpoint_ids: set[str] = set()
    missing_endpoint_evidence_ids: set[str] = set()
    endpoint_residual_failures: list[dict[str, Any]] = []
    residual_checked_ids: set[
        tuple[str, tuple[tuple[int, int, int], ...]]
    ] = set()
    untrusted_segment_ids: set[str] = set()
    untrusted_endpoint_override_segment_ids: set[str] = set()
    direction_invalid_segment_ids: set[str] = set()
    out_of_paper_segment_ids: set[str] = set()
    zero_length_segment_ids: set[str] = set()
    duplicate_segment_ids: set[str] = set()
    missing_line_type_ids: set[str] = set()
    invalid_line_type_ids: set[str] = set()
    untrusted_line_type_ids: set[str] = set()
    seen_segment_keys: dict[tuple[tuple[int, int, int], ...], str] = {}
    endpoint_degree: Counter[str] = Counter()
    candidate_segments: list[dict[str, Any]] = []
    accepted_line_type_assignments: dict[str, dict[str, Any]] = {}
    default_line_type_count = 0
    internal_rows: list[tuple[int, float, float, float, float]] = []
    finite_segment_exact_points: list[ExactPoint] = []
    endpoint_cp_overrides: dict[tuple[str, str], tuple[float, float]] = {}
    observed_cp_points: dict[str, tuple[float, float]] = {}
    proved_crease_ids = {
        str(item)
        for item in construction_proof.get("proved_crease_ids", [])
        if str(item)
    }

    def proved_parent_contains(
        point: ExactPoint | None,
        parent_id: str,
    ) -> bool:
        if point is None:
            return False
        exact = entities.get(parent_id, {}).get("exact_geometry")
        if not isinstance(exact, Mapping):
            return False
        through = _exact_point(exact.get("through_point_project"))
        try:
            direction = int(exact.get("direction_index"))
        except (TypeError, ValueError):
            return False
        return bool(
            through is not None
            and 0 <= direction < 8
            and _on_exact_line(point, through, direction)
        )

    for entity_id, entity in entities.items():
        if entity.get("kind") != "point":
            continue
        observed = entity.get("observed_geometry")
        if not isinstance(observed, Mapping):
            continue
        point = _pixel_point_to_cp(
            observed.get("point_px"),
            maximum,
            observed.get("boundary_sides"),
        )
        if point is not None:
            observed_cp_points[entity_id] = point

    for segment in raw_segments:
        segment_id = str(segment.get("id") or "")
        start_id = str(segment.get("start_point_id") or "")
        end_id = str(segment.get("end_point_id") or "")
        crease_id = str(segment.get("crease_entity_id") or "")
        if (
            segment.get("source") != _RAW_SEGMENT_SOURCE
            or crease_id not in entities
            or entities.get(crease_id, {}).get("kind") != "crease"
        ):
            untrusted_segment_ids.add(segment_id)
        try:
            coverage = float(segment.get("visible_coverage"))
            unsupported = float(segment.get("unsupported_length_px"))
            maximum_unsupported = float(
                topology.get("invariants", {}).get(
                    "maximum_segment_unsupported_length_px",
                    0.0,
                )
            )
            if coverage < 0.8 or unsupported > maximum_unsupported + 1e-9:
                untrusted_segment_ids.add(segment_id)
        except (TypeError, ValueError, AttributeError):
            untrusted_segment_ids.add(segment_id)

        line_type, line_type_source, type_error = _segment_line_type(
            segment, assignments
        )
        if type_error == "missing_segment_line_type":
            missing_line_type_ids.add(segment_id)
        elif type_error == "invalid_segment_line_type":
            invalid_line_type_ids.add(segment_id)
        elif type_error == "untrusted_segment_line_type_source":
            untrusted_line_type_ids.add(segment_id)
        else:
            accepted_line_type_assignments[segment_id] = {
                "line_type": line_type,
                "source": line_type_source,
            }
            if line_type_source == "source_image_default_mountain":
                default_line_type_count += 1

        start_override_raw = segment.get("start_exact_project_coordinate")
        end_override_raw = segment.get("end_exact_project_coordinate")
        start = _exact_point(start_override_raw)
        end = _exact_point(end_override_raw)
        endpoint_closure_detail = segment.get("endpoint_closure")
        for endpoint_name, raw_override, override_point in (
            ("start", start_override_raw, start),
            ("end", end_override_raw, end),
        ):
            if raw_override is None:
                continue
            detail = (
                endpoint_closure_detail.get(endpoint_name)
                if isinstance(endpoint_closure_detail, Mapping)
                else None
            )
            boundary_override_is_valid = bool(
                override_point is not None
                and side_length is not None
                and _on_boundary(override_point, side_length)
                and isinstance(detail, Mapping)
                and detail.get("target_kind")
                == "known_paper_boundary_intersection"
                and detail.get("source")
                in {
                    "selected_exact_crease_known_paper_boundary_intersection",
                    "selected_exact_crease_source_verified_near_paper_boundary",
                }
            )
            parent_ids = {
                str(item)
                for item in (
                    detail.get("parent_entity_ids", [])
                    if isinstance(detail, Mapping)
                    else []
                )
                if str(item)
            }
            intersection_override_is_valid = bool(
                override_point is not None
                and side_length is not None
                and _inside_paper(override_point, side_length)
                and isinstance(detail, Mapping)
                and detail.get("target_kind")
                == "proved_exact_crease_intersection"
                and detail.get("source")
                == "proved_exact_crease_intersection_near_observed_endpoint"
                and crease_id in parent_ids
                and len(parent_ids) >= 2
                and parent_ids <= proved_crease_ids
                and all(
                    proved_parent_contains(override_point, parent_id)
                    for parent_id in parent_ids
                )
            )
            if not (boundary_override_is_valid or intersection_override_is_valid):
                untrusted_endpoint_override_segment_ids.add(segment_id)
        if start is None:
            start = exact_points_by_id.get(start_id)
        if end is None:
            end = exact_points_by_id.get(end_id)
        if side_length is not None:
            if start is not None:
                endpoint_cp_overrides[(segment_id, "start")] = _project_to_cp(
                    start, side_length
                )
            if end is not None:
                endpoint_cp_overrides[(segment_id, "end")] = _project_to_cp(
                    end, side_length
                )
        if start is None:
            unresolved_endpoint_ids.add(start_id)
        if end is None:
            unresolved_endpoint_ids.add(end_id)
        if start is None or end is None or side_length is None:
            continue
        finite_segment_exact_points.extend((start, end))

        for point_id, point in ((start_id, start), (end_id, end)):
            endpoint_degree[point_id] += 1
            residual_key = (point_id, _point_key(point))
            if residual_key in residual_checked_ids:
                continue
            residual_checked_ids.add(residual_key)
            entity = entities.get(point_id, {})
            observed = entity.get("observed_geometry")
            observed_point = (
                observed.get("point_px") if isinstance(observed, Mapping) else None
            )
            if (
                not isinstance(observed_point, (list, tuple))
                or len(observed_point) < 2
                or maximum <= 0
            ):
                missing_endpoint_evidence_ids.add(point_id)
                continue
            try:
                fitted = _project_to_pixel(point, side_length, maximum)
                residual = math.dist(
                    fitted,
                    (float(observed_point[0]), float(observed_point[1])),
                )
            except (TypeError, ValueError, ZeroDivisionError):
                missing_endpoint_evidence_ids.add(point_id)
                continue
            if (
                residual > max(0.0, float(max_endpoint_residual_px)) + 1e-9
                and point_id not in trusted_endpoint_override_point_ids
            ):
                endpoint_residual_failures.append(
                    {
                        "id": point_id,
                        "residual_px": round(residual, 6),
                        "tolerance_px": round(float(max_endpoint_residual_px), 6),
                    }
                )

        if not _inside_paper(start, side_length) or not _inside_paper(end, side_length):
            out_of_paper_segment_ids.add(segment_id)
        if start == end:
            zero_length_segment_ids.add(segment_id)

        crease = entities.get(crease_id, {})
        exact_crease = effective_exact_crease(crease_id, crease)
        observed_crease = crease.get("observed_geometry")
        if not isinstance(exact_crease, Mapping):
            exact_crease = {}
        if not isinstance(observed_crease, Mapping):
            observed_crease = {}
        through = _exact_point(exact_crease.get("through_point_project"))
        try:
            exact_direction = int(exact_crease.get("direction_index"))
            observed_direction = int(observed_crease.get("direction_index"))
            segment_direction = int(segment.get("orientation"))
        except (TypeError, ValueError):
            exact_direction = observed_direction = segment_direction = -1
        if (
            through is None
            or not 0 <= exact_direction < 8
            or exact_direction != observed_direction
            or exact_direction != segment_direction
            or not _on_exact_line(start, through, exact_direction)
            or not _on_exact_line(end, through, exact_direction)
        ):
            direction_invalid_segment_ids.add(segment_id)

        first_key, second_key = sorted((_point_key(start), _point_key(end)))
        segment_key = (*first_key, *second_key)
        if segment_key in seen_segment_keys:
            duplicate_segment_ids.add(segment_id)
            duplicate_segment_ids.add(seen_segment_keys[segment_key])
        else:
            seen_segment_keys[segment_key] = segment_id

        start_cp = _project_to_cp(start, side_length)
        end_cp = _project_to_cp(end, side_length)
        candidate_segments.append(
            {
                "id": segment_id,
                "source": segment.get("source"),
                "crease_entity_id": crease_id,
                "start_point_id": start_id,
                "end_point_id": end_id,
                "observed_start_point_id": segment.get(
                    "observed_start_point_id", start_id
                ),
                "observed_end_point_id": segment.get(
                    "observed_end_point_id", end_id
                ),
                "orientation": segment_direction,
                "line_type": line_type,
                "line_type_source": line_type_source,
                "start_cp": [round(value, 12) for value in start_cp],
                "end_cp": [round(value, 12) for value in end_cp],
                "visible_coverage": segment.get("visible_coverage"),
            }
        )
        if line_type in {2, 3}:
            internal_rows.append((line_type, *start_cp, *end_cp))

    if unresolved_endpoint_ids:
        block(
            "unresolved_finite_segment_endpoints",
            count=len(unresolved_endpoint_ids),
            point_entity_ids=sorted(unresolved_endpoint_ids),
        )
    if missing_endpoint_evidence_ids:
        block(
            "missing_endpoint_image_evidence",
            count=len(missing_endpoint_evidence_ids),
            point_entity_ids=sorted(missing_endpoint_evidence_ids),
        )
    if endpoint_residual_failures:
        block(
            "endpoint_residual_exceeds_tolerance",
            count=len(endpoint_residual_failures),
            points=endpoint_residual_failures,
        )
    if untrusted_segment_ids:
        block(
            "untrusted_finite_segment_provenance",
            count=len(untrusted_segment_ids),
            segment_ids=sorted(untrusted_segment_ids),
        )
    if untrusted_endpoint_override_segment_ids:
        block(
            "untrusted_finite_endpoint_overrides",
            count=len(untrusted_endpoint_override_segment_ids),
            segment_ids=sorted(untrusted_endpoint_override_segment_ids),
        )
    if direction_invalid_segment_ids:
        block(
            "finite_segment_direction_mismatch",
            count=len(direction_invalid_segment_ids),
            segment_ids=sorted(direction_invalid_segment_ids),
        )
    if out_of_paper_segment_ids:
        block(
            "finite_segment_outside_paper",
            count=len(out_of_paper_segment_ids),
            segment_ids=sorted(out_of_paper_segment_ids),
        )
    if zero_length_segment_ids:
        block(
            "zero_length_finite_segment",
            count=len(zero_length_segment_ids),
            segment_ids=sorted(zero_length_segment_ids),
        )
    if duplicate_segment_ids:
        block(
            "duplicate_finite_segment",
            count=len(duplicate_segment_ids),
            segment_ids=sorted(duplicate_segment_ids),
        )
    if missing_line_type_ids:
        block(
            "missing_segment_line_types",
            count=len(missing_line_type_ids),
            segment_ids=sorted(missing_line_type_ids),
        )
    if invalid_line_type_ids:
        block(
            "invalid_segment_line_types",
            count=len(invalid_line_type_ids),
            segment_ids=sorted(invalid_line_type_ids),
        )
    if untrusted_line_type_ids:
        block(
            "untrusted_segment_line_type_provenance",
            count=len(untrusted_line_type_ids),
            segment_ids=sorted(untrusted_line_type_ids),
        )

    absorbed_observed_point_ids = {
        str(item)
        for item in (
            endpoint_closure.get("absorbed_observed_point_ids", [])
            if isinstance(endpoint_closure, Mapping)
            else []
        )
    }
    resolved_boundary_observed_point_ids = {
        str(item)
        for item in (
            endpoint_closure.get("resolved_boundary_observed_point_ids", [])
            if isinstance(endpoint_closure, Mapping)
            else []
        )
    }
    unresolved_boundary_contact_ids: set[str] = set()
    for entity in point_entities:
        observed = entity.get("observed_geometry")
        sides = observed.get("boundary_sides") if isinstance(observed, Mapping) else None
        entity_id = str(entity.get("id"))
        if (
            sides
            and entity_id not in exact_points_by_id
            and entity_id not in absorbed_observed_point_ids
            and entity_id not in resolved_boundary_observed_point_ids
        ):
            unresolved_boundary_contact_ids.add(entity_id)
    if unresolved_boundary_contact_ids:
        block(
            "unresolved_boundary_contacts",
            count=len(unresolved_boundary_contact_ids),
            point_entity_ids=sorted(unresolved_boundary_contact_ids),
        )

    boundary_rows: list[tuple[int, float, float, float, float]] = []
    boundary_counts: dict[str, int] = {}
    if side_length is not None:
        boundary_rows, boundary_counts = _boundary_rows(
            finite_segment_exact_points,
            side_length,
        )
        if any(boundary_counts.get(side, 0) < 1 for side in ("top", "right", "bottom", "left")):
            block("incomplete_paper_boundary")

    if not unresolved_endpoint_ids and len(candidate_segments) == len(raw_segments):
        dangling_ids = sorted(
            point_id
            for point_id, degree in endpoint_degree.items()
            if degree == 1
            and point_id in exact_points_by_id
            and not _on_boundary(exact_points_by_id[point_id], side_length)
        ) if side_length is not None else []
        if dangling_ids:
            block(
                "internal_dangling_segment_endpoints",
                count=len(dangling_ids),
                point_entity_ids=dangling_ids,
            )

    draft = _build_topology_draft_cp(
        topology,
        assignments,
        endpoint_cp_overrides=endpoint_cp_overrides,
        observed_cp_points=observed_cp_points,
    )
    cp_rows = draft["_rows"]
    draft_cp = draft["cp"]

    camv = None
    if cp_rows:
        camv = audit_camv_structure(
            [
                GeometrySegment(line_type, (x1, y1), (x2, y2), row=index)
                for index, (line_type, x1, y1, x2, y2) in enumerate(cp_rows)
            ],
            folding_types={2, 3},
            include_mv=True,
        )
        camv_violation_count = int(camv.get("violation_count", 0) or 0)
        if camv_violation_count:
            block(
                "camv_foldability_violations",
                count=camv_violation_count,
                rule_counts=dict(camv.get("rule_counts") or {}),
                violations=list(camv.get("violations") or []),
            )

    gate_codes = {
        "exact_crease_closure": {
            "guided_report_disabled",
            "missing_geometry_graph",
            "missing_or_invalid_side_length",
            "unresolved_existing_creases",
            "missing_exact_creases",
        },
        "construction_proof": {
            "missing_construction_proof_topology",
            "unproved_observed_segments",
        },
        "exact_endpoint_closure": {
            "unresolved_finite_segment_endpoints",
            "unresolved_boundary_contacts",
        },
        "observed_source_provenance": {
            "missing_raw_finite_topology",
            "untrusted_topology_provenance",
            "invalid_crease_exact_overrides",
            "no_raw_finite_segments",
            "crease_without_raw_image_provenance",
            "untrusted_finite_segment_provenance",
            "untrusted_finite_endpoint_overrides",
            "missing_endpoint_image_evidence",
        },
        "direction_consistency": {
            "exact_observed_direction_mismatch",
            "finite_segment_direction_mismatch",
        },
        "residual_tolerance": {
            "crease_residual_exceeds_tolerance",
            "endpoint_residual_exceeds_tolerance",
        },
        "finite_planar_geometry": {
            "finite_segment_outside_paper",
            "zero_length_finite_segment",
            "duplicate_finite_segment",
            "internal_dangling_segment_endpoints",
        },
        "segment_line_types": {
            "missing_segment_line_types",
            "invalid_segment_line_types",
            "untrusted_segment_line_type_provenance",
            "unknown_segment_line_type_assignments",
        },
        "paper_boundary_closure": {
            "unresolved_boundary_contacts",
            "incomplete_paper_boundary",
        },
        "flat_foldability": {
            "camv_foldability_violations",
        },
    }
    blocker_counts: Counter[str] = Counter()
    for item in blockers:
        try:
            affected = int(item.get("count", 1))
        except (TypeError, ValueError):
            affected = 1
        blocker_counts[str(item["code"])] += max(1, affected)
    gate_results = {
        name: {
            "passed": not any(code in blocker_counts for code in codes),
            "blocker_codes": sorted(code for code in codes if code in blocker_counts),
        }
        for name, codes in gate_codes.items()
    }
    checks_passed = bool(guided_report.get("enabled", False)) and not blockers
    # Validation and availability are intentionally separate.  A representable
    # draft remains useful for inspection in Oriedita even when endpoint or
    # cAMV diagnostics have not passed; callers must keep it visibly marked as
    # unverified instead of suppressing the serialized CP.
    cp = draft_cp
    cp_available = bool(cp)

    return {
        "enabled": True,
        "mode": "guided_finite_cp_output_contract_v1",
        "status": "ready" if checks_passed else "unverified",
        "output_ready": checks_passed,
        "checks_passed": checks_passed,
        "cp_available": cp_available,
        "cp": cp,
        "required_internal_segment_count": len(raw_segments),
        "candidate_internal_segment_count": len(candidate_segments),
        "typed_candidate_internal_segment_count": len(internal_rows),
        "draft_internal_segment_count": draft["internal_segment_count"],
        "draft_observed_endpoint_fallback_count": draft[
            "observed_endpoint_fallback_count"
        ],
        "draft_red_fallback_segment_count": draft["red_fallback_segment_count"],
        "draft_skipped_internal_segment_ids": draft[
            "skipped_internal_segment_ids"
        ],
        "suppressed_unanchored_segment_ids": sorted(
            suppressed_unanchored_segment_ids
        ),
        "boundary_segment_count": draft["boundary_segment_count"],
        "boundary_segment_counts_by_side": draft[
            "boundary_segment_counts_by_side"
        ],
        "checked_boundary_segment_count": len(boundary_rows),
        "checked_boundary_segment_counts_by_side": boundary_counts,
        "unresolved_endpoint_point_ids": sorted(unresolved_endpoint_ids),
        "unassigned_segment_ids": sorted(missing_line_type_ids),
        "segment_line_type_assignments": dict(
            sorted(accepted_line_type_assignments.items())
        ),
        "candidate_segments": candidate_segments,
        "construction_proof_topology": construction_proof,
        "gate_results": gate_results,
        "blocker_count": len(blockers),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "blockers": blockers,
        "soft_diagnostics": {
            "camv": camv,
            "camv_blocks_output": False,
            "camv_blocks_verification": True,
        },
        "invariants": {
            "old_cp_reused": False,
            "generated_internal_segment_count": 0,
            "generated_direction_count": 0,
            "default_line_type_count": default_line_type_count,
            "line_type_scope": "finite_segment_not_infinite_crease",
            "boundary_geometry_source": "known_square_paper",
            "finite_topology_mode": topology_mode,
            "collapsed_detector_linehead_count": len(collapsed_segment_ids),
            "suppressed_unanchored_segment_count": len(
                suppressed_unanchored_segment_ids
            ),
            "crease_placement_repair_count": len(crease_exact_overrides),
            "observation_and_construction_topology_separated": True,
        },
    }


__all__ = ["build_guided_cp_output_contract", "build_raw_topology_draft_cp"]
