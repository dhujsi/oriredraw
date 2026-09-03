"""Generate exact canonical ray candidates from construction-proved points.

This stage is deliberately geometry-only.  It neither reads raster angles nor
adds a crease to the proof topology.  Image evidence is allowed to rank these
candidates in a later stage, but can never choose a new direction or source
point here.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping

from exact_qsqrt2 import Qsqrt2
from qsqrt2_coordinates import qsqrt2_from_mapping, qsqrt2_to_mapping


ExactPoint = tuple[Qsqrt2, Qsqrt2]
_MODE = "proved_node_canonical_ray_candidates_v1"


def _exact_point(raw: Any) -> ExactPoint | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return qsqrt2_from_mapping(raw[0]), qsqrt2_from_mapping(raw[1])
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _direction_vector(direction_index: int) -> ExactPoint:
    one = Qsqrt2(1)
    zero = Qsqrt2()
    diagonal = Qsqrt2(1, 1)
    first_half = (
        (one, zero),
        (diagonal, one),
        (one, one),
        (one, diagonal),
        (zero, one),
        (-one, diagonal),
        (-one, one),
        (-diagonal, one),
    )
    if not 0 <= direction_index < 16:
        raise ValueError(f"invalid directed 22.5-degree index: {direction_index}")
    if direction_index < 8:
        return first_half[direction_index]
    opposite = first_half[direction_index - 8]
    return -opposite[0], -opposite[1]


def _point_key(point: ExactPoint) -> tuple[float, float]:
    return round(float(point[0]), 12), round(float(point[1]), 12)


def _boundary_exit(
    point: ExactPoint,
    direction: ExactPoint,
    side_length: Qsqrt2,
) -> tuple[ExactPoint, list[str], Qsqrt2] | None:
    zero = Qsqrt2()
    x, y = point
    dx, dy = direction

    # A boundary point may emit only into the paper.  Tangent and outward rays
    # are not internal crease candidates.
    if x == zero and dx <= zero:
        return None
    if x == side_length and dx >= zero:
        return None
    if y == zero and dy <= zero:
        return None
    if y == side_length and dy >= zero:
        return None

    hits: list[tuple[Qsqrt2, str]] = []
    if dx > zero:
        hits.append(((side_length - x) / dx, "right"))
    elif dx < zero:
        hits.append(((zero - x) / dx, "left"))
    if dy > zero:
        hits.append(((side_length - y) / dy, "bottom"))
    elif dy < zero:
        hits.append(((zero - y) / dy, "top"))
    positive = [(distance, side) for distance, side in hits if distance > zero]
    if not positive:
        return None
    distance = min((item[0] for item in positive), key=float)
    sides = sorted(
        side for candidate_distance, side in positive if candidate_distance == distance
    )
    endpoint = x + distance * dx, y + distance * dy
    return endpoint, sides, distance


def _directed_index(first: ExactPoint, second: ExactPoint) -> int | None:
    dx = float(second[0] - first[0])
    dy = float(second[1] - first[1])
    if math.hypot(dx, dy) <= 1e-12:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    index = int(round(angle / 22.5)) % 16
    expected = index * 22.5
    error = abs((angle - expected + 180.0) % 360.0 - 180.0)
    return index if error <= 1e-7 else None


def _candidate_id(point_id: str, direction_index: int) -> str:
    digest = hashlib.sha1(
        f"{point_id}|canonical-22.5|{direction_index}".encode("utf-8")
    ).hexdigest()[:12]
    return f"proof-ray-{digest}"


def build_proved_node_canonical_ray_candidates(
    geometry_graph: Mapping[str, Any] | None,
    topology: Mapping[str, Any] | None,
    construction_proof: Mapping[str, Any] | None,
    side_length_mapping: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return unapplied 22.5-degree rays rooted only at proved exact points."""

    if not isinstance(geometry_graph, Mapping):
        return {"enabled": False, "mode": _MODE, "reason": "missing_geometry_graph"}
    if not isinstance(topology, Mapping) or not topology.get("enabled", False):
        return {"enabled": False, "mode": _MODE, "reason": "missing_topology"}
    if not isinstance(construction_proof, Mapping) or not construction_proof.get(
        "enabled", False
    ):
        return {
            "enabled": False,
            "mode": _MODE,
            "reason": "missing_construction_proof",
        }
    try:
        side_length = (
            qsqrt2_from_mapping(side_length_mapping)
            if isinstance(side_length_mapping, Mapping)
            else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        side_length = None
    if side_length is None or side_length <= Qsqrt2():
        return {"enabled": False, "mode": _MODE, "reason": "missing_side_length"}

    entities = {
        str(item.get("id")): item
        for item in geometry_graph.get("entities", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    exact_points = {
        entity_id: point
        for entity_id, entity in entities.items()
        if entity.get("kind") == "point"
        and (point := _exact_point(
            (entity.get("exact_geometry") or {}).get("project_coordinate")
            if isinstance(entity.get("exact_geometry"), Mapping)
            else None
        ))
        is not None
    }
    proved_point_ids = {
        str(item)
        for item in construction_proof.get("proved_point_ids", [])
        if str(item) in exact_points
    }
    proved_segment_ids = {
        str(item) for item in construction_proof.get("proved_segment_ids", [])
    }
    occupied: dict[str, set[int]] = {point_id: set() for point_id in proved_point_ids}
    segments = [
        item for item in topology.get("segments", []) if isinstance(item, Mapping)
    ]
    for segment in segments:
        if str(segment.get("id") or "") not in proved_segment_ids:
            continue
        start_id = str(segment.get("start_point_id") or "")
        end_id = str(segment.get("end_point_id") or "")
        start = _exact_point(segment.get("start_exact_project_coordinate"))
        if start is None:
            start = exact_points.get(start_id)
        end = _exact_point(segment.get("end_exact_project_coordinate"))
        if end is None:
            end = exact_points.get(end_id)
        if start is None or end is None:
            continue
        if start_id in occupied:
            index = _directed_index(start, end)
            if index is not None:
                occupied[start_id].add(index)
        if end_id in occupied:
            index = _directed_index(end, start)
            if index is not None:
                occupied[end_id].add(index)

    entity_proof = {
        str(item.get("id")): item
        for item in construction_proof.get("entity_records", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    candidates: list[dict[str, Any]] = []
    boundary_rejected = 0
    for point_id in sorted(proved_point_ids):
        source = exact_points[point_id]
        for direction_index in range(16):
            if direction_index in occupied[point_id]:
                continue
            direction = _direction_vector(direction_index)
            exit_data = _boundary_exit(source, direction, side_length)
            if exit_data is None:
                boundary_rejected += 1
                continue
            endpoint, boundary_sides, distance = exit_data
            proof_record = entity_proof.get(point_id, {})
            candidates.append(
                {
                    "id": _candidate_id(point_id, direction_index),
                    "kind": "canonical_22_5_ray",
                    "status": "unapplied_candidate",
                    "source_point_id": point_id,
                    "source_point_project": [
                        qsqrt2_to_mapping(source[0]),
                        qsqrt2_to_mapping(source[1]),
                    ],
                    "source_point_proof_kind": proof_record.get("proof_kind"),
                    "parent_entity_ids": [point_id],
                    "directed_direction_index": direction_index,
                    "line_orientation_index": direction_index % 8,
                    "direction_deg": round(direction_index * 22.5, 6),
                    "direction_vector_project": [
                        qsqrt2_to_mapping(direction[0]),
                        qsqrt2_to_mapping(direction[1]),
                    ],
                    "search_endpoint_project": [
                        qsqrt2_to_mapping(endpoint[0]),
                        qsqrt2_to_mapping(endpoint[1]),
                    ],
                    "search_endpoint_boundary_sides": boundary_sides,
                    "maximum_parameter": qsqrt2_to_mapping(distance),
                    "generation_rule": "canonical_22_5_ray_from_proved_point",
                    "image_evidence_status": "not_evaluated",
                }
            )

    return {
        "enabled": True,
        "mode": _MODE,
        "status": "candidates_available" if candidates else "no_new_canonical_rays",
        "source_proved_point_count": len(proved_point_ids),
        "candidate_count": len(candidates),
        "occupied_proved_ray_count": sum(len(items) for items in occupied.values()),
        "boundary_outward_or_tangent_rejection_count": boundary_rejected,
        "candidates": candidates,
        "invariants": {
            "all_candidates_start_at_construction_proved_points": True,
            "all_directions_are_exact_22_5_degree_multiples": True,
            "raster_angles_choose_no_candidate_direction": True,
            "candidates_are_not_applied_to_topology": True,
            "search_intervals_end_at_known_paper_boundary": True,
            "existing_proved_rays_are_not_duplicated": True,
        },
    }


__all__ = ["build_proved_node_canonical_ray_candidates"]
