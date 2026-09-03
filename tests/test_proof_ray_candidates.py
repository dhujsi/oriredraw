import copy
import unittest

from exact_qsqrt2 import Qsqrt2
from proof_ray_candidates import build_proved_node_canonical_ray_candidates
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


if __name__ == "__main__":
    unittest.main()
