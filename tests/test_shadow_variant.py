import math

from shadow_variant import build_candidate_cp, refine_trace_offsets_from_cp


def _px_to_cp(value: float, maximum: float = 100.0) -> float:
    return -200.0 + 400.0 * value / maximum


def _row(line_type: int, start: tuple[float, float], end: tuple[float, float]) -> str:
    return (
        f"{line_type} {_px_to_cp(start[0])} {_px_to_cp(start[1])} "
        f"{_px_to_cp(end[0])} {_px_to_cp(end[1])}\n"
    )


def _synthetic_result() -> dict:
    # Root: x=30. Parent 1: y=50. Child 2: 45-degree ray from their
    # intersection to the bottom edge. Moving the root to x=40 must move the
    # shared node and regenerate the child's exact boundary contact.
    child_offset = (-30.0 + 50.0) / math.sqrt(2.0)
    return {
        "cp": (
            _row(2, (30.0, 0.0), (30.0, 50.0))
            + _row(2, (0.0, 50.0), (30.0, 50.0))
            + _row(2, (30.0, 50.0), (80.0, 100.0))
        ),
        "stats": {"analysis_size_used": 101},
        "playback_trace": [
            {
                "trace_id": 0,
                "trace_parent_ids": [],
                "generation": 0,
                "angle": 90.0,
                "line_offset_px": -30.000001,
                "anchor_point_px": [30.0, 0.0],
                "forms_output": True,
                "source": "唯一内部 a+b√2 种子",
            },
            {
                "trace_id": 1,
                "trace_parent_ids": [],
                "generation": 0,
                "angle": 0.0,
                "line_offset_px": 50.000001,
                "anchor_point_px": [0.0, 50.0],
                "forms_output": True,
                "source": "角点种子",
            },
            {
                "trace_id": 2,
                "trace_parent_ids": [0, 1],
                "generation": 1,
                "angle": 45.0,
                "line_offset_px": child_offset + 0.000001,
                "anchor_point_px": [30.0, 50.0],
                "forms_output": True,
                "source": "第 1 代交点",
            },
        ],
    }


def test_trace_offsets_are_rebound_from_precise_cp_geometry():
    result = _synthetic_result()
    count = refine_trace_offsets_from_cp(result)

    assert count == 3
    by_id = {item["trace_id"]: item for item in result["playback_trace"]}
    assert by_id[0]["line_offset_px"] == pytest.approx(-30.0, abs=1e-10)
    assert by_id[1]["line_offset_px"] == pytest.approx(50.0, abs=1e-10)


def test_candidate_cp_recomputes_shared_nodes_and_boundary_contacts():
    result = _synthetic_result()
    refine_trace_offsets_from_cp(result)
    report = {
        "suspicious_seed_routes": [
            {
                "trace_id": 0,
                "route_improved": True,
                "score_improvement": 3.0,
                "residual_improvement_px": 0.8,
                "selected_offset_px": -40.0,
                "proof_operations": [
                    {"kind": "direct_point", "target_trace_id": 0}
                ],
            }
        ]
    }

    built = build_candidate_cp(result, report)

    assert built is not None
    cp_text, info, _ = built
    # x=40 -> CP x=-40; the diagonal then reaches bottom at x=90 -> CP x=160.
    assert "-40" in cp_text
    assert "160" in cp_text
    assert info["changed_rays"] >= 2
    assert info["topology_max_residual_px"] < 1e-6


def test_candidate_is_not_emitted_without_a_winning_shadow_route():
    result = _synthetic_result()
    refine_trace_offsets_from_cp(result)
    assert build_candidate_cp(result, {"suspicious_seed_routes": []}) is None
