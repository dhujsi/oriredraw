import unittest

from exact_qsqrt2 import Qsqrt2 as CanonicalQsqrt2
from qsqrt2_coordinates import (
    Qsqrt2,
    boundary_project_point,
    infer_boundary_coordinate_gauges,
    qsqrt2_expression,
    qsqrt2_from_coefficients,
    qsqrt2_to_mapping,
)


class Qsqrt2CoordinatesTest(unittest.TestCase):
    def test_coordinate_layer_reuses_the_repository_exact_number_type(self):
        self.assertIs(Qsqrt2, CanonicalQsqrt2)

    def test_semantic_side_ratio_round_trips_without_losing_the_paper_length(self):
        side_length = qsqrt2_from_coefficients(2, 1)
        distance = qsqrt2_from_coefficients(3, 0, 2)

        ratio = distance / side_length

        self.assertEqual(qsqrt2_expression(ratio), "(6-3√2)/4")
        self.assertEqual(qsqrt2_expression(ratio * side_length), "3/2")
        self.assertIsInstance(ratio, Qsqrt2)

    def test_trex_top_relation_prefers_two_plus_sqrt2_project_scale(self):
        parameters = [
            {"id": "A", "role": "start", "parameter": qsqrt2_to_mapping(Qsqrt2())},
            {
                "id": "P2",
                "role": "1/3",
                "parameter": qsqrt2_to_mapping(qsqrt2_from_coefficients(2, -1, 4)),
            },
            {
                "id": "P3",
                "role": "2/3",
                "parameter": qsqrt2_to_mapping(qsqrt2_from_coefficients(2, -1, 2)),
            },
            {
                "id": "P5",
                "role": "end",
                "parameter": qsqrt2_to_mapping(qsqrt2_from_coefficients(6, -3, 4)),
            },
        ]

        candidates = infer_boundary_coordinate_gauges("top", parameters)

        recommended = candidates[0]
        self.assertEqual(recommended["origin"], "top_left")
        self.assertEqual(recommended["side_length"]["expression"], "2+√2")
        self.assertEqual(
            [point["edge_distance"]["expression"] for point in recommended["points"]],
            ["0", "1/2", "1", "3/2"],
        )
        self.assertTrue(
            all(point["coordinate"][1]["expression"] == "0" for point in recommended["points"])
        )

    def test_clockwise_boundary_distances_share_one_top_left_coordinate_system(self):
        side_length = qsqrt2_from_coefficients(2, 1)
        distance = qsqrt2_from_coefficients(1)

        expressions = {
            side: tuple(
                qsqrt2_expression(value)
                for value in boundary_project_point(side, distance, side_length)
            )
            for side in ("top", "right", "bottom", "left")
        }

        self.assertEqual(expressions["top"], ("1", "0"))
        self.assertEqual(expressions["right"], ("2+√2", "1"))
        self.assertEqual(expressions["bottom"], ("1+√2", "2+√2"))
        self.assertEqual(expressions["left"], ("0", "1+√2"))


if __name__ == "__main__":
    unittest.main()
