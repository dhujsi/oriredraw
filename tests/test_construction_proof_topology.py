import copy
import unittest

from construction_proof_topology import build_construction_proof_topology


class ConstructionProofTopologyTest(unittest.TestCase):
    def test_raster_fit_and_its_downstream_crease_remain_observation_only(self):
        graph = {
            "entities": [
                {
                    "id": "relation-point",
                    "kind": "point",
                    "exact_geometry": {
                        "source_relation_id": "relation",
                        "project_coordinate": [{"a": 0}, {"a": 0}],
                    },
                },
                {
                    "id": "relation-crease",
                    "kind": "crease",
                    "exact_geometry": {
                        "source_relation_id": "relation",
                        "direction_index": 0,
                    },
                },
                {
                    "id": "boundary-point",
                    "kind": "point",
                    "exact_geometry": {
                        "source": "existing_crease_paper_boundary_intersection",
                        "parent_entity_ids": ["relation-crease", "paper_boundary:right"],
                        "project_coordinate": [{"a": 1}, {"a": 0}],
                    },
                },
                {
                    "id": "fitted-point",
                    "kind": "point",
                    "exact_geometry": {
                        "source": "guided_automatic_topology_point",
                        "project_coordinate": [{"a": 1}, {"a": 1}],
                    },
                },
                {
                    "id": "fit-dependent-crease",
                    "kind": "crease",
                    "exact_geometry": {
                        "source": "existing_incident_crease_from_exact_point",
                        "source_point_id": "fitted-point",
                        "direction_index": 4,
                    },
                },
            ]
        }
        topology = {
            "enabled": True,
            "mode": "raw_finite_crease_topology_v1",
            "segments": [
                {
                    "id": "proved",
                    "crease_entity_id": "relation-crease",
                    "start_point_id": "relation-point",
                    "end_point_id": "boundary-point",
                },
                {
                    "id": "fit-dependent",
                    "crease_entity_id": "fit-dependent-crease",
                    "start_point_id": "fitted-point",
                    "end_point_id": "boundary-point",
                },
            ],
        }
        original_graph = copy.deepcopy(graph)
        original_topology = copy.deepcopy(topology)

        proof = build_construction_proof_topology(graph, topology)

        self.assertEqual(proof["proved_segment_ids"], ["proved"])
        self.assertEqual(proof["observed_only_segment_ids"], ["fit-dependent"])
        self.assertIn("relation-crease", proof["proved_crease_ids"])
        self.assertNotIn("fit-dependent-crease", proof["proved_crease_ids"])
        fitted = next(
            item for item in proof["entity_records"] if item["id"] == "fitted-point"
        )
        self.assertEqual(fitted["status"], "observed_fit_only")
        downstream = next(
            item
            for item in proof["entity_records"]
            if item["id"] == "fit-dependent-crease"
        )
        self.assertEqual(downstream["reason"], "unproved_parent_entities")
        self.assertEqual(graph, original_graph)
        self.assertEqual(topology, original_topology)


if __name__ == "__main__":
    unittest.main()
