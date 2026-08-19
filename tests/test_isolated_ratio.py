import numpy as np

from isolated_ratio import _square_candidates


def _row(start, end, line_type=2):
    return {
        "line_type": line_type,
        "start": np.asarray(start, dtype=float),
        "end": np.asarray(end, dtype=float),
    }


def test_rotated_square_is_detected_from_internal_segments_not_paper_edges():
    # A=(50,0) and B=(50,100) are opposite corners (a vertical diagonal).
    # The four actual square edges run at +/-45 degrees.
    a = (50.0, 0.0)
    b = (50.0, 100.0)
    c = (100.0, 50.0)
    d = (0.0, 50.0)
    rows = [
        _row(a, c),
        _row(c, b),
        _row(b, d),
        _row(d, a),
    ]
    squares = _square_candidates(rows)
    assert squares
    square = squares[0]
    assert {square["u_orientation"], square["v_orientation"]} == {2, 6}
    np.testing.assert_allclose(square["a"], a, atol=1e-6)
    np.testing.assert_allclose(square["b"], b, atol=1e-6)


def test_paper_boundary_rows_are_not_used_as_square_construction_edges():
    rows = [
        _row((0, 0), (100, 0), line_type=1),
        _row((100, 0), (100, 100), line_type=1),
        _row((100, 100), (0, 100), line_type=1),
        _row((0, 100), (0, 0), line_type=1),
    ]
    assert _square_candidates(rows) == []
