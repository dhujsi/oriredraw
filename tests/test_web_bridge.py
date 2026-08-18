import json
import unittest
from unittest.mock import patch

import numpy as np
import cv2

from web_bridge import (
    _build_playback_trace,
    _filter_anchors_to_final_output,
    reconstruct_for_web_json,
    rectify_for_web_json,
)


class WebBridgeTest(unittest.TestCase):
    def test_browser_payload_excludes_native_images_and_is_json_safe(self):
        fake_result = {
            "cp": "2 -200 -200 200 200\n",
            "stats": {"internal_segments": np.int64(1)},
            "anchors": [],
            "warnings": [],
            "overlay_data_uri": "data:image/png;base64,AA==",
            "reconstruction_data_uri": "data:image/png;base64,AA==",
            "overlay_image": np.zeros((2, 2, 3), dtype=np.uint8),
            "reconstruction_image": np.zeros((2, 2, 3), dtype=np.uint8),
        }
        with patch("web_bridge.reconstruct", return_value=fake_result) as mocked:
            payload = json.loads(
                reconstruct_for_web_json(
                    b"image",
                    '{"angle_tolerance_deg": 4.5, "construction_offset_tolerance_px": 5.4}',
                )
            )

        self.assertEqual(payload["stats"]["internal_segments"], 1)
        self.assertNotIn("overlay_image", payload)
        self.assertIn("playback_trace", payload)
        settings = mocked.call_args.kwargs["settings"]
        self.assertEqual(settings.angle_tolerance_deg, 4.5)
        self.assertEqual(settings.construction_offset_tolerance_px, 5.4)

    def test_playback_anchors_are_limited_to_final_cp_lines(self):
        result = {
            "cp": "2 -200 0 200 0\n",
            "stats": {"analysis_size_used": 101},
            "anchors": [
                {
                    "angle": 0.0,
                    "line_offset_px": 50.0,
                    "anchor_point_px": [0.0, 50.0],
                    "generation": 0,
                    "source": "kept",
                },
                {
                    "angle": 0.0,
                    "line_offset_px": 25.0,
                    "anchor_point_px": [0.0, 25.0],
                    "generation": 0,
                    "source": "discarded",
                },
            ],
        }

        _filter_anchors_to_final_output(result)

        self.assertEqual([item["source"] for item in result["anchors"]], ["kept"])

    def test_playback_trace_keeps_only_final_rays_and_required_auxiliary_ancestors(self):
        result = {
            "cp": "2 -200 0 200 0\n2 0 -200 0 200\n",
            "stats": {"analysis_size_used": 101},
            "anchors": [
                {
                    "angle": 0.0,
                    "line_offset_px": 50.0,
                    "anchor_point_px": [0.0, 50.0],
                    "generation": 0,
                    "source": "结果种子",
                    "parents": None,
                },
                {
                    "angle": 45.0,
                    "line_offset_px": 0.0,
                    "anchor_point_px": [0.0, 0.0],
                    "generation": 0,
                    "source": "纯辅助种子",
                    "parents": None,
                },
                {
                    "angle": 90.0,
                    "line_offset_px": -50.0,
                    "anchor_point_px": [50.0, 50.0],
                    "generation": 1,
                    "source": "第 1 代交点",
                    "parents": [0, 1],
                },
                {
                    "angle": 0.0,
                    "line_offset_px": 25.0,
                    "anchor_point_px": [0.0, 25.0],
                    "generation": 0,
                    "source": "无关候选",
                    "parents": None,
                },
            ],
        }

        _build_playback_trace(result)

        trace = {item["source"]: item for item in result["playback_trace"]}
        self.assertEqual(
            set(trace),
            {"结果种子", "纯辅助种子", "第 1 代交点"},
        )
        self.assertFalse(trace["纯辅助种子"]["forms_output"])
        self.assertTrue(trace["结果种子"]["forms_output"])
        self.assertTrue(trace["第 1 代交点"]["forms_output"])
        self.assertEqual(trace["结果种子"]["last_used_generation"], 1)
        self.assertEqual(trace["纯辅助种子"]["last_used_generation"], 1)
        self.assertEqual(len(trace["结果种子"]["formed_segments_px"]), 1)
        self.assertEqual(len(trace["第 1 代交点"]["formed_segments_px"]), 1)

    def test_playback_trace_falls_back_to_geometric_parent_recovery(self):
        result = {
            "cp": "2 -200 0 200 0\n",
            "stats": {"analysis_size_used": 101},
            "anchors": [
                {
                    "angle": 90.0,
                    "line_offset_px": -50.0,
                    "anchor_point_px": [50.0, 0.0],
                    "generation": 0,
                    "source": "辅助 A",
                    "parents": None,
                },
                {
                    "angle": 45.0,
                    "line_offset_px": 0.0,
                    "anchor_point_px": [0.0, 0.0],
                    "generation": 0,
                    "source": "辅助 B",
                    "parents": None,
                },
                {
                    "angle": 0.0,
                    "line_offset_px": 50.0,
                    "anchor_point_px": [50.0, 50.0],
                    "generation": 1,
                    "source": "第 1 代交点",
                    "parents": [99, 98],
                },
            ],
        }

        _build_playback_trace(result)

        self.assertEqual(
            {item["source"] for item in result["playback_trace"]},
            {"辅助 A", "辅助 B", "第 1 代交点"},
        )

    def test_rectify_bridge_returns_downloadable_square_png(self):
        image = np.full((160, 220, 3), 255, np.uint8)
        corners = [[0.1, 0.1], [0.9, 0.14], [0.86, 0.9], [0.12, 0.86]]
        cv2.polylines(
            image,
            [
                np.rint(
                    np.asarray(corners) * np.array([219.0, 159.0])
                ).astype(np.int32)
            ],
            True,
            (0, 0, 0),
            2,
        )
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        payload = json.loads(
            rectify_for_web_json(encoded.tobytes(), json.dumps(corners))
        )
        self.assertEqual(payload["width"], payload["height"])
        self.assertTrue(payload["image_data_uri"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
