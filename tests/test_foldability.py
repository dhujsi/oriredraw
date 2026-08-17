import math
import unittest

from foldability import GeometrySegment, audit_camv_structure


def segment(line_type, start, end, row=None):
    return GeometrySegment(line_type, start, end, row)


def square_boundary():
    return [
        segment(1, (-100, -100), (100, -100)),
        segment(1, (100, -100), (100, 100)),
        segment(1, (100, 100), (-100, 100)),
        segment(1, (-100, 100), (-100, -100)),
    ]


def ray(angle_deg, line_type=2):
    angle = math.radians(angle_deg)
    x, y = math.cos(angle), math.sin(angle)
    scale = 100.0 / max(abs(x), abs(y))
    return segment(line_type, (0, 0), (scale * x, scale * y))


class CamvStructureAuditTest(unittest.TestCase):
    def test_full_camv_accepts_cross_with_three_mountains_one_valley(self):
        report = audit_camv_structure(
            square_boundary()
            + [ray(0, 2), ray(90, 2), ray(180, 2), ray(270, 3)],
            folding_types={2, 3},
            include_mv=True,
        )
        self.assertTrue(report["passes_camv"])

    def test_full_camv_reports_maekawa(self):
        report = audit_camv_structure(
            square_boundary()
            + [ray(0, 2), ray(90, 2), ray(180, 3), ray(270, 3)],
            folding_types={2, 3},
            include_mv=True,
        )
        self.assertEqual(report["rule_counts"]["maekawa"], 1)

    def test_full_camv_reports_little_big_little_order(self):
        report = audit_camv_structure(
            square_boundary()
            + [ray(0, 2), ray(22.5, 2), ray(45, 2), ray(202.5, 3)],
            folding_types={2, 3},
            include_mv=True,
        )
        self.assertEqual(report["rule_counts"]["little_big_little"], 1)

    def test_four_ray_cross_satisfies_kawasaki(self):
        report = audit_camv_structure(
            square_boundary() + [
                segment(2, (-100, 0), (100, 0)),
                segment(2, (0, -100), (0, 100)),
            ]
        )
        self.assertTrue(report["passes_structure_subset"])
        self.assertEqual(report["violation_count"], 0)

    def test_internal_three_ray_vertex_fails_number_of_folds(self):
        report = audit_camv_structure(
            square_boundary() + [ray(0), ray(90), ray(180)]
        )
        self.assertEqual(report["rule_counts"]["number_of_folds"], 1)

    def test_even_vertex_can_fail_kawasaki(self):
        report = audit_camv_structure(
            square_boundary() + [ray(0), ray(30), ray(90), ray(200)]
        )
        self.assertEqual(report["rule_counts"]["kawasaki_angles"], 1)

    def test_boundary_vertex_is_not_forced_to_even_crease_degree(self):
        report = audit_camv_structure(
            square_boundary() + [segment(2, (0, -100), (0, 100))]
        )
        self.assertTrue(report["passes_structure_subset"])

    def test_malformed_boundary_incidence_is_reported(self):
        report = audit_camv_structure(
            [
                segment(1, (0, 0), (100, 0)),
                segment(2, (0, 0), (0, 100)),
            ]
        )
        self.assertGreaterEqual(report["rule_counts"]["boundary_topology"], 1)

    def test_cp_auxiliary_lines_can_be_excluded_like_oriedita(self):
        report = audit_camv_structure(
            square_boundary() + [segment(4, (0, 0), (100, 0))],
            folding_types={2, 3},
        )
        self.assertTrue(report["passes_structure_subset"])
        self.assertEqual(report["checked_vertex_count"], 0)


if __name__ == "__main__":
    unittest.main()
