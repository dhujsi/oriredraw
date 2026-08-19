# Changelog

All notable Oriredraw changes are recorded here. Versions follow Semantic Versioning.

## [Unreleased]

### Added
- Construction-search v2 foundation: candidate construction DAG, multiple provenance per node, beam search, route-level scoring, and a cAMV scoring hook.
- Formal project versioning with a canonical `VERSION` file.

### Changed
- Algebraic construction complexity is modeled as a soft cost rather than a hard ban, so large-coefficient expressions can still win when the evidence genuinely supports them.
- Symmetry has no special preference or penalty; unnecessary symmetry loses naturally when it adds construction steps, while reusable symmetry can still win globally.

## [0.2.0-dev.1] - 2026-08-19

- Baseline development version for the construction-search v2 optimization branch.
