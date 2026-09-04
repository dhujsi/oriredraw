import json
import unittest

import cv2
import numpy as np

from raw_primary_bridge import analyze_raw_primary_from_square
from reconstructor import Settings


def _boundary_trisection_square(size: int = 161) -> np.ndarray:
    square = np.full((size, size, 3), 255, dtype=np.uint8)
    maximum = size - 1
    cv2.rectangle(square, (0, 0), (maximum, maximum), (0, 0, 0), 2)
    for x in (16, 56, 96, 136):
        cv2.line(square, (x, 1), (x, 145), (0, 0, 255), 2)
    cv2.line(square, (10, 100), (150, 100), (255, 0, 0), 2)
    return square


class RawPrimaryBridgeTest(unittest.TestCase):
    def test_fast_entry_suppresses_unvalidated_cp_before_human_selection(self):
        square = _boundary_trisection_square()
        progress = []

        payload = analyze_raw_primary_from_square(
            square,
            Settings(analysis_size=square.shape[0]),
            progress_callback=lambda percent, message: progress.append(
                (percent, message)
            ),
        )

        self.assertEqual(payload["mode"], "guided_raw_primary_v1")
        self.assertEqual(payload["phase"], "awaiting_boundary_relation")
        self.assertFalse(payload["output_ready"])
        self.assertFalse(payload["cp_available"])
        self.assertIsNone(payload["cp"])
        self.assertFalse(payload["stats"]["strict_reconstruction_executed"])
        self.assertFalse(payload["stats"]["construction_search_executed"])
        self.assertEqual(payload["invariants"]["generated_crease_count"], 0)
        self.assertEqual(payload["invariants"]["generated_direction_count"], 0)
        self.assertFalse(payload["invariants"]["cp_emitted"])
        self.assertFalse(payload["invariants"]["cp_is_current_draft"])
        self.assertTrue(payload["invariants"]["unsafe_raw_draft_suppressed"])
        self.assertNotIn("cp", payload["cp_draft"])
        self.assertTrue(payload["source_data_uri"].startswith("data:image/png;base64,"))
        self.assertTrue(payload["redraw_data_uri"].startswith("data:image/png;base64,"))
        self.assertTrue(payload["overlay_data_uri"].startswith("data:image/png;base64,"))
        self.assertTrue(
            payload["reconstruction_data_uri"].startswith("data:image/png;base64,")
        )
        self.assertEqual(progress[-1][0], 100)

        shadow = payload["shadow_search"]
        self.assertTrue(shadow["raw_crease_evidence"]["observed_only"])
        self.assertTrue(shadow["raw_crease_topology"]["enabled"])
        line_types = shadow["raw_crease_evidence"][
            "segment_line_type_evidence"
        ]
        self.assertEqual(
            line_types["assigned_segment_count"],
            shadow["raw_crease_topology"]["segment_count"],
        )
        self.assertEqual(line_types["ambiguous_segment_count"], 0)
        self.assertTrue(
            all(
                segment["line_type"] in {2, 3}
                and segment["line_type_source"]
                == "source_image_color_evidence"
                for segment in shadow["raw_crease_topology"]["segments"]
            )
        )
        self.assertEqual(
            shadow["boundary_relation_candidate_source"],
            "raw_image_finite_topology",
        )
        trisection = next(
            item
            for item in shadow["boundary_relation_candidates"]
            if item["kind"] == "trisection" and item["side"] == "top"
        )
        self.assertEqual(trisection["evidence_source"], "raw_image_finite_topology")
        self.assertEqual(len(trisection["points"]), 4)
        json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
