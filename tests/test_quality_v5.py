from quality_v5 import build_quality_report_v5


def _cp_point(value):
    return value / 100.0 * 400.0 - 200.0


def _row(line_type, start, end):
    return (
        f"{line_type} {_cp_point(start[0]):.6f} {_cp_point(start[1]):.6f} "
        f"{_cp_point(end[0]):.6f} {_cp_point(end[1]):.6f}"
    )


def test_duplicate_parallel_geometry_is_not_treated_as_unresolved_evidence():
    cp = "\n".join(
        [
            _row(2, (10, 10), (70, 70)),
            _row(2, (10, 11), (70, 71)),
            _row(3, (70, 10), (70, 69)),  # near-miss at the first diagonal endpoint region
        ]
    )
    result = {
        "cp": cp,
        "stats": {
            "analysis_size_used": 101,
            "unresolved_rays": 5,
            "mv_input_mode": "color",
            "camv_structure": {"violations": []},
        },
        "playback_trace": [],
    }
    report = build_quality_report_v5(result)
    assert report["duplicate_parallel_count"] >= 1
    assert report["geometry_error_score"] > 0
    assert report["unresolved_observation_count"] == 5


def test_clean_camv_violation_remains_structural_prior_not_geometry_blame():
    cp = "\n".join(
        [
            _row(2, (10, 50), (90, 50)),
            _row(3, (50, 10), (50, 90)),
        ]
    )
    result = {
        "cp": cp,
        "stats": {
            "analysis_size_used": 101,
            "unresolved_rays": 0,
            "mv_input_mode": "color",
            "camv_structure": {
                "violations": [
                    {"point": [50.0, 50.0], "rule": "kawasaki_angles"},
                ]
            },
        },
        "playback_trace": [],
    }
    report = build_quality_report_v5(result)
    assert report["camv_reconstruction_geometry_count"] == 0
    assert report["camv_structural_unresolved_count"] == 1
    assert report["camv_diagnosis"][0]["cause"] == "structural_unresolved"
