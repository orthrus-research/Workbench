# Feature Studio read-only snapshot V2

`workbench-feature-studio-snapshot-v2` is a bounded, IDE-neutral projection of
one fully validated Feature Studio result V2. It is presentation data, not a
material, recipe, profile, evidence, construction, approval, or runtime
authority.

## API

- `project_feature_snapshot(result, suite_root=...)` projects an in-memory
  result;
- `open_feature_snapshot(..., result_path=...)` opens one retained result;
- `open_feature_snapshot(..., receipt_path=...)` opens one retained
  material-flow V2 receipt;
- `refresh_feature_snapshot(...)` reopens the exact retained origin; and
- `validate_feature_snapshot(...)` validates identity and cross-field links.

Inputs are ordinary local JSON files and reads stop at 128 MiB. Symbolic links
are rejected. Retained refresh requires the original URI, digest, and size to
match; a replacement at the same path is drift. An in-memory projection
honestly reports that it has no retained refresh source.

The schema is `feature-studio-snapshot-v2.schema.json`. The snapshot contains
the result summary, plan and source identities, profile/runtime identities,
exact source ranges and diffs, assertions, actions, review binding, owner
links, export summary, and limitations. Owner links omit embedded
`canonical_json`; clients navigate to owner records only after checking the
declared URI, digest, and size.

Source ranges use
`unified-diff-lines-one-based-zero-for-empty-inclusive`. Positive lines are
one-based and inclusive; `0,0` is the only empty range.

Client-local layout, selection, viewport, caching, and dismissed hints never
enter snapshot identity and cannot authorize execution.
