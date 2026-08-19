import math
import unittest

from shadow_search import build_shadow_report


class ShadowSearchTest(unittest.TestCase):
    def test_shadow_report_scores_required_auxiliary_rays_without_changing_output(self):
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
        self.assertEqual(report["required_observations"], 3)
        self.assertEqual(report["output_rays"], 1)
        self.assertEqual(report["v2"]["unexplained_observations"], 0)
        self.assertFalse(report["route_changed"])
        self.assertEqual(report["legacy"]["operation_count"], 3)
        self.assertEqual(report["v2"]["operation_count"], 3)
        self.assertEqual(len(report["high_complexity_algebraic_candidates"]), 1)
        high = report["high_complexity_algebraic_candidates"][0]
        self.assertTrue(high["high_coefficient"])
        self.assertIn(24, high["algebraic_coefficients"])
        self.assertIn(-34, high["algebraic_coefficients"])

    def test_existing_point_can_replace_expensive_algebraic_seed_directly(self):
        result = {
            "stats": {},
            "playback_trace": [
                {
                    "trace_id": 0,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "角点种子",
                    "expression": "角点",
                    "anchor_point_px": [10.0, 20.0],
                    "angle": 0.0,
                    "line_offset_px": 20.0,
                    "snap_error_px": 0.0,
                    "forms_output": True,
                },
                {
                    "trace_id": 1,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "唯一内部 a+b√2 种子",
                    "coordinate_expression": ["-34+24√2", "3+2√2"],
                    "anchor_point_px": [10.0, 20.0],
                    "angle": 90.0,
                    "line_offset_px": -10.0,
                    "snap_error_px": 0.0,
                    "forms_output": True,
                },
            ],
        }

        report = build_shadow_report(result)

        self.assertTrue(report["route_changed"])
        kinds = [item["kind"] for item in report["v2"]["selected_alternatives"]]
        self.assertIn("direct_point", kinds)
        self.assertLess(report["v2"]["score"], report["legacy"]["score"])

    def test_dinosaur_symmetry_route_beats_large_coefficient_seed(self):
        source_x = -100.0 * math.sqrt(2.0)
        axis_x = 200.0 * (1.0 - math.sqrt(2.0))
        correct_x = 400.0 - 300.0 * math.sqrt(2.0)
        bad_x = 400.0 * (-34.0 + 24.0 * math.sqrt(2.0))
        delta = abs(correct_x - bad_x)

        result = {
            "stats": {},
            "playback_trace": [
                {
                    "trace_id": 0,
                    "trace_parent_ids": [],
                    "generation": 1,
                    "source": "第 1 代交点",
                    "expression": "由母射线相交",
                    "anchor_point_px": [source_x, -200.0],
                    "angle": 0.0,
                    "line_offset_px": -200.0,
                    "snap_error_px": 0.0,
                    "forms_output": True,
                },
                {
                    "trace_id": 1,
                    "trace_parent_ids": [],
                    "generation": 1,
                    "source": "第 1 代交点",
                    "expression": "由母射线相交",
                    "anchor_point_px": [axis_x, -200.0],
                    "angle": 90.0,
                    "line_offset_px": -axis_x,
                    "snap_error_px": 0.0,
                    "forms_output": True,
                },
                {
                    "trace_id": 2,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "唯一内部 a+b√2 种子",
                    "coordinate_expression": ["-34+24√2", "-200"],
                    "anchor_point_px": [bad_x, -200.0],
                    "angle": 90.0,
                    "line_offset_px": -bad_x,
                    "snap_error_px": delta,
                    "forms_output": True,
                },
            ],
        }

        report = build_shadow_report(result, beam_width=24)

        self.assertAlmostEqual(delta, 0.7142674936, places=9)
        self.assertGreaterEqual(report["alternative_candidates"].get("symmetry_point", 0), 1)
        self.assertTrue(report["route_changed"])
        selected = report["v2"]["selected_alternatives"]
        mirror = next(item for item in selected if item["kind"] == "symmetry_point")
        self.assertEqual(mirror["source_trace_id"], 0)
        self.assertEqual(mirror["axis_trace_id"], 1)
        self.assertEqual(mirror["target_trace_id"], 2)
        self.assertAlmostEqual(mirror["point_delta_px"], delta, places=6)
        self.assertLess(report["v2"]["score"], report["legacy"]["score"])

    def test_camv_is_reported_as_global_state_signal(self):
        result = {
            "stats": {"camv_structure_violation_count": 4},
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
            ],
        }

        report = build_shadow_report(result)

        self.assertEqual(report["camv_violation_count"], 4.0)
        self.assertGreater(report["v2"]["score"], 1.0)

    def test_no_trace_returns_disabled_shadow_report(self):
        report = build_shadow_report({"stats": {}, "playback_trace": []})
        self.assertFalse(report["enabled"])
        self.assertTrue(report["output_unchanged"])


if __name__ == "__main__":
    unittest.main()
