import unittest
from fractions import Fraction
import math

from exact_qsqrt2 import Qsqrt2
from finite_endpoint_closure import build_finite_endpoint_closed_topology
from guided_cp_output import build_guided_cp_output_contract
from qsqrt2_coordinates import qsqrt2_to_mapping


def _q(numerator, denominator=1):
    return Qsqrt2(Fraction(numerator, denominator))


def _point(entity_id, exact_xy, observed_xy, *, boundary_sides=()):
    exact_geometry = {}
    if exact_xy is not None:
        exact_geometry = {
            "project_coordinate": [
                qsqrt2_to_mapping(exact_xy[0]),
                qsqrt2_to_mapping(exact_xy[1]),
            ]
        }
    return {
        "id": entity_id,
        "kind": "point",
        "observed_geometry": {
            "point_px": list(observed_xy),
            "point_kind": "boundary_contact" if boundary_sides else "finite_endpoint",
            "boundary_sides": list(boundary_sides),
        },
        "exact_geometry": exact_geometry,
        "evidence_sources": ["raw_image_finite_line_evidence"],
    }


def _crease(entity_id, through_xy, direction):
    return {
        "id": entity_id,
        "kind": "crease",
        "observed_geometry": {"direction_index": direction},
        "exact_geometry": {
            "through_point_project": [
                qsqrt2_to_mapping(through_xy[0]),
                qsqrt2_to_mapping(through_xy[1]),
            ],
            "direction_index": direction,
        },
        "evidence_sources": ["raw_image_finite_line_evidence"],
    }


def _segment(segment_id, crease_id, start_id, end_id, orientation, length):
    return {
        "id": segment_id,
        "source": "raw_image_finite_line_evidence",
        "crease_entity_id": crease_id,
        "orientation": orientation,
        "start_point_id": start_id,
        "end_point_id": end_id,
        "length_px": length,
        "visible_coverage": 1.0,
        "unsupported_length_px": 0.0,
    }


def _report(entities, segments):
    return {
        "enabled": True,
        "phase": "complete_existing_creases",
        "global_side_length": qsqrt2_to_mapping(Qsqrt2(1)),
        "geometry_propagation": {"unresolved_crease_count": 0},
        "geometry_graph": {"entities": entities},
        "raw_topology": {
            "enabled": True,
            "mode": "raw_finite_crease_topology_v1",
            "maximum_coordinate_px": 100.0,
            "segments": segments,
            "tolerances": {"incidence_margin_px": 3.5},
            "invariants": {
                "generated_crease_count": 0,
                "generated_direction_count": 0,
            },
        },
    }


class FiniteEndpointClosureTest(unittest.TestCase):
    def test_missing_detector_endpoint_binds_to_existing_exact_node_on_crease(self):
        report = _report(
            [
                _point("left", (_q(1, 5), _q(1, 2)), (20, 50)),
                _point("terminal", None, (75, 50)),
                _point("target", (_q(4, 5), _q(1, 2)), (80, 50)),
                _crease("horizontal", (Qsqrt2(), _q(1, 2)), 0),
            ],
            [_segment("segment", "horizontal", "left", "terminal", 0, 55)],
        )

        closed = build_finite_endpoint_closed_topology(report)

        self.assertTrue(closed["enabled"])
        self.assertEqual(closed["endpoint_closure"]["status"], "complete")
        self.assertEqual(closed["endpoint_closure"]["binding_count"], 1)
        self.assertEqual(closed["segments"][0]["end_point_id"], "target")
        binding = closed["endpoint_closure"]["bindings"][0]
        self.assertEqual(binding["observed_point_id"], "terminal")
        self.assertEqual(binding["resolved_point_id"], "target")
        self.assertEqual(binding["gap_px"], 5.0)
        self.assertEqual(binding["endpoint_gap_limit_px"], 5.0)
        self.assertEqual(closed["invariants"]["generated_point_count"], 0)
        self.assertEqual(closed["invariants"]["generated_crease_count"], 0)
        self.assertEqual(closed["invariants"]["generated_direction_count"], 0)

    def test_short_detector_linehead_collapses_instead_of_becoming_zero_length(self):
        report = _report(
            [
                _point("node", (_q(1, 2), _q(1, 2)), (50, 50)),
                _point("terminal", None, (55, 50)),
                _crease("horizontal", (Qsqrt2(), _q(1, 2)), 0),
            ],
            [_segment("linehead", "horizontal", "node", "terminal", 0, 5)],
        )

        closed = build_finite_endpoint_closed_topology(report)

        self.assertEqual(closed["segments"], [])
        self.assertEqual(closed["endpoint_closure"]["collapsed_segment_count"], 1)
        self.assertEqual(
            closed["endpoint_closure"]["collapsed_segments"][0]["reason"],
            "collapsed_detector_linehead",
        )
        self.assertEqual(
            closed["endpoint_closure"]["absorbed_observed_point_ids"],
            ["terminal"],
        )

    def test_shared_fuzzy_point_is_resolved_per_segment_not_globally(self):
        half = _q(1, 2)
        report = _report(
            [
                _point("fuzzy", None, (50, 50)),
                _point("left", (_q(1, 5), half), (20, 50)),
                _point("top", (half, _q(1, 5)), (50, 20)),
                _point("horizontal-target", (_q(11, 20), half), (55, 50)),
                _point("vertical-target", (half, _q(11, 20)), (50, 55)),
                _crease("horizontal", (Qsqrt2(), half), 0),
                _crease("vertical", (half, Qsqrt2()), 4),
            ],
            [
                _segment("h", "horizontal", "left", "fuzzy", 0, 30),
                _segment("v", "vertical", "top", "fuzzy", 4, 30),
            ],
        )

        closed = build_finite_endpoint_closed_topology(report)

        by_id = {item["id"]: item for item in closed["segments"]}
        self.assertEqual(by_id["h"]["end_point_id"], "horizontal-target")
        self.assertEqual(by_id["v"]["end_point_id"], "vertical-target")
        self.assertEqual(closed["endpoint_closure"]["binding_count"], 2)
        self.assertTrue(
            closed["endpoint_closure"]["invariants"][
                "closure_is_per_segment_endpoint"
            ]
        )
        self.assertEqual(
            closed["endpoint_closure"]["invariants"]["global_observed_point_merges"],
            0,
        )

    def test_far_existing_node_is_rejected_by_scale_and_local_gap_limit(self):
        half = _q(1, 2)
        report = _report(
            [
                _point("left", (_q(1, 5), half), (20, 50)),
                _point("terminal", None, (74, 50)),
                _point("far", (_q(4, 5), half), (80, 50)),
                _crease("horizontal", (Qsqrt2(), half), 0),
            ],
            [_segment("segment", "horizontal", "left", "terminal", 0, 54)],
        )

        closed = build_finite_endpoint_closed_topology(report)

        self.assertEqual(closed["endpoint_closure"]["status"], "partial")
        self.assertEqual(closed["segments"][0]["end_point_id"], "terminal")
        rejection = closed["endpoint_closure"]["unresolved_endpoint_occurrences"][0]
        self.assertEqual(rejection["reason"], "no_existing_exact_node_within_gap_limit")
        self.assertEqual(rejection["endpoint_gap_limit_px"], 5.0)

    def test_exact_endpoint_on_wrong_line_is_rebound_to_collinear_node(self):
        half = _q(1, 2)
        report = _report(
            [
                _point("left", (_q(1, 5), half), (20, 50)),
                _point("wrong", (_q(3, 4), _q(51, 100)), (75, 50)),
                _point("target", (_q(4, 5), half), (80, 50)),
                _crease("horizontal", (Qsqrt2(), half), 0),
            ],
            [_segment("segment", "horizontal", "left", "wrong", 0, 55)],
        )

        closed = build_finite_endpoint_closed_topology(report)

        self.assertEqual(closed["segments"][0]["end_point_id"], "target")
        self.assertEqual(
            closed["endpoint_closure"]["bindings"][0]["reason"],
            "exact_endpoint_not_on_selected_crease",
        )

    def test_observed_boundary_terminal_uses_exact_crease_boundary_intersection(self):
        report = _report(
            [
                _point("top-terminal", None, (40, 0), boundary_sides=("top",)),
                _point("inside", (_q(3, 5), _q(9, 50)), (60, 18)),
                _crease("diagonal", (_q(21, 50), Qsqrt2()), 2),
            ],
            [_segment("segment", "diagonal", "top-terminal", "inside", 2, 26.9)],
        )

        closed = build_finite_endpoint_closed_topology(report)

        segment = closed["segments"][0]
        self.assertEqual(segment["start_point_id"], "top-terminal")
        self.assertIn("start_exact_project_coordinate", segment)
        binding = closed["endpoint_closure"]["bindings"][0]
        self.assertEqual(binding["target_kind"], "known_paper_boundary_intersection")
        self.assertEqual(binding["boundary_side"], "top")
        self.assertEqual(binding["gap_px"], 2.0)
        self.assertEqual(
            closed["endpoint_closure"]["resolved_boundary_observed_point_ids"],
            ["top-terminal"],
        )

    def test_near_boundary_finite_endpoint_recovers_occluded_boundary_contact(self):
        report = _report(
            [
                _point("top-terminal", None, (39, 3)),
                _point("inside", (_q(3, 5), _q(6, 25)), (60, 24)),
                _crease("diagonal", (_q(9, 25), Qsqrt2()), 2),
            ],
            [_segment("segment", "diagonal", "top-terminal", "inside", 2, 30)],
        )

        closed = build_finite_endpoint_closed_topology(report)

        self.assertEqual(closed["endpoint_closure"]["status"], "complete")
        binding = closed["endpoint_closure"]["bindings"][0]
        self.assertEqual(binding["target_kind"], "known_paper_boundary_intersection")
        self.assertEqual(binding["boundary_side"], "top")
        self.assertTrue(binding["boundary_side_inferred"])
        self.assertAlmostEqual(binding["gap_px"], math.sqrt(18), places=6)
        self.assertEqual(
            closed["endpoint_closure"]["invariants"]["generated_point_count"],
            0,
        )
        self.assertEqual(
            closed["endpoint_closure"]["invariants"]["generated_crease_count"],
            0,
        )

        report["finite_topology"] = closed
        contract = build_guided_cp_output_contract(
            report,
            segment_line_types={"segment": 2},
        )
        blocker_codes = {item["code"] for item in contract["blockers"]}
        self.assertNotIn("endpoint_residual_exceeds_tolerance", blocker_codes)

    def test_dangling_split_terminal_rebases_existing_crease_without_new_direction(self):
        p31_exact = (_q(12, 25), _q(49, 100))
        p32_exact = (_q(1, 2), _q(1, 2))
        direction_one = (Qsqrt2(1, 1), Qsqrt2(1))
        old_left_y = p31_exact[1] - p31_exact[0] * direction_one[1] / direction_one[0]
        old_left_px = float(old_left_y) * 100.0
        crease_a = _crease("a", p31_exact, 1)
        angle = math.radians(22.5)
        crease_a["observed_geometry"].update(
            {
                "angle_deg": 22.5,
                "line_offset_px": -math.sin(angle) * 48.0 + math.cos(angle) * 49.0,
                "match_tolerance_px": 2.5,
            }
        )
        crease_b = _crease("b", (Qsqrt2(), _q(1, 2)), 0)
        crease_b["observed_geometry"].update(
            {
                "angle_deg": 0.0,
                "line_offset_px": 50.0,
                "match_tolerance_px": 2.5,
            }
        )
        report = _report(
            [
                _point("a-left", (Qsqrt2(), old_left_y), (0, old_left_px), boundary_sides=("left",)),
                _point("b-left", (Qsqrt2(), _q(1, 2)), (0, 50), boundary_sides=("left",)),
                _point("split", p31_exact, (48, 50)),
                _point("node", p32_exact, (50, 50)),
                crease_a,
                crease_b,
            ],
            [
                _segment("a-segment", "a", "a-left", "split", 1, 53),
                _segment("b-main", "b", "b-left", "split", 0, 48),
                _segment("b-linehead", "b", "split", "node", 0, 2),
            ],
        )

        closed = build_finite_endpoint_closed_topology(report)

        closure = closed["endpoint_closure"]
        self.assertEqual(closure["crease_placement_repair_count"], 1)
        self.assertEqual(closure["internal_dangling_endpoint_count"], 0)
        repair = closure["crease_placement_repairs"][0]
        self.assertEqual(repair["crease_entity_id"], "a")
        self.assertEqual(repair["resolved_point_id"], "node")
        self.assertEqual(repair["direction_index"], 1)
        self.assertEqual(repair["generated_crease_count"], 0)
        self.assertEqual(repair["generated_direction_count"], 0)
        by_id = {item["id"]: item for item in closed["segments"]}
        self.assertEqual(by_id["a-segment"]["end_point_id"], "node")
        self.assertIn("start_exact_project_coordinate", by_id["a-segment"])

        report["finite_topology"] = closed
        contract = build_guided_cp_output_contract(
            report,
            segment_line_types={"a-segment": 2, "b-main": 3},
        )
        self.assertFalse(contract["output_ready"])
        self.assertFalse(contract["cp_available"])
        self.assertIsNone(contract["cp"])
        self.assertEqual(
            contract["blocker_counts"]["camv_foldability_violations"],
            1,
        )
        self.assertEqual(contract["invariants"]["crease_placement_repair_count"], 1)


if __name__ == "__main__":
    unittest.main()
