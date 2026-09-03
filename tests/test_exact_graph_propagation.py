import unittest

from construction_search import ConstructionGraph, GeometryEntity
from exact_graph_propagation import propagate_exact_geometry
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

    def test_observed_canonical_endpoint_gap_bridges_to_one_exact_line(self):
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

        exact = graph.geometry_entity("observed-horizontal").exact_geometry
        self.assertEqual(
            exact["source"],
            "existing_canonical_crease_endpoint_from_exact_point",
        )
        self.assertEqual(exact["source_point_id"], "proved")
        self.assertEqual(exact["direction_index"], 0)
        self.assertEqual(exact["endpoint_gap_px"], 3.0)
        self.assertIn("observed-horizontal", graph.incidence["proved"])
        self.assertEqual(report["endpoint_bridge_applied_count"], 1)
        self.assertEqual(report["endpoint_bridge_added_incidence_count"], 1)
        self.assertEqual(report["invariants"]["enumerated_direction_count"], 0)

    def test_endpoint_bridge_rejects_two_distinct_exact_parallel_lines(self):
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
        self.assertEqual(
            report["rejection_counts"].get(
                "ambiguous_endpoint_bridge_exact_line", 0
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
