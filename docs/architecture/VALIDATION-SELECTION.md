# Validation ownership, behavior and focused selection

The canonical Python run remains every suite in `validation/suite_catalog.py`.
Its explicit file partitions assign every repository Python test file exactly
once. A focused selection is useful during a change; it does not replace
canonical PR, main, manual or scheduled coverage.

## Explain a changed-path selection

```bash
python3 validation/suite_selection.py \
  --changed-file modules/project-intelligence/src/workbench_project_intelligence/inspector.py \
  --json
```

This command prints a plan and executes no tests. Supply all changed paths,
including deleted files and both old and new paths for a rename. Paths are
repository-relative POSIX paths; directory traversal, absolute paths, missing
change evidence and ambiguous syntax broaden to every Python suite.

The selector combines native package requirements from each `pyproject.toml`
with the catalog's declared test import roots and static imports in source,
tests and their helpers (including literal dynamic imports). It selects the affected owner
and its transitive consumers. Shell product routing consumes profile packages
through discovery, so profile changes also select Shell's tests. The runner,
conformance and product-open checks are included in every focused plan.

Every suite has a selected or omitted decision and a reason in the JSON result.
Unknown/unregistered owners, invalid dependency metadata, API, Core, shared
material semantics, schema, contract, protocol, service-host, package/build
metadata and validator changes broaden to canonical Python coverage. Changes
outside declared ownership, including top-level documentation, also broaden;
there is no implicit documentation-only exemption.

`selection_kind` is `focused` for a proper subset and `canonical` only for the
complete catalog. These names describe planned scope. Neither a plan nor an
individual suite pass establishes that native, installed, IDE, physical runtime
or release requirements ran. CI's full canonical sweep remains independent of
this optional selector to expose errors in dependency mapping.

## Source-backed behavior categories

```bash
python3 validation/coverage_catalog.py
```

The JSON map connects each owner to important executable checks and their
prerequisites. It validates that every reference remains in the owner's exact
test-file partition and lists other assigned files explicitly. It does not
delete or deselect tests, infer correctness from a count, or claim an exhaustive
review of every assertion.

| Category | What the referenced checks establish when executed |
|---|---|
| `policy` | Documentation, manifests, source structure and declared policy |
| `isolated-behavior` | Logic or synthetic-data behavior and rejection boundaries |
| `owner-integration` | Owner APIs, CLI, filesystem and cross-owner integration |
| `installed-product` | Installed packages, process lifecycle and installation boundaries |
| `runtime-custody` | Runtime evidence, durable state, recovery and identity/custody boundaries |
| `physical-runtime` | Actual platform/build/runtime checks with external prerequisites |

These categories are descriptive. Mixed files can contribute to multiple
categories, and prerequisites apply to their relevant cases. A physical test
that skips is still unexecuted physical coverage. Synthetic evidence tests do
not establish that a real server ran. Existing skip requirements, resource
locks, exclusive execution and repository/same-filesystem temporary storage
remain separate constraints.

Examples include Atlas corpus/route evidence and stale continuation rejection;
Crucible independent reconstruction, incremental reuse, recovery and Node
canonical vectors; Blueprints simulation/rollback and publication boundaries;
and Shell installed-service upgrade, interruption and duplicate-owner tests.
The `validation-authority` suite currently owns the single product-open
integration regression. Its historical name does not imply internal ledger or
evidence-compiler reconstruction.

## Changes to test ownership and diagnostic histories

Before moving, splitting, consolidating or retiring tests, compare the exact
old and new suite-qualified test-ID sets. Preserve each unique behavior and
explain identity changes through explicit old-to-new references. A same-count
replacement is not evidence that the same checks survived. New collector roots,
empty partitions, duplicate assignments, missing files and import failures must
remain visible and fail the applicable admission checks.

Retained timing files remain historical diagnostics. A current trend or
scheduling estimate needs a matching source, complete current test inventory
and suitable execution/environment context. Missing old suites/files, renamed
tests and newly added tests make older inventories incomparable; do not delete
reports or revive retired tests to make totals align. Timings never satisfy a
new run's report, source or inventory admission requirements.
