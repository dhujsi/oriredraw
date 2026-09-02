import copy
import json
import math
import unittest
from unittest.mock import patch

from guided_construction import (
    _next_relation_rank_key,
    build_boundary_relation_catalog,
    build_guided_boundary_report,
)


def _anchor(trace_id, point, angle, source, *, generation=0, parents=()):
    radians = math.radians(angle)
    normal = (-math.sin(radians), math.cos(radians))
    return {
        "trace_id": trace_id,
        "anchor_point_px": list(point),
        "angle": angle,
        "line_offset_px": normal[0] * point[0] + normal[1] * point[1],
        "snap_error_px": 0.0,
        "generation": generation,
        "trace_parent_ids": list(parents),
        "source": source,
        "forms_output": True,
    }


class GuidedConstructionTest(unittest.TestCase):
    def setUp(self):
        # Three observed points split the finite top-edge interval from the
        # paper corner at x=0 to the observed point at x=90 into equal thirds.
        self.result = {
            "stats": {"analysis_size_used": 101},
            "playback_trace": [
                _anchor(0, (0.0, 0.0), 0.0, "角点种子"),
                _anchor(1, (30.0, 0.0), 45.0, "唯一纸边 a+b√2 种子"),
                _anchor(2, (60.0, 0.0), 135.0, "唯一纸边 a+b√2 种子"),
                _anchor(3, (90.0, 0.0), 90.0, "唯一纸边 a+b√2 种子"),
            ],
        }

    def test_catalog_lists_complete_boundary_relation_with_ranked_radical_points(self):
        catalog = build_boundary_relation_catalog(self.result)

        relation = next(
            item
            for item in catalog
            if item["kind"] == "trisection"
            and item["side"] == "top"
            and item["endpoint_coordinates"] == [0.0, 90.0]
        )
        self.assertEqual(relation["priority"], 1)
        self.assertEqual(relation["evidence_source"], "strict_playback_trace_boundary_contacts")
        self.assertEqual(len(relation["points"]), 4)
        self.assertTrue(all("coordinate_expression" in point for point in relation["points"]))

    def test_selected_relation_replaces_unselected_parentless_roots_for_guided_propagation(self):
        relation = next(
            item
            for item in build_boundary_relation_catalog(self.result)
            if item["kind"] == "trisection"
            and item["side"] == "top"
            and item["endpoint_coordinates"] == [0.0, 90.0]
        )

        report = build_guided_boundary_report(self.result, {"id": relation["id"]})

        self.assertTrue(report["enabled"])
        self.assertEqual(report["mode"], "guided_boundary_relation_v4")
        self.assertFalse(report["legacy_search_executed"])
        self.assertEqual(report["status"], "complete_propagation")
        self.assertTrue(report["relation_used_by_selected_route"])
        self.assertEqual(report["unexplained_observations"], 0)
        self.assertEqual(report["suppressed_unselected_root_operations"], 3)
        self.assertGreaterEqual(report["guided_selected_ray_count"], 3)
        self.assertTrue(report["output_unchanged"])
        self.assertIn("construction_angle_candidates", report)
        self.assertIn("construction_angle_repair", report)
        json.dumps(report, ensure_ascii=False)

        geometry = report["geometry_graph"]
        self.assertEqual(geometry["mode"], "observed_exact_incidence_graph_v1")
        self.assertEqual(geometry["crease_count"], len(self.result["playback_trace"]))
        self.assertEqual(geometry["exact_point_count"], 4)
        self.assertEqual(geometry["invariants"]["invented_crease_count"], 0)
        self.assertEqual(geometry["invariants"]["invented_direction_count"], 0)
        exact_creases = [
            entity
            for entity in geometry["entities"]
            if entity["kind"] == "crease" and entity["exact_geometry"]
        ]
        self.assertEqual(len(exact_creases), 3)

        self.assertTrue(
            all(
                entity["observed_geometry"]["direction_index"]
                == entity["exact_geometry"]["direction_index"]
                for entity in exact_creases
            )
        )
        # Trace 0 lies along the paper boundary. It is observed but must not be
        # exactified as an entering crease from this boundary relation.
        tangent = next(
            entity
            for entity in geometry["entities"]
            if entity["kind"] == "crease"
            and entity["observed_geometry"]["trace_id"] == 0
        )
        self.assertFalse(tangent["exact_geometry"])
        propagation = report["geometry_propagation"]
        self.assertEqual(propagation["mode"], "deterministic_exact_frontier_v1")
        self.assertEqual(propagation["status"], "complete_existing_creases")
        self.assertFalse(propagation["frontier_limit_reached"])
        self.assertEqual(propagation["invariants"]["enumerated_direction_count"], 0)
        self.assertEqual(propagation["invariants"]["created_crease_count"], 0)
        self.assertGreater(
            propagation["rejection_counts"].get("paper_boundary_tangent_pruned", 0),
            0,
        )

    def test_transactionally_repaired_contract_is_the_only_promoted_output(self):
        relation = next(
            item
            for item in build_boundary_relation_catalog(self.result)
            if item["kind"] == "trisection"
            and item["side"] == "top"
            and item["endpoint_coordinates"] == [0.0, 90.0]
        )
        base_contract = {
            "output_ready": False,
            "checks_passed": False,
            "cp_available": False,
            "cp": None,
            "segment_line_type_assignments": {
                "observed": {"line_type": 2, "source": "user_confirmed"}
            },
        }
        candidate_report = {"enabled": True, "candidates": [{"id": "repair"}]}
        repair_report = {"enabled": True, "output_promoted": True}
        repaired_contract = {
            "output_ready": True,
            "checks_passed": True,
            "cp_available": True,
            "cp": "1 -200 -200 200 -200\n",
            "segment_line_type_assignments": {
                "generated": {
                    "line_type": 3,
                    "source": "camv_maekawa_single_line_solution",
                }
            },
        }

        with (
            patch(
                "guided_construction.build_guided_cp_output_contract",
                return_value=base_contract,
            ),
            patch(
                "guided_construction.build_constrained_angle_candidates",
                return_value=candidate_report,
            ),
            patch(
                "guided_construction.build_transactional_angle_repair",
                return_value=(repair_report, repaired_contract),
            ),
        ):
            report = build_guided_boundary_report(
                self.result,
                {"id": relation["id"]},
            )

        self.assertIs(report["cp_output_contract"], repaired_contract)
        self.assertIs(report["construction_angle_candidates"], candidate_report)
        self.assertIs(report["construction_angle_repair"], repair_report)
        self.assertTrue(report["output_ready"])
        self.assertTrue(report["cp_available"])
        self.assertEqual(report["cp"], repaired_contract["cp"])
        self.assertEqual(
            report["segment_line_type_assignments"],
            base_contract["segment_line_type_assignments"],
        )

    def test_raw_observation_and_exact_relation_coordinate_share_one_point_without_overwrite(self):
        relation = next(
            item
            for item in build_boundary_relation_catalog(self.result)
            if item["kind"] == "trisection"
            and item["side"] == "top"
            and item["endpoint_coordinates"] == [0.0, 90.0]
        )
        relation = copy.deepcopy(relation)
        relation["evidence_source"] = "raw_image_directional_scan"
        target_point = relation["points"][1]
        target_point["observed_point_px"] = [29.0, 0.0]
        target_point["point_px"] = [30.0, 0.0]
        result = dict(self.result)
        result["boundary_relation_candidates"] = [relation]

        report = build_guided_boundary_report(result, {"id": relation["id"]})

        entity = next(
            item
            for item in report["geometry_graph"]["entities"]
            if any(
                membership.get("point_id") == target_point["id"]
                for membership in item["metadata"].get("selected_relation_points", [])
            )
        )
        self.assertEqual(entity["observed_geometry"]["point_px"], [30.0, 0.0])
        relation_observation = next(
            item
            for item in entity["observed_geometry"]["observations"]
            if item.get("source") == "raw_image_directional_scan"
        )
        self.assertEqual(relation_observation["point_px"], [29.0, 0.0])
        self.assertEqual(entity["observed_geometry"]["relation_fitted_point_px"], [30.0, 0.0])
        self.assertEqual(
            entity["exact_geometry"]["project_coordinate"],
            target_point["project_coordinate"],
        )

    def test_unknown_relation_id_is_rejected_instead_of_becoming_a_free_seed(self):
        report = build_guided_boundary_report(self.result, {"id": "boundary-not-real"})

        self.assertFalse(report["enabled"])
        self.assertEqual(report["reason"], "invalid_guided_boundary_relation")

    def test_equal_gain_next_relation_prefers_simple_well_fitted_global_gauge(self):
        high_complexity = {
            "id": "high-complexity",
            "projected_new_crease_count": 7,
            "direct_unresolved_seed_count": 2,
            "recommended_coordinate_gauge": {"score": 119.7},
            "algebraic_max_residual_px": 2.8529,
            "priority": 1,
        }
        simple = {
            "id": "simple",
            "projected_new_crease_count": 7,
            "direct_unresolved_seed_count": 1,
            "recommended_coordinate_gauge": {"score": 4.7},
            "algebraic_max_residual_px": 0.1,
            "priority": 20,
        }
        larger_gain = {
            "id": "larger-gain",
            "projected_new_crease_count": 8,
            "direct_unresolved_seed_count": 1,
            "recommended_coordinate_gauge": {"score": 500.0},
            "algebraic_max_residual_px": 3.0,
            "priority": 30,
        }

        ranked = sorted(
            [high_complexity, simple, larger_gain],
            key=_next_relation_rank_key,
        )

        self.assertEqual(
            [item["id"] for item in ranked],
            ["larger-gain", "simple", "high-complexity"],
        )


if __name__ == "__main__":
    unittest.main()
