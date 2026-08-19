from __future__ import annotations

import math
from typing import Any, Mapping

import shadow_geometry as base
import shadow_geometry_v2 as v2
from construction_search import SearchWeights

Point = base.Point
LineGeometry = base.LineGeometry


def _same_boundary(first: Point, second: Point, maximum: float) -> bool:
    eps = 1e-6
    return (
        (abs(first[0]) <= eps and abs(second[0]) <= eps)
        or (abs(first[0] - maximum) <= eps and abs(second[0] - maximum) <= eps)
        or (abs(first[1]) <= eps and abs(second[1]) <= eps)
        or (abs(first[1] - maximum) <= eps and abs(second[1] - maximum) <= eps)
    )


def _structural_sources(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    maximum: float,
) -> list[dict[str, Any]]:
    """Return low-complexity points that can seed a construction proof.

    Paper corners are intrinsic and therefore free. Existing anchor points and
    exact paper-boundary contacts from low-generation unaffected rays are also
    admitted. The list is deliberately capped before pairwise subdivision so
    browser-side search stays bounded.
    """

    raw: list[dict[str, Any]] = [
        {"point": (0.0, 0.0), "ops": [{"kind": "paper_corner_point", "corner": "top_left", "point_px": [0.0, 0.0]}], "extra": 0.0, "rank": (0, 0)},
        {"point": (maximum, 0.0), "ops": [{"kind": "paper_corner_point", "corner": "top_right", "point_px": [maximum, 0.0]}], "extra": 0.0, "rank": (0, 1)},
        {"point": (maximum, maximum), "ops": [{"kind": "paper_corner_point", "corner": "bottom_right", "point_px": [maximum, maximum]}], "extra": 0.0, "rank": (0, 2)},
        {"point": (0.0, maximum), "ops": [{"kind": "paper_corner_point", "corner": "bottom_left", "point_px": [0.0, maximum]}], "extra": 0.0, "rank": (0, 3)},
    ]

    unaffected = sorted(
        set(anchors) - affected,
        key=lambda trace_id: (base._generation(anchors[trace_id]), trace_id),
    )
    for trace_id in unaffected:
        anchor = anchors[trace_id]
        generation = base._generation(anchor)
        point = base._anchor_point(anchor)
        if point is not None and base._inside(point, maximum):
            raw.append(
                {
                    "point": point,
                    "ops": [
                        {
                            "kind": "existing_point",
                            "source_trace_id": trace_id,
                            "point_px": [round(point[0], 6), round(point[1], 6)],
                        }
                    ],
                    "extra": 0.0,
                    "rank": (1, max(0, generation), trace_id),
                }
            )

        orientation = base._orientation(anchor)
        if orientation is None or anchor.get("line_offset_px") is None or generation > 3:
            continue
        geometry = base._line_geometry(orientation, float(anchor["line_offset_px"]))
        for side, contact in v2._boundary_hits(geometry, maximum):
            raw.append(
                {
                    "point": contact,
                    "ops": [
                        {
                            "kind": "boundary_contact_point",
                            "source_trace_id": trace_id,
                            "side": side,
                            "point_px": [round(contact[0], 6), round(contact[1], 6)],
                        }
                    ],
                    "extra": 0.0,
                    "rank": (2, max(0, generation), trace_id),
                }
            )

    dedup: dict[tuple[float, float], dict[str, Any]] = {}
    for item in sorted(raw, key=lambda value: value["rank"]):
        point = item["point"]
        key = (round(point[0], 5), round(point[1], 5))
        dedup.setdefault(key, item)
    return list(dedup.values())[:36]


def _subdivision_sources(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    maximum: float,
    weights: SearchWeights,
) -> list[dict[str, Any]]:
    sources = _structural_sources(anchors, affected, maximum)
    result = list(sources)

    # Low-denominator equal/ratio divisions are generic construction
    # operations. 1/3 is not special-cased; it is merely denominator=3.
    for first_index, first in enumerate(sources):
        for second in sources[first_index + 1 :]:
            a = first["point"]
            b = second["point"]
            if base._distance(a, b) < 2.0:
                continue
            if not (_same_boundary(a, b, maximum) or base._legal_connector(a, b)):
                continue
            for denominator in (2, 3, 4):
                for numerator in range(1, denominator):
                    if math.gcd(numerator, denominator) != 1:
                        continue
                    t = numerator / denominator
                    point = (
                        a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t,
                    )
                    if not base._inside(point, maximum):
                        continue
                    result.append(
                        {
                            "point": point,
                            "ops": [
                                *first["ops"],
                                *second["ops"],
                                {
                                    "kind": "rational_subdivision_point",
                                    "numerator": numerator,
                                    "denominator": denominator,
                                    "source_points_px": [
                                        [round(a[0], 6), round(a[1], 6)],
                                        [round(b[0], 6), round(b[1], 6)],
                                    ],
                                    "point_px": [round(point[0], 6), round(point[1], 6)],
                                },
                            ],
                            "extra": weights.independent_parameter * math.log2(denominator),
                            "rank": (3, denominator, numerator),
                        }
                    )

    dedup: dict[tuple[float, float], dict[str, Any]] = {}
    for item in sorted(result, key=lambda value: (value.get("extra", 0.0), value.get("rank", ()))):
        point = item["point"]
        key = (round(point[0], 5), round(point[1], 5))
        previous = dedup.get(key)
        if previous is None or item.get("extra", 0.0) < previous.get("extra", 0.0):
            dedup[key] = item
    return list(dedup.values())[:96]


def _reference_lines(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
    maximum: float,
) -> list[dict[str, Any]]:
    middle = maximum / 2.0
    refs: list[dict[str, Any]] = [
        {
            "geometry": base._line_geometry(0, middle),
            "op": {"kind": "paper_midline", "midline": "horizontal"},
            "rank": (0, 0),
        },
        {
            "geometry": base._line_geometry(4, -middle),
            "op": {"kind": "paper_midline", "midline": "vertical"},
            "rank": (0, 1),
        },
    ]
    for trace_id in sorted(
        set(anchors) - affected,
        key=lambda value: (base._generation(anchors[value]), value),
    ):
        anchor = anchors[trace_id]
        if base._generation(anchor) > 3:
            continue
        orientation = base._orientation(anchor)
        if orientation is None or anchor.get("line_offset_px") is None:
            continue
        refs.append(
            {
                "geometry": base._line_geometry(orientation, float(anchor["line_offset_px"])),
                "op": {"kind": "existing_reference_ray", "source_trace_id": trace_id},
                "rank": (1, max(0, base._generation(anchor)), trace_id),
            }
        )
    return sorted(refs, key=lambda value: value["rank"])[:28]


def _axes(
    anchors: Mapping[int, Mapping[str, Any]],
    affected: set[int],
) -> list[tuple[int, LineGeometry]]:
    result: list[tuple[int, LineGeometry]] = []
    for trace_id in sorted(
        set(anchors) - affected,
        key=lambda value: (base._generation(anchors[value]), value),
    ):
        anchor = anchors[trace_id]
        if base._generation(anchor) > 3:
            continue
        orientation = base._orientation(anchor)
        if orientation is None or anchor.get("line_offset_px") is None:
            continue
        result.append(
            (trace_id, base._line_geometry(orientation, float(anchor["line_offset_px"])))
        )
        if len(result) >= 24:
            break
    return result


def _generic_reroot_proofs(
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
    sources = _subdivision_sources(anchors, affected, maximum, weights)
    references = _reference_lines(anchors, affected, maximum)
    axes = _axes(anchors, affected)

    candidates: dict[tuple[float, tuple[int, ...]], dict[str, Any]] = {}

    def add_candidate(
        point: Point,
        prefix: list[dict[str, Any]],
        extra_cost: float,
        *,
        seed_offsets: Mapping[int, float] | None = None,
        reused: list[int] | None = None,
        support_residual: float = 0.0,
    ) -> None:
        root_offset = base._ray_offset(root_orientation, point)
        root_residual = abs(root_offset - observed)
        if root_residual > tolerance:
            return
        operations = [*prefix, {"kind": "ray_from_point", "target_trace_id": root_id}]
        proof_cost = (
            weights.step * len(operations)
            + extra_cost
            + weights.residual * (root_residual + support_residual)
        )
        seeds = {int(key): float(value) for key, value in (seed_offsets or {}).items()}
        key = (round(root_offset, 6), tuple(sorted(seeds)))
        item = {
            "offset_px": root_offset,
            "image_residual_px": root_residual,
            "proof_cost": proof_cost,
            "proof_operations": operations,
            "seed_offsets": seeds,
            "reused_trace_ids": list(reused or []),
        }
        previous = candidates.get(key)
        if previous is None or (proof_cost, root_residual) < (
            previous["proof_cost"], previous["image_residual_px"]
        ):
            candidates[key] = item

    for source in sources:
        point = source["point"]
        prefix = list(source["ops"])
        extra = float(source.get("extra", 0.0))
        add_candidate(point, prefix, extra)

        reflected_sources: list[tuple[Point, list[dict[str, Any]], float]] = [(point, prefix, extra)]
        for axis_id, axis in axes:
            reflected = base._reflect(point, axis)
            if not base._inside(reflected, maximum) or base._distance(point, reflected) < 0.25:
                continue
            reflected_sources.append(
                (
                    reflected,
                    [
                        *prefix,
                        {
                            "kind": "symmetry_point",
                            "axis_trace_id": axis_id,
                            "source_point_px": [round(point[0], 6), round(point[1], 6)],
                            "reflected_point_px": [round(reflected[0], 6), round(reflected[1], 6)],
                        },
                    ],
                    extra,
                )
            )

        # A low-complexity point may first establish a downstream observed ray;
        # intersecting that ray with another already-known reference can then
        # re-root the old dependency DAG. This is what lets search genuinely
        # reverse an incorrect legacy dependency direction.
        for seed_point, seed_prefix, seed_extra in reflected_sources[:10]:
            for support_orientation in range(8):
                support_offset = base._ray_offset(support_orientation, seed_point)
                support = v2._best_trace(
                    anchors, affected, support_orientation, support_offset, root_id
                )
                if support is None:
                    continue
                support_id, support_residual = support
                support_line = base._line_geometry(support_orientation, support_offset)
                support_ops = [
                    *seed_prefix,
                    {
                        "kind": "ray_from_point",
                        "target_trace_id": support_id,
                        "offset_px": round(support_offset, 6),
                    },
                ]
                for reference in references:
                    intersection = base._intersection(support_line, reference["geometry"])
                    if intersection is None or not base._inside(intersection, maximum):
                        continue
                    add_candidate(
                        intersection,
                        [
                            *support_ops,
                            reference["op"],
                            {
                                "kind": "reference_intersection",
                                "point_px": [
                                    round(intersection[0], 6),
                                    round(intersection[1], 6),
                                ],
                            },
                        ],
                        seed_extra,
                        seed_offsets={support_id: support_offset},
                        reused=[support_id],
                        support_residual=support_residual,
                    )

    return sorted(
        candidates.values(),
        key=lambda item: (item["proof_cost"], item["image_residual_px"]),
    )[:48]


def build_geometry_shadow_report_v3(
    result: Mapping[str, Any], *, weights: SearchWeights = SearchWeights()
) -> dict[str, Any]:
    trace = [x for x in list(result.get("playback_trace") or []) if isinstance(x, Mapping)]
    if not trace:
        return {
            "enabled": False,
            "mode": "shadow_geometry_v3",
            "output_unchanged": True,
            "reason": "no_playback_trace",
        }
    anchors = {base._trace_id(anchor, index): anchor for index, anchor in enumerate(trace)}
    try:
        maximum = float(int(result.get("stats", {}).get("analysis_size_used") or 512) - 1)
    except (TypeError, ValueError):
        maximum = 511.0

    reports: list[dict[str, Any]] = []
    for root_id, anchor in anchors.items():
        coefficients = base._coefficients(anchor)
        if not coefficients or max(abs(value) for value in coefficients) <= base._HIGH_COEFFICIENT:
            continue
        affected = base._affected_descendants(anchors, root_id)
        legacy_score, legacy_residual = base._legacy_route_score(anchors, affected, weights)
        proofs = list(base._candidate_root_proofs(anchors, root_id, affected, maximum, weights))
        proofs += v2._reroot_proofs(anchors, root_id, affected, maximum, weights)
        proofs += _generic_reroot_proofs(anchors, root_id, affected, maximum, weights)

        best: dict[str, Any] | None = None
        for proof in proofs:
            offsets, unresolved = v2._propagate(
                anchors,
                affected,
                root_id,
                float(proof["offset_px"]),
                maximum,
                proof.get("seed_offsets"),
            )
            if unresolved:
                continue
            score, residual = v2._replacement_score(
                anchors, affected, offsets, proof, root_id, weights
            )
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
                    "selected_root_shift_px": round(
                        float(best["offset_px"]) - float(anchor["line_offset_px"]), 6
                    ),
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

    improved = [item for item in reports if item.get("route_improved")]
    return {
        "enabled": True,
        "mode": "shadow_geometry_v3",
        "output_unchanged": True,
        "suspicious_seed_routes": reports,
        "improved_suspicious_seed_routes": len(improved),
        "route_changed": bool(improved),
        "notes": [
            "Paper corners are zero-cost known points.",
            "Low-denominator rational subdivisions are generic provenance candidates; thirds are denominator=3 rather than a model-specific rule.",
            "Corner/subdivision/symmetry rays can re-root a legacy dependency chain through existing references.",
        ],
    }


__all__ = ["build_geometry_shadow_report_v3"]
