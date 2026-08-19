# Changelog

All notable Oriredraw changes are recorded here. Versions follow Semantic Versioning.

## [Unreleased]

### Added
- Construction-search v2 foundation: candidate construction DAG, multiple provenance per node, beam search, route-level scoring, and a cAMV scoring hook.
- Formal project versioning with a canonical `VERSION` file.
- Browser shadow-search adapter: the current reconstruction is translated into a construction DAG, scored by v2, and returned as diagnostics without changing the exported `.cp`.
- High-complexity algebraic diagnostics for expressions such as large-coefficient `a+b√2` seeds.
- Conservative `direct_point` and `symmetry_point` provenance candidates generated from already-constructed anchor points and exact rays.
- Regression coverage for the dinosaur symmetry case, including the `-100√2`, `200(1-√2)`, and `400-300√2` construction relation.

### Changed
- Algebraic construction complexity is modeled as a soft cost rather than a hard ban, so large-coefficient expressions can still win when the evidence genuinely supports them.
- Symmetry has no special preference or penalty; unnecessary symmetry loses naturally when it adds construction steps, while reusable symmetry can still win globally.
- Required auxiliary construction rays are scored as part of the shadow DAG so beam search does not treat necessary parents as disposable overhead.
- Alternative provenance is spatially gated near the legacy anchor and indexed by point buckets, limiting symmetry overuse and avoiding cubic target scans.

## [0.2.0-dev.3] - 2026-08-19

- Shadow search can now choose direct-point or symmetry-point provenance instead of an expensive legacy algebraic seed, while exported CP geometry remains unchanged.

## [0.2.0-dev.2] - 2026-08-19

- First browser-integrated shadow-search milestone. The v2 scorer observes and reports on the v1 construction route but does not control geometry yet.

## [0.2.0-dev.1] - 2026-08-19

- Baseline development version for the construction-search v2 optimization branch.
