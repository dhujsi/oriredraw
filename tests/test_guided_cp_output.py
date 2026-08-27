import unittest

from cp_audit import parse_cp
from exact_qsqrt2 import Qsqrt2
from finite_endpoint_closure import build_finite_endpoint_closed_topology
from guided_cp_output import build_guided_cp_output_contract
from qsqrt2_coordinates import qsqrt2_to_mapping


def _point_entity(entity_id, coordinate, observed, boundary_sides=()):
    return {
        "id": entity_id,
        "kind": "point",
        "observed_geometry": {
            "point_px": list(observed),
            "point_kind": "boundary_contact" if boundary_sides else "line_intersection",
            "boundary_sides": list(boundary_sides),
            "match_tolerance_px": 1.0,
        },
        "exact_geometry": {
            "project_coordinate": [
                qsqrt2_to_mapping(coordinate[0]),
                qsqrt2_to_mapping(coordinate[1]),
            ]
        },
        "evidence_sources": ["raw_image_finite_line_evidence"],
    }


def _complete_report():
    side_length = Qsqrt2(1, 1)
    half = side_length / 2
    points = [
        _point_entity("left", (Qsqrt2(), half), (0.0, 50.0), ("left",)),
        _point_entity("middle", (half, half), (50.0, 50.0)),
        _point_entity("right", (side_length, half), (100.0, 50.0), ("right",)),
    ]
    crease = {
        "id": "crease",
        "kind": "crease",
        "observed_geometry": {
            "direction_index": 0,
            "angle_deg": 0.0,
            "line_offset_px": 50.0,
            "match_tolerance_px": 1.0,
        },
        "exact_geometry": {
            "direction_index": 0,
            "through_point_project": [
                qsqrt2_to_mapping(Qsqrt2()),
                qsqrt2_to_mapping(half),
            ],
        },
        "evidence_sources": ["raw_image_finite_line_evidence"],
    }
    segments = [
        {
            "id": "left-half",
            "source": "raw_image_finite_line_evidence",
            "crease_entity_id": "crease",
            "orientation": 0,
            "start_point_id": "left",
            "end_point_id": "middle",
            "visible_coverage": 1.0,
            "unsupported_length_px": 0.0,
        },
        {
            "id": "right-half",
            "source": "raw_image_finite_line_evidence",
            "crease_entity_id": "crease",
            "orientation": 0,
            "start_point_id": "middle",
            "end_point_id": "right",
            "visible_coverage": 1.0,
            "unsupported_length_px": 0.0,
        },
    ]
    return {
        "enabled": True,
        "phase": "complete_existing_creases",
        "global_side_length": qsqrt2_to_mapping(side_length),
        "geometry_propagation": {"unresolved_crease_count": 0},
        "geometry_graph": {"entities": [*points, crease]},
        "raw_topology": {
            "enabled": True,
            "mode": "raw_finite_crease_topology_v1",
            "maximum_coordinate_px": 100.0,
            "segments": segments,
            "tolerances": {"incidence_margin_px": 1.0},
            "invariants": {
                "generated_crease_count": 0,
                "generated_direction_count": 0,
            },
        },
        # A stale upstream CP must never be reused by this contract.
        "cp": "2 -200 -200 200 200\n",
    }


class GuidedCpOutputContractTest(unittest.TestCase):
    def test_embedded_source_color_evidence_needs_no_manual_assignment(self):
        report = _complete_report()
        for index, segment in enumerate(report["raw_topology"]["segments"]):
            segment["line_type"] = 2 if index == 0 else 3
            segment["line_type_source"] = "source_image_color_evidence"

        contract = build_guided_cp_output_contract(report)

        self.assertTrue(contract["output_ready"])
        self.assertEqual(contract["unassigned_segment_ids"], [])
        self.assertEqual(
            contract["segment_line_type_assignments"],
            {
                "left-half": {
                    "line_type": 2,
                    "source": "source_image_color_evidence",
                },
                "right-half": {
                    "line_type": 3,
                    "source": "source_image_color_evidence",
                },
            },
        )

    def test_emits_only_when_finite_geometry_and_segment_types_are_complete(self):
        report = _complete_report()
        contract = build_guided_cp_output_contract(
            report,
            segment_line_types={
                "left-half": {"line_type": 2, "source": "source_image_color_evidence"},
                "right-half": {"line_type": 3, "source": "user_confirmed"},
            },
        )

        self.assertTrue(contract["output_ready"])
        self.assertEqual(contract["status"], "ready")
        self.assertEqual(contract["required_internal_segment_count"], 2)
        self.assertEqual(contract["candidate_internal_segment_count"], 2)
        self.assertEqual(contract["typed_candidate_internal_segment_count"], 2)
        self.assertEqual(
            contract["segment_line_type_assignments"],
            {
                "left-half": {
                    "line_type": 2,
                    "source": "source_image_color_evidence",
                },
                "right-half": {"line_type": 3, "source": "user_confirmed"},
            },
        )
        self.assertEqual(contract["boundary_segment_count"], 6)
        self.assertTrue(all(item["passed"] for item in contract["gate_results"].values()))
        self.assertFalse(contract["invariants"]["old_cp_reused"])
        self.assertEqual(contract["invariants"]["default_line_type_count"], 0)

        rows, issues = parse_cp(contract["cp"])
        self.assertEqual(issues, [])
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(row.line_type == 1 for row in rows), 6)
        self.assertEqual(sum(row.line_type == 2 for row in rows), 1)
        self.assertEqual(sum(row.line_type == 3 for row in rows), 1)
        self.assertNotEqual(contract["cp"], report["cp"])

    def test_complete_lines_do_not_bypass_missing_finite_endpoint(self):
        report = _complete_report()
        right = next(
            item for item in report["geometry_graph"]["entities"] if item["id"] == "right"
        )
        right["exact_geometry"] = {}
        contract = build_guided_cp_output_contract(
            report,
            segment_line_types={"left-half": 2, "right-half": 3},
        )

        self.assertFalse(contract["output_ready"])
        self.assertIsNone(contract["cp"])
        self.assertEqual(contract["unresolved_endpoint_point_ids"], ["right"])
        self.assertIn(
            "unresolved_finite_segment_endpoints",
            contract["blocker_counts"],
        )
        self.assertTrue(contract["gate_results"]["exact_crease_closure"]["passed"])
        self.assertFalse(contract["gate_results"]["exact_endpoint_closure"]["passed"])

    def test_line_type_is_required_per_segment_and_never_defaulted(self):
        contract = build_guided_cp_output_contract(_complete_report())

        self.assertFalse(contract["output_ready"])
        self.assertIsNone(contract["cp"])
        self.assertEqual(
            contract["unassigned_segment_ids"],
            ["left-half", "right-half"],
        )
        self.assertEqual(contract["blocker_counts"]["missing_segment_line_types"], 2)
        self.assertEqual(contract["segment_line_type_assignments"], {})
        self.assertFalse(contract["gate_results"]["segment_line_types"]["passed"])

    def test_untrusted_type_source_and_direction_mismatch_both_block_export(self):
        report = _complete_report()
        crease = next(
            item for item in report["geometry_graph"]["entities"] if item["id"] == "crease"
        )
        crease["exact_geometry"]["direction_index"] = 2
        contract = build_guided_cp_output_contract(
            report,
            segment_line_types={
                "left-half": {"line_type": 2, "source": "guessed_default"},
                "right-half": 3,
            },
        )

        self.assertFalse(contract["output_ready"])
        self.assertIn("untrusted_segment_line_type_provenance", contract["blocker_counts"])
        self.assertNotIn("left-half", contract["segment_line_type_assignments"])
        self.assertEqual(
            contract["segment_line_type_assignments"]["right-half"],
            {"line_type": 3, "source": "explicit_segment_assignment"},
        )
        self.assertIn("exact_observed_direction_mismatch", contract["blocker_counts"])
        self.assertIn("finite_segment_direction_mismatch", contract["blocker_counts"])

    def test_stale_assignment_for_unknown_segment_blocks_export(self):
        contract = build_guided_cp_output_contract(
            _complete_report(),
            segment_line_types={
                "left-half": 2,
                "right-half": 3,
                "removed-segment": 2,
            },
        )

        self.assertFalse(contract["output_ready"])
        self.assertEqual(
            contract["blocker_counts"]["unknown_segment_line_type_assignments"],
            1,
        )

    def test_contract_consumes_closed_topology_and_ignores_absorbed_boundary_terminal(self):
        report = _complete_report()
        right = next(
            item for item in report["geometry_graph"]["entities"] if item["id"] == "right"
        )
        right["exact_geometry"] = {}
        side_length = Qsqrt2(1, 1)
        half = side_length / 2
        report["geometry_graph"]["entities"].append(
            _point_entity(
                "right-target",
                (side_length, half),
                (100.0, 50.0),
                ("right",),
            )
        )
        report["finite_topology"] = build_finite_endpoint_closed_topology(report)

        contract = build_guided_cp_output_contract(
            report,
            segment_line_types={"left-half": 2, "right-half": 3},
        )

        self.assertTrue(contract["output_ready"])
        self.assertEqual(
            contract["invariants"]["finite_topology_mode"],
            "finite_endpoint_closed_topology_v1",
        )
        self.assertNotIn("unresolved_boundary_contacts", contract["blocker_counts"])
        right_segment = next(
            item for item in contract["candidate_segments"] if item["id"] == "right-half"
        )
        self.assertEqual(right_segment["end_point_id"], "right-target")


if __name__ == "__main__":
    unittest.main()
