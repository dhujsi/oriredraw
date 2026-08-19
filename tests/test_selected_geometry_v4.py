import math

import pytest

from selected_geometry_v4 import resolve_selected_geometry_v4


def test_changed_corner_route_recomputes_legacy_descendant_intersection():
    result = {
        "stats": {"analysis_size_used": 101},
        "playback_trace": [
            {
                "trace_id": 0,
                "angle": 0.0,
                "line_offset_px": 5.0,
                "anchor_point_px": [0.0, 5.0],
                "trace_parent_ids": [],
                "source": "a+b√2 独立取线",
            },
            {
                "trace_id": 1,
                "angle": 90.0,
                "line_offset_px": -50.0,
                "anchor_point_px": [50.0, 30.0],
                "trace_parent_ids": [],
                "source": "已有取线",
            },
            {
                "trace_id": 2,
                "angle": 45.0,
                "line_offset_px": -30.0,
                "anchor_point_px": [50.0, 5.0],
                "trace_parent_ids": [0, 1],
                "source": "交点",
            },
        ],
    }
    report = {
        "selected_operations": [
            {
                "provenance": "paper_corner_ray",
                "target_trace_id": 0,
                "anchor_point_px": [0.0, 0.0],
                "candidate_offset_px": 0.0,
            },
            {
                "provenance": "legacy",
                "target_trace_id": 1,
                "candidate_offset_px": -50.0,
            },
            {
                "provenance": "legacy",
                "target_trace_id": 2,
                "candidate_offset_px": -30.0,
            },
        ]
    }

    offsets, points, unresolved = resolve_selected_geometry_v4(result, report)
    assert unresolved == []
    assert offsets[0] == pytest.approx(0.0)
    assert points[2][0] == pytest.approx(50.0)
    assert points[2][1] == pytest.approx(0.0)
    expected = -50.0 / math.sqrt(2.0)
    assert offsets[2] == pytest.approx(expected)
    assert offsets[2] != pytest.approx(-30.0)
