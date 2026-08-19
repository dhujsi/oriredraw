from __future__ import annotations

import math
from typing import Any, Mapping

import shadow_geometry as base
from construction_search import SearchWeights

Point = base.Point
LineGeometry = base.LineGeometry


def _boundary_hits(line: LineGeometry, m: float) -> list[tuple[str, Point]]:
    _, n, o = line
    nx, ny = n
    out: list[tuple[str, Point]] = []

    def add(side: str, p: Point) -> None:
        if -1e-7 <= p[0] <= m + 1e-7 and -1e-7 <= p[1] <= m + 1e-7:
            q = (min(m, max(0.0, p[0])), min(m, max(0.0, p[1])))
            if not any(base._distance(q, old) < 1e-6 for _, old in out):
                out.append((side, q))

    if abs(ny) > 1e-9:
        add("left", (0.0, o / ny))
        add("right", (m, (o - nx * m) / ny))
    if abs(nx) > 1e-9:
        add("top", (o / nx, 0.0))
        add("bottom", ((o - ny * m) / nx, m))
    return out


def _corners(side: str, m: float) -> tuple[Point, Point]:
    return {
        "top": ((0.0, 0.0), (m, 0.0)),
        "bottom": ((0.0, m), (m, m)),
        "left": ((0.0, 0.0), (0.0, m)),
        "right": ((m, 0.0), (m, m)),
    }[side]


def _tangent(side: str) -> Point:
    return (1.0, 0.0) if side in {"top", "bottom"} else (0.0, 1.0)


def _best_trace(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    orientation: int,
    offset: float,
    root_id: int,
) -> tuple[int, float] | None:
    options: list[tuple[float, int]] = []
    for trace_id in affected - {root_id}:
        anchor = anchors[trace_id]
        if base._orientation(anchor) != orientation:
            continue
        residual = abs(offset - base._observed_offset(anchor))
        if residual <= min(3.0, max(1.2, base._root_tolerance(anchor))):
            options.append((residual, trace_id))
    if not options:
        return None
    residual, trace_id = min(options)
    return trace_id, residual


def _reroot_proofs(
    anchors: Mapping[int, Mapping[str, Any]],
    root_id: int,
    affected: set[int],
    maximum: float,
    weights: SearchWeights,
) -> list[dict[str, Any]]:
    root_orientation = base._orientation(anchors[root_id])
    if root_orientation is None:
        return []
    observed = base._observed_offset(anchors[root_id])
    tolerance = base._root_tolerance(anchors[root_id])
    unaffected = sorted(set(anchors) - affected)
    result: list[dict[str, Any]] = []
    middle = maximum / 2.0
    midlines = [
        ("horizontal", base._line_geometry(0, middle)),
        ("vertical", base._line_geometry(4, -middle)),
    ]

    for axis_id in unaffected:
        axis_anchor = anchors[axis_id]
        axis_orientation = base._orientation(axis_anchor)
        if axis_orientation is None or axis_anchor.get("line_offset_px") is None:
            continue
        axis = base._line_geometry(axis_orientation, float(axis_anchor["line_offset_px"]))
        direction = axis[0]
        for side, contact in _boundary_hits(axis, maximum):
            tangent = _tangent(side)
            if abs(direction[0] * tangent[0] + direction[1] * tangent[1]) > 1e-6:
                continue
            for corner_index, corner in enumerate(_corners(side, maximum)):
                if base._distance(contact, corner) < 1.0:
                    continue
                midpoint = ((contact[0] + corner[0]) / 2.0, (contact[1] + corner[1]) / 2.0)
                reflected = base._reflect(midpoint, axis)
                if not base._inside(reflected, maximum):
                    continue
                prefix = [
                    {
                        "kind": "boundary_midpoint_point",
                        "axis_trace_id": axis_id,
                        "side": side,
                        "corner_index": corner_index,
                        "boundary_point_px": [round(contact[0], 6), round(contact[1], 6)],
                        "midpoint_px": [round(midpoint[0], 6), round(midpoint[1], 6)],
                    },
                    {
                        "kind": "symmetry_point",
                        "axis_trace_id": axis_id,
                        "reflected_point_px": [round(reflected[0], 6), round(reflected[1], 6)],
                    },
                ]
                for support_orientation in range(8):
                    support_offset = base._ray_offset(support_orientation, reflected)
                    support = _best_trace(
                        anchors, affected, support_orientation, support_offset, root_id
                    )
                    if support is None:
                        continue
                    support_id, support_residual = support
                    support_line = base._line_geometry(support_orientation, support_offset)
                    for midline_name, midline in midlines:
                        point = base._intersection(support_line, midline)
                        if point is None or not base._inside(point, maximum):
                            continue
                        root_offset = base._ray_offset(root_orientation, point)
                        root_residual = abs(root_offset - observed)
                        if root_residual > tolerance:
                            continue
                        operations = prefix + [
                            {
                                "kind": "ray_from_point",
                                "target_trace_id": support_id,
                                "offset_px": round(support_offset, 6),
                            },
                            {
                                "kind": "paper_midline_intersection",
                                "midline": midline_name,
                                "point_px": [round(point[0], 6), round(point[1], 6)],
                            },
                            {"kind": "ray_from_point", "target_trace_id": root_id},
                        ]
                        result.append(
                            {
                                "offset_px": root_offset,
                                "image_residual_px": root_residual,
                                "proof_cost": weights.step * len(operations)
                                + weights.residual * (root_residual + support_residual),
                                "proof_operations": operations,
                                "seed_offsets": {support_id: support_offset},
                                "reused_trace_ids": [support_id],
                            }
                        )
    result.sort(key=lambda item: (item["proof_cost"], item["image_residual_px"]))
    return result[:32]


def _propagate(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    root_id: int,
    root_offset: float,
    maximum: float,
    seed_offsets: Mapping[int, float] | None,
) -> tuple[dict[int, float], list[int]]:
    valid = set(anchors)
    seeds = {int(k): float(v) for k, v in (seed_offsets or {}).items()}
    offsets = {
        i: float(a["line_offset_px"])
        for i, a in anchors.items()
        if i not in affected and a.get("line_offset_px") is not None
    }
    offsets[root_id] = float(root_offset)
    offsets.update(seeds)
    unresolved: list[int] = []
    for trace_id in sorted(
        affected - {root_id} - set(seeds),
        key=lambda i: (base._generation(anchors[i]), i),
    ):
        anchor = anchors[trace_id]
        parents = base._parent_ids(anchor, valid)
        point: Point | None = None
        if len(parents) >= 2 and parents[0] in offsets and parents[1] in offsets:
            a = base._orientation(anchors[parents[0]])
            b = base._orientation(anchors[parents[1]])
            if a is not None and b is not None:
                point = base._intersection(
                    base._line_geometry(a, offsets[parents[0]]),
                    base._line_geometry(b, offsets[parents[1]]),
                )
        elif len(parents) == 1 and parents[0] in offsets and "纸边交点" in str(anchor.get("source") or ""):
            parent_orientation = base._orientation(anchors[parents[0]])
            legacy_point = base._anchor_point(anchor)
            if parent_orientation is not None and legacy_point is not None:
                point = base._boundary_contact(
                    base._line_geometry(parent_orientation, offsets[parents[0]]),
                    legacy_point,
                    maximum,
                )
        orientation = base._orientation(anchor)
        if point is None or orientation is None or not base._inside(point, maximum):
            unresolved.append(trace_id)
            continue
        offsets[trace_id] = base._ray_offset(orientation, point)
    return offsets, unresolved


def _replacement_score(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    offsets: Mapping[int, float],
    proof: Mapping[str, Any],
    root_id: int,
    weights: SearchWeights,
) -> tuple[float, float]:
    reused = set(int(v) for v in proof.get("reused_trace_ids", ()))
    residual = sum(
        abs(float(offsets[i]) - base._observed_offset(anchors[i])) for i in affected
    )
    cost = float(proof["proof_cost"])
    for trace_id in affected - {root_id} - reused:
        r = abs(float(offsets[trace_id]) - base._observed_offset(anchors[trace_id]))
        cost += weights.step + weights.residual * r
    cost += weights.generation_depth * max(
        (base._generation(anchors[i]) for i in affected), default=0
    )
    return cost, residual


def build_geometry_shadow_report_v2(
    result: Mapping[str, Any], *, weights: SearchWeights = SearchWeights()
) -> dict[str, Any]:
    trace = [x for x in list(result.get("playback_trace") or []) if isinstance(x, Mapping)]
    if not trace:
        return {"enabled": False, "mode": "shadow_geometry_v2", "output_unchanged": True, "reason": "no_playback_trace"}
    anchors = {base._trace_id(a, i): a for i, a in enumerate(trace)}
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 512) - 1)
    except (TypeError, ValueError):
        maximum = 511.0
    reports: list[dict[str, Any]] = []
    for root_id, anchor in anchors.items():
        coefficients = base._coefficients(anchor)
        if not coefficients or max(abs(v) for v in coefficients) <= base._HIGH_COEFFICIENT:
            continue
        affected = base._affected_descendants(anchors, root_id)
        legacy_score, legacy_residual = base._legacy_route_score(anchors, affected, weights)
        proofs = list(base._candidate_root_proofs(anchors, root_id, affected, maximum, weights))
        proofs += _reroot_proofs(anchors, root_id, affected, maximum, weights)
        best: dict[str, Any] | None = None
        for proof in proofs:
            offsets, unresolved = _propagate(
                anchors, affected, root_id, float(proof["offset_px"]), maximum, proof.get("seed_offsets")
            )
            if unresolved:
                continue
            score, residual = _replacement_score(anchors, affected, offsets, proof, root_id, weights)
            if best is None or (score, residual) < (best["score"], best["residual"]):
                best = {**proof, "score": score, "residual": residual, "offsets": offsets}
        item: dict[str, Any] = {
            "trace_id": root_id,
            "expression": anchor.get("coordinate_expression") or anchor.get("expression"),
            "affected_ray_count": len(affected),
            "legacy_route_score": round(legacy_score, 6),
            "legacy_image_residual_sum_px": round(legacy_residual, 6),
            "replacement_found": best is not None,
        }
        if best is not None:
            item.update(
                {
                    "selected_offset_px": round(float(best["offset_px"]), 6),
                    "selected_root_shift_px": round(float(best["offset_px"]) - float(anchor["line_offset_px"]), 6),
                    "replacement_route_score": round(float(best["score"]), 6),
                    "replacement_image_residual_sum_px": round(float(best["residual"]), 6),
                    "score_improvement": round(legacy_score - float(best["score"]), 6),
                    "residual_improvement_px": round(legacy_residual - float(best["residual"]), 6),
                    "route_improved": float(best["score"]) < legacy_score,
                    "proof_operations": best["proof_operations"],
                    "reused_trace_ids": best.get("reused_trace_ids", []),
                }
            )
        reports.append(item)
    improved = [x for x in reports if x.get("route_improved")]
    return {
        "enabled": True,
        "mode": "shadow_geometry_v2",
        "output_unchanged": True,
        "suspicious_seed_routes": reports,
        "improved_suspicious_seed_routes": len(improved),
        "route_changed": bool(improved),
        "notes": [
            "Boundary contacts may create midpoint and symmetry points without hard-coded division ratios.",
            "A formerly downstream observed ray may receive new provenance and re-root the legacy dependency DAG.",
        ],
    }


__all__ = ["build_geometry_shadow_report_v2"]
