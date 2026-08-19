from construction_search import ConstructionGraph, ConstructionOperation
from provenance_v6 import _coreless_graph, _stable_reference_points


def _anchor(trace_id, angle, offset, point, generation=0):
    return {
        "trace_id": trace_id,
        "angle": angle,
        "line_offset_px": offset,
        "observed_offset_px": offset,
        "anchor_point_px": list(point),
        "generation": generation,
        "trace_parent_ids": [],
        "snap_error_px": 0.0,
    }


def test_coreless_graph_rejects_legacy_core_seed_and_geometry_reroot():
    graph = ConstructionGraph()
    legacy = ConstructionOperation(
        id=("legacy", 9),
        kind="algebraic_seed",
        parents=(),
        outputs=(("ray", 9),),
        explains=frozenset({("required_ray", 9)}),
    )
    reroot = ConstructionOperation(
        id=("geometry_reroot_v5", 9, 12.3),
        kind="geometry_reroot",
        parents=(),
        outputs=(("ray", 9),),
        explains=frozenset({("required_ray", 9)}),
    )
    coreless = ConstructionOperation(
        id=("coreless_reference_ray", 9, 9, "stable_intersection", 1, 2),
        kind="coreless_reference_ray",
        parents=(("ray", 1), ("ray", 2)),
        outputs=(("ray", 9),),
        explains=frozenset({("required_ray", 9)}),
    )
    child = ConstructionOperation(
        id=("legacy", 10),
        kind="intersection",
        parents=(("ray", 9),),
        outputs=(("ray", 10),),
        explains=frozenset({("required_ray", 10)}),
    )
    for operation in (legacy, reroot, coreless, child):
        graph.add_operation(operation)

    details = {
        legacy.id: {"target_trace_id": 9, "provenance": "legacy"},
        reroot.id: {"target_trace_id": 9, "provenance": "geometry_reroot"},
        coreless.id: {
            "target_trace_id": 9,
            "provenance": "coreless_reference_ray",
            "source_trace_ids": [1, 2],
        },
        child.id: {"target_trace_id": 10, "provenance": "legacy"},
    }

    filtered, removed = _coreless_graph(graph, details, {9, 10}, 9)
    assert removed == 2
    assert legacy.id not in filtered.operations
    assert reroot.id not in filtered.operations
    assert coreless.id in filtered.operations
    assert child.id in filtered.operations


def test_reference_pool_never_uses_affected_core_descendants():
    anchors = {
        1: _anchor(1, 0.0, 0.0, (10.0, 10.0)),
        2: _anchor(2, 90.0, -20.0, (20.0, 10.0)),
        9: _anchor(9, 45.0, 0.0, (50.0, 50.0)),
        10: _anchor(10, 135.0, -10.0, (55.0, 55.0)),
    }
    points, stable_ids = _stable_reference_points(
        anchors,
        {9, 10},
        100.0,
        {"suspect_trace_penalties": {}},
    )
    assert set(stable_ids) == {1, 2}
    assert points
    for item in points:
        assert not (set(item.get("source_trace_ids", ())) & {9, 10})
