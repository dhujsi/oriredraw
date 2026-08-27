import json
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from guided_construction import build_boundary_relation_catalog_from_points
from raw_boundary_evidence import detect_boundary_contacts_from_confidence
from shadow_bridge import reconstruct_for_web_shadow_json


class RawBoundaryEvidenceTest(unittest.TestCase):
    def test_directional_scan_finds_complete_observed_top_trisection(self):
        confidence = np.zeros((101, 101), dtype=np.float32)
        # The horizontal frame is deliberately present.  Tangent directions
        # must not turn the whole paper edge into crease contacts.
        cv2.line(confidence, (0, 0), (100, 0), 1.0, 2)
        cv2.line(confidence, (30, 0), (52, 22), 1.0, 2)
        cv2.line(confidence, (60, 0), (38, 22), 1.0, 2)
        cv2.line(confidence, (90, 0), (90, 24), 1.0, 2)

        contacts = detect_boundary_contacts_from_confidence(confidence)
        top = [point for point in contacts if point["side"] == "top"]
        coordinates = [point["side_coordinate_px"] for point in top]
        for expected in (30.0, 60.0, 90.0):
            self.assertLess(min(abs(value - expected) for value in coordinates), 3.0)

        catalog = build_boundary_relation_catalog_from_points(
            contacts,
            100.0,
            evidence_source="raw_image_directional_scan",
            tolerance_px=3.0,
        )
        relation = next(
            item
            for item in catalog
            if item["kind"] == "trisection"
            and item["side"] == "top"
            and item["endpoint_coordinates"][0] == 0.0
            and abs(item["endpoint_coordinates"][1] - 90.0) < 3.0
        )
        self.assertEqual(relation["evidence_source"], "raw_image_directional_scan")
        self.assertEqual(len(relation["points"]), 4)

    def test_noisy_observations_fit_the_expected_qsqrt2_relation_as_a_group(self):
        # These are the independent raw-image peaks near the霸王龙 top-edge
        # relation.  They are intentionally not exact construction points.
        observations = [
            {"id": "raw:p2", "label": "P2 observation", "point": [71.0, 0.0], "directional_score": 0.26},
            {"id": "raw:p3", "label": "P3 observation", "point": [147.0, 0.0], "directional_score": 0.22},
            {"id": "raw:p5", "label": "P5 observation", "point": [224.0, 0.0], "directional_score": 0.76},
        ]

        with patch(
            "guided_construction._radical_coordinate",
            side_effect=AssertionError("group coordinates must not be refitted point by point"),
        ):
            catalog = build_boundary_relation_catalog_from_points(
                observations,
                511.0,
                evidence_source="raw_image_directional_scan",
                tolerance_px=6.0,
                fit_algebraic_geometry=True,
            )
        relation = next(
            item
            for item in catalog
            if item["kind"] == "trisection"
            and item["side"] == "top"
            and item["endpoint_coordinates"] == [0.0, 224.0]
        )
        expressions = [point["coordinate_expression"][0] for point in relation["points"]]
        self.assertEqual(
            expressions,
            ["-1", "-√2/2", "1-√2", "(4-3√2)/2"],
        )
        self.assertEqual(relation["geometry_mode"], "fitted_qsqrt2_boundary_relation")
        self.assertLess(relation["fitted_geometry_max_residual_px"], 4.0)
        gauge = relation["recommended_coordinate_gauge"]
        self.assertEqual(gauge["side_length"]["expression"], "2+√2")
        self.assertEqual(
            [point["edge_distance"]["expression"] for point in gauge["points"]],
            ["0", "1/2", "1", "3/2"],
        )

    def test_shadow_bridge_catalog_uses_raw_evidence_instead_of_playback(self):
        payload = {"stats": {"analysis_size_used": 101}, "playback_trace": []}
        raw_report = {
            "enabled": True,
            "mode": "raw_boundary_directional_scan_v1",
            "source": "raw_image_directional_scan",
            "maximum_coordinate_px": 100,
            "points": [
                {"id": "raw:a", "point": [30.0, 0.0], "directional_score": 0.8},
                {"id": "raw:b", "point": [60.0, 0.0], "directional_score": 0.8},
                {"id": "raw:c", "point": [90.0, 0.0], "directional_score": 0.8},
            ],
        }
        raw_crease_report = {
            "enabled": True,
            "mode": "raw_finite_crease_entities_v1",
            "source": "raw_image_finite_line_evidence",
            "lines": [],
        }
        with (
            patch("shadow_bridge.reconstruct_for_web", return_value=payload),
            patch("shadow_bridge.refine_trace_offsets_from_cp", return_value=0),
            patch("shadow_bridge.attach_observed_offsets", return_value={}),
            patch("shadow_bridge.extract_raw_boundary_contacts", return_value=raw_report),
            patch("shadow_bridge.extract_raw_crease_entities", return_value=raw_crease_report),
            patch("shadow_bridge.build_geometry_shadow_report_v2", return_value={}),
            patch("shadow_bridge.build_quality_report_v5", return_value={}),
            patch("shadow_bridge.build_provenance_report_v6", return_value={}),
        ):
            result = json.loads(
                reconstruct_for_web_shadow_json(
                    b"not-read-by-mocks",
                    '{"construction_variants":false}',
                )
            )

        shadow = result["shadow_search"]
        self.assertEqual(shadow["raw_crease_evidence"], raw_crease_report)
        self.assertEqual(shadow["boundary_relation_candidate_source"], "raw_image_directional_scan")
        self.assertTrue(shadow["boundary_relation_candidates"])
        self.assertTrue(
            all(
                item["evidence_source"] == "raw_image_directional_scan"
                for item in shadow["boundary_relation_candidates"]
            )
        )

    def test_shadow_bridge_prefers_finite_topology_boundary_contacts(self):
        payload = {"stats": {"analysis_size_used": 101}, "playback_trace": []}
        raw_boundary = {
            "enabled": True,
            "maximum_coordinate_px": 100,
            "points": [],
        }
        raw_creases = {
            "enabled": True,
            "mode": "raw_finite_crease_entities_v1",
            "lines": [{"id": "placeholder"}],
        }
        topology = {
            "enabled": True,
            "mode": "raw_finite_crease_topology_v1",
            "maximum_coordinate_px": 100,
            "boundary_contacts": [
                {"id": "topology:a", "point": [30.0, 0.0], "directional_score": 1.0},
                {"id": "topology:b", "point": [60.0, 0.0], "directional_score": 1.0},
                {"id": "topology:c", "point": [90.0, 0.0], "directional_score": 1.0},
            ],
        }
        with (
            patch("shadow_bridge.reconstruct_for_web", return_value=payload),
            patch("shadow_bridge.refine_trace_offsets_from_cp", return_value=0),
            patch("shadow_bridge.attach_observed_offsets", return_value={}),
            patch("shadow_bridge.extract_raw_boundary_contacts", return_value=raw_boundary),
            patch("shadow_bridge.extract_raw_crease_entities", return_value=raw_creases),
            patch(
                "shadow_bridge.build_raw_crease_topology_graph",
                return_value=(None, {}, topology),
            ),
            patch("shadow_bridge.build_geometry_shadow_report_v2", return_value={}),
            patch("shadow_bridge.build_quality_report_v5", return_value={}),
            patch("shadow_bridge.build_provenance_report_v6", return_value={}),
        ):
            result = json.loads(
                reconstruct_for_web_shadow_json(
                    b"not-read-by-mocks",
                    '{"construction_variants":false}',
                )
            )

        shadow = result["shadow_search"]
        self.assertEqual(
            shadow["boundary_relation_candidate_source"],
            "raw_image_finite_topology",
        )
        self.assertEqual(shadow["raw_crease_topology"], topology)
        self.assertTrue(shadow["boundary_relation_candidates"])
        self.assertTrue(
            all(
                item["evidence_source"] == "raw_image_finite_topology"
                for item in shadow["boundary_relation_candidates"]
            )
        )


if __name__ == "__main__":
    unittest.main()
