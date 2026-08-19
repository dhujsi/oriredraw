# Versioning

`VERSION` is the canonical project version. Oriredraw uses Semantic Versioning (`MAJOR.MINOR.PATCH`) with prerelease identifiers such as `0.2.0-dev.1` while a change is still under active development.

- **MAJOR**: incompatible changes to CP semantics, public interfaces, or saved-result interpretation.
- **MINOR**: new reconstruction capabilities or materially new user-visible behavior.
- **PATCH**: compatible bug fixes, numerical fixes, and UI corrections.
- **Prerelease**: development iterations that are not yet intended to be treated as a stable release.

Every release-worthy change should be summarized under `Unreleased` in `CHANGELOG.md`. When a release is cut, move those entries under the released version and date, then bump `VERSION` for the next development cycle.

Browser cache fingerprints such as `WEB_ENGINE_VERSION` are implementation cache keys, not release numbers. They may change more often than `VERSION`; a cache-key change must not be presented to users as a project release.
