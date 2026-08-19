import numpy as np

from isolated_ratio import _candidate_span


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


def test_ratio_recovery_source_contains_no_square_detector():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "isolated_ratio.py").read_text(encoding="utf-8")
    assert "_square_candidates" not in source
    assert "infer_isolated_segment_ratio_segments" in source
    assert "No square, paper-edge division, or named region is assumed" in source
