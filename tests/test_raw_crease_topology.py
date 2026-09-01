import math
import unittest

from exact_graph_propagation import propagate_exact_geometry
from guided_construction import build_guided_boundary_report
from qsqrt2_coordinates import qsqrt2_from_coefficients, qsqrt2_to_mapping
from raw_crease_topology import (
    apply_segment_line_type_evidence,
    build_raw_crease_topology_graph,
    resolve_topology_segment_line_types,
)


def _line(raw_id, orientation, offset, intervals):
    return {
        "id": raw_id,
        "orientation": orientation,
        "orientation_deg": orientation * 22.5,
        "observed_offset_px": offset,
        "evidence_intervals_px": intervals,
        "support_fraction": 1.0,
        "mean_confidence": 1.0,
    }


def _report(lines):
    return {
        "enabled": True,
        "mode": "raw_finite_crease_entities_v1",
        "analysis_size": 101,
        "maximum_coordinate_px": 100,
        "lines": lines,
        "evidence_stats": {"adaptive_evidence_distance_px": 1.75},
    }


def _boundary_seed_relation(
    relation_id,
    label,
    side,
    point_px,
    project_coordinate,
    side_length,
):
    return {
        "id": relation_id,
        "label": label,
        "kind": "test_relation",
        "side": side,
        "evidence_source": "raw_image_finite_topology",
        "priority": 1,
        "recommended_coordinate_gauge": {
            "side_length": qsqrt2_to_mapping(side_length),
        },
        "points": [
            {
                "id": f"{relation_id}:point",
                "point_px": list(point_px),
                "observed_point_px": list(point_px),
                "project_coordinate": [
                    qsqrt2_to_mapping(project_coordinate[0]),
                    qsqrt2_to_mapping(project_coordinate[1]),
                ],
            }
        ],
    }


def _point_near(graph, expected, tolerance=0.5):
    return next(
        entity
        for entity in graph.geometry_entities.values()
        if entity.kind == "point"
        and math.hypot(
            entity.observed_geometry["point_px"][0] - expected[0],
            entity.observed_geometry["point_px"][1] - expected[1],
        )
        <= tolerance
    )


def _mv_record(line_type=None):
    if line_type in {2, 3}:
        return {
            "status": "assigned",
            "line_type": line_type,
            "source": "source_image_color_evidence",
            "ambiguous": False,
        }
    return {
        "status": "ambiguous",
        "line_type": None,
        "source": None,
        "reason": "ambiguous_source_image_mv",
        "ambiguous": True,
    }


def _direct_topology(point_specs, segment_specs):
    return {
        "enabled": True,
        "points": [
            {
                "id": point_id,
                "point": [float(index), 0.0],
                "boundary_sides": list(boundary_sides),
            }
            for index, (point_id, boundary_sides) in enumerate(point_specs)
        ],
        "segments": [
            {
                "id": segment_id,
                "start_point_id": start_id,
                "end_point_id": end_id,
            }
            for segment_id, start_id, end_id in segment_specs
        ],
    }


class RawCreaseTopologyTest(unittest.TestCase):
    def test_trusted_source_color_evidence_follows_segment_id_into_topology(self):
        raw = _report([_line("horizontal", 0, 50.0, [[10.0, 90.0]])])
        raw["segment_line_type_evidence"] = {
            "enabled": True,
            "mode": "raw_topology_segment_mv_evidence_v1",
            "segments": {
                "raw-segment:0:0": {
                    "status": "assigned",
                    "line_type": 3,
                    "source": "source_image_color_evidence",
                    "red_probability": 0.01,
                    "confidence": 0.99,
                    "coverage": 1.0,
                    "ambiguous": False,
                }
            },
        }

        _, _, topology = build_raw_crease_topology_graph(raw)

        self.assertEqual(topology["segment_count"], 1)
        segment = topology["segments"][0]
        self.assertEqual(segment["id"], "raw-segment:0:0")
        self.assertEqual(segment["line_type"], 3)
        self.assertEqual(
            segment["line_type_source"],
            "source_image_color_evidence",
        )

    def test_default_mountain_evidence_follows_segment_id_into_topology(self):
        segments = [{"id": "neutral-segment"}]
        applied = apply_segment_line_type_evidence(
            segments,
            {
                "segments": {
                    "neutral-segment": {
                        "status": "assigned",
                        "line_type": 2,
                        "source": "source_image_default_mountain",
                        "ambiguous": True,
                    }
                }
            },
        )

        self.assertEqual(segments[0]["line_type"], 2)
        self.assertEqual(
            segments[0]["line_type_source"],
            "source_image_default_mountain",
        )
        self.assertEqual(applied["default_mountain_segment_count"], 1)

    def test_single_unknown_internal_vertex_is_inferred_by_maekawa(self):
        topology = _direct_topology(
            [("center", ()), *((f"edge-{index}", ("top",)) for index in range(4))],
            [
                ("m1", "center", "edge-0"),
                ("m2", "center", "edge-1"),
                ("m3", "center", "edge-2"),
                ("unknown", "center", "edge-3"),
            ],
        )
        report = resolve_topology_segment_line_types(
            topology,
            {
                "segments": {
                    "m1": _mv_record(2),
                    "m2": _mv_record(2),
                    "m3": _mv_record(2),
                    "unknown": _mv_record(),
                }
            },
        )

        unknown = next(item for item in topology["segments"] if item["id"] == "unknown")
        self.assertEqual(unknown["line_type"], 3)
        self.assertEqual(
            unknown["line_type_source"],
            "maekawa_single_unknown_propagation",
        )
        self.assertEqual(report["maekawa_inferred_segment_count"], 1)
        self.assertEqual(report["default_mountain_segment_count"], 0)
        self.assertEqual(report["propagation_round_count"], 1)

    def test_inferred_segment_can_unlock_the_next_internal_vertex(self):
        topology = _direct_topology(
            [
                ("first", ()),
                ("second", ()),
                *((f"edge-{index}", ("top",)) for index in range(6)),
            ],
            [
                ("a", "first", "edge-0"),
                ("b", "first", "edge-1"),
                ("c", "first", "edge-2"),
                ("shared", "first", "second"),
                ("d", "second", "edge-3"),
                ("e", "second", "edge-4"),
                ("tail", "second", "edge-5"),
            ],
        )
        report = resolve_topology_segment_line_types(
            topology,
            {
                "segments": {
                    **{segment_id: _mv_record(2) for segment_id in ("a", "b", "c", "d", "e")},
                    "shared": _mv_record(),
                    "tail": _mv_record(),
                }
            },
        )
        resolved = {item["id"]: item for item in topology["segments"]}

        self.assertEqual(resolved["shared"]["line_type"], 3)
        self.assertEqual(resolved["tail"]["line_type"], 2)
        self.assertEqual(report["maekawa_inferred_segment_count"], 2)
        self.assertEqual(report["propagation_round_count"], 2)
        self.assertEqual(report["default_mountain_segment_count"], 0)

    def test_multiple_unknowns_default_to_red_only_after_fixed_point(self):
        topology = _direct_topology(
            [("center", ()), *((f"edge-{index}", ("top",)) for index in range(4))],
            [
                ("m1", "center", "edge-0"),
                ("m2", "center", "edge-1"),
                ("unknown-a", "center", "edge-2"),
                ("unknown-b", "center", "edge-3"),
            ],
        )
        report = resolve_topology_segment_line_types(
            topology,
            {
                "segments": {
                    "m1": _mv_record(2),
                    "m2": _mv_record(2),
                    "unknown-a": _mv_record(),
                    "unknown-b": _mv_record(),
                }
            },
        )
        resolved = {item["id"]: item for item in topology["segments"]}

        self.assertEqual(report["maekawa_inferred_segment_count"], 0)
        self.assertEqual(report["propagation_round_count"], 0)
        self.assertEqual(report["default_mountain_segment_count"], 2)
        self.assertEqual(
            report["unresolved_before_fallback_segment_ids"],
            ["unknown-a", "unknown-b"],
        )
        self.assertTrue(
            all(
                resolved[segment_id]["line_type"] == 2
                and resolved[segment_id]["line_type_source"]
                == "source_image_default_mountain"
                for segment_id in ("unknown-a", "unknown-b")
            )
        )

    def test_boundary_vertex_does_not_apply_internal_maekawa_rule(self):
        topology = _direct_topology(
            [("boundary", ("top",)), *((f"edge-{index}", ("left",)) for index in range(4))],
            [
                ("m1", "boundary", "edge-0"),
                ("m2", "boundary", "edge-1"),
                ("m3", "boundary", "edge-2"),
                ("unknown", "boundary", "edge-3"),
            ],
        )
        report = resolve_topology_segment_line_types(
            topology,
            {
                "segments": {
                    "m1": _mv_record(2),
                    "m2": _mv_record(2),
                    "m3": _mv_record(2),
                    "unknown": _mv_record(),
                }
            },
        )
        unknown = next(item for item in topology["segments"] if item["id"] == "unknown")

        self.assertEqual(report["maekawa_inferred_segment_count"], 0)
        self.assertEqual(report["skipped_boundary_node_count"], 5)
        self.assertEqual(unknown["line_type"], 2)
        self.assertEqual(unknown["line_type_source"], "source_image_default_mountain")

    def test_opposite_endpoint_inferences_do_not_depend_on_iteration_order(self):
        topology = _direct_topology(
            [
                ("first", ()),
                ("second", ()),
                *((f"edge-{index}", ("top",)) for index in range(6)),
            ],
            [
                ("a", "first", "edge-0"),
                ("b", "first", "edge-1"),
                ("c", "first", "edge-2"),
                ("shared", "first", "second"),
                ("d", "second", "edge-3"),
                ("e", "second", "edge-4"),
                ("f", "second", "edge-5"),
            ],
        )
        report = resolve_topology_segment_line_types(
            topology,
            {
                "segments": {
                    "a": _mv_record(2),
                    "b": _mv_record(2),
                    "c": _mv_record(2),
                    "shared": _mv_record(),
                    "d": _mv_record(2),
                    "e": _mv_record(2),
                    "f": _mv_record(3),
                }
            },
        )
        shared = next(item for item in topology["segments"] if item["id"] == "shared")

        self.assertEqual(report["maekawa_inferred_segment_count"], 0)
        self.assertEqual(shared["line_type_source"], "source_image_default_mountain")
        self.assertIn(
            "opposite_maekawa_inferences_at_segment_ends",
            {item["reason"] for item in report["conflicts"]},
        )

    def test_supported_crossing_becomes_one_incident_point_and_four_segments(self):
        graph, _, topology = build_raw_crease_topology_graph(
            _report(
                [
                    _line("horizontal", 0, 50.0, [[10.0, 90.0]]),
                    _line("vertical", 4, -50.0, [[10.0, 90.0]]),
                ]
            )
        )

        crossing = _point_near(graph, (50.0, 50.0))
        self.assertEqual(crossing.observed_geometry["point_kind"], "line_intersection")
        self.assertEqual(len(graph.incident_entities(crossing.id)), 2)
        self.assertEqual(topology["segment_count"], 4)
        self.assertEqual(
            topology["invariants"]["generated_direction_count"], 0
        )
        self.assertEqual(
            topology["invariants"]["generated_crease_count"], 0
        )

    def test_infinite_extensions_do_not_create_an_unsupported_intersection(self):
        graph, _, topology = build_raw_crease_topology_graph(
            _report(
                [
                    _line("short-horizontal", 0, 20.0, [[10.0, 30.0]]),
                    _line("short-vertical", 4, -50.0, [[40.0, 70.0]]),
                ]
            )
        )

        intersections = [
            entity
            for entity in graph.geometry_entities.values()
            if entity.kind == "point"
            and entity.observed_geometry["point_kind"] == "line_intersection"
        ]
        self.assertEqual(intersections, [])
        self.assertGreater(
            topology["candidate_stats"]["finite_evidence_rejected_intersections"],
            0,
        )
        self.assertTrue(
            topology["invariants"][
                "intersection_requires_two_finite_evidence_intervals"
            ]
        )

    def test_boundary_contact_requires_the_visible_interval_to_reach_the_side(self):
        graph, _, topology = build_raw_crease_topology_graph(
            _report(
                [
                    _line("reaches-top", 4, -30.0, [[0.5, 60.0]]),
                    _line("interior-horizontal", 0, 70.0, [[20.0, 80.0]]),
                ]
            )
        )

        boundary_points = [
            entity
            for entity in graph.geometry_entities.values()
            if entity.kind == "point"
            and entity.observed_geometry["point_kind"] == "boundary_contact"
        ]
        self.assertEqual(len(boundary_points), 1)
        self.assertEqual(boundary_points[0].observed_geometry["boundary_sides"], ["top"])
        self.assertEqual(
            topology["candidate_stats"]["finite_evidence_boundary_contacts"],
            1,
        )
        self.assertGreaterEqual(
            topology["candidate_stats"]["finite_evidence_rejected_boundary_hits"],
            3,
        )

    def test_raw_topology_feeds_the_existing_deterministic_exact_frontier(self):
        diagonal_length = 100.0 * math.sqrt(2.0)
        graph, _, _ = build_raw_crease_topology_graph(
            _report(
                [
                    _line("diagonal", 2, 0.0, [[0.0, diagonal_length]]),
                    _line("vertical", 4, -50.0, [[0.0, 100.0]]),
                    _line("horizontal", 0, 50.0, [[0.0, 100.0]]),
                ]
            )
        )
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        corner = _point_near(graph, (0.0, 0.0))
        top = _point_near(graph, (50.0, 0.0))
        graph.exactify_geometry(
            corner.id,
            {
                "project_coordinate": [qsqrt2_to_mapping(zero), qsqrt2_to_mapping(zero)],
                "side_length": qsqrt2_to_mapping(side_length),
                "exact_generation": 0,
            },
        )
        graph.exactify_geometry(
            top.id,
            {
                "project_coordinate": [qsqrt2_to_mapping(one), qsqrt2_to_mapping(zero)],
                "side_length": qsqrt2_to_mapping(side_length),
                "exact_generation": 0,
            },
        )

        propagation = propagate_exact_geometry(graph, maximum=100.0)
        center = _point_near(graph, (50.0, 50.0))
        horizontal = next(
            entity
            for entity in graph.geometry_entities.values()
            if entity.kind == "crease"
            and entity.observed_geometry["raw_line_id"] == "horizontal"
        )

        self.assertTrue(center.is_exact)
        self.assertTrue(horizontal.is_exact)
        self.assertGreaterEqual(propagation["propagated_point_count"], 1)
        self.assertGreaterEqual(propagation["propagated_crease_count"], 3)
        self.assertEqual(
            propagation["invariants"]["created_point_count"], 0
        )
        self.assertEqual(
            propagation["invariants"]["created_crease_count"], 0
        )

    def test_guided_relation_uses_raw_topology_without_playback_trace(self):
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        raw = _report(
            [
                _line("diagonal", 2, 0.0, [[0.0, 100.0 * math.sqrt(2.0)]]),
                _line("vertical", 4, -50.0, [[0.0, 100.0]]),
                _line("horizontal", 0, 50.0, [[0.0, 100.0]]),
            ]
        )
        relation = {
            "id": "test:top-seeds",
            "label": "top seeds",
            "kind": "test_relation",
            "side": "top",
            "evidence_source": "raw_image_directional_scan",
            "recommended_coordinate_gauge": {
                "side_length": qsqrt2_to_mapping(side_length),
            },
            "points": [
                {
                    "id": "corner",
                    "point_px": [0.0, 0.0],
                    "observed_point_px": [0.0, 0.0],
                    "project_coordinate": [
                        qsqrt2_to_mapping(zero),
                        qsqrt2_to_mapping(zero),
                    ],
                },
                {
                    "id": "top-midpoint",
                    "point_px": [50.0, 0.0],
                    "observed_point_px": [50.0, 0.0],
                    "project_coordinate": [
                        qsqrt2_to_mapping(one),
                        qsqrt2_to_mapping(zero),
                    ],
                },
            ],
        }

        guided = build_guided_boundary_report(
            {
                "stats": {"analysis_size_used": 101},
                "raw_crease_evidence": raw,
                "boundary_relation_candidates": [relation],
            },
            {"id": relation["id"]},
        )

        self.assertTrue(guided["enabled"])
        self.assertEqual(guided["mode"], "guided_boundary_relation_v5")
        self.assertEqual(
            guided["observed_graph_source"],
            "raw_image_finite_line_evidence",
        )
        self.assertEqual(guided["guided_candidate_ray_count"], 2)
        self.assertEqual(
            guided["geometry_graph"]["invariants"]["invented_crease_count"],
            0,
        )
        self.assertEqual(guided["raw_topology"]["crease_count"], 3)
        self.assertEqual(
            guided["geometry_propagation"]["final_exact_crease_count"],
            3,
        )

    def test_guided_relation_cannot_activate_an_infinite_line_extension(self):
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        raw = _report(
            [_line("interior-vertical", 4, -50.0, [[20.0, 80.0]])]
        )
        relation = {
            "id": "test:false-top-contact",
            "label": "false top contact",
            "kind": "test_relation",
            "side": "top",
            "evidence_source": "raw_image_directional_scan",
            "recommended_coordinate_gauge": {
                "side_length": qsqrt2_to_mapping(side_length),
            },
            "points": [
                {
                    "id": "top-midpoint",
                    "point_px": [50.0, 0.0],
                    "observed_point_px": [50.0, 0.0],
                    "project_coordinate": [
                        qsqrt2_to_mapping(one),
                        qsqrt2_to_mapping(zero),
                    ],
                }
            ],
        }

        guided = build_guided_boundary_report(
            {
                "stats": {"analysis_size_used": 101},
                "raw_crease_evidence": raw,
                "boundary_relation_candidates": [relation],
            },
            {"id": relation["id"]},
        )

        self.assertEqual(guided["status"], "no_matching_observed_creases")
        self.assertEqual(guided["guided_candidate_ray_count"], 0)
        self.assertEqual(
            guided["geometry_propagation"]["final_exact_crease_count"],
            0,
        )

    def test_one_boundary_seed_auto_fits_a_separate_existing_component(self):
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        half = qsqrt2_from_coefficients(1, 0, 2)
        one_and_half = qsqrt2_from_coefficients(3, 0, 2)
        two = qsqrt2_from_coefficients(2)
        raw = _report(
            [
                _line("top-component", 4, -25.0, [[0.0, 35.0]]),
                _line("bottom-component", 4, -75.0, [[65.0, 100.0]]),
            ]
        )
        top = _boundary_seed_relation(
            "test:top",
            "top start",
            "top",
            (25.0, 0.0),
            (half, zero),
            side_length,
        )
        bottom = _boundary_seed_relation(
            "test:bottom",
            "bottom continuation",
            "bottom",
            (75.0, 100.0),
            (one_and_half, two),
            side_length,
        )
        bottom["priority"] = 2
        result = {
            "stats": {"analysis_size_used": 101},
            "raw_crease_evidence": raw,
            "boundary_relation_candidates": [top, bottom],
        }

        first = build_guided_boundary_report(
            result,
            {"relation_ids": [top["id"]]},
        )

        self.assertTrue(first["enabled"])
        self.assertEqual(first["phase"], "complete_existing_creases")
        self.assertEqual(first["selected_relation_ids"], [top["id"]])
        self.assertEqual(first["unexplained_observations"], 0)
        self.assertEqual(first["unresolved_crease_entity_ids"], [])
        self.assertEqual(first["next_relation_candidate_count"], 0)
        self.assertEqual(first["next_relation_candidates"], [])
        self.assertEqual(first["automatic_topology_point_count"], 1)
        self.assertEqual(
            first["automatic_topology_point_history"][0][
                "resolved_crease_count"
            ],
            1,
        )
        self.assertEqual(first["geometry_graph"]["crease_count"], 2)
        self.assertEqual(
            first["geometry_graph"]["invariants"]["invented_crease_count"],
            0,
        )

        # Existing projects that explicitly stored the second relation still
        # replay with the same final observed graph.
        second = build_guided_boundary_report(
            result,
            {"relation_ids": [top["id"], bottom["id"]]},
        )

        self.assertTrue(second["enabled"])
        self.assertEqual(second["phase"], "complete_existing_creases")
        self.assertEqual(second["selection_round"], 2)
        self.assertEqual(second["unexplained_observations"], 0)
        self.assertEqual(second["next_relation_candidates"], [])
        self.assertEqual(second["automatic_topology_point_ids"], [])
        self.assertEqual(second["geometry_graph"]["crease_count"], 2)
        self.assertEqual(
            second["geometry_graph"]["invariants"]["invented_crease_count"],
            0,
        )

    def test_multiple_rounds_reject_a_different_global_side_length(self):
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        half = qsqrt2_from_coefficients(1, 0, 2)
        top = _boundary_seed_relation(
            "test:top",
            "top start",
            "top",
            (25.0, 0.0),
            (half, zero),
            side_length,
        )
        incompatible = _boundary_seed_relation(
            "test:wrong-scale",
            "wrong scale",
            "bottom",
            (75.0, 100.0),
            (qsqrt2_from_coefficients(9, 0, 4), qsqrt2_from_coefficients(3)),
            qsqrt2_from_coefficients(3),
        )
        report = build_guided_boundary_report(
            {
                "stats": {"analysis_size_used": 101},
                "raw_crease_evidence": _report(
                    [
                        _line("top-component", 4, -25.0, [[0.0, 35.0]]),
                        _line("bottom-component", 4, -75.0, [[65.0, 100.0]]),
                    ]
                ),
                "boundary_relation_candidates": [top, incompatible],
            },
            {"relation_ids": [top["id"], incompatible["id"]]},
        )

        self.assertFalse(report["enabled"])
        self.assertEqual(report["reason"], "incompatible_relation_coordinate_gauge")
        self.assertEqual(report["incompatible_relation_ids"], [incompatible["id"]])

    def test_internal_topology_point_is_automatically_fitted_after_one_boundary_seed(self):
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        half = qsqrt2_from_coefficients(1, 0, 2)
        raw = _report(
            [
                _line("top-component", 4, -25.0, [[0.0, 35.0]]),
                _line("interior-horizontal", 0, 75.0, [[60.0, 90.0]]),
                _line("interior-vertical", 4, -75.0, [[60.0, 90.0]]),
            ]
        )
        top = _boundary_seed_relation(
            "test:top",
            "top start",
            "top",
            (25.0, 0.0),
            (half, zero),
            side_length,
        )
        result = {
            "stats": {"analysis_size_used": 101},
            "raw_crease_evidence": raw,
            "boundary_relation_candidates": [top],
        }

        first = build_guided_boundary_report(
            result,
            {
                "selection_steps": [
                    {"kind": "boundary_relation", "id": top["id"]},
                ]
            },
        )

        self.assertTrue(first["enabled"])
        self.assertEqual(first["phase"], "complete_existing_creases")
        self.assertEqual(first["next_relation_candidates"], [])
        self.assertEqual(first["next_topology_point_candidates"], [])
        self.assertEqual(first["unexplained_observations"], 0)
        self.assertEqual(first["selection_steps"], [
            {"kind": "boundary_relation", "id": top["id"]},
        ])
        self.assertEqual(first["selected_topology_point_ids"], [])
        self.assertEqual(first["automatic_topology_point_count"], 1)
        crossing = first["automatic_topology_point_history"][0]
        self.assertEqual(first["automatic_topology_point_ids"], [crossing["id"]])
        self.assertEqual(crossing["point_kind"], "line_intersection")
        self.assertEqual(crossing["coordinate_expression"], ["3/2", "3/2"])
        self.assertAlmostEqual(crossing["fit_residual_px"], 0.0, places=6)
        self.assertEqual(crossing["resolved_crease_count"], 2)
        self.assertEqual(crossing["remaining_unresolved_crease_count"], 0)
        self.assertEqual(
            [item["step_kind"] for item in first["selection_history"]],
            ["boundary_relation"],
        )
        self.assertEqual(first["geometry_graph"]["crease_count"], 3)
        self.assertEqual(
            first["geometry_graph"]["invariants"]["invented_crease_count"],
            0,
        )
        self.assertEqual(
            first["geometry_graph"]["invariants"]["invented_direction_count"],
            0,
        )

        # Old projects that already stored the same point as an explicit step
        # still replay deterministically instead of being invalidated.
        steps = [
            {"kind": "boundary_relation", "id": top["id"]},
            {"kind": "topology_point", "id": crossing["id"]},
        ]
        completed = build_guided_boundary_report(
            result,
            {"selection_steps": steps},
        )

        self.assertTrue(completed["enabled"])
        self.assertEqual(completed["phase"], "complete_existing_creases")
        self.assertEqual(completed["selection_steps"], steps)
        self.assertEqual(completed["selected_topology_point_ids"], [crossing["id"]])
        self.assertEqual(completed["automatic_topology_point_ids"], [])
        self.assertEqual(
            [item["step_kind"] for item in completed["selection_history"]],
            ["boundary_relation", "topology_point"],
        )
        self.assertEqual(completed["unexplained_observations"], 0)
        self.assertEqual(completed["geometry_graph"]["crease_count"], 3)
        self.assertEqual(
            completed["geometry_graph"]["invariants"]["invented_crease_count"],
            0,
        )
        self.assertEqual(
            completed["geometry_graph"]["invariants"]["invented_direction_count"],
            0,
        )

    def test_arbitrary_cursor_coordinate_cannot_become_a_topology_seed(self):
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        half = qsqrt2_from_coefficients(1, 0, 2)
        top = _boundary_seed_relation(
            "test:top",
            "top start",
            "top",
            (25.0, 0.0),
            (half, zero),
            side_length,
        )
        report = build_guided_boundary_report(
            {
                "stats": {"analysis_size_used": 101},
                "raw_crease_evidence": _report(
                    [
                        _line("top-component", 4, -25.0, [[0.0, 35.0]]),
                        _line("interior-horizontal", 0, 75.0, [[60.0, 90.0]]),
                        _line("interior-vertical", 4, -75.0, [[60.0, 90.0]]),
                    ]
                ),
                "boundary_relation_candidates": [top],
            },
            {
                "selection_steps": [
                    {"kind": "boundary_relation", "id": top["id"]},
                    {"kind": "topology_point", "id": "cursor:73.2,74.8"},
                ]
            },
        )

        self.assertFalse(report["enabled"])
        self.assertEqual(report["reason"], "invalid_guided_topology_point")
        self.assertNotIn(
            "cursor:73.2,74.8",
            report["available_topology_point_ids"],
        )


if __name__ == "__main__":
    unittest.main()
