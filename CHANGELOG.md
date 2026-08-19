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
- Raster-ridge offset measurements for shadow scoring, using the same rectified paper square and adaptive evidence field as reconstruction.
- Generic boundary-contact midpoint and symmetry reroot proofs. A formerly downstream observed ray can receive new provenance, intersect a paper midline, and regenerate a suspicious algebraic root without a dependency cycle.
- A browser-downloadable `construction-v2-shadow` CP variant. Winning shadow offsets are propagated through the dependency graph and then applied by rebuilding the original CP topology from exact line intersections and paper-boundary contacts.
- Precision rebound from exported `.cp` geometry before shadow search, so six-decimal diagnostic trace serialization cannot degrade a newly selected exact construction.
- A first-use derivation hint pointing out the optional CP underlay without enabling it by default.

### Changed
- Algebraic construction complexity is modeled as a soft cost rather than a hard ban, so large-coefficient expressions can still win when the evidence genuinely supports them.
- Symmetry has no special preference or penalty; unnecessary symmetry loses naturally when it adds construction steps, while reusable symmetry can still win globally.
- Required auxiliary construction rays are scored as part of the shadow DAG so beam search does not treat necessary parents as disposable overhead.
- Alternative provenance is spatially gated near the legacy anchor and indexed by point buckets, limiting symmetry overuse and avoiding cubic target scans.
- GitHub Pages now packages every Python module referenced by the Pyodide worker, including the shadow evidence, geometry, and candidate-variant modules.
- Candidate-variant failures are isolated from both the strict reconstruction and the shadow diagnostic report.
- Derivation playback now uses semantic line states instead of the old thick highlight overlay: current crease segments are blue (mountain dash-dot, valley dashed), completed crease segments remain red dashed, still-needed construction helpers remain grey dashed, and dead helpers disappear.
- The CP underlay is now off by default and remains an explicit viewing aid rather than part of the derivation drawing.

## [0.2.0-dev.6] - 2026-08-19

- Derivation-playback visual cleanup. The playback overlay now separates active crease formation, persistent CP segments, reusable helper rays, and dead construction lines while keeping the final rendered CP unchanged.

## [0.2.0-dev.5] - 2026-08-19

- The first real A/B output milestone: construction-search v2 can emit a separate downloadable CP and preview while the strict v1 result remains the default. The candidate is topology-rebuilt rather than coordinate-patched, and its cAMV structure is re-audited independently.

## [0.2.0-dev.4] - 2026-08-19

- Shadow geometry can now derive an implicit paper-edge contact, take its midpoint with a corner, reflect that point about an existing perpendicular ray, re-root a downstream observed ray, and propagate the resulting exact geometry through the old dependency graph. Exported CP geometry is still unchanged.

## [0.2.0-dev.3] - 2026-08-19

- Shadow search can now choose direct-point or symmetry-point provenance instead of an expensive legacy algebraic seed, while exported CP geometry remains unchanged.

## [0.2.0-dev.2] - 2026-08-19

- First browser-integrated shadow-search milestone. The v2 scorer observes and reports on the v1 construction route but does not control geometry yet.

## [0.2.0-dev.1] - 2026-08-19

- Baseline development version for the construction-search v2 optimization branch.
