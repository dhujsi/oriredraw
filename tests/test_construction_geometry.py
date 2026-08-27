import unittest

from construction_search import ConstructionGraph, GeometryEntity


class ConstructionGeometryTest(unittest.TestCase):
    def test_point_and_crease_keep_observed_and_exact_geometry_on_one_entity(self):
        graph = ConstructionGraph()
        point_id = ("point", 1)
        crease_id = ("ray", 7)
        graph.add_geometry_entity(
            GeometryEntity(
                id=point_id,
                kind="point",
                observed_geometry={"point_px": [31.0, 0.0]},
                evidence_sources={"raw_image"},
            )
        )
        graph.add_geometry_entity(
            GeometryEntity(
                id=crease_id,
                kind="crease",
                observed_geometry={"direction_index": 2, "line_offset_px": -30.5},
                evidence_sources={"playback_trace"},
            )
        )

        graph.connect_incidence(point_id, crease_id)
        graph.exactify_geometry(
            point_id,
            {"project_coordinate": [{"expression": "1/2"}, {"expression": "0"}]},
        )
        graph.exactify_geometry(
            crease_id,
            {"direction_index": 2, "through_point_id": str(point_id)},
        )

        self.assertEqual(graph.geometry_entity(point_id).observed_geometry["point_px"], [31.0, 0.0])
        self.assertEqual(graph.geometry_entity(point_id).exact_geometry["project_coordinate"][0]["expression"], "1/2")
        self.assertEqual([item.id for item in graph.incident_entities(point_id)], [crease_id])
        snapshot = graph.geometry_snapshot()
        self.assertEqual(snapshot["point_count"], 1)
        self.assertEqual(snapshot["crease_count"], 1)
        self.assertEqual(snapshot["exact_point_count"], 1)
        self.assertEqual(snapshot["exact_crease_count"], 1)

    def test_conflicting_exact_geometry_is_rejected(self):
        graph = ConstructionGraph()
        graph.add_geometry_entity(GeometryEntity(id="P", kind="point"))
        graph.exactify_geometry("P", {"x": "1/2"})

        with self.assertRaises(ValueError):
            graph.exactify_geometry("P", {"x": "1/3"})


if __name__ == "__main__":
    unittest.main()
