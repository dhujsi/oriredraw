import unittest

import cv2
import numpy as np

from cp_audit import audit_runtime_reliability, compare_cp_data


class CpAuditTest(unittest.TestCase):
    def test_development_comparison_is_invariant_to_collinear_splitting(self):
        reference = "\n".join(
            [
                "2 -100 0 100 0",
                "3 0 -100 0 100",
            ]
        )
        prediction = "\n".join(
            [
                "4 -100 0 0 0",
                "4 0 0 100 0",
                "4 0 -100 0 0",
                "4 0 0 0 100",
            ]
        )
        report = compare_cp_data(prediction, reference)
        self.assertTrue(report["exact_geometry_match"])
        self.assertEqual(report["ray_metrics"]["matched"], 2)
        self.assertEqual(report["finite_geometry_metrics"]["precision"], 1.0)
        self.assertEqual(report["finite_geometry_metrics"]["recall"], 1.0)

    def test_development_comparison_reports_shifted_ray_as_missing_and_extra(self):
        reference = "2 -100 0 100 0\n"
        prediction = "4 -100 2 100 2\n"
        report = compare_cp_data(
            prediction, reference, ray_tolerance=0.5
        )
        self.assertEqual(report["ray_metrics"]["missing"], 1)
        self.assertEqual(report["ray_metrics"]["extra"], 1)
        self.assertEqual(report["finite_geometry_metrics"]["matched_length"], 0.0)

    def test_development_comparison_reports_mv_separately_from_geometry(self):
        report = compare_cp_data(
            "3 -100 0 100 0\n",
            "2 -100 0 100 0\n",
        )
        self.assertTrue(report["exact_geometry_match"])
        self.assertEqual(
            report["mv_assignment_metrics"]["accuracy_on_comparable_geometry"],
            0.0,
        )
        self.assertFalse(report["exact_mv_match_on_comparable_geometry"])

    def test_runtime_reliability_does_not_claim_recall_or_correctness(self):
        image = np.full((240, 240, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (219, 219), (0, 0, 0), 1)
        cv2.line(image, (20, 120), (219, 120), (0, 0, 255), 1)
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        report = audit_runtime_reliability(
            "4 -200 1 200 1\n", encoded.tobytes()
        )
        self.assertEqual(report["report_kind"], "runtime_source_reliability")
        self.assertIn("missing_lines", report["does_not_measure"])
        self.assertNotIn("recall", report)
        self.assertNotIn("correctness", report)
        self.assertIn("camv_structure", report["constraints"])


if __name__ == "__main__":
    unittest.main()
