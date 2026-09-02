"""Separate raster observations from construction-backed topology facts.

The geometry graph deliberately keeps observed and exact-looking coordinates on
the same entities for propagation.  Not every exact-looking coordinate is a
construction proof, however: a coordinate fitted directly to a raster point is
still only an observation hypothesis.  This module derives a separate, read-only
proof layer by following only selected boundary relations and deterministic
consequences whose parents are already proved.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


_MODE = "construction_proof_topology_v1"
_FIT_ONLY_SOURCES = {
    "guided_internal_topology_point",
    "guided_automatic_topology_point",
    "guided_internal_topology_point_trial",
}


def _entities(graph: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("id")): item
        for item in graph.get("entities", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }


def _derived_parents(
    entity: Mapping[str, Any],
) -> tuple[str | None, list[str], bool]:
    exact = entity.get("exact_geometry")
    if not isinstance(exact, Mapping) or not exact:
        return None, [], False
    source = str(exact.get("source") or "")
    if source == "existing_incident_crease_from_exact_point":
        parent = str(exact.get("source_point_id") or "")
        return source, ([parent] if parent else []), False
    if source == "existing_crease_intersection":
        parents = [str(item) for item in exact.get("parent_entity_ids", []) if str(item)]
        return source, parents, False
    if source == "existing_crease_paper_boundary_intersection":
        raw_parents = [str(item) for item in exact.get("parent_entity_ids", []) if str(item)]
        parents = [item for item in raw_parents if not item.startswith("paper_boundary:")]
        has_boundary = any(item.startswith("paper_boundary:") for item in raw_parents)
        return source, parents, has_boundary
    return source or None, [], False


def _endpoint_is_proved(
    segment: Mapping[str, Any],
    endpoint: str,
    proved_points: set[str],
    crease_is_proved: bool,
) -> bool:
    point_id = str(segment.get(f"{endpoint}_point_id") or "")
    if point_id in proved_points:
        return True
    override = segment.get(f"{endpoint}_exact_project_coordinate")
    details = segment.get("endpoint_closure")
    detail = details.get(endpoint) if isinstance(details, Mapping) else None
    return bool(
        crease_is_proved
        and override is not None
        and isinstance(detail, Mapping)
        and detail.get("target_kind") == "known_paper_boundary_intersection"
        and detail.get("source")
        == "selected_exact_crease_known_paper_boundary_intersection"
    )


def build_construction_proof_topology(
    geometry_graph: Mapping[str, Any] | None,
    observation_topology: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify exact geometry without promoting raster fits into proof facts."""

    if not isinstance(geometry_graph, Mapping):
        return {"enabled": False, "mode": _MODE, "reason": "missing_geometry_graph"}
    if not isinstance(observation_topology, Mapping) or not observation_topology.get(
        "enabled", False
    ):
        return {
            "enabled": False,
            "mode": _MODE,
            "reason": "missing_observation_topology",
        }

    entities = _entities(geometry_graph)
    proved: set[str] = set()
    statuses: dict[str, dict[str, Any]] = {}
    pending: dict[str, tuple[str | None, list[str], bool]] = {}

    for entity_id, entity in entities.items():
        exact = entity.get("exact_geometry")
        if not isinstance(exact, Mapping) or not exact:
            statuses[entity_id] = {
                "id": entity_id,
                "kind": entity.get("kind"),
                "status": "unresolved",
                "reason": "no_exact_geometry",
            }
            continue
        source = str(exact.get("source") or "")
        relation_id = str(exact.get("source_relation_id") or "")
        if relation_id:
            proved.add(entity_id)
            statuses[entity_id] = {
                "id": entity_id,
                "kind": entity.get("kind"),
                "status": "proved",
                "proof_kind": "selected_boundary_relation",
                "source_relation_id": relation_id,
                "parent_entity_ids": [],
            }
            continue
        if source in _FIT_ONLY_SOURCES:
            statuses[entity_id] = {
                "id": entity_id,
                "kind": entity.get("kind"),
                "status": "observed_fit_only",
                "reason": "raster_coordinate_fit_is_not_construction_proof",
                "exact_geometry_source": source,
            }
            continue
        pending[entity_id] = _derived_parents(entity)

    changed = True
    while changed:
        changed = False
        for entity_id, (source, parents, has_boundary) in list(pending.items()):
            expected_parent_count = 1 if source in {
                "existing_incident_crease_from_exact_point",
                "existing_crease_paper_boundary_intersection",
            } else 2 if source == "existing_crease_intersection" else 0
            if (
                expected_parent_count
                and len(parents) >= expected_parent_count
                and all(parent in proved for parent in parents)
                and (source != "existing_crease_paper_boundary_intersection" or has_boundary)
            ):
                proved.add(entity_id)
                statuses[entity_id] = {
                    "id": entity_id,
                    "kind": entities[entity_id].get("kind"),
                    "status": "proved",
                    "proof_kind": source,
                    "parent_entity_ids": parents,
                    "uses_known_paper_boundary": has_boundary,
                }
                del pending[entity_id]
                changed = True

    for entity_id, (source, parents, has_boundary) in pending.items():
        known_source = source in {
            "existing_incident_crease_from_exact_point",
            "existing_crease_intersection",
            "existing_crease_paper_boundary_intersection",
        }
        statuses[entity_id] = {
            "id": entity_id,
            "kind": entities[entity_id].get("kind"),
            "status": "unproved",
            "reason": (
                "unproved_parent_entities"
                if known_source
                else "unknown_exact_geometry_source"
            ),
            "exact_geometry_source": source,
            "parent_entity_ids": parents,
            "unproved_parent_entity_ids": [item for item in parents if item not in proved],
            "uses_known_paper_boundary": has_boundary,
        }

    proved_points = {
        entity_id
        for entity_id in proved
        if entities.get(entity_id, {}).get("kind") == "point"
    }
    proved_creases = {
        entity_id
        for entity_id in proved
        if entities.get(entity_id, {}).get("kind") == "crease"
    }
    segment_records: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    segments = [
        item
        for item in observation_topology.get("segments", [])
        if isinstance(item, Mapping)
    ]
    for segment in segments:
        segment_id = str(segment.get("id") or "")
        crease_id = str(segment.get("crease_entity_id") or "")
        crease_is_proved = crease_id in proved_creases
        start_is_proved = _endpoint_is_proved(
            segment, "start", proved_points, crease_is_proved
        )
        end_is_proved = _endpoint_is_proved(
            segment, "end", proved_points, crease_is_proved
        )
        reasons: list[str] = []
        if not crease_is_proved:
            reasons.append("crease_not_construction_proved")
        if not start_is_proved:
            reasons.append("start_endpoint_not_construction_proved")
        if not end_is_proved:
            reasons.append("end_endpoint_not_construction_proved")
        for reason in reasons:
            reason_counts[reason] += 1
        segment_records.append(
            {
                "id": segment_id,
                "status": "proved" if not reasons else "observed_only",
                "crease_entity_id": crease_id,
                "start_point_id": str(segment.get("start_point_id") or ""),
                "end_point_id": str(segment.get("end_point_id") or ""),
                "reasons": reasons,
            }
        )

    proved_segment_ids = sorted(
        item["id"] for item in segment_records if item["status"] == "proved"
    )
    observed_only_segment_ids = sorted(
        item["id"] for item in segment_records if item["status"] != "proved"
    )
    return {
        "enabled": True,
        "mode": _MODE,
        "status": "complete" if segments and not observed_only_segment_ids else "partial",
        "proof_complete": bool(segments) and not observed_only_segment_ids,
        "observation_topology_mode": observation_topology.get("mode"),
        "observation_segment_count": len(segments),
        "proved_segment_count": len(proved_segment_ids),
        "observed_only_segment_count": len(observed_only_segment_ids),
        "proved_point_ids": sorted(proved_points),
        "proved_crease_ids": sorted(proved_creases),
        "proved_segment_ids": proved_segment_ids,
        "observed_only_segment_ids": observed_only_segment_ids,
        "segment_records": segment_records,
        "entity_records": [statuses[item] for item in sorted(statuses)],
        "unproved_segment_reason_counts": dict(sorted(reason_counts.items())),
        "invariants": {
            "observation_topology_is_not_mutated": True,
            "raster_coordinate_fit_is_not_a_proof_root": True,
            "derived_fact_requires_all_parent_facts_proved": True,
            "paper_boundary_is_the_only_parentless_geometric_constraint": True,
        },
    }


__all__ = ["build_construction_proof_topology"]
