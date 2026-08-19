from __future__ import annotations

from dataclasses import dataclass, field
from math import log1p
from typing import Callable, Hashable, Iterable


NodeId = Hashable
ObservationId = Hashable


@dataclass(frozen=True)
class ConstructionOperation:
    """One exact construction explanation that may be selected by the search.

    An output node may have several operations in ``ConstructionGraph``. The
    search chooses a route globally; merely discovering a symmetry therefore
    never forces the reconstruction to use it.
    """

    id: Hashable
    kind: str
    parents: tuple[NodeId, ...]
    outputs: tuple[NodeId, ...]
    explains: frozenset[ObservationId] = frozenset()
    residual: float = 0.0
    generation: int = 0
    independent_parameters: int = 0
    algebraic_coefficients: tuple[int, ...] = ()

    def complexity(self, weights: "SearchWeights") -> float:
        # Every construction step pays the same base cost. Symmetry does not
        # receive a special penalty: a detour loses because it needs more steps.
        cost = weights.step
        cost += weights.independent_parameter * self.independent_parameters
        if self.algebraic_coefficients:
            magnitude = sum(abs(value) for value in self.algebraic_coefficients)
            cost += weights.algebraic_complexity * log1p(magnitude)
        return cost


@dataclass(frozen=True)
class SearchWeights:
    step: float = 1.0
    independent_parameter: float = 1.5
    algebraic_complexity: float = 0.45
    residual: float = 4.0
    unexplained: float = 8.0
    camv_violation: float = 2.5
    generation_depth: float = 0.12


@dataclass(frozen=True)
class SearchState:
    known_nodes: frozenset[NodeId]
    selected_operations: tuple[ConstructionOperation, ...] = ()
    explained_observations: frozenset[ObservationId] = frozenset()
    residual: float = 0.0
    max_generation: int = 0
    camv_violations: float = 0.0

    @property
    def operation_ids(self) -> frozenset[Hashable]:
        return frozenset(operation.id for operation in self.selected_operations)

    def apply(self, operation: ConstructionOperation) -> "SearchState":
        if not set(operation.parents).issubset(self.known_nodes):
            raise ValueError("construction parents are not available")
        if operation.id in self.operation_ids:
            return self
        return SearchState(
            known_nodes=self.known_nodes | frozenset(operation.outputs),
            selected_operations=self.selected_operations + (operation,),
            explained_observations=(
                self.explained_observations | operation.explains
            ),
            residual=self.residual + max(0.0, operation.residual),
            max_generation=max(self.max_generation, operation.generation),
            camv_violations=self.camv_violations,
        )


@dataclass
class ConstructionGraph:
    """Candidate construction DAG with multiple provenance per output node."""

    operations: dict[Hashable, ConstructionOperation] = field(default_factory=dict)
    provenance: dict[NodeId, list[Hashable]] = field(default_factory=dict)

    def add_operation(self, operation: ConstructionOperation) -> None:
        if operation.id in self.operations:
            raise ValueError(f"duplicate construction operation: {operation.id!r}")
        self.operations[operation.id] = operation
        for output in operation.outputs:
            self.provenance.setdefault(output, []).append(operation.id)

    def candidate_provenance(self, node: NodeId) -> tuple[ConstructionOperation, ...]:
        return tuple(self.operations[item] for item in self.provenance.get(node, ()))

    def available_operations(self, state: SearchState) -> list[ConstructionOperation]:
        selected = state.operation_ids
        result: list[ConstructionOperation] = []
        for operation in self.operations.values():
            if operation.id in selected:
                continue
            if not set(operation.parents).issubset(state.known_nodes):
                continue
            adds_node = any(output not in state.known_nodes for output in operation.outputs)
            adds_evidence = bool(operation.explains - state.explained_observations)
            if adds_node or adds_evidence:
                result.append(operation)
        return result


def score_state(
    state: SearchState,
    all_observations: frozenset[ObservationId],
    weights: SearchWeights = SearchWeights(),
) -> float:
    operation_cost = sum(
        operation.complexity(weights) for operation in state.selected_operations
    )
    unexplained = len(all_observations - state.explained_observations)
    return (
        operation_cost
        + weights.residual * state.residual
        + weights.unexplained * unexplained
        + weights.camv_violation * state.camv_violations
        + weights.generation_depth * state.max_generation
    )


def _default_diversity_key(state: SearchState) -> tuple[str, ...]:
    # Preserve materially different construction grammars instead of filling a
    # beam with tiny variants of the same route.
    return tuple(sorted({operation.kind for operation in state.selected_operations}))


def beam_search(
    graph: ConstructionGraph,
    initial_state: SearchState,
    observations: Iterable[ObservationId],
    *,
    weights: SearchWeights = SearchWeights(),
    beam_width: int = 32,
    max_rounds: int = 64,
    per_diversity: int = 4,
    diversity_key: Callable[[SearchState], Hashable] | None = None,
    state_adjuster: Callable[[SearchState], SearchState] | None = None,
) -> SearchState:
    """Search candidate construction DAGs without committing to local greediness.

    ``state_adjuster`` is the hook for expensive global signals such as cAMV.
    It can update ``camv_violations`` after a candidate has been constructed;
    cAMV therefore ranks whole states instead of dictating a particular rule.
    """

    if beam_width < 1 or per_diversity < 1:
        raise ValueError("beam_width and per_diversity must be positive")

    all_observations = frozenset(observations)
    key_fn = diversity_key or _default_diversity_key
    beam = [initial_state]
    best = initial_state

    for _ in range(max_rounds):
        candidates: list[SearchState] = []
        seen: set[tuple[frozenset[Hashable], frozenset[NodeId]]] = set()
        for state in beam:
            for operation in graph.available_operations(state):
                child = state.apply(operation)
                if state_adjuster is not None:
                    child = state_adjuster(child)
                fingerprint = (child.operation_ids, child.known_nodes)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                candidates.append(child)

        if not candidates:
            break

        candidates.sort(key=lambda state: score_state(state, all_observations, weights))
        if score_state(candidates[0], all_observations, weights) < score_state(
            best, all_observations, weights
        ):
            best = candidates[0]

        next_beam: list[SearchState] = []
        diversity_counts: dict[Hashable, int] = {}
        for state in candidates:
            signature = key_fn(state)
            if diversity_counts.get(signature, 0) >= per_diversity:
                continue
            diversity_counts[signature] = diversity_counts.get(signature, 0) + 1
            next_beam.append(state)
            if len(next_beam) >= beam_width:
                break
        beam = next_beam

        complete = [
            state for state in beam
            if all_observations.issubset(state.explained_observations)
        ]
        if complete:
            complete.sort(key=lambda state: score_state(state, all_observations, weights))
            if score_state(complete[0], all_observations, weights) <= score_state(
                best, all_observations, weights
            ):
                best = complete[0]
            # Do not keep expanding a complete route just to accumulate useless
            # constructions. A later integration can add an admissible lower
            # bound when a stronger optimality guarantee is useful.
            break

    return best
