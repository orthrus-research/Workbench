# Validation and testing

Validation should catch broken behavior at the smallest useful boundary. A
passing check describes only what ran in that environment; it does not create
a support or release claim.

## Default repository check

Run before handing off an ordinary change:

```bash
python3 validation/validate.py
git diff --check
```

The validator checks source policy and structured data, compiles Python
sources, then runs the registered quick suites in isolated temporary
directories. Ignored dependency, IDE, game, build, and `.workbench/` trees are
not source inputs.

List the current suite registry or run a focused suite while iterating:

```bash
python3 validation/validate.py --list-suites
python3 validation/validate.py --suite workbench-shell
python3 validation/validate.py --suite atlas
```

A focused pass proves only that suite. Add the smallest regression that
reproduces a failure, then run the default check before handoff.

To inspect or execute a conservative change-based selection:

```bash
python3 validation/validate.py --changed-since master --explain-selection
python3 validation/validate.py --changed-since master
```

The diff includes committed changes since the merge base, staged and unstaged
changes, deletions, both sides of renames, and legitimate untracked files.
Unknown or shared boundaries broaden to every Python suite. An empty diff also
broadens; it never silently runs zero tests. See
[ownership and selection](VALIDATION-SELECTION.md) for the dependency map and
behavior categories. Manually dispatched CI runs canonical Python independently
of this optional local selector.

## Deeper checks

Use the depth that matches the changed boundary:

```bash
# Include intensive Python suites.
python3 validation/validate.py --tier canonical

# Add native IDE unit, compile, and package checks.
python3 validation/validate.py --ide

# Run canonical Python, IDE host integration, compatibility, and policy audits.
python3 validation/validate.py --full

# Audit documentation links, retained migration identities, and catalog
# consistency.
python3 validation/validate.py --policy
```

`--jobs N` changes only the bounded Python worker count. Suites marked
exclusive still run alone. Use `--jobs 1` when diagnosing scheduling or shared
resource behavior.

Exclusive work forms the first scheduling phase. Independent suites then use
measured process wall time, with catalog order as the fallback. Estimates must
match the current source and recorded environment; old formats and changed
inventories remain historical diagnostics. Resource locks coordinate suites in
one invocation. Separate validator invocations still need separate checkouts or
external coordination for shared physical resources.

Full validation is appropriate for shared protocols, packaging, client
integration, and large cross-module refactors. A reversible local experiment
does not need release-depth checks merely because those checks exist.

## Component checks

Run the owning toolchain directly when working inside a client or packaging
surface. The repository validator remains the final integrated check.

```bash
python3 tools/component_versions.py check
python3 tools/validate_public_tree.py
```

The VS Code and IntelliJ client READMEs list their native test and package
commands. Release builders must additionally inspect the exact archive member
list and test installation from a clean artifact rather than from the source
tree.

## Diagnostics

Validation logs and timing data are written under ignored
`.workbench/validation/`. They help diagnose failures and schedule expensive
suites; they are not source, qualification evidence, or package inputs.

Each command writes an overall result under
`.workbench/validation/invocations/`. Use `--result PATH` to give that fresh
output a known name in ignored storage or outside the checkout. An existing
result path is rejected. The result records
preflight, Python and any requested IDE stages separately, including later
stages that never ran after a failure. It references the exact Python run ID
and source fingerprint; a Python-only pass cannot stand in for a full pass.

Python runs retain their manifest, logs, and version 3 suite reports under
`.workbench/validation/runs/`. The child collects once and waits for the scheduler
to admit its exact IDs before executing that same collection. The scheduler
compares terminal IDs with its retained admission, so a same-count replacement
cannot hide missing coverage. Reports retain skipped/expected-failure reasons,
subtest and fixture diagnostics, and explicit unrun cases after fixture errors.

Measurements separate configuration/import/collection, module and class
fixtures, test setup, bodies, teardown and cleanup. Nested phase measurements
exclude their children to avoid double counting. Suite elapsed time includes
its fixtures; process wall time also includes interpreter startup and report
work. Queue time is separate. Summed suite times overlap when workers run in
parallel and must not be presented as total wall time. Interpreter, dependency,
platform, environment-lock and storage information accompanies each run.

IDE and native commands retain bounded stdout/stderr tails and phase states
under `.workbench/validation/stages/`, or a fresh `--diagnostics` directory.
They retain the exact hashes of native assemblies reused for inspection and
installation. Core-only wheelhouses are derived from dependency metadata in
already verified wheels, while actual offline installation remains required.

`validation/validate_ide.py --full --jobs 2` runs VS Code and IntelliJ concurrently
after provisioning. Each client has separate configuration, cache, temporary
storage and diagnostic logs. VS Code's minimum/current host sequence remains
ordered. Use `--jobs 1` (the default) for a sequential comparison. A client failure
cancels the other client's owned commands and leaves a failed overall result.

## Continuous integration

GitHub Actions runs only when a maintainer dispatches a workflow. Pushes, pull
requests, component tags and schedules do not start CI. Dispatch `validate` for
quick checks, source CI, Linux installed packages, Axiom, IDE hosts and physical
Cleanroom build and custody probes. Dispatch `portability-observation` separately
for Windows and macOS package observations. Required Node is provisioned
explicitly. Local validation remains necessary before handoff.

The source CI lane requires the real pip isolation probe to pass. The separate
Windows observation runs the source owners' native long-path probes. The manual
physical sweep requires all three named physical probes to pass. A skip cannot
satisfy these required checks. Platform-inapplicable and optional tests remain
visible in reports; their absence is not a claim that their behavior was exercised.

The aggregate `required-validation` job reconciles the plan with all job
outcomes. Missing, unexpectedly skipped, cancelled or failed required jobs
cannot produce a successful aggregate. Diagnostic artifacts are uploaded even
after failures. These job definitions provide CI coverage only when dispatched;
a local source run does not establish that the remote platform matrix passed.

If a required check cannot run, report it explicitly and keep the resulting
claim narrow. A crash, timeout, corrupt output, missing report, or failed
required check is a failure.
