import copy
import unittest

from exact_qsqrt2 import Qsqrt2
from proof_ray_candidates import (
    apply_image_supported_canonical_rays,
    build_proved_node_canonical_ray_candidates,
)
from qsqrt2_coordinates import qsqrt2_to_mapping


def _coordinate(x, y):
    return [qsqrt2_to_mapping(Qsqrt2(x)), qsqrt2_to_mapping(Qsqrt2(y))]


class ProvedNodeCanonicalRayCandidatesTest(unittest.TestCase):
    def test_candidates_start_at_proved_points_and_skip_existing_ray(self):
        graph = {
            "entities": [
                {
                    "id": "center",
                    "kind": "point",
                    "exact_geometry": {"project_coordinate": _coordinate(1, 1)},
                },
                {
                    "id": "right",
                    "kind": "point",
                    "exact_geometry": {"project_coordinate": _coordinate(2, 1)},
                },
                {
                    "id": "fitted-only",
                    "kind": "point",
                    "exact_geometry": {"project_coordinate": _coordinate(1, 0)},
                },
            ]
        }
        topology = {
            "enabled": True,
            "mode": "raw_finite_crease_topology_v1",
            "segments": [
                {
                    "id": "center-right",
                    "start_point_id": "center",
                    "end_point_id": "right",
                }
            ],
        }
        proof = {
            "enabled": True,
            "proved_point_ids": ["center", "right"],
            "proved_segment_ids": ["center-right"],
            "entity_records": [
                {
                    "id": "center",
                    "status": "proved",
                    "proof_kind": "existing_crease_intersection",
                },
                {
                    "id": "right",
                    "status": "proved",
                    "proof_kind": "existing_crease_paper_boundary_intersection",
                },
                {
                    "id": "fitted-only",
                    "status": "observed_fit_only",
                },
            ],
        }
        original_graph = copy.deepcopy(graph)
        original_topology = copy.deepcopy(topology)

        report = build_proved_node_canonical_ray_candidates(
            graph,
            topology,
            proof,
            qsqrt2_to_mapping(Qsqrt2(2)),
        )

        self.assertTrue(report["enabled"])
        self.assertEqual(report["source_proved_point_count"], 2)
        self.assertEqual(report["candidate_count"], 21)
        self.assertEqual(report["occupied_proved_ray_count"], 2)
        self.assertNotIn(
            ("center", 0),
            {
                (item["source_point_id"], item["directed_direction_index"])
                for item in report["candidates"]
            },
        )
        self.assertNotIn(
            ("right", 8),
            {
                (item["source_point_id"], item["directed_direction_index"])
                for item in report["candidates"]
            },
        )
        self.assertEqual(
            {item["source_point_id"] for item in report["candidates"]},
            {"center", "right"},
        )
        self.assertTrue(
            all(item["direction_deg"] % 22.5 == 0 for item in report["candidates"])
        )
        self.assertTrue(
            all(item["image_evidence_status"] == "not_evaluated" for item in report["candidates"])
        )
        self.assertEqual(graph, original_graph)
        self.assertEqual(topology, original_topology)

    def test_continuously_supported_candidate_is_applied_to_first_exact_contact(self):
        candidate = {
            "id": "proof-ray:test",
            "kind": "canonical_22_5_ray",
            "status": "unapplied_candidate",
            "source_point_id": "proved-center",
            "source_point_project": _coordinate(1, 1),
            "parent_entity_ids": ["proved-center"],
            "directed_direction_index": 0,
            "line_orientation_index": 0,
            "direction_deg": 0.0,
            "generation_rule": "canonical_22_5_ray_from_proved_point",
        }
        candidate_report = {
            "enabled": True,
            "candidate_count": 1,
            "candidates": [candidate],
        }
        raw_report = {
            "maximum_coordinate_px": 100,
            "lines": [
                {
                    "id": "observed-horizontal",
                    "orientation_deg": 0.0,
                    "source_channels": ["red"],
                    "visible_segments_px": [
                        {"start": [50.0, 50.0], "end": [75.0, 50.0]}
                    ],
                }
            ],
        }
        base_contract = {
            "enabled": True,
            "candidate_segments": [
                {
                    "id": "exact-source-anchor",
                    "start_point_id": "paper-top",
                    "end_point_id": "paper-bottom",
                    "start_cp": [0.0, -200.0],
                    "end_cp": [0.0, 200.0],
                    "line_type": 2,
                    "line_type_source": "source_image_color_evidence",
                },
                {
                    "id": "exact-vertical-contact",
                    "start_point_id": "top",
                    "end_point_id": "bottom",
                    "start_cp": [100.0, -200.0],
                    "end_cp": [100.0, 200.0],
                    "line_type": 2,
                    "line_type_source": "source_image_color_evidence",
                },
                {
                    "id": "isolated-base-segment",
                    "start_point_id": "isolated-a",
                    "end_point_id": "isolated-b",
                    "start_cp": [150.0, 50.0],
                    "end_cp": [175.0, 50.0],
                    "line_type": 2,
                    "line_type_source": "source_image_color_evidence",
                }
            ],
            "blockers": [],
            "gate_results": {},
            "invariants": {"generated_internal_segment_count": 0},
        }

        report, effective = apply_image_supported_canonical_rays(
            candidate_report,
            raw_report,
            base_contract,
            qsqrt2_to_mapping(Qsqrt2(2)),
        )

        self.assertTrue(report["enabled"])
        self.assertEqual(report["accepted_candidate_count"], 1)
        self.assertEqual(report["accepted_candidates"][0]["start_cp"], [0.0, 0.0])
        self.assertEqual(report["accepted_candidates"][0]["end_cp"], [100.0, 0.0])
        self.assertEqual(
            report["accepted_candidates"][0]["target"]["target_kind"],
            "existing_exact_segment",
        )
        self.assertIsNotNone(effective)
        self.assertEqual(effective["candidate_internal_segment_count"], 5)
        generated = [
            item
            for item in effective["candidate_segments"]
            if item.get("source") == "proved_canonical_22_5_image_supported"
        ]
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["direction_angle_deg"], 0.0)
        self.assertEqual(generated[0]["line_type"], 2)
        self.assertEqual(
            effective["invariants"]["raster_created_direction_count"], 0
        )
        self.assertTrue(
            report["invariants"]["every_output_segment_endpoint_is_boundary_or_shared"]
        )
        self.assertEqual(report["internal_dangling_pruned_segment_count"], 1)
        self.assertEqual(
            report["internal_dangling_pruned_segment_ids"],
            ["isolated-base-segment"],
        )

    def test_candidate_with_detached_source_is_rejected(self):
        candidate = {
            "id": "proof-ray:detached",
            "kind": "canonical_22_5_ray",
            "status": "unapplied_candidate",
            "source_point_id": "proved-but-not-output",
            "source_point_project": _coordinate(1, 1),
            "parent_entity_ids": ["proved-but-not-output"],
            "directed_direction_index": 0,
            "line_orientation_index": 0,
            "direction_deg": 0.0,
            "generation_rule": "canonical_22_5_ray_from_proved_point",
        }
        report, effective = apply_image_supported_canonical_rays(
            {"enabled": True, "candidate_count": 1, "candidates": [candidate]},
            {
                "maximum_coordinate_px": 100,
                "lines": [
                    {
                        "id": "observed-horizontal",
                        "orientation_deg": 0.0,
                        "source_channels": ["red"],
                        "visible_segments_px": [
                            {"start": [50.0, 50.0], "end": [75.0, 50.0]}
                        ],
                    }
                ],
            },
            {
                "enabled": True,
                "candidate_segments": [
                    {
                        "id": "exact-contact-only",
                        "start_cp": [100.0, -200.0],
                        "end_cp": [100.0, 200.0],
                        "line_type": 2,
                    }
                ],
            },
            qsqrt2_to_mapping(Qsqrt2(2)),
        )

        self.assertTrue(report["enabled"])
        self.assertEqual(report["accepted_candidate_count"], 0)
        self.assertEqual(
            report["rejection_counts"][
                "source_not_attached_to_current_output_topology"
            ],
            1,
        )
        self.assertIsNone(effective)


if __name__ == "__main__":
    unittest.main()
