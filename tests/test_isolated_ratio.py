import numpy as np

from isolated_ratio import (
    _candidate_span,
    _match_ratio_rays,
    _ratio_points,
    _ratio_source_segments,
)


def _row(start, end, line_type=2):
    return {
        "line_type": line_type,
        "start": np.asarray(start, dtype=float),
        "end": np.asarray(end, dtype=float),
    }


def test_candidate_span_uses_nearest_constructed_intersections_not_square_shape():
    rows = [
        _row((20, 0), (20, 100)),
        _row((80, 0), (80, 100)),
        _row((5, 15), (30, 40)),
    ]
    point = np.asarray((50.0, 50.0), dtype=float)
    span = _candidate_span(point, 0, rows, 100.0)
    assert span is not None
    start, end = span
    np.testing.assert_allclose(start, (20.0, 50.0), atol=1e-6)
    np.testing.assert_allclose(end, (80.0, 50.0), atol=1e-6)


def test_constructed_point_segment_can_be_trisected_without_being_a_crease():
    # The vertical ruler segment (50,20)->(50,80) is absent from the CP. Its
    # endpoints are nevertheless exact constructed vertices, so it may be used
    # as a finite auxiliary segment for taking thirds.
    rows = [
        _row((20, 20), (50, 20)),
        _row((50, 20), (80, 20)),
        _row((20, 80), (50, 80)),
        _row((50, 80), (80, 80)),
        _row((50, 80), (80, 50)),
    ]
    sources = _ratio_source_segments(rows, 200.0)
    source = next(
        item for item in sources
        if item["kind"] == "constructed_point_segment"
        and {
            tuple(np.asarray(item["start"]).tolist()),
            tuple(np.asarray(item["end"]).tolist()),
        } == {(50.0, 20.0), (50.0, 80.0)}
    )
    points = _ratio_points([source], rows)
    one_third = next(item for item in points if item["ratio"] == "1/3")
    np.testing.assert_allclose(one_third["point"], (50.0, 40.0), atol=1e-6)


def test_parallel_shifted_raster_does_not_move_exact_ratio_geometry():
    observations = [
        {
            "orientation": 0,
            "observed_offset_px": 36.5,
            "t0": 20.0,
            "t1": 80.0,
            "evidence_score": 1.0,
        }
    ]
    ratio_points = [
        {
            "point": np.asarray((50.0, 40.0), dtype=float),
            "ratio": "1/3",
            "derivation": "segment_trisection",
            "source": {
                "kind": "constructed_point_segment",
                "cost": 2,
                "orientation": 4,
            },
        }
    ]
    rays = _match_ratio_rays(observations, ratio_points, [], 4.8)
    assert rays
    assert abs(float(rays[0]["offset_px"]) - 40.0) < 1e-9
    assert abs(float(rays[0]["best_support"]["normal_error_px"]) - 3.5) < 1e-9


def test_ratio_recovery_source_contains_no_square_detector():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "isolated_ratio.py").read_text(encoding="utf-8")
    assert "_square_candidates" not in source
    assert "infer_isolated_segment_ratio_segments" in source
    assert "no enclosing square or named region is assumed" in source
