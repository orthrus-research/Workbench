# Blueprints implementation standard contract v1

Status: implemented

Contract ID: `BLUEPRINTS-IMPLEMENTATION-STANDARD-V1`

This contract defines how maintainers admit immutable executable standards for
the Blueprints engine. It implements the standards boundary required by
`BLUEPRINTS-EXECUTABLE-ENGINE-V1`; it does not select a standard, plan a
feature, allocate an identity, render code, run a hook, mutate a target, or
release an implementation.

## Authority and admission

The only admitted standards are direct or nested `*.yaml` files below the
explicit registry root selected by the caller. The generic module does not
provide a default registry.

A YAML file is authoritative only when the S01 compiler accepts the complete
registry. Directory placement is the human approval act. The generated
`registry.json` is a derived immutable-version lock and index, not a second
approval workflow. Files outside the directory, including the synthetic S01
fixtures, can never be selected by the engine.

The registry fails closed as a unit. One malformed, ambiguous, untracked,
changed-in-place, or removed standard prevents registry admission.
`README.md` and the generated `registry.json` are the only non-standard files
permitted in the registry root. Symlinks and `.yml` aliases are forbidden.

No source example, source frequency, Atlas observation, Manual, or compiler
default creates an executable implementation rule. All such rules must be
written in an admitted standard.

## YAML authoring subset

Authoring uses `susy-blueprints-standard-authoring-v1` and the closed
`blueprints-standard-v1.schema.json` schema. For deterministic and reviewable
input, the compiler accepts one YAML document containing only JSON data:

- mappings have string keys and no duplicates;
- aliases, anchors, custom tags, merge keys, and sets are forbidden;
- scalar values are strings, booleans, nulls, integers, or finite numbers;
- all strings are normalized to Unicode NFC;
- every schema object is closed to undeclared fields; and
- the source is at most 1 MiB.

YAML presentation is not identity-bearing. Comments, indentation, quoting,
mapping order, and set-like list order disappear during compilation.

## Required standard contents

Every primary or component standard declares:

- a stable `standard_key`, semantic `version`, lifecycle, kind, feature
  family, maintainer priority, and specificity;
- one mandatory core template and zero or more approved variants;
- typed parameters classified as `required`, `derived`, `allocated`,
  `defaulted`, or `optional`;
- target repository identities, integration surfaces, and normalized allowed
  path prefixes;
- allowed component versions and variants plus explicit conflict precedence;
- an exact Atlas baseline and required query invariants;
- one or more render outputs binding an operation, target surface, declarative
  path expression, template or trusted hook, and activating variants;
- deterministic formatters, tests, disposable fixtures, and added gates;
- diagnostic templates and idempotent reconciliation rules;
- explicit migrations; and
- content-pinned trusted hooks when declarative behavior is insufficient.

Templates and hooks are repository-relative regular-file paths paired with
SHA-256 content identities. Their bytes are not copied into a compiled
standard. P01 and X01 must resolve and recheck them before use.

All declared gates are required. Optional validation cannot authorize a
release and is therefore not part of a v1 standard. Runtime and formed-world
fixtures must be disposable and never a developer’s live world.

## Canonical compilation

Compilation:

1. strictly parses the YAML subset;
2. validates the closed schema and semantic cross-references;
3. normalizes every string to NFC;
4. replaces the authoring format with
   `susy-blueprints-standard-compiled-v1`;
5. sorts every set-like array named below;
6. inserts only the explicit v1 defaults named below; and
7. emits canonical UTF-8 JSON with sorted object keys, compact separators,
   finite numbers, and no trailing newline.

The canonical compiled standard is the compiled JSON object itself. It does
not contain its own digest, its source path, or YAML file bytes. This resolves
the otherwise circular C01 identity definition:

```text
standard_sha256 = sha256(canonical compiled standard bytes)
standard_id = "blueprints-standard:sha256:" + standard_sha256
```

The registry binds both values to the source path and source-file digest.

Set-like arrays are sorted by these keys:

| Location | Sort key |
| --- | --- |
| `variants` | `id` |
| `variants[].compatible_components` | string value |
| `parameters` | `name` |
| `targets` | `repository_id`, then `integration_surface` |
| `targets[].allowed_paths` | string value |
| `composition.allowed` | `standard_key` |
| component version/variant lists | string value |
| `composition.precedence` | `surface`, `winner`, `loser` |
| Atlas queries and invariants | `id` |
| render outputs and their variant lists | `id` or string value |
| formatters, tests, fixtures, gates | `id` |
| formatter/test/fixture path lists | string value |
| diagnostics | `code` |
| reconciliation rules | `id` |
| migrations | semantic `from_version`, then `id` |
| hooks | `id` |
| hook repository and path lists | string value |
| allocation domains and evidence | `name` or string value |

`compatible_components`, `composition.allowed`,
`composition.precedence`, `formatters`, `tests`, `fixtures`, `gates`,
`diagnostics`, `reconciliation`, `migrations`, `hooks`, and allocation
`domains` default to empty arrays. Other omissions retain their authored
meaning and are not guessed by the compiler.

Order inside a command, a predicate group, a migration step list, or a
diagnostic argument list is semantic and is retained.

## Semantic admission rules

In addition to the JSON Schema, the compiler enforces:

- the standard path is normalized, beneath the registry, and has `.yaml`;
- `standard_key` plus `version` is unique;
- primary standards have `standard_key == feature_family`;
- component standards have a distinct `standard_key`;
- variant, parameter, target, component, query, invariant, gate, fixture,
  test, formatter, diagnostic, reconciliation, migration, hook, and
  allocation-domain identifiers are unique in their respective scopes;
- all referenced variants, components, repositories, hooks, fixtures, tests,
  and diagnostics exist;
- component compatibility and precedence are explicit and non-self-
  conflicting;
- every path is normalized repository-relative POSIX; allowed directory
  prefixes end in `/`, while file paths do not;
- `.git`, `.workbench/blueprints/`, absolute paths, backslashes, empty
  segments, `.` segments, and `..` segments are forbidden;
- every `required` parameter has no derivation, default, or allocation domain;
- every `derived` parameter has exactly one derivation;
- every `allocated` parameter names exactly one declared allocation domain;
- every `defaulted` parameter has a default;
- every `optional` parameter has none of those value sources;
- defaults match their declared scalar type and constraints;
- every Atlas query has at least one required invariant;
- every standard has at least one target and one Atlas query;
- every render output names one declared repository/surface pair and its path
  expression references only declared parameters;
- one unconditional render output uses the mandatory core template, every
  variant template is gated by its own variant, delete outputs have no source,
  and every other output names a declared template or render-stage hook;
- gate references match their declared runner kind;
- trusted hooks are content-pinned and restricted to declared target
  repositories and allowed paths; and
- migrations point from an older semantic version and never replace the
  immutable current version in place.

The compiler reports stable diagnostic codes and JSON-pointer-like locations.
It does not “repair” invalid standards.

## Composition and precedence

`composition.allowed` is the complete allowlist. Absence means the component
is incompatible. Each entry names admitted version requirements, compatible
primary variants, compatible component variants, and whether the component
is required.

`composition.precedence` is the only way two standards may claim the same
named surface. `winner` and `loser` name `primary` or an allowed component
standard key. Both must be possible participants and must differ. An
undeclared runtime conflict remains blocking in P01.

Priority and specificity are maintainer-authored integers. The compiler
preserves them; it does not choose among standards or variants.

## Parameters

`value_type` is one of `string`, `integer`, `number`, `boolean`, or `json`.
Declarative constraints can specify an enum, numeric bounds, a string pattern,
and length bounds. A derivation is a closed expression tree using only the
v1 operators:

- `literal`;
- `parameter`;
- `concat`;
- `lowercase`;
- `uppercase`;
- `replace`;
- `slugify`;
- `posix-path-join`; and
- `json-pointer-get`.

S01 validates expression shape and referenced parameter names. The accepted
[planner and sealed-synthesis contract](planner-and-sealed-synthesis-v1.md)
defines P01 evaluation. S01 does not execute derivations or render paths.

## Allocation finding and ledger boundary

S01 inspected the exact pinned pack and GTCEu sources before defining an
allocation contract.

The audit is bound to pack revision
`9d3aa7ae0294bf27f0b8acbb893d61da23a06972` and GTCEu revision
`9fe140febe8747bbe2f06dfd570421331ec06f4b` from the source registry:

| Authority | Path and exact span | File SHA-256 | Finding |
| --- | --- | --- | --- |
| `SRC-PACK` | `groovy/preInit/MaterialChanges.groovy:14-22` | `0fdc926962b12dcf55dfb6c0780a64254a85814e13b1960443ad98ba5434199f` | the material event directly invokes `SuSyMaterials.init()` |
| `SRC-PACK` | `groovy/material/SuSyMaterials.groovy:2700-2712` | `65e7fc32ab017be410b2489b72bb7468f35cf940185e0ebfb52645629d3a7bc4` | material-family registration order is explicit |
| `SRC-PACK` | `groovy/preInit/RegisterMetaItems.groovy:14-45` | `6603ffa6f7dd85fff4ea04208c724a66ba62576dcce1c37b565fccd49aedd51c` | meta-item sub-IDs are written manually; even a free gap is only a comment |
| `SRC-PACK` | `groovy/material/FirstDegreeMaterialsA.groovy:783-786,1892-1895` | `869d491c989f3915c1148cd20c6b35f3f7966369c899387dba69b1f49184d2b4` | the same `decarburized_air` resource name is built with IDs 8202 and 8371 |
| `SRC-GTCEU` | `src/main/java/gregtech/api/unification/material/Material.java:139-141,475-485` | `46b7cf9c92c06fb3c6db97b06e71f3b826d30d2889f417909418bd406245425e` | the caller supplies the supposedly unique ID and construction registers by mod ID |
| `SRC-GTCEU` | `src/main/java/gregtech/core/unification/material/internal/MaterialRegistryImpl.java:28-40` | `a13df098c1b3cf58283317e9afb27a39c7919fbb792d73f1936a7edd0fca0fb8` | material registration delegates the supplied ID and key |
| `SRC-GTCEU` | `src/main/java/gregtech/api/util/GTControlledRegistry.java:53-66` | `2d4409ec994ca7eeb7e6747e1f771f484a4a4acba42c7524f0017c6c50d250fd` | numeric range and collision are checked only during registry insertion |

Observed Supersymmetry materials are manually assigned numeric IDs in
`groovy/material/*.groovy`, for example
`new Material.Builder(8202, SuSyUtility.susyId('decarburized_air'))`.
`groovy/material/SuSyMaterials.groovy` invokes the material families in an
explicit order. Meta-items are likewise assigned explicit sub-IDs by
`addItem(id, name)` in `groovy/preInit/RegisterMetaItems.groovy`, including
comments such as `FREE ID: 156`. No allocator, reservation ledger, or
transactional identity service was found.

At pinned GTCEu revision
`9fe140febe8747bbe2f06dfd570421331ec06f4b`,
`Material.Builder` accepts a caller-supplied ID; `Material.registerMaterial`
selects a registry by mod ID; `MaterialRegistryImpl.register` delegates to
`GTControlledRegistry.register`; and that registry rejects an occupied
numeric ID. It provides collision enforcement, not allocation. Registration
also places the name before checking the numeric collision, so runtime failure
is not a safe reservation transaction.

The pack currently contains repeated material resource names with different
numeric IDs, including `decarburized_air`, `rutile_slurry`,
`two_four_five_xylenol_mixture`, `ethylenediamine`, `heavy_gas_oil`, and
`ammonium_fluoroberyllate_solution`. This is further evidence that source gaps
or apparent naming conventions cannot serve as allocation authority.

Consequently, each allocated parameter must reference an allocation domain
whose mode is exactly one of:

- `existing-authority`: a named external authority with pinned Atlas/source
  evidence and no Blueprints fallback; or
- `blueprints-ledger`: a declared inclusive numeric pool owned by the one
  future content-addressed Blueprints reservation ledger.

The compiler validates declarations only. It never scans for a free number,
reserves a number, treats a source gap as free, or silently reuses a retired
identity. P01 may make a sealed provisional choice from the validated ledger
and current authority evidence. A01 alone may commit that choice by atomically
adding its durable reservation under ledger and target compare-and-swap.

### Reservation ledger contract

Each profile owns its tracked ledger, shaped by
`blueprints-allocation-ledger-v1.schema.json`. The caller must select that
ledger explicitly alongside the registry. Its identity is:

```text
ledger_id = "blueprints-allocation-ledger:sha256:" +
            sha256(canonical ledger without ledger_id)
```

Each reservation identity uses the same rule over the complete reservation
without `reservation_id` and the prefix
`blueprints-allocation-reservation:sha256:`. Reservations are sorted by
`domain_name`, numeric `value`, and `reservation_id`.

The initial ledger has generation zero, a null parent, and no reservations.
Every later generation binds the prior `ledger_id` as `parent_ledger_id`.
Application must compare-and-swap the bound generation and ledger identity
together with the C01 target state. A concurrent change invalidates the plan;
the engine does not select a replacement without a new plan and simulation.

A committed reservation binds its global domain, numeric value, stable feature
identity, admitted standard, request, and release. Retirement changes status
through a new release and ledger generation, but the row remains forever.
Active and retired rows both occupy their value. A domain/value pair therefore
appears at most once across the entire current ledger.

Global domain names are unique across the admitted registry. Multiple
standards may declare the same domain only with byte-identical mode,
authority, evidence, and pool. A reservation must name a
`blueprints-ledger` domain, use a value inside its inclusive pool, and bind a
standard that declares that domain. The ledger cannot contain a pending row:
provisional plan choices stay sealed and become reservations only in the same
atomic application that releases their target operations.

The module supplies the schema and validation rules. A profile may supply an
empty identity-valid initial ledger.
Its transition validator requires exactly one generation, the exact prior
ledger identity, every old reservation, unchanged binding fields, and only an
active-to-retired status transition. It does not implement selection,
reservation, retirement, compare-and-swap application, or multi-file
application; those remain P01 and A01 work.

## Immutable version lock

`registry.json` contains canonical entries sorted by `standard_key` and
semantic version. `build` may add a new standard version. It fails when an
existing key/version changes digest or disappears. Retirement is represented
by an immutable new version with `lifecycle: retired`; deleting history is not
an admission operation.

`check` recompiles the directory and requires byte equality with
`registry.json`. Thus a YAML edit without a version increment fails, and a new
admission without a rebuilt lock also fails.

## Compiler interface

The implementation is [`standards.py`](../src/workbench_blueprints/standards.py):

```text
PYTHONPATH=modules/blueprints/src python3 -m workbench_blueprints.standards compile PATH --registry-root ROOT
PYTHONPATH=modules/blueprints/src python3 -m workbench_blueprints.standards check --registry-root ROOT
PYTHONPATH=modules/blueprints/src python3 -m workbench_blueprints.standards build --registry-root ROOT
```

`compile` prints one canonical compiled standard but does not admit it.
`check` is read-only. `build` writes only the derived `registry.json` and
refuses immutable-version drift or removal.

Every error is written to standard error as:

```text
<CODE> <location>: <message>
```

and exits non-zero. Successful `check` prints the registry identity and
standard count. No command executes templates, formatters, hooks, tests, Atlas
queries, allocation, or target operations.

## Fail-closed handoff

P01 may consume only entries from a byte-current registry produced by this
compiler. It must independently revalidate standard identity, target
authorization, Atlas evidence, composition, parameters, and allocation
authority. A valid S01 record is necessary, never sufficient, for a plan.
