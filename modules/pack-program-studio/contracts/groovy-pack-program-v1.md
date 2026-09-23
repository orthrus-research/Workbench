# Groovy Pack Program V1 contract

Status: implemented experimental read-only vertical slice

## Developer outcome

Given an explicit pack adapter and an exact source tree, a developer can see
what GroovyScript is configured to load, which high-value program effects are
statically present, what a candidate changes, and which reload/restart/save
boundary is safest—without executing source or touching a Minecraft instance.

## Inputs

V1 requires:

1. one explicit `workbench-groovy-pack-profile-v1` profile or named adapter;
2. one pack root or Groovy root containing a regular `runConfig.json`;
3. one physical-side context; and
4. only regular, non-symlink UTF-8 source files within profile bounds.

Optional inputs are one baseline tree, changed relative paths, an effective
packmode/debug state, an explicit installed-mod set, one GroovyScript log, and
one Workbench runtime diagnosis.

The command does not download dependencies, start Java, execute Groovy,
invalidate caches, call reload, or mutate source/runtime state. `--output` is
the only write and requires a fresh explicit destination.

## Program binding

The program identity binds:

- pack and platform profile identities/hashes;
- complete configured and unconfigured Groovy file path/hash/stage inventory;
- `runConfig.json` hash;
- side, packmode, and debug state; and
- aggregate source hash.

Git revision and dirty state are retained as navigation context but are not a
substitute for file hashes.

Loader entries are evaluated in declared stage/path order. Directory members
are recursively sorted, duplicate entries execute once, and one file in two
stages is rejected. `prePostInit/` has no special meaning: its configured
loader owns its stage. Unconfigured and preprocessor-excluded files remain
visible as non-executing source.

## Static effect model

Pack profiles declare bounded call/token rules and identity policies. Each
effect retains rule, category, kind, operation, literal/resolved fields,
unresolved fields, normalized semantic key, exact source hash/line/column,
stage, conditions, execution state, and reload classification.

The only V1 evidence state is `static-candidate`. A candidate never becomes an
observed registry effect because it matches a call shape. Comments and string
contents are excluded lexically. Concatenated/interpolated identities remain
unresolved instead of being shortened into false literals.

Machine recipe chains end only at a bounded `buildAndRegister` call before the
next builder. V1 records the recipe-map qualifier, recognized builder
properties, literal resolver calls, and normalized chain hash. It does not
expand loops, helpers, closures, or runtime dispatch.

## Dependencies and collision candidates

Local dependency edges come from imports and unambiguous local type-name
references. Strongly connected components and inbound hubs are presentation
over that static graph; dynamic class loading is outside coverage.

A collision candidate requires at least two non-excluded literal effects under
one explicit profile identity policy. Its disposition is review or reject as
declared by that profile, but its evidence state remains static. Exact registry
execution may confirm, reject, or contextualize it later.

## Comparison and reload guidance

Baseline/candidate comparison retains changed file hashes and a multiset diff
over normalized static effects. Source movement/order and semantic-call
differences are shown separately. `runConfig.json` changes are always visible.

Reload guidance uses the exact platform stage policy, `no_reload`, pack rule
classification, selected change paths, and identity-bearing save policy:

- `reload-candidate` requires cold-start, first-reload, second-reload, and
  effective-state comparison;
- unresolved direct/dynamic behavior produces `restart-recommended`;
- preInit/init/run-config/unconfigured changes produce `restart-required`;
- excluded-only changes produce `not-executed`; and
- no supplied change scope produces `not-evaluated`.

This is guidance, not proof that reload or save migration is safe.

## Runtime correlation

Groovy logs are parsed for version, executed script/class rows, timings, and
severity. Runtime diagnoses contribute checkpoint, primary-failure, and
exception observations. They remain unbound to candidate source unless a
future execution manifest supplies exact source/mod/cache identities.

A clean Groovy log cannot hide broader runtime exceptions. Conversely, a
neighboring runtime exception is not attributed to Groovy without causal
evidence.

## Serialization and bounds

The result format is `workbench-groovy-pack-program-report-v1`. The semantic
validator recomputes report/program identities, counts, effect uniqueness,
collision references, and comparison bindings. JSON schemas cover pack
profiles, platform profiles, and reports.

Default profile bounds cap files, individual bytes, aggregate bytes, effects,
dependency edges, diff rows, and runtime evidence. Overflow fails rather than
silently claiming complete coverage.

The Exact Runtime Explorer validates this report before projecting effects and
collisions as `static-possible` facets. Recorded source paths are not treated
as current navigation without revalidation.

## Exit codes

- `0`: analysis completed; attention may still be represented in the report;
- `1`: `--strict` and the completed report requires attention;
- `2`: invalid, unsafe, changed, unsupported, or unavailable input.

## Additive horizons

The separate
[`workbench-groovy-language-service-result-v1`](groovy-language-service-v1.md)
family now provides source-bound canonicalization diagnostics from the upstream
GroovyScript service; none of those claims are retroactively implied by this
static report. The additive managed-session family now provides receipt-bound
Prism launch and endpoint custody without upgrading static candidates. Content-
addressed cache custody, observed before/after effect ledgers, reload
idempotence qualification, Blueprint-backed authoring wizards, native IDE/full
query parity, and Relay views remain successor work.
