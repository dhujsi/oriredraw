import unittest

from boundary_relations import detect_boundary_ratio_relations


class BoundaryRelationTest(unittest.TestCase):
    def test_complete_boundary_trisection_is_one_grouped_hypothesis(self):
        points = [
            {"id": "corner", "point": [0, 0]},
            {"id": "third_a", "point": [10, 0]},
            {"id": "third_b", "point": [20, 0]},
            {"id": "target", "point": [30, 0]},
        ]

        relations = detect_boundary_ratio_relations(points, 100)

        self.assertEqual(len(relations), 1)
        relation = relations[0]
        self.assertEqual(relation["kind"], "trisection")
        self.assertEqual(relation["endpoint_ids"], ["corner", "target"])
        self.assertEqual(
            {item["ratio"] for item in relation["dividers"]},
            {"1/3", "2/3"},
        )
        self.assertTrue(relation["is_complete"])

    def test_missing_divider_is_not_invented_as_a_complete_relation(self):
        points = [
            {"id": "corner", "point": [0, 0]},
            {"id": "third_a", "point": [10, 0]},
            {"id": "target", "point": [30, 0]},
        ]

        relations = detect_boundary_ratio_relations(points, 100)

        self.assertEqual(relations, [])

    def test_boundary_relation_does_not_use_an_interior_point(self):
        points = [
            {"id": "corner", "point": [0, 0]},
            {"id": "third_a", "point": [10, 0]},
            {"id": "third_b", "point": [20, 4]},
            {"id": "target", "point": [30, 0]},
            {"id": "interior", "point": [20, 10]},
        ]

        relations = detect_boundary_ratio_relations(points, 100, tolerance_px=0.1)

        self.assertEqual(relations, [])


if __name__ == "__main__":
    unittest.main()
