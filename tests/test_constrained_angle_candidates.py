import math
import unittest

from constrained_angle_candidates import build_constrained_angle_candidates


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


def _boundary_point(angle_deg):
    radians = math.radians(angle_deg)
    direction = math.cos(radians), math.sin(radians)
    parameters = [
        boundary / component
        for boundary in (-200.0, 200.0)
        for component in direction
        if abs(component) > 1e-9 and boundary / component > 0.0
    ]
    parameter = min(parameters)
    return direction[0] * parameter, direction[1] * parameter


def _segment(segment_id, angle_deg, line_type=2):
    end = _boundary_point(angle_deg)
    return {
        "id": segment_id,
        "start_point_id": "center",
        "end_point_id": f"{segment_id}-end",
        "start_cp": [0.0, 0.0],
        "end_cp": [end[0], end[1]],
        "line_type": line_type,
        "line_type_source": "explicit_segment_assignment",
    }


def _contract(segments, *, rule="number_of_folds"):
    return {
        "candidate_segments": segments,
        "gate_results": {
            name: {"passed": name != "flat_foldability"}
            for name in _GATE_NAMES
        },
        "soft_diagnostics": {
            "camv": {
                "violations": [
                    {
                        "point": [0.0, 0.0],
                        "rule": rule,
                        "crease_degree": len(segments),
                        "boundary_degree": 0,
                    }
                ]
            }
        },
    }


def _pixel(point):
    return (point[0] + 200.0) / 4.0, (point[1] + 200.0) / 4.0


def _visible_interval(angle_deg):
    radians = math.radians(angle_deg)
    direction = math.cos(radians), math.sin(radians)
    end = _boundary_point(angle_deg)
    start_cp = direction[0] * 4.0, direction[1] * 4.0
    end_cp = end[0] - direction[0] * 4.0, end[1] - direction[1] * 4.0
    return {"start": list(_pixel(start_cp)), "end": list(_pixel(end_cp))}


def _canonical_line(line_id, angle_deg, *, channel="blue"):
    return {
        "id": line_id,
        "orientation_deg": angle_deg % 180.0,
        "source_channels": [channel],
        "visible_segments_px": [_visible_interval(angle_deg)],
    }


def _noncanonical_observation(observation_id, angle_deg, *, channel="blue"):
    interval = _visible_interval(angle_deg)
    return {
        "id": observation_id,
        "source": "raw_image_noncanonical_finite_stroke_observation",
        "channel": channel,
        "start_px": interval["start"],
        "end_px": interval["end"],
        "observed_angle_deg": angle_deg % 180.0,
    }


def _raw_report(*, lines=(), noncanonical=()):
    return {
        "maximum_coordinate_px": 100.0,
        "lines": list(lines),
        "noncanonical_angle_observations": list(noncanonical),
    }


class ConstrainedAngleCandidatesTest(unittest.TestCase):
    def test_kawasaki_missing_ray_is_canonical_and_image_supported(self):
        segments = [
            _segment("east", 0.0),
            _segment("south", 90.0),
            _segment("west", 180.0),
        ]
        raw = _raw_report(lines=[_canonical_line("missing-up", 270.0)])

        report = build_constrained_angle_candidates(raw, _contract(segments))

        self.assertEqual(report["candidate_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["kind"], "kawasaki_single_missing_ray")
        self.assertEqual(candidate["priority"], 0)
        self.assertEqual(candidate["direction_family"], "canonical_22_5")
        self.assertEqual(candidate["direction_index"], 4)
        self.assertAlmostEqual(candidate["direction_angle_deg"], 270.0)
        self.assertEqual(candidate["end_cp"], [0.0, -200.0])
        self.assertEqual(candidate["maekawa_line_type_options"], [3])
        self.assertEqual(candidate["proposed_line_type"], 3)
        self.assertFalse(candidate["applied"])
        self.assertTrue(candidate["requires_transactional_camv_recheck"])

    def test_degree_two_noncollinear_vertex_does_not_invent_a_bisector(self):
        segments = [_segment("east", 0.0), _segment("south", 90.0)]
        raw = _raw_report(
            noncanonical=[_noncanonical_observation("image-diagonal", 45.0)]
        )

        report = build_constrained_angle_candidates(
            raw,
            _contract(segments, rule="kawasaki_angles"),
        )

        self.assertEqual(report["candidate_count"], 0)
        self.assertEqual(
            report["rejection_counts"]["not_a_single_missing_ray_parity_case"],
            1,
        )

    def test_bisector_that_does_not_solve_kawasaki_is_not_a_missing_ray(self):
        segments = [
            _segment("east", 0.0),
            _segment("southeast", 67.5),
            _segment("west", 180.0),
        ]
        raw = _raw_report(
            noncanonical=[_noncanonical_observation("bisector-stroke", 33.75)]
        )

        report = build_constrained_angle_candidates(raw, _contract(segments))

        self.assertEqual(report["candidate_count"], 0)
        self.assertGreaterEqual(
            report["rejection_counts"]["insufficient_source_image_evidence"],
            1,
        )

    def test_noncanonical_missing_ray_must_be_both_kawasaki_and_bisector(self):
        segments = [
            _segment("east", 0.0),
            _segment("ray-50", 50.0),
            _segment("ray-100", 100.0),
        ]
        raw = _raw_report(
            noncanonical=[_noncanonical_observation("joint-proof", 230.0)]
        )

        report = build_constrained_angle_candidates(raw, _contract(segments))

        self.assertEqual(report["candidate_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["kind"], "kawasaki_single_missing_ray")
        self.assertEqual(candidate["priority"], 1)
        self.assertEqual(
            candidate["direction_family"],
            "derived_existing_sector_angle_bisector",
        )
        self.assertEqual(
            candidate["construction_sources"],
            ["kawasaki_single_missing_ray", "existing_sector_angle_bisector"],
        )
        self.assertEqual(
            candidate["angle_bisector_derivations"][0]["parent_segment_ids"],
            ["east", "ray-100"],
        )

    def test_supported_bisector_is_ignored_when_it_is_not_the_kawasaki_solution(self):
        segments = [
            _segment("east", 0.0),
            _segment("southeast", 45.0),
            _segment("west", 180.0),
        ]
        raw = _raw_report(
            lines=[
                _canonical_line("kawasaki-stroke", 315.0),
                _canonical_line("bisector-stroke", 22.5),
            ]
        )

        report = build_constrained_angle_candidates(raw, _contract(segments))

        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["kind"], "kawasaki_single_missing_ray")
        self.assertEqual(report["candidates"][0]["direction_angle_deg"], 315.0)
        self.assertEqual(
            report["candidates"][0]["image_evidence"]["matched_observation_ids"],
            ["kawasaki-stroke:0"],
        )

    def test_arbitrary_image_direction_is_not_a_construction_source(self):
        segments = [
            _segment("east", 0.0),
            _segment("southeast", 67.5),
            _segment("west", 180.0),
        ]
        raw = _raw_report(
            noncanonical=[_noncanonical_observation("arbitrary-stroke", 41.0)]
        )

        report = build_constrained_angle_candidates(raw, _contract(segments))

        self.assertEqual(report["candidate_count"], 0)
        self.assertEqual(report["invariants"]["free_image_fitted_directions"], 0)

    def test_candidate_is_rejected_when_one_line_cannot_satisfy_maekawa(self):
        segments = [
            _segment("east", 0.0),
            _segment("southeast-1", 45.0),
            _segment("south", 90.0),
            _segment("southeast-2", 135.0),
            _segment("west", 180.0),
        ]
        raw = _raw_report(lines=[_canonical_line("missing-up", 270.0)])

        report = build_constrained_angle_candidates(raw, _contract(segments))

        self.assertEqual(report["candidate_count"], 0)
        self.assertEqual(
            report["rejection_counts"]["maekawa_has_no_single_line_solution"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
