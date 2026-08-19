import math
import unittest

from shadow_geometry_v2 import build_geometry_shadow_report_v2


class ShadowGeometryV2Test(unittest.TestCase):
    def test_boundary_midpoint_symmetry_can_reroot_a_descendant(self):
        # Deliberately use x=30 on a 0..100 paper: this regression is about the
        # generic boundary-midpoint rule, not a hard-coded sixth or dinosaur value.
        correct_root_offset = -(45.0 + 50.0) / math.sqrt(2.0)
        result = {
            "stats": {"analysis_size_used": 101},
            "playback_trace": [
                {
                    "trace_id": 0,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "唯一内部 a+b√2 种子",
                    "coordinate_expression": ["-34+24√2", "-34+24√2"],
                    "angle": 135.0,
                    "line_offset_px": -67.8,
                    "observed_offset_px": correct_root_offset,
                    "legacy_image_residual_px": abs(-67.8 - correct_root_offset),
                    "snap_error_px": 1.5,
                    "anchor_point_px": [48.0, 48.0],
                    "forms_output": True,
                },
                {
                    "trace_id": 1,
                    "trace_parent_ids": [],
                    "generation": 0,
                    "source": "第 0 代交点",
                    "expression": "由母射线相交",
                    "angle": 90.0,
                    "line_offset_px": -30.0,
                    "observed_offset_px": -30.0,
                    "legacy_image_residual_px": 0.0,
                    "snap_error_px": 0.1,
                    "anchor_point_px": [30.0, 30.0],
                    "forms_output": True,
                },
                {
                    "trace_id": 2,
                    "trace_parent_ids": [0],
                    "generation": 1,
                    "source": "第 1 代纸边交点",
                    "expression": "由已有射线与纸边相交",
                    "angle": 90.0,
                    "line_offset_px": -46.0,
                    "observed_offset_px": -45.2,
                    "legacy_image_residual_px": 0.8,
                    "snap_error_px": 1.0,
                    "anchor_point_px": [46.0, 0.0],
                    "forms_output": True,
                },
            ],
        }

        report = build_geometry_shadow_report_v2(result)

        self.assertTrue(report["enabled"])
        self.assertTrue(report["output_unchanged"])
        self.assertTrue(report["route_changed"])
        route = report["suspicious_seed_routes"][0]
        self.assertTrue(route["route_improved"])
        self.assertAlmostEqual(route["selected_offset_px"], correct_root_offset, places=5)
        self.assertEqual(route["reused_trace_ids"], [2])
        kinds = [item["kind"] for item in route["proof_operations"]]
        self.assertIn("boundary_midpoint_point", kinds)
        self.assertIn("symmetry_point", kinds)
        self.assertIn("paper_midline_intersection", kinds)


if __name__ == "__main__":
    unittest.main()
