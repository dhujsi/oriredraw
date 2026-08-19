from construction_search import (
    ConstructionGraph,
    ConstructionOperation,
    SearchState,
    SearchWeights,
    beam_search,
    score_state,
)


def test_graph_keeps_multiple_provenance_for_same_point():
    graph = ConstructionGraph()
    graph.add_operation(ConstructionOperation("direct", "direct", ("A",), ("P",)))
    graph.add_operation(ConstructionOperation("mirror", "symmetry", ("Q", "axis"), ("P",)))

    assert {item.id for item in graph.candidate_provenance("P")} == {"direct", "mirror"}


def test_direct_route_beats_needless_symmetry_detour():
    graph = ConstructionGraph()
    graph.add_operation(
        ConstructionOperation("direct", "direct", ("A",), ("P",), frozenset({"p"}), generation=1)
    )
    graph.add_operation(
        ConstructionOperation("make-q", "direct", ("A",), ("Q",), generation=1)
    )
    graph.add_operation(
        ConstructionOperation(
            "mirror-q", "symmetry", ("Q", "axis"), ("P",), frozenset({"p"}), generation=2
        )
    )

    result = beam_search(
        graph,
        SearchState(frozenset({"A", "axis"})),
        {"p"},
        beam_width=8,
    )

    assert [item.id for item in result.selected_operations] == ["direct"]


def test_extra_step_can_win_when_it_explains_more_of_the_cp():
    graph = ConstructionGraph()
    graph.add_operation(
        ConstructionOperation("local", "direct", ("A",), ("P",), frozenset({"p1"}), generation=1)
    )
    graph.add_operation(
        ConstructionOperation("axis", "midpoint", ("A", "B"), ("axis",), generation=1)
    )
    graph.add_operation(
        ConstructionOperation(
            "mirror", "symmetry", ("P0", "axis"), ("P",), frozenset({"p1", "p2", "p3"}), generation=2
        )
    )

    result = beam_search(
        graph,
        SearchState(frozenset({"A", "B", "P0"})),
        {"p1", "p2", "p3"},
        beam_width=8,
    )

    assert [item.id for item in result.selected_operations] == ["axis", "mirror"]


def test_large_algebraic_coefficients_are_penalized_not_forbidden():
    weights = SearchWeights(unexplained=0.0)
    simple = SearchState(frozenset({"P"}), selected_operations=(
        ConstructionOperation(
            "simple", "algebraic", (), ("P",), algebraic_coefficients=(1, -1)
        ),
    ))
    large = SearchState(frozenset({"P"}), selected_operations=(
        ConstructionOperation(
            "large", "algebraic", (), ("P",), algebraic_coefficients=(-34, 24)
        ),
    ))

    assert score_state(simple, frozenset(), weights) < score_state(large, frozenset(), weights)
    assert score_state(large, frozenset(), weights) < 10.0
