import math
import unittest

import cv2
import numpy as np

from reconstructor import (
    ALLOWED_ANGLES,
    AlgebraicValue,
    CandidateLine,
    Settings,
    _line_intersection,
    _close_internal_lineheads,
    _adaptive_geometry_evidence,
    _planarize_edges,
    _prune_post_planar_lineheads,
    _repair_near_focus_camv_violations,
    _recover_camv_supported_paths,
    _edge_mv_evidence,
    _assign_and_optimize_mv,
    _propagate_constructible_rays,
    _recover_fragmented_rays_from_primary,
    _recover_exact_skeleton_node_edges,
    _recover_supported_graph_chords,
    _prune_unsupported_local_cycles,
    _refine_centerline_offset,
    _snap_and_prune_dangling_edges,
    Edge,
    snap_qsqrt2,
    snap_qsqrt2_bounded,
)


class RootTwoGeometryTest(unittest.TestCase):
    def test_red_blue_evidence_is_measured_without_ai(self):
        image = np.full((101, 101, 3), 255, dtype=np.uint8)
        edge = Edge(np.array([10.0, 50.0]), np.array([90.0, 50.0]), 4)
        cv2.line(image, (10, 50), (90, 50), (0, 0, 255), 1)
        evidence = _edge_mv_evidence(image, edge)
        self.assertGreater(evidence["red_probability"], 0.95)
        self.assertFalse(evidence["ambiguous"])

    def test_full_camv_can_flip_only_ambiguous_color_call(self):
        image = np.full((101, 101, 3), 255, dtype=np.uint8)
        center = np.array([50.0, 50.0])
        edges = [
            Edge(center.copy(), np.array([100.0, 50.0]), 4),
            Edge(center.copy(), np.array([50.0, 0.0]), 4),
            Edge(center.copy(), np.array([0.0, 50.0]), 4),
            Edge(center.copy(), np.array([50.0, 100.0]), 4),
        ]
        for edge in edges:
            cv2.line(
                image,
                tuple(edge.start.astype(int)),
                tuple(edge.end.astype(int)),
                (0, 0, 255),
                1,
            )
        # This arm looks slightly more red than blue, so its first call is
        # mountain but deliberately weak enough for cAMV to revise.
        cv2.line(image, (50, 79), (50, 100), (255, 0, 0), 1)
        assigned, stats, report = _assign_and_optimize_mv(image, edges)
        self.assertEqual(stats["mv_camv_changed_segments"], 1)
        self.assertEqual(sum(edge.line_type == 3 for edge in assigned), 1)
        self.assertTrue(report["passes_camv"])

    def test_full_camv_never_flips_strong_color_evidence(self):
        image = np.full((101, 101, 3), 255, dtype=np.uint8)
        center = np.array([50.0, 50.0])
        edges = [
            Edge(center.copy(), np.array([100.0, 50.0]), 4),
            Edge(center.copy(), np.array([50.0, 0.0]), 4),
            Edge(center.copy(), np.array([0.0, 50.0]), 4),
            Edge(center.copy(), np.array([50.0, 100.0]), 4),
        ]
        for edge in edges:
            cv2.line(
                image,
                tuple(edge.start.astype(int)),
                tuple(edge.end.astype(int)),
                (0, 0, 255),
                1,
            )
        assigned, stats, report = _assign_and_optimize_mv(image, edges)
        self.assertEqual(stats["mv_camv_changed_segments"], 0)
        self.assertEqual(sum(edge.line_type == 2 for edge in assigned), 4)
        self.assertGreater(report["mv_violation_count"], 0)

    def test_camv_repairs_unprotected_near_focus_boundary_arm(self):
        size = 101
        focus = np.array([50.0, 50.0])
        satellite = np.array([49.0, 50.0])
        edges = [
            Edge(focus.copy(), np.array([100.0, 50.0]), 4),
            Edge(focus.copy(), np.array([0.0, 100.0]), 4),
            Edge(focus.copy(), np.array([100.0, 0.0]), 4),
            Edge(focus.copy(), satellite.copy(), 4),
            Edge(satellite.copy(), np.array([49.0, 0.0]), 4),
        ]
        ink = np.zeros((size, size), dtype=np.uint8)
        for edge in edges[:3]:
            cv2.line(
                ink,
                tuple(np.rint(edge.start).astype(int)),
                tuple(np.rint(edge.end).astype(int)),
                255,
                1,
            )
        cv2.line(ink, (50, 50), (50, 0), 255, 1)
        source = CandidateLine(
            4,
            -49.0,
            1.0,
            0.0,
            "",
            AlgebraicValue(0, 0, 0.0, 0.0),
            satellite.copy(),
            origin_kind="intersection",
        )
        repaired, stats = _repair_near_focus_camv_violations(
            edges, [source], ink, Settings()
        )
        self.assertEqual(stats["camv_near_focus_repairs"], 1)
        self.assertEqual(stats["camv_violations_after_repair"], 0)
        self.assertTrue(
            any(
                np.linalg.norm(edge.start - focus) < 1e-6
                and np.linalg.norm(edge.end - np.array([50.0, 0.0])) < 1e-6
                for edge in repaired
            )
        )

    def test_camv_never_reanchors_corner_seed_ray(self):
        size = 101
        focus = np.array([50.0, 50.0])
        satellite = np.array([49.0, 50.0])
        edges = [
            Edge(focus.copy(), np.array([100.0, 50.0]), 4),
            Edge(focus.copy(), np.array([0.0, 100.0]), 4),
            Edge(focus.copy(), np.array([100.0, 0.0]), 4),
            Edge(focus.copy(), satellite.copy(), 4),
            Edge(satellite.copy(), np.array([49.0, 0.0]), 4),
        ]
        ink = np.zeros((size, size), dtype=np.uint8)
        cv2.line(ink, (50, 50), (50, 0), 255, 1)
        protected = CandidateLine(
            4,
            -49.0,
            1.0,
            0.0,
            "",
            AlgebraicValue(0, 0, 0.0, 0.0),
            satellite.copy(),
            origin_kind="corner",
        )
        _, stats = _repair_near_focus_camv_violations(
            edges, [protected], ink, Settings()
        )
        self.assertEqual(stats["camv_near_focus_repairs"], 0)
        self.assertGreater(stats["camv_protected_reanchors_rejected"], 0)

    def test_camv_recheck_recovers_only_strong_exact_contact_arm(self):
        size = 101
        square = np.full((size, size, 3), 255, dtype=np.uint8)
        ink = np.zeros((size, size), dtype=np.uint8)
        center = np.array([50.0, 50.0])
        west = np.array([0.0, 50.0])
        north = np.array([50.0, 0.0])
        south = np.array([50.0, 100.0])
        east = np.array([100.0, 50.0])
        edges = [
            Edge(center.copy(), west, 4),
            Edge(center.copy(), north, 4),
            Edge(center.copy(), south, 4),
        ]
        for endpoint in (west, north, south, east):
            cv2.line(
                square,
                tuple(center.astype(int)),
                tuple(endpoint.astype(int)),
                (0, 0, 255),
                1,
            )
            cv2.line(
                ink,
                tuple(center.astype(int)),
                tuple(endpoint.astype(int)),
                255,
                1,
            )

        recovered, stats = _recover_camv_supported_paths(
            square, ink, edges, Settings()
        )

        self.assertEqual(stats["camv_path_violations_before"], 1)
        self.assertEqual(stats["camv_path_violations_after"], 0)
        self.assertEqual(stats["camv_path_committed_arms"], 1)
        self.assertTrue(
            any(
                np.linalg.norm(edge.start - center) < 1e-6
                and np.linalg.norm(edge.end - east) < 1e-6
                for edge in recovered
            )
        )

    def test_camv_recheck_rolls_back_without_missing_line_evidence(self):
        size = 101
        square = np.full((size, size, 3), 255, dtype=np.uint8)
        ink = np.zeros((size, size), dtype=np.uint8)
        center = np.array([50.0, 50.0])
        endpoints = (
            np.array([0.0, 50.0]),
            np.array([50.0, 0.0]),
            np.array([50.0, 100.0]),
        )
        edges = [Edge(center.copy(), endpoint.copy(), 4) for endpoint in endpoints]
        for endpoint in endpoints:
            cv2.line(
                square,
                tuple(center.astype(int)),
                tuple(endpoint.astype(int)),
                (0, 0, 255),
                1,
            )
            cv2.line(
                ink,
                tuple(center.astype(int)),
                tuple(endpoint.astype(int)),
                255,
                1,
            )

        recovered, stats = _recover_camv_supported_paths(
            square, ink, edges, Settings()
        )

        self.assertEqual(stats["camv_path_recheck_improved"], 0)
        self.assertEqual(stats["camv_path_committed_arms"], 0)
        self.assertEqual(len(recovered), len(edges))

    def test_unsupported_local_cycle_edge_is_removed(self):
        size = 121
        square = np.full((size, size, 3), 255, dtype=np.uint8)
        points = [
            np.array([50.0, 50.0]),
            np.array([58.0, 50.0]),
            np.array([58.0, 58.0]),
            np.array([50.0, 58.0]),
        ]
        cycle = [
            Edge(points[0], points[1], 4),
            Edge(points[1], points[2], 4),
            Edge(points[2], points[3], 4),
            Edge(points[3], points[0], 4),
        ]
        arms = [
            Edge(np.array([30.0, 50.0]), points[0], 4),
            Edge(points[1], np.array([78.0, 50.0]), 4),
            Edge(points[2], np.array([78.0, 58.0]), 4),
            Edge(np.array([30.0, 58.0]), points[3], 4),
        ]
        # Draw every edge except the cycle's lower side.
        for edge in cycle[:2] + cycle[3:] + arms:
            cv2.line(
                square,
                tuple(np.rint(edge.start).astype(int)),
                tuple(np.rint(edge.end).astype(int)),
                (0, 0, 255),
                1,
            )
        ink = (np.min(square, axis=2) < 245).astype(np.uint8) * 255
        kept, stats = _prune_unsupported_local_cycles(
            square, ink, cycle + arms
        )
        missing_key = tuple(sorted((tuple(points[2]), tuple(points[3]))))
        kept_keys = {
            tuple(sorted((tuple(edge.start), tuple(edge.end))))
            for edge in kept
        }
        self.assertNotIn(missing_key, kept_keys)
        self.assertEqual(stats["unsupported_local_cycle_edges_rejected"], 1)

    def test_supported_graph_chord_uses_only_existing_exact_nodes(self):
        size = 121
        square = np.full((size, size, 3), 255, dtype=np.uint8)
        left = np.array([50.0, 60.0])
        right = np.array([70.0, 60.0])
        edges = [
            Edge(np.array([30.0, 40.0]), left.copy(), 4),
            Edge(np.array([30.0, 80.0]), left.copy(), 4),
            Edge(np.array([50.0, 20.0]), left.copy(), 4),
            Edge(right.copy(), np.array([90.0, 40.0]), 4),
            Edge(right.copy(), np.array([90.0, 80.0]), 4),
            Edge(right.copy(), np.array([70.0, 100.0]), 4),
        ]
        for edge in edges:
            cv2.line(
                square,
                tuple(np.rint(edge.start).astype(int)),
                tuple(np.rint(edge.end).astype(int)),
                (0, 0, 255),
                1,
            )
        cv2.line(square, (50, 60), (70, 60), (0, 0, 255), 1)
        ink = (np.min(square, axis=2) < 245).astype(np.uint8) * 255

        recovered, stats = _recover_supported_graph_chords(
            square, ink, edges, Settings()
        )
        links = {
            tuple(
                sorted(
                    (
                        tuple(np.round(edge.start, 7)),
                        tuple(np.round(edge.end, 7)),
                    )
                )
            )
            for edge in recovered
        }
        self.assertIn(tuple(sorted((tuple(left), tuple(right)))), links)
        self.assertEqual(stats["supported_graph_chords_recovered"], 1)

    def test_skeleton_recovery_exports_only_exact_constructed_nodes(self):
        size = 201
        square = np.full((size, size, 3), 255, dtype=np.uint8)
        center = np.array([80.0, 80.0])
        target = np.array([120.0, 120.0])
        cv2.line(square, (80, 80), (120, 120), (0, 0, 255), 1)
        # Existing arms make both exact points visible skeleton topology nodes.
        cv2.line(square, (60, 80), (80, 80), (0, 0, 255), 1)
        cv2.line(square, (80, 60), (80, 80), (0, 0, 255), 1)
        cv2.line(square, (120, 100), (120, 120), (0, 0, 255), 1)
        cv2.line(square, (100, 120), (120, 120), (0, 0, 255), 1)
        ink = (np.min(square, axis=2) < 245).astype(np.uint8) * 255
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def line(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            return CandidateLine(
                orientation,
                float(normal @ point),
                10.0,
                0.0,
                "",
                reference,
                point.copy(),
            )

        construction = [
            line(0, center),
            line(4, center),
            line(0, target),
            line(4, target),
        ]
        recovered, stats = _recover_exact_skeleton_node_edges(
            square,
            ink,
            [],
            construction,
            Settings(),
        )
        diagonal = [
            edge
            for edge in recovered
            if np.allclose(edge.start, center) and np.allclose(edge.end, target)
            or np.allclose(edge.start, target) and np.allclose(edge.end, center)
        ]
        self.assertEqual(len(diagonal), 1)
        self.assertGreaterEqual(stats["skeleton_exact_edges_recovered"], 1)

    def test_final_planar_audit_removes_internal_degree_one_arm(self):
        edges = [
            Edge(np.array([20.0, 100.0]), np.array([100.0, 100.0]), 4),
            Edge(np.array([100.0, 100.0]), np.array([180.0, 100.0]), 4),
            Edge(np.array([100.0, 20.0]), np.array([100.0, 100.0]), 4),
            Edge(np.array([100.0, 100.0]), np.array([100.0, 180.0]), 4),
            Edge(np.array([100.0, 100.0]), np.array([106.0, 106.0]), 4),
        ]
        kept, removed = _prune_post_planar_lineheads(edges, 201)
        self.assertEqual(removed, 5)
        self.assertEqual(kept, [])

    def test_fragmented_ray_requires_a_high_degree_primary_node(self):
        size = 201
        center = np.array([100.0, 100.0])
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def ray(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            value = CandidateLine(
                orientation,
                float(normal @ point),
                20.0,
                0.0,
                "",
                reference,
                point.copy(),
            )
            value.generation = 0
            return value

        primary_lines = [ray(0, center), ray(4, center)]
        primary_edges = [
            Edge(np.array([0.0, 100.0]), center.copy(), 4),
            Edge(center.copy(), np.array([200.0, 100.0]), 4),
            Edge(np.array([100.0, 0.0]), center.copy(), 4),
            Edge(center.copy(), np.array([100.0, 200.0]), 4),
        ]
        direction = np.array([math.sqrt(0.5), math.sqrt(0.5)])
        starts = [0.0, 35.0, 70.0, 105.0]
        raw = []
        for start in starts:
            first = center + direction * start
            second = center + direction * (start + 30.0)
            raw.append(
                {
                    "orientation": 2,
                    "offset": 0.0,
                    "length": 30.0,
                    "start": first,
                    "end": second,
                }
            )
        ink = np.zeros((size, size), dtype=np.uint8)
        cv2.line(ink, (100, 100), (200, 200), 255, 3)
        distance = cv2.distanceTransform(
            np.where(ink > 0, 0, 255).astype(np.uint8),
            cv2.DIST_L2,
            3,
        )

        edges, lines, stats = _recover_fragmented_rays_from_primary(
            primary_edges,
            primary_lines,
            raw,
            [list(range(len(raw)))],
            size,
            distance,
            Settings(),
        )
        self.assertEqual(stats["fragmented_rays_recovered"], 1)
        self.assertEqual(len(lines), 1)
        self.assertGreaterEqual(len(edges), 1)
        self.assertEqual(lines[0].origin_kind, "intersection")

        # The same weak evidence cannot self-activate without the established
        # four-arm node supplied by the primary construction graph.
        edges, lines, stats = _recover_fragmented_rays_from_primary(
            primary_edges[:2],
            primary_lines,
            raw,
            [list(range(len(raw)))],
            size,
            distance,
            Settings(),
        )
        self.assertEqual(stats["fragmented_rays_recovered"], 0)
        self.assertEqual(edges, [])
        self.assertEqual(lines, [])

    def test_blur_increases_adaptive_stroke_uncertainty(self):
        clean = np.full((160, 160, 3), 255, dtype=np.uint8)
        cv2.line(clean, (10, 80), (149, 80), (0, 0, 255), 1)
        blurred = cv2.GaussianBlur(clean, (0, 0), 1.8)

        _, _, clean_stats = _adaptive_geometry_evidence(clean)
        _, _, blurred_stats = _adaptive_geometry_evidence(blurred)

        self.assertGreaterEqual(
            blurred_stats["adaptive_evidence_distance_px"],
            clean_stats["adaptive_evidence_distance_px"],
        )

    def test_blurred_stroke_edge_refines_to_same_centerline(self):
        size = 96
        signal = np.zeros((size, size), dtype=np.float32)
        signal[:, 46:51] = 1.0
        signal = cv2.GaussianBlur(signal, (0, 0), 1.6)
        start = np.array([48.0, 8.0])
        end = np.array([48.0, 88.0])

        first, _ = _refine_centerline_offset(
            signal, start, end, 4, -45.5, 6.0
        )
        second, _ = _refine_centerline_offset(
            signal, start, end, 4, -50.5, 6.0
        )

        self.assertAlmostEqual(first, -48.0, delta=0.7)
        self.assertAlmostEqual(second, -48.0, delta=0.7)

    def test_known_root_two_references(self):
        cases = [
            (math.sqrt(2) - 1, -1, 1),
            (2 - math.sqrt(2), 2, -1),
            (3 - 2 * math.sqrt(2), 3, -2),
            (5 * math.sqrt(2) - 7, -7, 5),
        ]
        for value, expected_a, expected_b in cases:
            snapped = snap_qsqrt2(value)
            self.assertEqual((snapped.a, snapped.b), (expected_a, expected_b))
            self.assertLess(snapped.error, 1e-12)

    def test_core_reference_can_prefer_coefficients_bounded_by_ten(self):
        value = 16 - 11 * math.sqrt(2)
        unrestricted = snap_qsqrt2(value)
        bounded = snap_qsqrt2_bounded(value, 10)

        self.assertGreater(max(abs(unrestricted.a), abs(unrestricted.b)), 10)
        self.assertLessEqual(max(abs(bounded.a), abs(bounded.b)), 10)

    def test_intersection_is_computed_from_rays(self):
        anchor = AlgebraicValue(-1, 1, math.sqrt(2) - 1, 0.0)
        horizontal = CandidateLine(0, 120.0, 10.0, 0.0, "左", anchor, np.array([0.0, 120.0]))
        diagonal = CandidateLine(2, 0.0, 10.0, 0.0, "上", anchor, np.array([0.0, 0.0]))
        point = _line_intersection(horizontal, diagonal)
        self.assertIsNotNone(point)
        self.assertTrue(np.allclose(point, [120.0, 120.0]))

    def test_allowed_angles_are_22_5_degree_family(self):
        self.assertEqual([round(math.degrees(value), 1) for value in ALLOWED_ANGLES], [0, 22.5, 45, 67.5, 90, 112.5, 135, 157.5])

    def test_only_seed_rays_use_boundary_references(self):
        size = 401
        center = np.array([200.0, 200.0])
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def ray(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            return CandidateLine(
                orientation,
                float(normal @ point),
                10.0,
                0.0,
                "",
                reference,
                point.copy(),
            )

        horizontal = ray(0, center)
        vertical = ray(4, center)
        derived = ray(1, center)
        derived_t = float(derived.u @ center)
        lines, _, stats = _propagate_constructible_rays(
            [horizontal, vertical, derived],
            [[[0.0, 400.0]], [[0.0, 400.0]], [[derived_t - 70.0, derived_t + 70.0]]],
            size,
            Settings(),
        )

        self.assertEqual(len(lines), 3)
        self.assertEqual([line.generation for line in lines], [0, 0, 1])
        self.assertEqual(lines[2].origin_kind, "intersection")
        self.assertEqual(stats["midpoint_seed_rays"], 2)
        self.assertEqual(stats["derived_rays"], 1)

    def test_existing_intersection_is_preferred_over_fallback_seed(self):
        size = 401
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def ray(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            return CandidateLine(
                orientation,
                float(normal @ point),
                10.0,
                0.0,
                "",
                reference,
                point.copy(),
            )

        left_corner_ray = ray(1, np.array([0.0, 0.0]))
        right_corner_ray = ray(7, np.array([400.0, 0.0]))
        focus = _line_intersection(left_corner_ray, right_corner_ray)
        self.assertIsNotNone(focus)
        child = ray(4, focus)
        focus_t = float(child.u @ focus)
        lines, _, stats = _propagate_constructible_rays(
            [left_corner_ray, right_corner_ray, child],
            [[[0.0, 300.0]], [[-400.0, 0.0]], [[focus_t - 50.0, focus_t + 50.0]]],
            size,
            Settings(),
        )

        self.assertEqual(len(lines), 3)
        self.assertEqual(stats["corner_seed_rays"], 2)
        self.assertEqual(stats["fallback_seed_points"], 0)
        self.assertEqual(stats["derived_rays"], 1)
        self.assertEqual(lines[2].origin_kind, "intersection")

    def test_active_ray_boundary_contact_is_a_derived_construction_point(self):
        size = 401
        maximum = float(size - 1)
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def ray(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            return CandidateLine(
                orientation,
                float(normal @ point),
                10.0,
                0.0,
                "",
                reference,
                point.copy(),
            )

        parent = ray(1, np.array([0.0, 0.0]))
        boundary_point = np.array(
            [maximum, maximum * math.tan(ALLOWED_ANGLES[1])]
        )
        child = ray(3, boundary_point)
        parent_end_t = float(parent.u @ boundary_point)
        child_t = float(child.u @ boundary_point)

        lines, _, stats = _propagate_constructible_rays(
            [parent, child],
            [
                [[0.0, parent_end_t]],
                [[child_t - 80.0, child_t + 5.0]],
            ],
            size,
            Settings(),
        )

        self.assertEqual(len(lines), 2)
        self.assertEqual(stats["internal_algebraic_seed_points"], 0)
        self.assertEqual(stats["boundary_algebraic_seed_points"], 0)
        self.assertEqual(stats["boundary_contact_derived_rays"], 1)
        self.assertEqual(lines[1].origin_kind, "boundary_contact")
        self.assertTrue(np.allclose(lines[1].anchor_point, boundary_point))

    def test_one_internal_algebraic_point_replaces_boundary_fallback(self):
        size = 401
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def ray(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            return CandidateLine(
                orientation,
                float(normal @ point),
                10.0,
                0.0,
                "",
                reference,
                point.copy(),
            )

        # The lower focus is an exact interior point (0, sqrt(2)-1) in
        # normalized coordinates. It may start the two incident inactive rays
        # once; no boundary a+b√2 seed is involved.
        upper_y = 200.0
        lower_y = 200.0 + 200.0 * (math.sqrt(2.0) - 1.0)
        upper = ray(0, np.array([0.0, upper_y]))
        lower = ray(0, np.array([0.0, lower_y]))
        child = ray(4, np.array([200.0, 0.0]))
        observed = np.array([[200.0, upper_y], [200.0, lower_y]])
        lines, _, stats = _propagate_constructible_rays(
            [upper, lower, child],
            [
                [[0.0, 400.0]],
                [[0.0, 400.0]],
                [[upper_y, lower_y]],
            ],
            size,
            Settings(),
            observed,
        )

        self.assertEqual(len(lines), 3)
        self.assertEqual(stats["internal_algebraic_seed_points"], 1)
        self.assertEqual(stats["boundary_algebraic_seed_points"], 0)
        self.assertEqual(stats["algebraic_seed_rays"], 2)
        self.assertEqual(stats["unresolved_rays"], 0)
        self.assertEqual(
            sum(line.origin_kind == "algebraic_internal" for line in lines),
            2,
        )
        references = {
            tuple(np.round(line.anchor_point, 7))
            for line in lines
            if line.origin_kind == "algebraic_internal"
        }
        self.assertEqual(len(references), 1)

    def test_second_internal_algebraic_reference_is_never_added(self):
        size = 401
        reference = AlgebraicValue(0, 0, 0.0, 0.0)

        def ray(orientation, point):
            theta = ALLOWED_ANGLES[orientation]
            normal = np.array([-math.sin(theta), math.cos(theta)])
            return CandidateLine(
                orientation,
                float(normal @ point),
                10.0,
                0.0,
                "",
                reference,
                point.copy(),
            )

        root_two_position = 200.0 + 200.0 * (math.sqrt(2.0) - 1.0)
        first_focus = np.array([200.0, root_two_position])
        second_focus = np.array([root_two_position, 200.0])
        free_horizontal = ray(0, np.array([0.0, 200.0]))
        first_horizontal = ray(0, first_focus)
        first_vertical = ray(4, first_focus)
        second_vertical = ray(4, second_focus)

        lines, _, stats = _propagate_constructible_rays(
            [
                free_horizontal,
                first_horizontal,
                first_vertical,
                second_vertical,
            ],
            [
                [[0.0, 400.0]],
                [[150.0, 250.0]],
                [[root_two_position - 45.0, root_two_position + 45.0]],
                [[155.0, 245.0]],
            ],
            size,
            Settings(),
            np.array([first_focus, second_focus]),
        )

        self.assertEqual(stats["internal_algebraic_seed_points"], 1)
        self.assertEqual(stats["boundary_algebraic_seed_points"], 0)
        self.assertEqual(stats["unresolved_rays"], 1)
        references = {
            tuple(np.round(line.anchor_point, 7))
            for line in lines
            if line.origin_kind == "algebraic_internal"
        }
        self.assertEqual(references, {tuple(np.round(first_focus, 7))})

    def test_t_junction_splits_parent_segment(self):
        horizontal = Edge(
            np.array([0.0, 100.0]), np.array([200.0, 100.0]), 4
        )
        vertical = Edge(
            np.array([100.0, 0.0]), np.array([100.0, 100.0]), 4
        )
        pieces = _planarize_edges([horizontal, vertical])
        self.assertEqual(len(pieces), 3)
        degree = {}
        for edge in pieces:
            for point in (edge.start, edge.end):
                key = tuple(np.round(point, 7))
                degree[key] = degree.get(key, 0) + 1
        self.assertEqual(degree[(100.0, 100.0)], 3)

    def test_touching_collinear_fragments_are_merged_before_endpoint_validation(self):
        reference = AlgebraicValue(0, 0, 0.0, 0.0)
        horizontal = CandidateLine(
            0,
            100.0,
            20.0,
            0.0,
            "",
            reference,
            np.array([0.0, 100.0]),
        )
        vertical_left = CandidateLine(
            4,
            -20.0,
            20.0,
            0.0,
            "",
            reference,
            np.array([20.0, 0.0]),
        )
        vertical_right = CandidateLine(
            4,
            -180.0,
            20.0,
            0.0,
            "",
            reference,
            np.array([180.0, 0.0]),
        )
        edges, stats = _snap_and_prune_dangling_edges(
            [
                Edge(np.array([20.0, 100.0]), np.array([100.0, 100.0]), 4),
                Edge(np.array([100.0, 100.0]), np.array([180.0, 100.0]), 4),
            ],
            201,
            construction_lines=[horizontal, vertical_left, vertical_right],
        )
        self.assertEqual(len(edges), 1)
        self.assertTrue(np.allclose(edges[0].start, [20.0, 100.0]))
        self.assertTrue(np.allclose(edges[0].end, [180.0, 100.0]))
        self.assertEqual(stats["collinear_fragments_merged"], 1)
        self.assertEqual(stats["dangling_edges_rejected"], 0)

    def test_planarization_canonicalizes_numerically_equal_nodes(self):
        first = Edge(
            np.array([0.0, 100.0]),
            np.array([200.0, 100.0]),
            4,
        )
        second = Edge(
            np.array([100.0 + 5e-8, 0.0]),
            np.array([100.0 + 5e-8, 100.0 + 5e-8]),
            4,
        )
        pieces = _planarize_edges([first, second])
        near_center = []
        for edge in pieces:
            for point in (edge.start, edge.end):
                if np.linalg.norm(point - np.array([100.0, 100.0])) < 1e-4:
                    near_center.append(tuple(point))
        self.assertGreaterEqual(len(near_center), 3)
        self.assertEqual(len(set(near_center)), 1)

    def test_hough_recovery_only_links_existing_exact_nodes(self):
        ink = np.zeros((201, 201), np.uint8)
        left = Edge(np.array([50.0, 0.0]), np.array([50.0, 100.0]), 4)
        right = Edge(np.array([150.0, 0.0]), np.array([150.0, 100.0]), 4)
        cv2.line(ink, (50, 0), (50, 100), 255, 1)
        cv2.line(ink, (150, 0), (150, 100), 255, 1)
        cv2.line(ink, (50, 100), (150, 100), 255, 1)

        edges, stats = _close_internal_lineheads(
            [left, right],
            [],
            ink,
            Settings(),
        )
        recovered = [
            edge
            for edge in edges
            if {
                tuple(np.round(edge.start, 7)),
                tuple(np.round(edge.end, 7)),
            }
            == {(50.0, 100.0), (150.0, 100.0)}
        ]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(stats["exact_node_links_recovered"], 1)

    def test_redundant_short_chord_is_removed_without_creating_lineheads(self):
        ink = np.zeros((201, 201), np.uint8)
        left = np.array([92.0, 100.0])
        right = np.array([108.0, 100.0])
        top = np.array([100.0, 80.0])
        bottom = np.array([100.0, 120.0])
        edges = [
            Edge(left, top, 4),
            Edge(top, right, 4),
            Edge(right, bottom, 4),
            Edge(bottom, left, 4),
            # This short chord has no source stroke and both endpoints remain
            # connected after it is removed.
            Edge(left, right, 4),
        ]
        for edge in edges[:-1]:
            cv2.line(
                ink,
                tuple(np.rint(edge.start).astype(int)),
                tuple(np.rint(edge.end).astype(int)),
                255,
                1,
            )
        filtered, stats = _close_internal_lineheads(edges, [], ink, Settings())
        links = {
            tuple(
                sorted(
                    (
                        tuple(np.round(edge.start, 7)),
                        tuple(np.round(edge.end, 7)),
                    )
                )
            )
            for edge in filtered
        }
        self.assertNotIn(tuple(sorted((tuple(left), tuple(right)))), links)
        self.assertEqual(stats["redundant_short_edges_rejected"], 1)


if __name__ == "__main__":
    unittest.main()
