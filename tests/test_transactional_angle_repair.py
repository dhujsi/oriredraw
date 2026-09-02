import copy
import math
import unittest

from cp_audit import parse_cp
from constrained_angle_candidates import build_constrained_angle_candidates
from transactional_angle_repair import (
    _audit,
    _materialize,
    build_transactional_angle_repair,
)


_GATE_NAMES = (
    "exact_crease_closure",
    "exact_endpoint_closure",
    "observed_source_provenance",
    "direction_consistency",
    "residual_tolerance",
    "finite_planar_geometry",
    "segment_line_types",
    "paper_boundary_closure",
    "flat_foldability",
)


def _segment(segment_id, end, *, start=(0.0, 0.0), line_type=2):
    return {
        "id": segment_id,
        "source": "raw_image_finite_line_evidence",
        "start_point_id": "center",
        "end_point_id": f"{segment_id}-end",
        "start_cp": list(start),
        "end_cp": list(end),
        "line_type": line_type,
        "line_type_source": "explicit_segment_assignment",
        "visible_coverage": 1.0,
    }


def _base_contract(segments):
    audit = _audit(_materialize(segments))
    return {
        "status": "incomplete",
        "output_ready": False,
        "checks_passed": False,
        "cp_available": False,
        "cp": None,
        "candidate_segments": copy.deepcopy(segments),
        "candidate_internal_segment_count": len(segments),
        "gate_results": {
            name: {
                "passed": name != "flat_foldability",
                "blocker_codes": (
                    []
                    if name != "flat_foldability"
                    else ["camv_foldability_violations"]
                ),
            }
            for name in _GATE_NAMES
        },
        "blocker_count": 1,
        "blocker_counts": {
            "camv_foldability_violations": audit["violation_count"]
        },
        "blockers": [
            {
                "code": "camv_foldability_violations",
                "count": audit["violation_count"],
            }
        ],
        "soft_diagnostics": {"camv": audit, "camv_blocks_output": True},
        "invariants": {
            "old_cp_reused": False,
            "generated_internal_segment_count": 0,
            "generated_direction_count": 0,
        },
    }


def _candidate(
    candidate_id,
    end,
    *,
    start=(0.0, 0.0),
    line_type=3,
    target=None,
    angle=270.0,
    direction_index=4,
    parents=("east", "south", "west"),
    family="canonical_22_5",
    sources=("kawasaki_single_missing_ray",),
    bisector_parents=None,
):
    return {
        "id": candidate_id,
        "kind": "kawasaki_single_missing_ray",
        "priority": 0 if family == "canonical_22_5" else 1,
        "direction_family": family,
        "direction_index": direction_index,
        "direction_angle_deg": angle,
        "start_cp": list(start),
        "end_cp": list(end),
        "source_point_ids": ["center"],
        "parent_segment_ids": list(parents),
        "trigger_camv_rules": ["number_of_folds"],
        "construction_sources": list(sources),
        "angle_bisector_derivations": (
            [
                {
                    "parent_segment_ids": list(
                        bisector_parents or parents[:2]
                    )
                }
            ]
            if "existing_sector_angle_bisector" in sources
            else []
        ),
        "target": target
        or {"target_kind": "paper_boundary", "target_side": "top"},
        "image_evidence": {
            "matched_observation_ids": [f"evidence:{candidate_id}"],
            "visible_coverage": 1.0,
            "unsupported_length_px": 0.0,
            "distance_tolerance_px": 3.2,
            "angle_tolerance_deg": 2.0,
            "image_line_type": line_type,
        },
        "maekawa_line_type_options": [2, 3],
        "proposed_line_type": line_type,
        "applied": False,
        "requires_transactional_camv_recheck": True,
    }


def _candidate_report(*candidates):
    return {
        "enabled": True,
        "mode": "constrained_angle_candidates_v1",
        "candidate_count": len(candidates),
        "candidates": list(candidates),
    }


class TransactionalAngleRepairTest(unittest.TestCase):
    def setUp(self):
        self.base_segments = [
            _segment("east", (200.0, 0.0)),
            _segment("south", (0.0, 200.0)),
            _segment("west", (-200.0, 0.0)),
        ]

    def test_valid_candidate_is_promoted_only_after_complete_camv_pass(self):
        contract = _base_contract(self.base_segments)
        original = copy.deepcopy(contract)
        candidate = _candidate("missing-up", (0.0, -200.0))

        report, effective = build_transactional_angle_repair(
            contract,
            _candidate_report(candidate),
        )

        self.assertEqual(contract, original)
        self.assertTrue(report["output_promoted"])
        self.assertEqual(report["base_camv_violation_count"], 1)
        self.assertEqual(report["final_trial_camv_violation_count"], 0)
        self.assertEqual(report["accepted_candidate_ids"], ["missing-up"])
        self.assertIsNotNone(effective)
        self.assertTrue(effective["output_ready"])
        self.assertTrue(effective["cp_available"])
        self.assertEqual(effective["blockers"], [])
        self.assertTrue(effective["gate_results"]["flat_foldability"]["passed"])
        self.assertEqual(effective["candidate_internal_segment_count"], 4)
        self.assertEqual(
            effective["soft_diagnostics"]["camv"]["violation_count"], 0
        )
        rows, issues = parse_cp(effective["cp"])
        self.assertEqual(issues, [])
        self.assertEqual(sum(row.line_type in {2, 3} for row in rows), 4)
        repaired = next(
            item
            for item in effective["candidate_segments"]
            if item.get("source_candidate_id") == "missing-up"
        )
        self.assertTrue(repaired["transactionally_validated"])

    def test_candidate_generation_and_transaction_run_end_to_end(self):
        contract = _base_contract(self.base_segments)
        raw_report = {
            "maximum_coordinate_px": 100.0,
            "lines": [
                {
                    "id": "missing-up-source",
                    "orientation_deg": 90.0,
                    "source_channels": ["blue"],
                    "visible_segments_px": [
                        {"start": [50.0, 49.0], "end": [50.0, 0.0]}
                    ],
                }
            ],
            "noncanonical_angle_observations": [],
        }

        candidates = build_constrained_angle_candidates(raw_report, contract)
        report, effective = build_transactional_angle_repair(
            contract,
            candidates,
        )

        self.assertEqual(candidates["candidate_count"], 1)
        self.assertTrue(report["output_promoted"])
        self.assertIsNotNone(effective)
        self.assertEqual(
            effective["transactional_angle_repair"]["accepted_candidate_ids"],
            [candidates["candidates"][0]["id"]],
        )

    def test_wrong_mv_type_is_rolled_back_when_it_reveals_maekawa_error(self):
        contract = _base_contract(self.base_segments)
        original = copy.deepcopy(contract)
        candidate = _candidate(
            "wrong-mv",
            (0.0, -200.0),
            line_type=2,
        )

        report, effective = build_transactional_angle_repair(
            contract,
            _candidate_report(candidate),
        )

        self.assertIsNone(effective)
        self.assertFalse(report["output_promoted"])
        self.assertEqual(contract, original)
        self.assertEqual(
            report["candidate_results"][0]["reason"],
            "introduces_new_camv_violation",
        )
        self.assertIn(
            [0.0, 0.0, "maekawa"],
            report["candidate_results"][0]["new_violation_keys"],
        )

    def test_target_segment_is_split_only_in_discarded_trial(self):
        target = _segment(
            "target",
            (200.0, -100.0),
            start=(-200.0, -100.0),
        )
        contract = _base_contract([*self.base_segments, target])
        original = copy.deepcopy(contract)
        candidate = _candidate(
            "t-junction",
            (0.0, -100.0),
            target={
                "target_kind": "existing_exact_segment",
                "target_segment_id": "target",
                "target_segment_parameter": 0.5,
            },
        )

        report, effective = build_transactional_angle_repair(
            contract,
            _candidate_report(candidate),
        )

        self.assertIsNone(effective)
        self.assertEqual(contract, original)
        self.assertEqual(
            report["candidate_results"][0]["reason"],
            "introduces_new_camv_violation",
        )
        self.assertIn(
            [0.0, -100.0, "number_of_folds"],
            report["candidate_results"][0]["new_violation_keys"],
        )
        self.assertEqual(
            [item["id"] for item in contract["candidate_segments"]],
            ["east", "south", "west", "target"],
        )

    def test_one_segment_can_resolve_two_odd_vertices_and_split_its_target(self):
        segments = [
            _segment("a-east", (200.0, 100.0), start=(0.0, 100.0)),
            _segment("a-south", (0.0, 200.0), start=(0.0, 100.0)),
            _segment("a-west", (-200.0, 100.0), start=(0.0, 100.0)),
            _segment(
                "b-horizontal",
                (200.0, -100.0),
                start=(-200.0, -100.0),
            ),
            _segment("b-north", (0.0, -200.0), start=(0.0, -100.0)),
        ]
        candidate = _candidate(
            "connect-two-odd-vertices",
            (0.0, -100.0),
            start=(0.0, 100.0),
            target={
                "target_kind": "existing_exact_segment",
                "target_segment_id": "b-horizontal",
                "target_segment_parameter": 0.5,
            },
            parents=("a-east", "a-south", "a-west"),
        )

        report, effective = build_transactional_angle_repair(
            _base_contract(segments),
            _candidate_report(candidate),
        )

        self.assertTrue(report["output_promoted"])
        self.assertEqual(report["base_camv_violation_count"], 2)
        self.assertEqual(report["final_trial_camv_violation_count"], 0)
        self.assertEqual(
            report["accepted_transactions"][0]["split_segment_count"],
            1,
        )
        self.assertIsNotNone(effective)
        self.assertEqual(effective["candidate_internal_segment_count"], 7)
        self.assertEqual(
            effective["invariants"]["transactional_split_segment_count"],
            1,
        )
        split_fragments = [
            item
            for item in effective["candidate_segments"]
            if item.get("transactional_root_segment_id") == "b-horizontal"
        ]
        self.assertEqual(len(split_fragments), 2)

    def test_equal_rank_valid_solutions_are_reported_as_ambiguous(self):
        segments = [
            _segment("ray-0", (200.0, 0.0)),
            _segment("ray-45", (200.0, 200.0)),
            _segment("ray-202", (-200.0, -82.8427124746)),
        ]
        parents = ("ray-0", "ray-45", "ray-202")
        first = _candidate(
            "solution-22.5",
            (200.0, 82.8427124746),
            target={"target_kind": "paper_boundary", "target_side": "right"},
            angle=22.5,
            direction_index=1,
            parents=parents,
        )
        second = _candidate(
            "solution-337.5",
            (200.0, -82.8427124746),
            target={"target_kind": "paper_boundary", "target_side": "right"},
            angle=337.5,
            direction_index=7,
            parents=parents,
        )

        report, effective = build_transactional_angle_repair(
            _base_contract(segments),
            _candidate_report(first, second),
        )

        self.assertIsNone(effective)
        self.assertFalse(report["output_promoted"])
        self.assertEqual(len(report["ambiguous_source_points"]), 1)
        self.assertEqual(
            set(report["ambiguous_source_points"][0]["candidate_ids"]),
            {"solution-22.5", "solution-337.5"},
        )
        self.assertEqual(
            report["rejection_counts"]["ambiguous_equal_rank_transaction"],
            2,
        )

    def test_noncanonical_kawasaki_without_bisector_provenance_is_rejected(self):
        candidate = _candidate(
            "free-noncanonical",
            (-167.819926, -200.0),
            angle=230.0,
            direction_index=None,
            family="derived_existing_sector_angle_bisector",
            sources=("kawasaki_single_missing_ray",),
        )

        report, effective = build_transactional_angle_repair(
            _base_contract(self.base_segments),
            _candidate_report(candidate),
        )

        self.assertIsNone(effective)
        self.assertEqual(
            report["candidate_results"][0]["reason"],
            "noncanonical_requires_existing_sector_bisector",
        )

    def test_noncanonical_direction_is_recomputed_as_existing_sector_bisector(self):
        def boundary_endpoint(angle_deg):
            radians = math.radians(angle_deg)
            direction = math.cos(radians), math.sin(radians)
            scale = min(
                edge / component
                for edge in (-200.0, 200.0)
                for component in direction
                if abs(component) > 1e-9 and edge / component > 0.0
            )
            return direction[0] * scale, direction[1] * scale

        segments = [
            _segment("ray-0", boundary_endpoint(0.0)),
            _segment("ray-50", boundary_endpoint(50.0)),
            _segment("ray-100", boundary_endpoint(100.0), line_type=3),
        ]
        candidate = _candidate(
            "joint-theorem-proof",
            boundary_endpoint(230.0),
            angle=230.0,
            direction_index=None,
            parents=("ray-0", "ray-50", "ray-100"),
            family="derived_existing_sector_angle_bisector",
            line_type=2,
            sources=(
                "kawasaki_single_missing_ray",
                "existing_sector_angle_bisector",
            ),
            bisector_parents=("ray-100", "ray-0"),
        )

        report, effective = build_transactional_angle_repair(
            _base_contract(segments),
            _candidate_report(candidate),
        )

        self.assertTrue(report["output_promoted"])
        self.assertIsNotNone(effective)
        self.assertEqual(
            effective["invariants"]["generated_direction_count"],
            1,
        )

    def test_non_camv_base_blocker_prevents_all_trials(self):
        contract = _base_contract(self.base_segments)
        contract["gate_results"]["residual_tolerance"] = {
            "passed": False,
            "blocker_codes": ["endpoint_residual_exceeds_tolerance"],
        }
        contract["blocker_counts"]["endpoint_residual_exceeds_tolerance"] = 1

        report, effective = build_transactional_angle_repair(
            contract,
            _candidate_report(_candidate("missing-up", (0.0, -200.0))),
        )

        self.assertIsNone(effective)
        self.assertFalse(report["enabled"])
        self.assertEqual(report["reason"], "base_prerequisite_gate_failed")


if __name__ == "__main__":
    unittest.main()
