import numpy as np

from shadow_variant import _line_geometry
from shadow_variant_v6 import _free_old_nodes


def _segment(line_type, trace_id, orientation, offset, old_start, old_end):
    line = _line_geometry(orientation, offset)
    start = np.asarray(old_start, dtype=float)
    end = np.asarray(old_end, dtype=float)
    _, normal, value = line

    def project(point):
        return point + normal * (value - float(normal @ point))

    return {
        "line_type": line_type,
        "trace_id": trace_id,
        "orientation": orientation,
        "line": line,
        "old_start": start.copy(),
        "old_end": end.copy(),
        "start": project(start.copy()),
        "end": project(end.copy()),
    }


def test_old_common_node_is_not_forced_back_to_one_least_squares_point():
    # Three old segments shared (50,50). After re-anchoring, their exact pairwise
    # intersections sit at distinct nearby points. v6 should allow the old node
    # to split rather than averaging all three back into a fake common point.
    segments = [
        _segment(2, 1, 0, 50.0, (20.0, 50.0), (50.0, 50.0)),
        _segment(2, 2, 4, -51.0, (50.0, 50.0), (50.0, 80.0)),
        _segment(3, 3, 2, -1.0, (30.0, 30.0), (50.0, 50.0)),
    ]

    split = _free_old_nodes(segments, 100.0)
    endpoints = [segments[0]["end"], segments[1]["start"], segments[2]["end"]]

    assert split >= 1
    rounded = {(round(float(point[0]), 3), round(float(point[1]), 3)) for point in endpoints}
    assert len(rounded) >= 2
