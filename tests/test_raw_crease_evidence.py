import unittest

import cv2
import numpy as np

from raw_crease_evidence import (
    classify_topology_segment_line_types,
    detect_raw_crease_entities_from_square,
)
from reconstructor import Settings


def _white_square(size: int = 121) -> np.ndarray:
    return np.full((size, size, 3), 255, dtype=np.uint8)


class RawCreaseEvidenceTest(unittest.TestCase):
    def test_source_color_is_measured_per_finite_topology_segment(self):
        square = _white_square()
        cv2.line(square, (10, 30), (110, 30), (0, 0, 255), 3)
        cv2.line(square, (10, 90), (110, 90), (255, 0, 0), 3)
        topology = {
            "points": [
                {"id": "red-start", "point": [10.0, 30.0]},
                {"id": "red-end", "point": [110.0, 30.0]},
                {"id": "blue-start", "point": [10.0, 90.0]},
                {"id": "blue-end", "point": [110.0, 90.0]},
            ],
            "segments": [
                {
                    "id": "red-segment",
                    "start_point_id": "red-start",
                    "end_point_id": "red-end",
                },
                {
                    "id": "blue-segment",
                    "start_point_id": "blue-start",
                    "end_point_id": "blue-end",
                },
            ],
        }

        report = classify_topology_segment_line_types(square, topology)

        self.assertEqual(report["assigned_segment_count"], 2)
        self.assertEqual(report["ambiguous_segment_count"], 0)
        self.assertEqual(report["mountain_segment_count"], 1)
        self.assertEqual(report["valley_segment_count"], 1)
        self.assertEqual(report["segments"]["red-segment"]["line_type"], 2)
        self.assertEqual(report["segments"]["blue-segment"]["line_type"], 3)
        self.assertTrue(
            all(
                item["source"] == "source_image_color_evidence"
                for item in report["segments"].values()
            )
        )

    def test_black_segment_stays_unknown_before_topology_propagation(self):
        square = _white_square()
        cv2.line(square, (10, 60), (110, 60), (0, 0, 0), 3)
        topology = {
            "points": [
                {"id": "start", "point": [10.0, 60.0]},
                {"id": "end", "point": [110.0, 60.0]},
            ],
            "segments": [
                {
                    "id": "black-segment",
                    "start_point_id": "start",
                    "end_point_id": "end",
                }
            ],
        }

        report = classify_topology_segment_line_types(square, topology)

        self.assertEqual(report["assigned_segment_count"], 0)
        self.assertEqual(report["ambiguous_segment_count"], 1)
        self.assertEqual(report["default_mountain_segment_count"], 0)
        self.assertTrue(report["detected_monochrome"])
        self.assertIsNone(report["segments"]["black-segment"]["line_type"])
        self.assertIsNone(report["segments"]["black-segment"]["source"])
        self.assertEqual(report["segments"]["black-segment"]["status"], "ambiguous")

    def test_ambiguous_colour_segment_stays_unknown_before_propagation(self):
        square = _white_square()
        cv2.line(square, (10, 60), (60, 60), (0, 0, 255), 3)
        cv2.line(square, (60, 60), (110, 60), (255, 0, 0), 3)
        topology = {
            "points": [
                {"id": "start", "point": [10.0, 60.0]},
                {"id": "end", "point": [110.0, 60.0]},
            ],
            "segments": [
                {
                    "id": "ambiguous-segment",
                    "start_point_id": "start",
                    "end_point_id": "end",
                }
            ],
        }

        report = classify_topology_segment_line_types(square, topology)

        segment = report["segments"]["ambiguous-segment"]
        self.assertIsNone(segment["line_type"])
        self.assertIsNone(segment["source"])
        self.assertEqual(segment["status"], "ambiguous")
        self.assertEqual(report["ambiguous_segment_count"], 1)

    def test_extracts_only_observed_legal_lines_and_rejects_paper_frame(self):
        square = _white_square()
        maximum = square.shape[0] - 1
        cv2.rectangle(square, (0, 0), (maximum, maximum), (0, 0, 0), 2)
        cv2.line(square, (12, 28), (108, 28), (0, 0, 255), 2)
        cv2.line(square, (82, 12), (82, 108), (255, 0, 0), 2)
        cv2.line(square, (16, 16), (104, 104), (0, 0, 255), 2)

        report = detect_raw_crease_entities_from_square(
            square, Settings(analysis_size=square.shape[0])
        )

        self.assertTrue(report["observed_only"])
        self.assertFalse(report["construction_search_executed"])
        self.assertEqual(report["directions_generated"], 0)
        self.assertGreaterEqual(report["line_count"], 3)
        self.assertTrue(
            any(
                line["orientation"] == 0
                and abs(line["observed_offset_px"] - 28.0) < 2.5
                for line in report["lines"]
            )
        )
        self.assertTrue(
            any(
                line["orientation"] == 4
                and abs(line["observed_offset_px"] + 82.0) < 2.5
                for line in report["lines"]
            )
        )
        self.assertTrue(
            any(
                line["orientation"] == 2
                and abs(line["observed_offset_px"]) < 2.5
                for line in report["lines"]
            )
        )
        self.assertFalse(
            any(
                line["orientation"] == 0
                and min(
                    abs(line["observed_offset_px"]),
                    abs(line["observed_offset_px"] - maximum),
                )
                < 3.0
                for line in report["lines"]
            )
        )
        self.assertFalse(
            any(
                line["orientation"] == 4
                and min(
                    abs(line["observed_offset_px"]),
                    abs(line["observed_offset_px"] + maximum),
                )
                < 3.0
                for line in report["lines"]
            )
        )
        self.assertGreater(
            report["pixel_agreement"]["observed_centerline_precision"],
            0.95,
        )

    def test_mixed_color_geometry_uses_one_colour_blind_geometry_path(self):
        square = _white_square(161)
        maximum = square.shape[0] - 1
        cv2.rectangle(square, (0, 0), (maximum, maximum), (0, 0, 0), 2)
        cv2.line(square, (14, 35), (146, 35), (0, 0, 255), 3)
        cv2.line(square, (110, 14), (110, 146), (255, 0, 0), 3)
        cv2.line(square, (14, 110), (146, 110), (185, 185, 185), 3)

        report = detect_raw_crease_entities_from_square(
            square, Settings(analysis_size=square.shape[0])
        )

        red_lines = [
            line
            for line in report["lines"]
            if line["orientation"] == 0
            and abs(line["observed_offset_px"] - 35.0) < 2.5
        ]
        neutral_lines = [
            line
            for line in report["lines"]
            if line["orientation"] == 0
            and abs(line["observed_offset_px"] - 110.0) < 2.5
        ]
        blue_lines = [
            line
            for line in report["lines"]
            if line["orientation"] == 4
            and abs(line["observed_offset_px"] + 110.0) < 2.5
        ]

        self.assertEqual(len(red_lines), 1)
        self.assertEqual(len(neutral_lines), 1)
        self.assertEqual(len(blue_lines), 1)
        for line in red_lines + neutral_lines + blue_lines:
            self.assertTrue(
                set(line["source_channels"])
                <= {"geometry_band", "geometry_centerline"}
            )
            self.assertFalse(
                {"red", "blue", "monochrome"}
                & set(line["source_channels"])
            )
        self.assertTrue(
            report["detector_stats"]["geometry_is_color_independent"]
        )
        self.assertEqual(
            report["detector_stats"]["color_geometry_channel_count"],
            0,
        )
        self.assertFalse(
            any(
                line["orientation"] == 0
                and min(
                    abs(line["observed_offset_px"]),
                    abs(line["observed_offset_px"] - maximum),
                )
                < 3.0
                for line in report["lines"]
            )
        )
        self.assertFalse(
            any(
                line["orientation"] == 4
                and min(
                    abs(line["observed_offset_px"]),
                    abs(line["observed_offset_px"] + maximum),
                )
                < 3.0
                for line in report["lines"]
            )
        )

    def test_recolouring_strokes_does_not_change_detected_geometry(self):
        def drawing(colors):
            square = _white_square(181)
            maximum = square.shape[0] - 1
            cv2.rectangle(square, (0, 0), (maximum, maximum), (0, 0, 0), 2)
            cv2.line(square, (18, 38), (162, 38), colors[0], 3)
            cv2.line(square, (126, 18), (126, 162), colors[1], 3)
            cv2.line(square, (28, 152), (152, 28), colors[2], 3)
            return square

        first = detect_raw_crease_entities_from_square(
            drawing(((0, 0, 255), (255, 0, 0), (180, 180, 180))),
            Settings(analysis_size=181),
        )
        second = detect_raw_crease_entities_from_square(
            drawing(((30, 30, 30), (185, 185, 185), (255, 0, 0))),
            Settings(analysis_size=181),
        )

        expected = ((0, 38.0), (4, -126.0), (6, -127.3))
        for report in (first, second):
            self.assertTrue(
                report["detector_stats"]["geometry_is_color_independent"]
            )
            for orientation, offset in expected:
                matches = [
                    line
                    for line in report["lines"]
                    if line["orientation"] == orientation
                    and abs(line["observed_offset_px"] - offset) < 2.5
                ]
                self.assertEqual(len(matches), 1)
                self.assertFalse(
                    {"red", "blue", "monochrome"}
                    & set(matches[0]["source_channels"])
                )

    def test_one_line_identity_preserves_two_disconnected_visible_intervals(self):
        square = _white_square()
        maximum = square.shape[0] - 1
        cv2.rectangle(square, (0, 0), (maximum, maximum), (0, 0, 0), 2)
        cv2.line(square, (12, 60), (40, 60), (0, 0, 255), 2)
        cv2.line(square, (80, 60), (108, 60), (0, 0, 255), 2)

        report = detect_raw_crease_entities_from_square(
            square, Settings(analysis_size=square.shape[0])
        )
        line = min(
            (
                item
                for item in report["lines"]
                if item["orientation"] == 0
            ),
            key=lambda item: abs(item["observed_offset_px"] - 60.0),
        )

        self.assertLess(abs(line["observed_offset_px"] - 60.0), 2.5)
        self.assertEqual(line["visible_interval_count"], 2)
        first, second = line["evidence_intervals_px"]
        self.assertGreater(second[0] - first[1], 25.0)

    def test_all_reported_directions_are_exact_22_5_degree_classes(self):
        square = _white_square()
        maximum = square.shape[0] - 1
        cv2.rectangle(square, (0, 0), (maximum, maximum), (0, 0, 0), 2)
        center = np.array([60.0, 60.0])
        for orientation, angle in enumerate(np.arange(0.0, 180.0, 22.5)):
            direction = np.array(
                [np.cos(np.radians(angle)), np.sin(np.radians(angle))]
            )
            start = tuple(np.rint(center - direction * 42.0).astype(int))
            end = tuple(np.rint(center + direction * 42.0).astype(int))
            color = (0, 0, 255) if orientation % 2 == 0 else (255, 0, 0)
            cv2.line(square, start, end, color, 2)

        report = detect_raw_crease_entities_from_square(
            square, Settings(analysis_size=square.shape[0])
        )
        for line in report["lines"]:
            self.assertEqual(
                line["orientation_deg"], line["orientation"] * 22.5
            )
            self.assertGreater(line["total_visible_length_px"], 0.0)
            self.assertTrue(line["visible_segments_px"])


if __name__ == "__main__":
    unittest.main()
