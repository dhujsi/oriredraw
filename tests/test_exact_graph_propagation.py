import unittest

from construction_search import ConstructionGraph, GeometryEntity
from exact_graph_propagation import exactify_anchored_boundary_divisions, propagate_exact_geometry
from construction_proof_topology import build_construction_proof_topology
from qsqrt2_coordinates import qsqrt2_from_coefficients, qsqrt2_to_mapping


def _point_mapping(x, y):
    return [qsqrt2_to_mapping(x), qsqrt2_to_mapping(y)]


def _point(entity_id, pixel, *, exact=None, side_length=None):
    exact_geometry = {}
    if exact is not None:
        exact_geometry = {
            "project_coordinate": _point_mapping(*exact),
            "side_length": qsqrt2_to_mapping(side_length),
            "exact_generation": 0,
        }
    return GeometryEntity(
        id=entity_id,
        kind="point",
        observed_geometry={"point_px": list(pixel)},
        exact_geometry=exact_geometry,
        evidence_sources={"playback_trace"},
    )


def _crease(entity_id, direction_index, offset, *, through=None, side_length=None):
    exact_geometry = {}
    if through is not None:
        exact_geometry = {
            "direction_index": direction_index,
            "direction_deg": direction_index * 22.5,
            "through_point_project": _point_mapping(*through),
            "side_length": qsqrt2_to_mapping(side_length),
            "exact_generation": 0,
        }
    return GeometryEntity(
        id=entity_id,
        kind="crease",
        observed_geometry={
            "direction_index": direction_index,
            "angle_deg": direction_index * 22.5,
            "line_offset_px": offset,
            "match_tolerance_px": 2.0,
        },
        exact_geometry=exact_geometry,
        evidence_sources={"playback_trace"},
    )


class ExactGraphPropagationTest(unittest.TestCase):
    @staticmethod
    def _bounded_stripes(rows=(12.5, 25.0, 37.5), *, paired=True):
        graph = ConstructionGraph()
        side = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        for edge, x in (("left", zero), ("right", side)):
            anchor = _point(f"{edge}-bound", (float(x) * 50, 50.0),
                            exact=(x, one), side_length=side)
            anchor.exact_geometry["source_relation_id"] = "selected-bound"
            graph.add_geometry_entity(anchor)
        for index, y in enumerate(rows):
            crease = _crease(f"stripe-{index}", 0, y)
            crease.observed_geometry.update(
                support_fraction=1.0, evidence_intervals_px=[[0.0, 100.0]],
            )
            graph.add_geometry_entity(crease)
            for edge, x in (("left", 0.0), ("right", 100.0)):
                if edge == "right" and not paired:
                    continue
                point = _point(f"{edge}-{index}", (x, y))
                point.observed_geometry["boundary_sides"] = [edge]
                graph.add_geometry_entity(point)
                graph.connect_incidence(point.id, crease.id)
        return graph

    def test_complete_two_sided_run_uses_exact_bounds_not_fitted_coordinates(self):
        graph = self._bounded_stripes((12.6, 24.9, 37.6))
        entity_count = len(graph.geometry_entities)
        report = exactify_anchored_boundary_divisions(graph, maximum=100.0)
        self.assertEqual(report["applied_point_count"], 6)
        self.assertEqual(report["runs"][0]["division_count"], 4)
        self.assertEqual(len(graph.geometry_entities), entity_count)
        self.assertEqual(
            graph.geometry_entity("left-0").exact_geometry["project_coordinate"],
            _point_mapping(qsqrt2_from_coefficients(0), qsqrt2_from_coefficients(1, 0, 4)),
        )
        propagated = propagate_exact_geometry(graph, maximum=100.0)
        self.assertEqual(propagated["final_exact_crease_count"], 3)
        proof = build_construction_proof_topology(
            graph.geometry_snapshot(), {"enabled": True, "segments": []},
        )
        self.assertIn("stripe-0", proof["proved_crease_ids"])
        self.assertIn("left-0", proof["proved_point_ids"])

    def test_boundary_division_rejects_missing_extra_or_nonuniform_members(self):
        for rows in ((12.5, 37.5), (12.5, 22.0, 25.0, 37.5), (10.0, 25.0, 39.0)):
            with self.subTest(rows=rows):
                graph = self._bounded_stripes(rows)
                report = exactify_anchored_boundary_divisions(graph, maximum=100.0)
                self.assertEqual(report["applied_point_count"], 0)
                self.assertFalse(graph.geometry_entity("stripe-0").is_exact)

    def test_boundary_division_also_supports_top_bottom_contacts(self):
        graph = self._bounded_stripes()
        for entity in graph.geometry_entities.values():
            observed, exact = entity.observed_geometry, entity.exact_geometry
            if entity.kind == "point":
                observed["point_px"].reverse()
                observed["boundary_sides"] = [
                    {"left": "top", "right": "bottom"}[s]
                    for s in observed.get("boundary_sides", [])
                ]
                if "project_coordinate" in exact:
                    exact["project_coordinate"].reverse()
            else:
                observed.update(direction_index=4, angle_deg=90.0,
                                line_offset_px=-observed["line_offset_px"])
        report = exactify_anchored_boundary_divisions(graph, maximum=100.0)
        self.assertEqual(report["applied_point_count"], 6)
        self.assertEqual(report["runs"][0]["sides"], ["top", "bottom"])
        self.assertEqual(
            graph.geometry_entity("left-0").exact_geometry["project_coordinate"],
            _point_mapping(qsqrt2_from_coefficients(1, 0, 4), qsqrt2_from_coefficients(0)),
        )

    def test_boundary_division_requires_opposite_edge_and_finite_stroke_evidence(self):
        graph = self._bounded_stripes(paired=False)
        self.assertEqual(
            exactify_anchored_boundary_divisions(graph, maximum=100.0)["applied_point_count"], 0,
        )
        for field, value in (("support_fraction", 0.2), ("evidence_intervals_px", [[20.0, 70.0]])):
            with self.subTest(field=field):
                graph = self._bounded_stripes()
                graph.geometry_entity("stripe-1").observed_geometry[field] = value
                self.assertEqual(
                    exactify_anchored_boundary_divisions(graph, maximum=100.0)["applied_point_count"], 0,
                )

    def test_boundary_division_requires_existing_proved_bounds(self):
        graph = self._bounded_stripes()
        for edge in ("left", "right"):
            graph.geometry_entity(f"{edge}-bound").exact_geometry = {
                "side_length": qsqrt2_to_mapping(qsqrt2_from_coefficients(2)),
            }
        self.assertEqual(
            exactify_anchored_boundary_divisions(graph, maximum=100.0)["applied_point_count"], 0,
        )

    def test_boundary_division_evidence_cannot_bypass_an_unproved_parent(self):
        graph = self._bounded_stripes()
        exactify_anchored_boundary_divisions(graph, maximum=100.0)
        parent = graph.geometry_entity("left-bound")
        parent.exact_geometry.pop("source_relation_id")
        parent.exact_geometry["source"] = "guided_automatic_topology_point"
        proof = build_construction_proof_topology(
            graph.geometry_snapshot(), {"enabled": True, "segments": []},
        )
        self.assertNotIn("left-0", proof["proved_point_ids"])
        graph.geometry_entity("right-0").exact_geometry["boundary_division_evidence"] = {}
        proof = build_construction_proof_topology(
            graph.geometry_snapshot(), {"enabled": True, "segments": []},
        )
        self.assertNotIn("right-0", proof["proved_point_ids"])

    def test_exact_crease_intersection_then_exact_point_propagates_to_existing_child(self):
        graph = ConstructionGraph()
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        graph.add_geometry_entity(
            _point("seed", (0.0, 0.0), exact=(zero, zero), side_length=side_length)
        )
        graph.add_geometry_entity(_point("intersection", (50.0, 50.0)))
        graph.add_geometry_entity(
            _crease("diagonal", 2, 0.0, through=(zero, zero), side_length=side_length)
        )
        graph.add_geometry_entity(
            _crease("vertical", 4, -50.0, through=(one, zero), side_length=side_length)
        )
        graph.add_geometry_entity(_crease("child", 0, 50.0))
        graph.connect_incidence("seed", "diagonal")
        for crease_id in ("diagonal", "vertical", "child"):
            graph.connect_incidence("intersection", crease_id)

        report = propagate_exact_geometry(graph, maximum=100.0)

        self.assertEqual(report["propagated_point_count"], 1)
        self.assertEqual(report["propagated_crease_count"], 1)
        self.assertEqual(
            graph.geometry_entity("intersection").exact_geometry["project_coordinate"],
            _point_mapping(one, one),
        )
        self.assertEqual(graph.geometry_entity("child").exact_geometry["direction_index"], 0)
        self.assertEqual(report["invariants"]["enumerated_direction_count"], 0)
        self.assertEqual(report["invariants"]["created_crease_count"], 0)

    def test_one_exact_crease_can_exactify_an_existing_paper_boundary_point(self):
        graph = ConstructionGraph()
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        graph.add_geometry_entity(_point("top-contact", (50.0, 0.0)))
        graph.add_geometry_entity(
            _crease("vertical", 4, -50.0, through=(one, one), side_length=side_length)
        )
        graph.connect_incidence("top-contact", "vertical")

        report = propagate_exact_geometry(graph, maximum=100.0)

        self.assertEqual(report["propagated_point_count"], 1)
        self.assertEqual(
            graph.geometry_entity("top-contact").exact_geometry["project_coordinate"],
            _point_mapping(one, zero),
        )
        self.assertEqual(
            graph.geometry_entity("top-contact").exact_geometry["source"],
            "existing_crease_paper_boundary_intersection",
        )

    def test_22_5_degree_intersection_remains_exact_in_qsqrt2(self):
        graph = ConstructionGraph()
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        two = qsqrt2_from_coefficients(2)
        expected_x = qsqrt2_from_coefficients(1)
        expected_y = qsqrt2_from_coefficients(-1, 1)
        graph.add_geometry_entity(
            _point("apex", (50.0, float(expected_y / side_length) * 100.0))
        )
        graph.add_geometry_entity(
            _crease("down-right", 1, 0.0, through=(zero, zero), side_length=side_length)
        )
        graph.add_geometry_entity(
            _crease("down-left", 7, 0.0, through=(two, zero), side_length=side_length)
        )
        graph.connect_incidence("apex", "down-right")
        graph.connect_incidence("apex", "down-left")

        report = propagate_exact_geometry(graph, maximum=100.0)

        self.assertEqual(report["propagated_point_count"], 1)
        self.assertEqual(
            graph.geometry_entity("apex").exact_geometry["project_coordinate"],
            _point_mapping(expected_x, expected_y),
        )

    def test_disagreeing_exact_intersections_are_pruned_instead_of_guessed(self):
        graph = ConstructionGraph()
        side_length = qsqrt2_from_coefficients(2)
        zero = qsqrt2_from_coefficients(0)
        one = qsqrt2_from_coefficients(1)
        shifted = qsqrt2_from_coefficients(51, 0, 50)
        graph.add_geometry_entity(_point("ambiguous", (50.5, 50.0)))
        graph.add_geometry_entity(
            _crease("vertical-a", 4, -50.0, through=(one, zero), side_length=side_length)
        )
        graph.add_geometry_entity(
            _crease("vertical-b", 4, -51.0, through=(shifted, zero), side_length=side_length)
        )
        graph.add_geometry_entity(
            _crease("horizontal", 0, 50.0, through=(zero, one), side_length=side_length)
        )
        for crease_id in ("vertical-a", "vertical-b", "horizontal"):
            graph.connect_incidence("ambiguous", crease_id)

        report = propagate_exact_geometry(graph, maximum=100.0)

        self.assertFalse(graph.geometry_entity("ambiguous").is_exact)
        self.assertGreater(
            report["rejection_counts"].get("conflicting_exact_candidates", 0),
            0,
        )

    def test_nearby_observed_endpoint_does_not_create_new_incidence(self):
        graph = ConstructionGraph()
        side_length = qsqrt2_from_coefficients(2)
        one = qsqrt2_from_coefficients(1)
        graph.add_geometry_entity(
            _point("proved", (50.0, 50.0), exact=(one, one), side_length=side_length)
        )
        graph.add_geometry_entity(_point("detector-terminal", (53.0, 50.0)))
        crease = _crease("observed-horizontal", 0, 50.0)
        crease.observed_geometry["evidence_intervals_px"] = [[53.0, 90.0]]
        graph.add_geometry_entity(crease)
        graph.connect_incidence("detector-terminal", "observed-horizontal")

        report = propagate_exact_geometry(graph, maximum=100.0)

        self.assertFalse(graph.geometry_entity("observed-horizontal").is_exact)
        self.assertNotIn("observed-horizontal", graph.incidence["proved"])
        self.assertEqual(report["endpoint_bridge_applied_count"], 0)
        self.assertEqual(report["endpoint_bridge_added_incidence_count"], 0)
        self.assertEqual(report["invariants"]["enumerated_direction_count"], 0)
        self.assertFalse(
            report["invariants"]["proximity_endpoint_bridge_enabled"]
        )

    def test_nearby_parallel_exact_lines_do_not_nominate_an_observed_crease(self):
        graph = ConstructionGraph()
        side_length = qsqrt2_from_coefficients(2)
        one = qsqrt2_from_coefficients(1)
        lower = qsqrt2_from_coefficients(49, 0, 50)
        upper = qsqrt2_from_coefficients(51, 0, 50)
        graph.add_geometry_entity(
            _point("lower", (50.0, 49.0), exact=(one, lower), side_length=side_length)
        )
        graph.add_geometry_entity(
            _point("upper", (50.0, 51.0), exact=(one, upper), side_length=side_length)
        )
        crease = _crease("ambiguous-horizontal", 0, 50.0)
        crease.observed_geometry["evidence_intervals_px"] = [[53.0, 90.0]]
        graph.add_geometry_entity(crease)

        report = propagate_exact_geometry(graph, maximum=100.0)

        self.assertFalse(graph.geometry_entity("ambiguous-horizontal").is_exact)
        self.assertEqual(report["endpoint_bridge_applied_count"], 0)
        self.assertTrue(
            report["invariants"][
                "new_incidence_requires_explicit_topology_evidence"
            ]
        )


if __name__ == "__main__":
    unittest.main()
