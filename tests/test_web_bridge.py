import json
import unittest
from unittest.mock import patch

import numpy as np

from web_bridge import reconstruct_for_web_json


class WebBridgeTest(unittest.TestCase):
    def test_browser_payload_excludes_native_images_and_is_json_safe(self):
        fake_result = {
            "cp": "2 -200 -200 200 200\n",
            "stats": {"internal_segments": np.int64(1)},
            "anchors": [],
            "warnings": [],
            "overlay_data_uri": "data:image/png;base64,AA==",
            "reconstruction_data_uri": "data:image/png;base64,AA==",
            "evidence_data_uri": "data:image/png;base64,AA==",
            "overlay_image": np.zeros((2, 2, 3), dtype=np.uint8),
            "reconstruction_image": np.zeros((2, 2, 3), dtype=np.uint8),
        }
        with patch("web_bridge.reconstruct", return_value=fake_result) as mocked:
            payload = json.loads(
                reconstruct_for_web_json(
                    b"image", '{"angle_tolerance_deg": 4.5}'
                )
            )

        self.assertEqual(payload["stats"]["internal_segments"], 1)
        self.assertNotIn("overlay_image", payload)
        settings = mocked.call_args.kwargs["settings"]
        self.assertEqual(settings.angle_tolerance_deg, 4.5)


if __name__ == "__main__":
    unittest.main()
