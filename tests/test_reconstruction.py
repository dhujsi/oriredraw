import math
import unittest
from collections import Counter

import cv2
import numpy as np

from reconstructor import (
    Edge,
    Settings,
    _reconstruct_lsd_rays,
    edges_to_cp,
    prepare_paper_square,
    reconstruct,
)


class ReconstructionSmokeTest(unittest.TestCase):
    def test_paper_preparation_upscales_small_screenshot_for_analysis(self):
        image = np.full((180, 190, 3), 255, np.uint8)
        cv2.rectangle(image, (20, 15), (169, 164), (0, 0, 0), 1)
        square, _, stats = prepare_paper_square(image, 512)
        self.assertEqual(square.shape[:2], (512, 512))
        self.assertEqual(stats["native_paper_size"], 150)
        self.assertEqual(stats["analysis_size_used"], 512)
        self.assertTrue(stats["source_upscaled"])
        self.assertGreater(stats["analysis_scale"], 3.0)

    def test_four_corner_photo_correction_rectifies_to_square(self):
        image = np.full((260, 340, 3), 255, np.uint8)
        corners = np.array(
            [[48.0, 31.0], [288.0, 52.0], [310.0, 229.0], [30.0, 214.0]],
            dtype=np.float32,
        )
        cv2.polylines(image, [corners.astype(np.int32)], True, (0, 0, 0), 2)
        cv2.line(
            image,
            tuple(corners[0].astype(int)),
            tuple(corners[2].astype(int)),
            (0, 0, 255),
            2,
        )
        normalized = (
            corners / np.array([image.shape[1] - 1, image.shape[0] - 1])
        ).tolist()
        square, _, stats = prepare_paper_square(
            image, 512, paper_corners=normalized
        )
        self.assertEqual(square.shape[0], square.shape[1])
        self.assertEqual(square.shape[:2], (512, 512))
        self.assertEqual(stats["paper_transform"], "four_corner_perspective")
        self.assertTrue(stats["source_upscaled"])

    def test_four_corner_selection_may_be_small_relative_to_full_screenshot(self):
        image = np.full((2000, 2000, 3), 255, np.uint8)
        corners = np.array(
            [[901.0, 947.0], [996.0, 945.0], [998.0, 1042.0], [899.0, 1044.0]],
            dtype=np.float32,
        )
        cv2.polylines(image, [corners.astype(np.int32)], True, (0, 0, 0), 1)
        normalized = (corners / np.array([1999.0, 1999.0])).tolist()
        square, _, stats = prepare_paper_square(
            image, 512, paper_corners=normalized
        )
        self.assertEqual(square.shape[:2], (512, 512))
        self.assertEqual(stats["paper_transform"], "four_corner_perspective")
        self.assertTrue(stats["source_upscaled"])

    def test_small_automatic_crop_is_detected_before_upscaled_analysis(self):
        image = np.full((72, 76, 3), 255, np.uint8)
        cv2.rectangle(image, (8, 6), (67, 65), (0, 0, 0), 1)
        square, bounds, stats = prepare_paper_square(image, 512)
        self.assertEqual(square.shape[:2], (512, 512))
        self.assertTrue(stats["source_upscaled"])
        self.assertGreater(stats["paper_detection_scale"], 1.0)
        self.assertLessEqual(abs((bounds[2] - bounds[0]) - 59), 2)

    def test_cp_export_uses_oriedita_downward_y_coordinates(self):
        rows = edges_to_cp(
            [Edge(np.array([0.0, 0.0]), np.array([511.0, 511.0]), 4)],
            512,
        ).split()
        self.assertEqual(rows, ["2", "-200", "-200", "200", "200"])

    def test_cp_export_preserves_classified_valley_type(self):
        rows = edges_to_cp(
            [Edge(np.array([0.0, 0.0]), np.array([511.0, 0.0]), 3)],
            512,
        ).split()
        self.assertEqual(rows, ["3", "-200", "-200", "200", "-200"])

    def test_disjoint_nearby_parallel_rays_do_not_single_link(self):
        square = np.full((512, 512, 3), 255, np.uint8)
        cv2.line(square, (100, 50), (100, 100), (255, 0, 0), 1)
        cv2.line(square, (104, 200), (104, 250), (255, 0, 0), 1)
        ink = (np.min(square, axis=2) < 245).astype(np.uint8) * 255
        _, _, stats = _reconstruct_lsd_rays(square, ink, Settings())
        # These isolated lines are intentionally not constructible and hence
        # are not exported, but they must remain two distinct candidate rays.
        self.assertEqual(stats["unresolved_rays"], 2)

    def test_disjoint_parts_of_one_ray_remain_one_group(self):
        square = np.full((512, 512, 3), 255, np.uint8)
        cv2.line(square, (100, 50), (100, 100), (255, 0, 0), 1)
        cv2.line(square, (100, 200), (100, 250), (255, 0, 0), 1)
        ink = (np.min(square, axis=2) < 245).astype(np.uint8) * 255
        _, _, stats = _reconstruct_lsd_rays(square, ink, Settings())
        self.assertEqual(stats["lsd_parallel_groups_split"], 0)

    def test_small_sparse_22_5_star_is_upscaled_and_exports_legal_cp_lines(self):
        image = np.full((120, 120, 3), 255, np.uint8)
        low, high = 10, 109
        center = np.array([59.0, 59.0])
        cv2.rectangle(image, (low, low), (high, high), (0, 0, 0), 1)
        for index in range(8):
            theta = index * math.pi / 8
            direction = np.array([math.cos(theta), math.sin(theta)])
            hits = []
            for dimension, target in ((0, low), (0, high), (1, low), (1, high)):
                if abs(direction[dimension]) < 1e-9:
                    continue
                point = center + (target - center[dimension]) / direction[dimension] * direction
                if low - 1 <= point[0] <= high + 1 and low - 1 <= point[1] <= high + 1:
                    hits.append(point)
            for point in hits:
                cv2.line(
                    image,
                    tuple(np.rint(center).astype(int)),
                    tuple(np.rint(point).astype(int)),
                    (0, 0, 255),
                    1,
                )
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        result = reconstruct(encoded.tobytes())
        self.assertTrue(result["stats"]["source_upscaled"])
        rows = [row.split() for row in result["cp"].splitlines()]
        self.assertGreater(len(rows), 4)
        self.assertEqual({int(row[0]) for row in rows}, {1, 2})
        for row in rows:
            _, x1, y1, x2, y2 = row
            angle = math.degrees(
                math.atan2(float(y2) - float(y1), float(x2) - float(x1))
            ) % 180
            target = round(angle / 22.5) * 22.5
            error = min(abs(angle - target), 180 - abs(angle - target))
            self.assertLess(error, 1e-6)

        degree = Counter()
        for row in rows:
            if int(row[0]) != 2:
                continue
            first = (round(float(row[1]), 7), round(float(row[2]), 7))
            second = (round(float(row[3]), 7), round(float(row[4]), 7))
            degree[first] += 1
            degree[second] += 1
        internal_lineheads = [
            point
            for point, count in degree.items()
            if count == 1
            and min(
                abs(point[0] + 200.0),
                abs(point[0] - 200.0),
                abs(point[1] + 200.0),
                abs(point[1] - 200.0),
            )
            > 1e-6
        ]
        self.assertEqual(internal_lineheads, [])


if __name__ == "__main__":
    unittest.main()
