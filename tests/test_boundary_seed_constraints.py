import copy
import math
from unittest.mock import patch

import pytest

from boundary_seed_constraints import _ExactSystem, resolve_boundary_seed_constraints
from construction_proof_topology import build_construction_proof_topology
from construction_search import ConstructionGraph, GeometryEntity
from exact_graph_propagation import _direction_vector, propagate_exact_geometry
from exact_qsqrt2 import Qsqrt2 as Q
from guided_construction import _apply_single_core_reference, _relation_with_proved_seed_coordinates
from qsqrt2_coordinates import qsqrt2_from_mapping, qsqrt2_to_mapping


MAXIMUM = 511.0
HEIGHT = Q(-1, 1) / Q(2)


def _fixture(jitter=0.3):
    """Two corner rays fix a height; two horizontal offsets remain free.

    The selected division can fix those offsets, but their measured coordinates
    cannot. This is geometry, not an image-specific expected-output fixture.
    """
    graph = ConstructionGraph()
    coordinates = {
        "corner:top_left": (Q(), Q()), "corner:top_right": (Q(1), Q()),
        "mid": (Q(1) / Q(2), Q()), "junction": (Q(1) / Q(2), HEIGHT),
    }
    for edge, x in (("left", Q()), ("right", Q(1))):
        for i in range(1, 4):
            coordinates[f"{edge}-{i}"] = (x, HEIGHT * Q(i) / Q(3))
    for name, (x, y) in coordinates.items():
        sides = [s for s, v, bound in (("left", x, Q()), ("right", x, Q(1)),
                                     ("top", y, Q()), ("bottom", y, Q(1))) if v == bound]
        observed = [float(x) * MAXIMUM, float(y) * MAXIMUM]
        if not sides:
            observed[0] += jitter
        if "top" not in sides:
            observed[1] += jitter
        graph.add_geometry_entity(GeometryEntity(name, "point", observed_geometry={
            "point_px": observed, "boundary_sides": sides,
            "point_kind": "line_intersection", "match_tolerance_px": 1.5,
        }))
    lines = [
        ("slope-left", 1, ["corner:top_left", "junction"]),
        ("slope-right", 7, ["corner:top_right", "junction"]),
        ("vertical", 4, ["mid", "junction"]),
        ("height", 0, ["left-3", "junction", "right-3"]),
        ("stripe-1", 0, ["left-1", "right-1"]),
        ("stripe-2", 0, ["left-2", "right-2"]),
    ]
    for name, direction, points in lines:
        dx, dy = _direction_vector(direction)
        x, y = coordinates[points[0]]
        offset = float(-dy * x + dx * y) * MAXIMUM / math.hypot(float(dx), float(dy))
        graph.add_geometry_entity(GeometryEntity(name, "crease", observed_geometry={
            "direction_index": direction, "angle_deg": direction * 22.5,
            "line_offset_px": offset, "match_tolerance_px": 1.5,
            "evidence_intervals_px": [[0.0, float(HEIGHT) * MAXIMUM if name == "vertical" else MAXIMUM]],
        }))
        for point in points:
            graph.connect_incidence(point, name)
    return graph, coordinates


def _relation(graph, side):
    ids = {
        "top": ["corner:top_left", "mid", "corner:top_right"],
        "right": ["corner:top_right", "right-1", "right-2", "right-3"],
        # Boundary parameters run clockwise, including up the left edge.
        "left": ["left-3", "left-2", "left-1", "corner:top_left"],
    }[side]
    return {
        "id": side, "side": side,
        "points": [{
            "id": name, "point_px": graph.geometry_entity(name).observed_geometry["point_px"],
            "relation_role": "start" if i == 0 else "end" if i == len(ids) - 1 else f"{i}/{len(ids)-1}",
        } for i, name in enumerate(ids)],
        "recommended_coordinate_gauge": {"side_length": qsqrt2_to_mapping(Q(2, 1))},
    }


@pytest.mark.parametrize("side", ["top", "left", "right"])
@pytest.mark.parametrize("jitter", [-0.4, 0.3])
def test_selected_seed_uses_unique_incidence_equations_not_pixel_coordinates(side, jitter):
    graph, coordinates = _fixture(jitter)
    relation = _relation(graph, side)
    before_graph, before_relation = graph.geometry_snapshot(), copy.deepcopy(relation)
    report = resolve_boundary_seed_constraints(graph, [relation], maximum=MAXIMUM)
    assert report["status"] == "resolved"
    assert not report["pixel_coordinates_used_as_equations"]
    assert not report["free_variables_fitted"]
    for point in relation["points"]:
        assert tuple(map(qsqrt2_from_mapping, report["resolved_points"][point["id"]])) == coordinates[point["id"]]
    assert graph.geometry_snapshot() == before_graph
    assert relation == before_relation

    updated = _relation_with_proved_seed_coordinates(relation, report["resolved_points"], MAXIMUM)
    for point in updated["points"]:
        x, y = coordinates[point["id"]]
        assert point["observed_point_px"] == graph.geometry_entity(point["id"]).observed_geometry["point_px"]
        assert tuple(map(qsqrt2_from_mapping, point["project_coordinate"])) == (x * Q(2, 1), y * Q(2, 1))
        parameter = {"top": x, "right": y, "left": Q(1) - y}[side]
        assert qsqrt2_from_mapping(point["edge_parameter"]) == parameter
        lengths = point["cross_segment_lengths"]["distances"]
        pair = ("left", "right") if side == "top" else ("top", "bottom")
        assert set(lengths) == set(pair)
        assert sum((qsqrt2_from_mapping(lengths[k]) for k in pair), Q()) == Q(2, 1)


def test_underconstrained_height_is_not_fitted_even_when_pixels_are_exact():
    graph, _ = _fixture(0.0)
    graph.incidence["junction"].remove("height")
    graph.incidence["height"].remove("junction")
    report = resolve_boundary_seed_constraints(graph, [_relation(graph, "right")], maximum=MAXIMUM)
    assert report["status"] == "underconstrained"
    assert not report["resolved_points"]


def test_inconsistent_incidence_is_rejected_without_mutation():
    graph, _ = _fixture()
    graph.connect_incidence("corner:top_right", "slope-left")
    before = graph.geometry_snapshot()
    report = resolve_boundary_seed_constraints(graph, [_relation(graph, "right")], maximum=MAXIMUM)
    assert report["status"] == "inconsistent_incidence_or_division"
    assert not report["resolved_points"]
    assert graph.geometry_snapshot() == before


def test_solution_must_still_match_source_evidence():
    graph, _ = _fixture()
    graph.geometry_entity("right-1").observed_geometry["point_px"][1] += 10
    report = resolve_boundary_seed_constraints(graph, [_relation(graph, "right")], maximum=MAXIMUM)
    assert report["status"] == "solution_outside_source_evidence"
    assert not report["resolved_points"]


def test_exact_equations_do_not_treat_a_small_contradiction_as_zero():
    system = _ExactSystem()
    system.add_zero(({0: Q(1)}, -Q(1, 1)))
    assert system.unique_value(({0: Q(1)}, Q())) == Q(1, 1)
    system.add_zero(({0: Q(1)}, -Q(1, 1) + Q(1) / Q(10**12)))
    assert not system.consistent
    assert system.unique_value(({0: Q(1)}, Q())) is None


def test_stalled_frontier_uses_proved_point_instead_of_fitting_another_scalar():
    graph, coordinates = _fixture()
    report = resolve_boundary_seed_constraints(graph, [_relation(graph, "top")], maximum=MAXIMUM)
    assert report["status"] == "resolved"
    assert report["rank"] < report["crease_variable_count"]
    # The selected point can be unique even while unrelated stripes are free.
    assert "junction" in report["determined_point_coordinates"]
    assert "right-1" not in report["determined_point_coordinates"]
    scale = Q(2, 1)
    graph.geometry_entity("vertical").add_exact_geometry({
        "source_relation_id": "top", "direction_index": 4,
        "through_point_project": [qsqrt2_to_mapping(v * scale) for v in coordinates["mid"]],
        "side_length": qsqrt2_to_mapping(scale),
    })
    baseline = propagate_exact_geometry(graph, maximum=MAXIMUM)
    before = graph.geometry_snapshot()
    with patch("guided_construction._fit_topology_point", return_value=None):
        result, _, operation, history, _, _ = _apply_single_core_reference(
            graph, {}, baseline, scale, maximum=MAXIMUM, incidence_solution=report,
        )
    assert operation is not None
    assert operation.independent_parameters == 0
    assert history["kind"] == "incidence_proved_frontier_reference"
    exact = result.geometry_entity("junction").exact_geometry
    assert exact["source"] == "existing_incidence_constraint_point"
    assert tuple(map(qsqrt2_from_mapping, exact["project_coordinate"])) == tuple(v * scale for v in coordinates["junction"])
    assert graph.geometry_snapshot() == before
    snapshot = result.geometry_snapshot()
    proof = build_construction_proof_topology(snapshot, {"enabled": True, "segments": []})
    assert "junction" in proof["proved_point_ids"]
    entity = next(e for e in snapshot["entities"] if e["id"] == "junction")
    entity["exact_geometry"]["incidence_constraint_proof"]["unique_solution"] = False
    proof = build_construction_proof_topology(snapshot, {"enabled": True, "segments": []})
    assert "junction" not in proof["proved_point_ids"]
