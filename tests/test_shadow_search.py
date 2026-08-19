import unittest

from shadow_search import build_shadow_report


class ShadowSearchTest(unittest.TestCase):
    def test_shadow_report_does_not_claim_to_change_output(self):
        result = {
            "stats": {"camv_structure_violation_count": 2},
            "playback_trace": [
                {
                    "trace_id": 0,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "角点种子",
                    "expression": "角点",
                    "snap_error_px": 0.0,
                    "forms_output": False,
                },
                {
                    "trace_id": 1,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "唯一内部 a+b√2 种子",
                    "coordinate_expression": ["-34+24√2", "-34+24√2"],
                    "snap_error_px": 0.2,
                    "forms_output": False,
                },
                {
                    "trace_id": 2,
                    "trace_parent_ids": [0, 1],
                    "generation": 1,
                    "source": "第 1 代交点",
                    "expression": "由母射线相交",
                    "snap_error_px": 0.0,
                    "forms_output": True,
                },
            ],
        }

        report = build_shadow_report(result)

        self.assertTrue(report["enabled"])
        self.assertTrue(report["output_unchanged"])
        self.assertEqual(report["output_observations"], 1)
        self.assertEqual(report["v2"]["unexplained_observations"], 0)
        self.assertEqual(len(report["high_complexity_algebraic_candidates"]), 1)
        self.assertTrue(
            report["high_complexity_algebraic_candidates"][0]["high_coefficient"]
        )

    def test_global_search_can_drop_irrelevant_legacy_candidate(self):
        result = {
            "stats": {},
            "playback_trace": [
                {
                    "trace_id": 0,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "角点种子",
                    "expression": "角点",
                    "snap_error_px": 0.0,
                    "forms_output": True,
                },
                {
                    "trace_id": 1,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "唯一内部 a+b√2 种子",
                    "coordinate_expression": ["-34+24√2", "-34+24√2"],
                    "snap_error_px": 0.1,
                    "forms_output": False,
                },
            ],
        }

        report = build_shadow_report(result)

        self.assertTrue(report["route_changed"])
        self.assertEqual(report["legacy"]["operation_count"], 2)
        self.assertEqual(report["v2"]["operation_count"], 1)
        self.assertLess(report["v2"]["score"], report["legacy"]["score"])

    def test_no_trace_returns_disabled_shadow_report(self):
        report = build_shadow_report({"stats": {}, "playback_trace": []})
        self.assertFalse(report["enabled"])
        self.assertTrue(report["output_unchanged"])


if __name__ == "__main__":
    unittest.main()
