"""Planar topology from finite raw-image crease evidence.

Only observed line identities and their finite visible intervals are inputs.
Two infinite supporting lines are never connected merely because their
mathematical extensions cross: both lines must carry interval evidence at the
candidate point.  The result is the same ``ConstructionGraph`` used by exact
propagation, with ordered incidence on each crease and separately serialized
finite topology segments.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any, Hashable, Mapping

import numpy as np

from construction_search import ConstructionGraph, GeometryEntity
from reconstructor import ALLOWED_ANGLES, _boundary_hits


_BOUNDARY_SIDE = {"上": "top", "右": "right", "下": "bottom", "左": "left"}
_CANDIDATE_PRIORITY = {"line_intersection": 0, "boundary_contact": 1, "finite_endpoint": 2}


def _line_basis(orientation: int) -> tuple[np.ndarray, np.ndarray]:
    theta = ALLOWED_ANGLES[orientation]
    direction = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    return direction, np.array([-direction[1], direction[0]], dtype=float)


def _merge_intervals(intervals: list[list[float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for first, second in sorted(intervals, key=lambda item: item[0]):
        if not merged or first > merged[-1][1] + 1e-6:
            merged.append([float(first), float(second)])
        else:
            merged[-1][1] = max(merged[-1][1], float(second))
    return merged


def _interval_distance(intervals: list[list[float]], value: float) -> float:
    if not intervals:
        return math.inf
    return min(
        0.0
        if first - 1e-9 <= value <= second + 1e-9
        else min(abs(value - first), abs(value - second))
        for first, second in intervals
    )


def _interval_overlap(intervals: list[list[float]], first: float, second: float) -> float:
    if second <= first:
        return 0.0
    return sum(
        max(0.0, min(second, end) - max(first, start))
        for start, end in intervals
    )


def _raw_line_sort_key(item: Mapping[str, Any]) -> tuple[int, float, str]:
    try:
        orientation = int(item.get("orientation", -1))
    except (TypeError, ValueError):
        orientation = 99
    try:
        offset = float(item.get("observed_offset_px", 0.0))
    except (TypeError, ValueError):
        offset = math.inf
    return orientation, offset, str(item.get("id") or "")


def _parse_lines(report: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    try:
        size = max(2, int(report.get("analysis_size") or 0))
    except (TypeError, ValueError):
        size = 0
    if size <= 1:
        try:
            size = max(2, int(report.get("maximum_coordinate_px")) + 1)
        except (TypeError, ValueError):
            size = 512
    maximum = float(size - 1)
    parsed: list[dict[str, Any]] = []
    raw_lines = [
        item for item in list(report.get("lines") or []) if isinstance(item, Mapping)
    ]
    ordered = sorted(raw_lines, key=_raw_line_sort_key)
    for target_id, raw in enumerate(ordered):
        try:
            orientation = int(raw["orientation"])
            offset = float(raw["observed_offset_px"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= orientation < 8 or not math.isfinite(offset):
            continue
        direction, normal = _line_basis(orientation)
        intervals: list[list[float]] = []
        for item in list(raw.get("evidence_intervals_px") or []):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                first, second = sorted((float(item[0]), float(item[1])))
            except (TypeError, ValueError):
                continue
            if math.isfinite(first) and math.isfinite(second) and second - first >= 0.5:
                intervals.append([first, second])
        if not intervals:
            for item in list(raw.get("visible_segments_px") or []):
                if not isinstance(item, Mapping):
                    continue
                start, end = item.get("start"), item.get("end")
                if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)):
                    continue
                try:
                    first = float(direction @ np.asarray(start[:2], dtype=float))
                    second = float(direction @ np.asarray(end[:2], dtype=float))
                except (TypeError, ValueError):
                    continue
                intervals.append([min(first, second), max(first, second)])
        hits = _boundary_hits(offset, orientation, size)
        if len(hits) < 2:
            continue
        lower = min(float(hit[0]) for hit in hits)
        upper = max(float(hit[0]) for hit in hits)
        intervals = _merge_intervals(
            [
                [max(lower, first), min(upper, second)]
                for first, second in intervals
                if min(upper, second) - max(lower, first) >= 0.5
            ]
        )
        if not intervals:
            continue
        raw_id = str(raw.get("id") or f"raw-crease:{target_id}")
        parsed.append(
            {
                "target_id": target_id,
                "raw_id": raw_id,
                "crease_id": ("crease", "raw", raw_id),
                "orientation": orientation,
                "angle_deg": round(orientation * 22.5, 6),
                "offset": offset,
                "direction": direction,
                "normal": normal,
                "intervals": intervals,
                "support_fraction": float(raw.get("support_fraction", 0.0) or 0.0),
                "mean_confidence": float(raw.get("mean_confidence", 0.0) or 0.0),
                "source": dict(raw),
            }
        )
    return parsed, size


def _intersection(first: Mapping[str, Any], second: Mapping[str, Any]) -> np.ndarray | None:
    matrix = np.array([first["normal"], second["normal"]], dtype=float)
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) < 1e-8:
        return None
    return np.linalg.solve(
        matrix,
        np.array([float(first["offset"]), float(second["offset"])], dtype=float),
    )


def _candidate(
    kind: str,
    point: np.ndarray,
    line_ids: set[int],
    *,
    boundary_sides: set[str] | None = None,
    interval_gaps: Mapping[int, float] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "point": np.asarray(point, dtype=float),
        "line_ids": set(line_ids),
        "boundary_sides": set(boundary_sides or ()),
        "interval_gaps": dict(interval_gaps or {}),
    }


def _topology_candidates(
    lines: list[dict[str, Any]],
    size: int,
    *,
    incidence_margin: float,
    boundary_margin: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    maximum = float(size - 1)
    candidates: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for first_index, first in enumerate(lines):
        for second in lines[first_index + 1 :]:
            if first["orientation"] == second["orientation"]:
                stats["parallel_line_pairs"] += 1
                continue
            stats["nonparallel_line_pairs"] += 1
            point = _intersection(first, second)
            if point is None:
                continue
            if not (
                -incidence_margin <= point[0] <= maximum + incidence_margin
                and -incidence_margin <= point[1] <= maximum + incidence_margin
            ):
                stats["outside_paper_intersections"] += 1
                continue
            first_t = float(first["direction"] @ point)
            second_t = float(second["direction"] @ point)
            first_gap = _interval_distance(first["intervals"], first_t)
            second_gap = _interval_distance(second["intervals"], second_t)
            if first_gap > incidence_margin or second_gap > incidence_margin:
                stats["finite_evidence_rejected_intersections"] += 1
                continue
            candidates.append(
                _candidate(
                    "line_intersection",
                    np.clip(point, 0.0, maximum),
                    {int(first["target_id"]), int(second["target_id"])},
                    interval_gaps={
                        int(first["target_id"]): first_gap,
                        int(second["target_id"]): second_gap,
                    },
                )
            )
            stats["finite_evidence_intersections"] += 1

    for line in lines:
        target_id = int(line["target_id"])
        grouped_hits: list[dict[str, Any]] = []
        for value, point, side_label in _boundary_hits(
            float(line["offset"]), int(line["orientation"]), size
        ):
            side = _BOUNDARY_SIDE.get(side_label)
            if side is None:
                continue
            existing = next(
                (item for item in grouped_hits if abs(float(item["t"]) - float(value)) <= 0.75),
                None,
            )
            if existing is None:
                grouped_hits.append(
                    {"t": float(value), "point": np.asarray(point, dtype=float), "sides": {side}}
                )
            else:
                existing["sides"].add(side)
        for hit in grouped_hits:
            gap = _interval_distance(line["intervals"], float(hit["t"]))
            if gap > boundary_margin:
                stats["finite_evidence_rejected_boundary_hits"] += 1
                continue
            candidates.append(
                _candidate(
                    "boundary_contact",
                    np.clip(hit["point"], 0.0, maximum),
                    {target_id},
                    boundary_sides=set(hit["sides"]),
                    interval_gaps={target_id: gap},
                )
            )
            stats["finite_evidence_boundary_contacts"] += 1

        for first, second in line["intervals"]:
            for value in (first, second):
                point = line["normal"] * float(line["offset"]) + line["direction"] * value
                candidates.append(
                    _candidate(
                        "finite_endpoint",
                        np.clip(point, 0.0, maximum),
                        {target_id},
                        interval_gaps={target_id: 0.0},
                    )
                )
                stats["finite_interval_endpoints"] += 1
    return candidates, dict(stats)


def _fit_cluster_point(
    candidates: list[dict[str, Any]],
    line_index: Mapping[int, Mapping[str, Any]],
    maximum: float,
) -> np.ndarray:
    line_ids = sorted(
        {
            int(line_id)
            for item in candidates
            for line_id in item["line_ids"]
            if int(line_id) in line_index
        }
    )
    orientations = {int(line_index[line_id]["orientation"]) for line_id in line_ids}
    if len(orientations) >= 2:
        matrix = np.array([line_index[line_id]["normal"] for line_id in line_ids], dtype=float)
        offsets = np.array([line_index[line_id]["offset"] for line_id in line_ids], dtype=float)
        point, *_ = np.linalg.lstsq(matrix, offsets, rcond=None)
    else:
        point = np.mean([item["point"] for item in candidates], axis=0)

    boundary_sides = {
        side for item in candidates for side in item.get("boundary_sides", ())
    }
    if "left" in boundary_sides:
        point[0] = 0.0
    elif "right" in boundary_sides:
        point[0] = maximum
    if "top" in boundary_sides:
        point[1] = 0.0
    elif "bottom" in boundary_sides:
        point[1] = maximum
    return np.clip(point, 0.0, maximum)


def _cluster_candidates(
    candidates: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    size: int,
    *,
    merge_radius: float,
    line_residual_tolerance: float,
) -> tuple[list[dict[str, Any]], int]:
    maximum = float(size - 1)
    line_index = {int(line["target_id"]): line for line in lines}
    clusters: list[dict[str, Any]] = []
    rejected_merges = 0
    ordered = sorted(
        candidates,
        key=lambda item: (
            _CANDIDATE_PRIORITY[item["kind"]],
            round(float(item["point"][1]), 6),
            round(float(item["point"][0]), 6),
            sorted(item["line_ids"]),
        ),
    )
    for candidate in ordered:
        ranked = sorted(
            (
                (float(np.linalg.norm(candidate["point"] - cluster["point"])), index)
                for index, cluster in enumerate(clusters)
                if float(np.linalg.norm(candidate["point"] - cluster["point"])) <= merge_radius
            )
        )
        assigned = False
        for _, cluster_index in ranked:
            cluster = clusters[cluster_index]
            proposed = cluster["candidates"] + [candidate]
            fitted = _fit_cluster_point(proposed, line_index, maximum)
            proposed_line_ids = {
                int(line_id)
                for item in proposed
                for line_id in item["line_ids"]
                if int(line_id) in line_index
            }
            residual = max(
                (
                    abs(
                        float(line_index[line_id]["normal"] @ fitted)
                        - float(line_index[line_id]["offset"])
                    )
                    for line_id in proposed_line_ids
                ),
                default=0.0,
            )
            spread = max(
                (float(np.linalg.norm(item["point"] - fitted)) for item in proposed),
                default=0.0,
            )
            if (
                residual <= line_residual_tolerance
                # Shallow-angle line pairs amplify a sub-pixel ridge offset
                # into a visibly displaced pairwise intersection.  The
                # cluster center and joint line residual are more reliable
                # than that one pair candidate; the center-distance gate above
                # still prevents merging two genuinely separate nearby nodes.
                and spread <= merge_radius * 3.5
            ):
                cluster["candidates"] = proposed
                cluster["point"] = fitted
                assigned = True
                break
            rejected_merges += 1
        if not assigned:
            clusters.append({"point": candidate["point"].copy(), "candidates": [candidate]})

    # A high-degree focus is assembled from many pairwise intersections.  Its
    # least-squares center can move toward the true node after an endpoint was
    # already considered, leaving two nearly identical early clusters.  Merge
    # again after all centers have stabilized, under the same joint residual
    # invariant used above.
    changed = True
    while changed:
        changed = False
        for first_index, first in enumerate(clusters):
            for second_index in range(first_index + 1, len(clusters)):
                second = clusters[second_index]
                if float(np.linalg.norm(first["point"] - second["point"])) > merge_radius:
                    continue
                proposed = first["candidates"] + second["candidates"]
                fitted = _fit_cluster_point(proposed, line_index, maximum)
                proposed_line_ids = {
                    int(line_id)
                    for item in proposed
                    for line_id in item["line_ids"]
                    if int(line_id) in line_index
                }
                residual = max(
                    (
                        abs(
                            float(line_index[line_id]["normal"] @ fitted)
                            - float(line_index[line_id]["offset"])
                        )
                        for line_id in proposed_line_ids
                    ),
                    default=0.0,
                )
                spread = max(
                    (float(np.linalg.norm(item["point"] - fitted)) for item in proposed),
                    default=0.0,
                )
                if (
                    residual <= line_residual_tolerance
                    and spread <= merge_radius * 3.5
                ):
                    first["candidates"] = proposed
                    first["point"] = fitted
                    del clusters[second_index]
                    changed = True
                    break
                rejected_merges += 1
            if changed:
                break
    return clusters, rejected_merges


def _point_records(
    clusters: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    size: int,
    *,
    incidence_margin: float,
    line_residual_tolerance: float,
) -> list[dict[str, Any]]:
    line_index = {int(line["target_id"]): line for line in lines}
    records: list[dict[str, Any]] = []
    for cluster in clusters:
        point = np.asarray(cluster["point"], dtype=float)
        candidates = cluster["candidates"]
        candidate_supported_ids = {
            int(line_id)
            for item in candidates
            for line_id in item["line_ids"]
            if int(line_id) in line_index
        }
        incident_ids: list[int] = []
        incidence_gaps: dict[int, float] = {}
        incidence_residuals: dict[int, float] = {}
        for target_id, line in line_index.items():
            residual = abs(float(line["normal"] @ point) - float(line["offset"]))
            if residual > line_residual_tolerance:
                continue
            value = float(line["direction"] @ point)
            gap = _interval_distance(line["intervals"], value)
            # Joint fitting can move a high-degree node slightly beyond one
            # stroke's original endpoint.  Preserve a line that independently
            # participated in an accepted finite-evidence candidate, but do
            # not grant the same extension to unrelated lines merely passing
            # near the fitted point.
            allowed_gap = (
                incidence_margin + min(2.0, line_residual_tolerance)
                if target_id in candidate_supported_ids
                else incidence_margin
            )
            if gap > allowed_gap:
                continue
            incident_ids.append(target_id)
            incidence_gaps[target_id] = gap
            incidence_residuals[target_id] = residual
        if not incident_ids:
            continue
        boundary_sides = sorted(
            {side for item in candidates for side in item.get("boundary_sides", ())}
        )
        orientation_count = len(
            {int(line_index[target_id]["orientation"]) for target_id in incident_ids}
        )
        candidate_kinds = {str(item["kind"]) for item in candidates}
        if boundary_sides:
            point_kind = "boundary_contact"
        elif orientation_count >= 2 and "line_intersection" in candidate_kinds:
            point_kind = "line_intersection"
        else:
            point_kind = "finite_endpoint"
        records.append(
            {
                "point": point,
                "point_kind": point_kind,
                "line_ids": sorted(incident_ids),
                "boundary_sides": boundary_sides,
                "incidence_gaps": incidence_gaps,
                "incidence_residuals": incidence_residuals,
                "candidate_kinds": sorted(candidate_kinds),
                "candidate_count": len(candidates),
                "observations": [
                    {
                        "source": "raw_image_finite_line_evidence",
                        "kind": str(item["kind"]),
                        "point_px": [round(float(value), 6) for value in item["point"]],
                        "raw_line_ids": [
                            line_index[int(line_id)]["raw_id"]
                            for line_id in sorted(item["line_ids"])
                            if int(line_id) in line_index
                        ],
                        "boundary_sides": sorted(item.get("boundary_sides", ())),
                        "interval_gaps_px": {
                            line_index[int(line_id)]["raw_id"]: round(float(gap), 6)
                            for line_id, gap in item.get("interval_gaps", {}).items()
                            if int(line_id) in line_index
                        },
                    }
                    for item in candidates
                ],
            }
        )
    return sorted(
        records,
        key=lambda item: (
            round(float(item["point"][1]), 6),
            round(float(item["point"][0]), 6),
            item["point_kind"],
        ),
    )


def _absorb_nearby_terminals(
    records: list[dict[str, Any]],
    *,
    radius: float,
) -> tuple[list[dict[str, Any]], int]:
    """Fold detector endpoints back into an already-supported real node.

    LSD commonly stops a few pixels before a many-line focus.  If that focus
    already admits every crease of the terminal record from independent finite
    evidence, retaining the detector endpoint would split one physical vertex
    into a real node plus a tiny artificial linehead.
    """

    retained: list[dict[str, Any]] = []
    absorbed = 0
    nonterminals = [
        record for record in records if record["point_kind"] != "finite_endpoint"
    ]
    for record in records:
        if record["point_kind"] != "finite_endpoint":
            retained.append(record)
            continue
        line_ids = set(record["line_ids"])
        matches = sorted(
            (
                (float(np.linalg.norm(record["point"] - target["point"])), target)
                for target in nonterminals
                if line_ids
                and line_ids.issubset(set(target["line_ids"]))
                and float(np.linalg.norm(record["point"] - target["point"])) <= radius
            ),
            key=lambda item: (
                item[0],
                round(float(item[1]["point"][1]), 6),
                round(float(item[1]["point"][0]), 6),
            ),
        )
        if not matches:
            retained.append(record)
            continue
        target = matches[0][1]
        target["observations"].extend(record["observations"])
        target["candidate_kinds"] = sorted(
            set(target["candidate_kinds"]) | set(record["candidate_kinds"])
        )
        target["candidate_count"] += int(record["candidate_count"])
        absorbed += 1
    return sorted(
        retained,
        key=lambda item: (
            round(float(item["point"][1]), 6),
            round(float(item["point"][0]), 6),
            item["point_kind"],
        ),
    ), absorbed


def _segments_for_graph(
    graph: ConstructionGraph,
    lines: list[dict[str, Any]],
    point_records: list[dict[str, Any]],
    point_ids: list[Hashable],
    *,
    incidence_margin: float,
    line_type_evidence: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    by_line: dict[int, list[tuple[float, Hashable]]] = {
        int(line["target_id"]): [] for line in lines
    }
    for point_id, record in zip(point_ids, point_records):
        point = record["point"]
        for target_id in record["line_ids"]:
            line = next(item for item in lines if int(item["target_id"]) == int(target_id))
            by_line[int(target_id)].append((float(line["direction"] @ point), point_id))

    segments: list[dict[str, Any]] = []
    minimum_coverage = 1.0
    for line in lines:
        target_id = int(line["target_id"])
        ordered: list[tuple[float, Hashable]] = []
        for value, point_id in sorted(by_line[target_id], key=lambda item: (item[0], repr(item[1]))):
            if ordered and abs(value - ordered[-1][0]) <= 0.5:
                continue
            ordered.append((value, point_id))
        line_segment_ids: list[str] = []
        for segment_index, ((first_t, first_id), (second_t, second_id)) in enumerate(
            zip(ordered, ordered[1:])
        ):
            length = second_t - first_t
            if length < 0.75:
                continue
            overlap = _interval_overlap(line["intervals"], first_t, second_t)
            coverage = min(1.0, overlap / length)
            unsupported = max(0.0, length - overlap)
            if coverage < 0.50 or unsupported > incidence_margin * 2.0:
                continue
            segment_id = f"raw-segment:{target_id}:{segment_index}"
            line_segment_ids.append(segment_id)
            minimum_coverage = min(minimum_coverage, coverage)
            segment = {
                "id": segment_id,
                "source": "raw_image_finite_line_evidence",
                "crease_entity_id": str(line["crease_id"]),
                "raw_line_id": line["raw_id"],
                "orientation": int(line["orientation"]),
                "start_point_id": str(first_id),
                "end_point_id": str(second_id),
                "start_t_px": round(first_t, 6),
                "end_t_px": round(second_t, 6),
                "length_px": round(length, 6),
                "visible_coverage": round(coverage, 6),
                "unsupported_length_px": round(unsupported, 6),
            }
            segments.append(segment)
            for point_id in (first_id, second_id):
                point = graph.geometry_entity(point_id)
                point.metadata.setdefault("topology_segment_ids", []).append(segment_id)
                other_id = second_id if point_id == first_id else first_id
                neighbors = point.metadata.setdefault("adjacent_point_ids", [])
                if str(other_id) not in neighbors:
                    neighbors.append(str(other_id))
        crease = graph.geometry_entity(line["crease_id"])
        crease.metadata["ordered_point_ids"] = [str(point_id) for _, point_id in ordered]
        crease.metadata["topology_segment_ids"] = line_segment_ids
    apply_segment_line_type_evidence(segments, line_type_evidence)
    return segments, (minimum_coverage if segments else 0.0)


def apply_segment_line_type_evidence(
    segments: list[dict[str, Any]],
    evidence_report: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Attach only trusted, non-ambiguous source-colour evidence by segment id."""

    records = (
        evidence_report.get("segments")
        if isinstance(evidence_report, Mapping)
        else None
    )
    records = records if isinstance(records, Mapping) else {}
    assigned = 0
    ambiguous = 0
    unavailable = 0
    for segment in segments:
        segment.pop("line_type", None)
        segment.pop("line_type_source", None)
        segment.pop("line_type_evidence", None)
        segment_id = str(segment.get("id") or "")
        raw = records.get(segment_id)
        if not isinstance(raw, Mapping):
            unavailable += 1
            continue
        evidence = dict(raw)
        segment["line_type_evidence"] = evidence
        try:
            line_type = int(evidence.get("line_type"))
        except (TypeError, ValueError):
            line_type = 0
        trusted = (
            evidence.get("status") == "assigned"
            and evidence.get("source") == "source_image_color_evidence"
            and evidence.get("ambiguous") is False
            and line_type in {2, 3}
        )
        if trusted:
            segment["line_type"] = line_type
            segment["line_type_source"] = "source_image_color_evidence"
            assigned += 1
        elif evidence.get("status") == "ambiguous":
            ambiguous += 1
        else:
            unavailable += 1
    return {
        "assigned_segment_count": assigned,
        "ambiguous_segment_count": ambiguous,
        "unavailable_segment_count": unavailable,
    }


def build_raw_crease_topology_graph(
    raw_report: Mapping[str, Any],
) -> tuple[ConstructionGraph, dict[int, dict[str, Any]], dict[str, Any]]:
    """Build the unified point/crease graph and finite planar segment report."""

    started = time.perf_counter()
    lines, size = _parse_lines(raw_report)
    graph = ConstructionGraph()
    if not lines:
        return graph, {}, {
            "enabled": False,
            "mode": "raw_finite_crease_topology_v1",
            "reason": "no_valid_raw_crease_lines",
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    evidence_stats = raw_report.get("evidence_stats")
    try:
        evidence_distance = float(
            evidence_stats.get("adaptive_evidence_distance_px", 1.75)
            if isinstance(evidence_stats, Mapping)
            else 1.75
        )
    except (TypeError, ValueError):
        evidence_distance = 1.75
    incidence_margin = float(np.clip(evidence_distance * 2.0, 3.5, 5.5))
    boundary_margin = float(np.clip(evidence_distance * 2.25, 4.0, 6.0))
    merge_radius = float(np.clip(evidence_distance * 1.65, 2.6, 4.0))
    line_residual_tolerance = float(np.clip(evidence_distance * 1.45, 2.2, 3.2))

    candidates, candidate_stats = _topology_candidates(
        lines,
        size,
        incidence_margin=incidence_margin,
        boundary_margin=boundary_margin,
    )
    clusters, rejected_merges = _cluster_candidates(
        candidates,
        lines,
        size,
        merge_radius=merge_radius,
        line_residual_tolerance=line_residual_tolerance,
    )
    point_records = _point_records(
        clusters,
        lines,
        size,
        incidence_margin=incidence_margin,
        line_residual_tolerance=line_residual_tolerance,
    )
    point_records, absorbed_terminal_count = _absorb_nearby_terminals(
        point_records,
        radius=incidence_margin + 2.0,
    )

    anchors: dict[int, dict[str, Any]] = {}
    for line in lines:
        match_tolerance = min(3.2, max(0.85, evidence_distance + 0.75))
        graph.add_geometry_entity(
            GeometryEntity(
                id=line["crease_id"],
                kind="crease",
                observed_geometry={
                    "raw_line_id": line["raw_id"],
                    "direction_index": int(line["orientation"]),
                    "angle_deg": float(line["angle_deg"]),
                    "line_offset_px": round(float(line["offset"]), 9),
                    "match_tolerance_px": round(match_tolerance, 6),
                    "evidence_intervals_px": [
                        [round(first, 6), round(second, 6)]
                        for first, second in line["intervals"]
                    ],
                    "support_fraction": round(float(line["support_fraction"]), 6),
                    "mean_confidence": round(float(line["mean_confidence"]), 6),
                },
                evidence_sources={"raw_image_finite_line_evidence"},
                metadata={
                    "source": "raw_image_finite_line_evidence",
                    "target_id": int(line["target_id"]),
                },
            )
        )

    point_ids: list[Hashable] = []
    point_kind_counts: Counter[str] = Counter()
    for index, record in enumerate(point_records):
        point_id: Hashable = ("point", "raw", index)
        point_ids.append(point_id)
        point_kind_counts[str(record["point_kind"])] += 1
        graph.add_geometry_entity(
            GeometryEntity(
                id=point_id,
                kind="point",
                observed_geometry={
                    "point_px": [round(float(value), 6) for value in record["point"]],
                    "point_kind": record["point_kind"],
                    "boundary_sides": list(record["boundary_sides"]),
                    "observations": list(record["observations"]),
                    "match_tolerance_px": round(line_residual_tolerance, 6),
                },
                evidence_sources={"raw_image_finite_line_evidence"},
                metadata={
                    "candidate_kinds": list(record["candidate_kinds"]),
                    "candidate_count": int(record["candidate_count"]),
                    "maximum_interval_gap_px": round(
                        max(record["incidence_gaps"].values(), default=0.0), 6
                    ),
                    "maximum_line_residual_px": round(
                        max(record["incidence_residuals"].values(), default=0.0), 6
                    ),
                },
            )
        )
        for target_id in record["line_ids"]:
            line = next(item for item in lines if int(item["target_id"]) == int(target_id))
            graph.connect_incidence(point_id, line["crease_id"])

    segments, minimum_segment_coverage = _segments_for_graph(
        graph,
        lines,
        point_records,
        point_ids,
        incidence_margin=incidence_margin,
        line_type_evidence=raw_report.get("segment_line_type_evidence"),
    )

    for line in lines:
        incident_points = [
            entity
            for entity in graph.incident_entities(line["crease_id"])
            if entity.kind == "point"
        ]
        boundary_points = [
            entity
            for entity in incident_points
            if entity.observed_geometry.get("boundary_sides")
        ]
        anchor_entity = (boundary_points or incident_points or [None])[0]
        anchor_point = (
            list(anchor_entity.observed_geometry["point_px"])
            if anchor_entity is not None
            else None
        )
        match_tolerance = float(
            graph.geometry_entity(line["crease_id"]).observed_geometry[
                "match_tolerance_px"
            ]
        )
        anchors[int(line["target_id"])] = {
            "trace_id": int(line["target_id"]),
            "raw_line_id": line["raw_id"],
            "angle": float(line["angle_deg"]),
            "line_offset_px": float(line["offset"]),
            "observed_offset_px": float(line["offset"]),
            "anchor_point_px": anchor_point,
            "snap_error_px": max(0.0, match_tolerance - 1.0),
            "generation": -1,
            "source": "raw_image_finite_line_evidence",
            "_geometry_entity_id": line["crease_id"],
        }

    incidence_count = sum(len(items) for items in graph.incidence.values()) // 2
    orphan_creases = sum(
        not graph.incidence.get(line["crease_id"]) for line in lines
    )
    serialized_points: list[dict[str, Any]] = []
    for point_id, record in zip(point_ids, point_records):
        incident_creases = [
            entity
            for entity in graph.incident_entities(point_id)
            if entity.kind == "crease"
        ]
        supports = [
            float(entity.observed_geometry.get("support_fraction", 0.0) or 0.0)
            for entity in incident_creases
        ]
        serialized_points.append(
            {
                "id": str(point_id),
                "label": (
                    "原图纸边接触"
                    if record["boundary_sides"]
                    else "原图折痕交点"
                    if record["point_kind"] == "line_intersection"
                    else "原图有限线端点"
                ),
                "source": "raw_image_finite_topology",
                "point": [round(float(value), 6) for value in record["point"]],
                "point_kind": record["point_kind"],
                "boundary_sides": list(record["boundary_sides"]),
                "incident_crease_count": len(incident_creases),
                "incident_raw_line_ids": [
                    str(entity.observed_geometry.get("raw_line_id") or entity.id)
                    for entity in incident_creases
                ],
                "directional_score": round(
                    float(np.mean(supports)) if supports else 0.0, 6
                ),
            }
        )
    boundary_contacts = [
        point for point in serialized_points if point["boundary_sides"]
    ]
    topology = {
        "enabled": True,
        "mode": "raw_finite_crease_topology_v1",
        "source": "raw_image_finite_line_evidence",
        "analysis_size": size,
        "maximum_coordinate_px": size - 1,
        "crease_count": len(lines),
        "point_count": len(point_records),
        "point_kind_counts": dict(sorted(point_kind_counts.items())),
        "incidence_count": incidence_count,
        "segment_count": len(segments),
        "orphan_crease_count": orphan_creases,
        "segments": segments,
        "points": serialized_points,
        "boundary_contacts": boundary_contacts,
        "candidate_stats": candidate_stats,
        "cluster_count": len(clusters),
        "cluster_merge_rejection_count": rejected_merges,
        "absorbed_detector_terminal_count": absorbed_terminal_count,
        "tolerances": {
            "incidence_margin_px": round(incidence_margin, 6),
            "boundary_margin_px": round(boundary_margin, 6),
            "point_merge_radius_px": round(merge_radius, 6),
            "line_residual_tolerance_px": round(line_residual_tolerance, 6),
        },
        "invariants": {
            "generated_crease_count": 0,
            "generated_direction_count": 0,
            "intersection_requires_two_finite_evidence_intervals": True,
            "boundary_contact_requires_finite_evidence_interval": True,
            "segments_join_consecutive_incident_points_only": True,
            "minimum_retained_segment_visible_coverage": round(
                minimum_segment_coverage, 6
            ),
        },
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
    return graph, anchors, topology


def build_raw_crease_topology_report(raw_report: Mapping[str, Any]) -> dict[str, Any]:
    graph, _, report = build_raw_crease_topology_graph(raw_report)
    output = dict(report)
    output["geometry_graph"] = graph.geometry_snapshot()
    return output


__all__ = [
    "apply_segment_line_type_evidence",
    "build_raw_crease_topology_graph",
    "build_raw_crease_topology_report",
]
