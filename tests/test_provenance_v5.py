from construction_search import ConstructionGraph, ConstructionOperation
from provenance_v5 import _apply_quality_quarantine


def test_suspect_geometry_penalizes_legacy_and_same_coordinate_rewrites():
    graph = ConstructionGraph()
    legacy = ConstructionOperation(
        id="legacy",
        kind="legacy",
        parents=(),
        outputs=(("ray", 7),),
        explains=frozenset({("required_ray", 7)}),
    )
    same = ConstructionOperation(
        id="same",
        kind="symmetry_point",
        parents=(),
        outputs=(("ray", 7),),
        explains=frozenset({("required_ray", 7)}),
    )
    moved = ConstructionOperation(
        id="moved",
        kind="geometry_reroot",
        parents=(),
        outputs=(("ray", 7),),
        explains=frozenset({("required_ray", 7)}),
    )
    for operation in (legacy, same, moved):
        graph.add_operation(operation)

    anchors = {7: {"line_offset_px": 10.0}}
    details = {
        "legacy": {"provenance": "legacy", "target_trace_id": 7, "candidate_offset_px": 10.0},
        "same": {"provenance": "symmetry_point", "target_trace_id": 7, "candidate_offset_px": 10.05},
        "moved": {"provenance": "geometry_reroot", "target_trace_id": 7, "candidate_offset_px": 10.8},
    }
    quality = {"suspect_trace_penalties": {"7": 4.5}}
    guarded = _apply_quality_quarantine(graph, anchors, details, quality)

    assert guarded.operations["legacy"].independent_parameters > 0
    assert guarded.operations["same"].independent_parameters > 0
    assert guarded.operations["moved"].independent_parameters == 0
    assert details["legacy"]["quality_quarantined_legacy"] is True
    assert details["same"]["quality_stagnation_penalty"] is True
