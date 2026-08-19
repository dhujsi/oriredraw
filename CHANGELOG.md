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
- `scripts/preview.py`, a one-command local preview helper that assembles the branch exactly like Pages, patches the shared engine version, disables browser caching, and opens the preview in the default browser.
- Segment-ratio construction provenance. Ratios are taken on already-constructed finite line segments; `1/6` is represented as midpoint plus trisection of the half-segment rather than a primitive sixth-division rule.
- Variant-specific playback traces, so the v2 derivation can show its own selected provenance instead of replaying the strict route.
- Quality-v5 diagnostics that separate low-quality reconstructed geometry from unresolved image evidence. Near-miss endpoints and duplicate parallel ridges quarantine the current geometry hypothesis without discarding the underlying observation.
- Generic paper-corner symmetry candidates and integration of geometry-v2 reroot proofs into the global provenance beam.
- Exported `.oriredraw` project files now include `app_version` separately from `format_version`.
- A deliberate coreless search branch for large-coefficient internal seeds. It removes the old core-point seed method, builds reference points only from outside the old dependency region, and independently re-anchors affected observations from corners, reliable intersections/anchors, corner symmetry, midpoints, and finite-segment thirds.
- Free-topology shadow rendering for coreless variants. Old high-degree CP nodes are only neighbourhood hints and may split into multiple nearby exact intersections instead of being forced back into one least-squares point.
- Standardized project export filenames with timestamp and app version.
- Observation-first ratio recovery for unresolved raster creases, including finite auxiliary segments between already-constructed exact points even when the segment itself is not a CP crease.
- Explicit browser progress stages for shadow diagnostics, coreless search, ratio recovery, candidate rendering, and final candidate quality review.

### Changed
- Large-coefficient algebraic construction is still available as a fallback, but coefficients above the guard threshold now incur an explicit magnitude-growing independent-parameter cost in addition to the algebraic complexity term.
- Symmetry has no special preference or penalty; unnecessary symmetry loses naturally when it adds construction steps, while reusable symmetry can still win globally.
- Required auxiliary construction rays are scored as part of the shadow DAG so beam search does not treat necessary parents as disposable overhead.
- Alternative provenance is spatially gated near the legacy anchor and indexed by point buckets, limiting symmetry overuse and avoiding cubic target scans.
- GitHub Pages now packages every Python module referenced by the Pyodide worker, including provenance-v6 and shadow-variant-v6 modules.
- Candidate-variant failures are isolated from both the strict reconstruction and the shadow diagnostic report.
- Derivation playback now uses semantic line states instead of the old thick highlight overlay: current crease segments are blue (mountain dash-dot, valley dashed), completed crease segments remain red dashed, and all historical construction helpers remain grey dashed even after their last dependency has been built.
- The CP underlay is now off by default and remains an explicit viewing aid rather than part of the derivation drawing.
- Ratio-line recovery no longer assumes a square or divides the paper boundary. Any reconstructed finite segment may supply simple ratio points; candidate creases are admitted only when the original raster supports them.
- cAMV is now treated as a strong structural prior with cause-aware handling: violations coincident with geometry misalignment are strongly attributed to reconstruction quality, while clean local violations remain repair/search targets rather than hard vetoes.
- A provenance rewrite that leaves a quarantined ray at essentially the same coordinates no longer counts as a repair; the search is pushed toward materially different reference points.
- The coreless branch no longer treats `geometry_reroot` as an acceptable repair: moving the suspect core coordinate is explicitly different from abandoning the core-point construction method.
- Ratio evidence may be parallel-shifted within the normal construction tolerance without moving the exact ratio-derived CP geometry; the raster displacement is retained as evidence metadata only.
- CP and project exports keep the original image basename as the prefix and append result kind, app version, and timestamp to avoid ambiguous repeated filenames.

## [0.2.0-dev.11] - 2026-08-19

- Extended the visible progress budget through all shadow/coreless post-processing, changed ratio recovery to start from unresolved raster observations and exact auxiliary point-segments, allowed hand-drawn raster ridges to validate nearby exact ratio lines without shifting them, and standardized CP/project filenames while preserving the source-image basename.

## [0.2.0-dev.10] - 2026-08-19

- Reworked the interrupted v6 attempt into a genuine coreless alternative. Large-coefficient core-point seeds and coordinate reroots are suppressed in that branch, affected observations are re-anchored from external reliable references, old common nodes may split instead of being forced to reconverge, and exported project files use formatted timestamp/version names.

## [0.2.0-dev.9] - 2026-08-19

- Introduced observation-vs-hypothesis quality handling. Duplicate/near-miss geometry can be quarantined while its raster evidence remains unresolved, cAMV is a strong but cause-aware structural prior, reroot and paper-corner-symmetry candidates compete globally, ratio-line recovery is segment-based with no square assumption, and exported projects record `app_version`.

## [0.2.0-dev.8] - 2026-08-19

- Corrected the ratio-construction model from paper-boundary division to finite constructed-segment division, added midpoint→half-trisection provenance for `1/6`, strengthened the large-`Q(√2)` fallback cost, added raster-validated isolated square parallel lines, kept expired helper lines visible, and gave variants their own playback trace.

## [0.2.0-dev.7] - 2026-08-19

- Added a one-command local branch preview so browser end-to-end testing does not require manually recreating the GitHub Pages `_site` layout.

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
