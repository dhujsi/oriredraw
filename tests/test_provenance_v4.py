import math

from provenance_v4 import _large_coefficient_extra, build_provenance_report_v4


def _anchor(
    trace_id,
    *,
    angle,
    offset,
    point,
    source,
    expression="",
    generation=0,
    parents=(),
):
    return {
        "trace_id": trace_id,
        "angle": angle,
        "line_offset_px": offset,
        "observed_offset_px": offset,
        "anchor_point_px": list(point),
        "source": source,
        "expression": expression,
        "coordinate_expression": [expression, expression] if expression else None,
        "generation": generation,
        "trace_parent_ids": list(parents),
        "snap_error_px": 0.0,
        "forms_output": True,
        "formed_segments_px": [],
    }


def test_large_radical_coefficients_trigger_explicit_guard_cost():
    assert _large_coefficient_extra((10, 3)) == 0
    assert _large_coefficient_extra((34, 24)) >= 5


def test_corner_route_beats_magic_large_coefficient_seed():
    result = {
        "stats": {"analysis_size_used": 101, "camv_structure_violation_count": 0},
        "playback_trace": [
            _anchor(
                0,
                angle=0.0,
                offset=0.0,
                point=(0.0, 0.0),
                source="a+b√2 独立取线",
                expression="-34+24√2",
            )
        ],
    }
    report = build_provenance_report_v4(result)
    assert report["unexplained_observations"] == 0
    assert report["large_coefficient_guarded_candidates"]
    assert any(
        item.get("provenance") == "paper_corner_ray"
        for item in report["selected_alternatives"]
    )
    assert not report["selected_large_coefficient_guarded_candidates"]


def test_segment_trisection_can_replace_large_coefficient_target():
    # ray 0 contains two already constructed points at x=0 and x=90.  The
    # target vertical ray at x=30 is therefore a genuine 1/3 segment take-line.
    result = {
        "stats": {"analysis_size_used": 121, "camv_structure_violation_count": 0},
        "playback_trace": [
            _anchor(0, angle=0.0, offset=50.0, point=(0.0, 50.0), source="角点"),
            _anchor(1, angle=90.0, offset=-90.0, point=(90.0, 50.0), source="已有交点"),
            _anchor(
                2,
                angle=90.0,
                offset=-30.0,
                point=(30.0, 50.0),
                source="a+b√2 独立取线",
                expression="-34+24√2",
            ),
        ],
    }
    report = build_provenance_report_v4(result)
    assert report["unexplained_observations"] == 0
    ratios = [
        item
        for item in report["selected_alternatives"]
        if item.get("provenance") == "segment_ratio_ray"
    ]
    assert any(item.get("ratio") == "1/3" and item.get("target_trace_id") == 2 for item in ratios)


def test_one_sixth_is_not_a_primitive_division_rule():
    result = {
        "stats": {"analysis_size_used": 121, "camv_structure_violation_count": 0},
        "playback_trace": [
            _anchor(0, angle=0.0, offset=50.0, point=(0.0, 50.0), source="角点"),
            _anchor(1, angle=90.0, offset=-90.0, point=(90.0, 50.0), source="已有交点"),
            _anchor(
                2,
                angle=90.0,
                offset=-15.0,
                point=(15.0, 50.0),
                source="a+b√2 独立取线",
                expression="-34+24√2",
            ),
        ],
    }
    report = build_provenance_report_v4(result)
    selected = report["selected_operations"]
    sixth = [item for item in selected if item.get("ratio") == "1/6"]
    assert sixth
    assert any(item.get("derived_as") == "midpoint_then_trisection" for item in sixth)
    assert any(item.get("kind") == "half_segment_trisection_point" for item in selected)
