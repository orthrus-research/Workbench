# Workbench managed run plan V1

Status: experimental product-generic read-only execution-plan contract

`workbench-managed-run-plan-v1` is the exact inert preview produced before a
named Workbench development run. Its `schema_version` is `1`,
`canonicalization_id` is `workbench-canonical-json-v1`, and
`read_only_preview` is always `true`. The machine-readable contract is
[managed-run-plan-v1.schema.json](../schemas/managed-run-plan-v1.schema.json).

The V1 working slice composes one existing operation: the dedicated-server
`worldgen-dev` runner. It records what the runner would execute and change; it
does not build, provision, configure, launch, open a viewer, or remove local
state. It is an operational plan, not a Crucible proof receipt, Atlas answer,
Blueprint approval, compatibility result, or mutation authorization.

## Identity

The plan binds:

- the generic capability definition by ID, path, and exact file SHA-256;
- the pack-owned managed-run catalog by pack, ID, path, exact file SHA-256,
  and canonical parsed SHA-256;
- the selected worldgen profile by ID, format, schema version, path, exact file
  SHA-256, and canonical parsed SHA-256;
- the selected recipe by ID, name, purpose, and canonical SHA-256;
- the exact dedicated-server target; and
- the existing runner implementation by ID, path, and exact file SHA-256.

`workbench-canonical-json-v1` is UTF-8 JSON with sorted object keys, no
insignificant whitespace, no trailing newline, and non-ASCII characters
retained as UTF-8. `plan_id` is `workbench-managed-run-plan:sha256:` followed
by the SHA-256 of canonical JSON for every top-level member except `plan_id`.
`target.target_id` is `workbench-managed-run-target:sha256:` followed by the
same projection over every target member except `target_id`.

File digests bind exact bytes. Canonical digests bind parsed mappings. A
producer must reject duplicate JSON keys and must not substitute one digest
kind for the other.

## Availability and status

`availability.state` is `available` or `unavailable`; `reason` always explains
that state. Availability says whether the named recipe has an implemented V1
execution path. `status` is `ready`, `attention`, or `blocked` and includes the
current Doctor result. An unavailable recipe or blocked Doctor result always
forces `status: blocked`. `attention` must not conceal a missing required
artifact, corrupt runtime, failed required check, ambiguous target, or
unsupported side.

V1's exact `target.side` is `dedicated-server`. Client and integrated-server
requests are unavailable rather than being represented as successful V1
targets. A `ready` preview remains bounded and does not predict that a later
build or launch will succeed.

## Practical target and runner projection

The target records the workspace, Minecraft and platform identity, candidate
lock when available, pack, fixture, runtime template, world type, selected
Java and Gradle identities, and Strata root. `platform_profile_id`, its
canonical digest, the candidate lock, Java, Gradle, and Strata may be `null`
in a blocked preview; null is unresolved or unavailable context, not observed
absence.

`runtime_template.state` is `resolved`, `unresolved`, or `unavailable`. A
resolved template has an exact path, canonical JAR-inventory SHA-256, and
server JAR path, size, and SHA-256. The other states carry null path,
JAR-inventory, and server JAR values and block execution. Non-JAR template
configuration is not content-bound by V1; the runner repeats its structural
safety audit immediately before copying the current template.

The Java projection is the existing Doctor executable identity: `path`,
`sha256`, raw `version_output`, parsed `version`, and `major`. The Gradle
projection contains `path`, `sha256`, raw `version_output`, and parsed
`version`. These are null when Doctor did not resolve the corresponding tool.

`runner.command` is the exact shell-escaped one-time command, including its
fresh iteration label. `runner.arguments` is the ordered argument vector after
the Workbench worldgen runner prefix. `runner.reproduction_command` projects
the same operation without the one-use `--label`, allowing the lower runner to
select a fresh label. Consumers must use the argument vector for the planned
execution and must not recover arguments by shell-splitting rendered text.
Command and reproduction text are null for a blocked or unavailable plan.

## Effective recipe

`effective` records only facts the current runner consumes:

- nullable runner `mode` and region, plus the exact signed 64-bit seed;
- nullable diagnostics containing sample modulo, structured-log selection,
  and JFR selection;
- heap, selected Groovy plan path and SHA-256, viewer/JFR/build flags, and
  ordered JVM arguments;
- ordered plain-language mutation, stage, output, and retention projections;
  and
- one explicit restart boundary.

The region stores its source, dimension, minimum chunk coordinates, width,
height, computed chunk count, and halo. Width times height must equal
`chunk_count` and must not exceed 1,024 chunks. Unavailable recipes use null
mode, region, and diagnostics and empty operational arrays; their seed, heap,
and exact Groovy plan remain visible because those profile facts were already
resolved read-only.

The plain-language arrays deliberately mirror the working runner instead of
introducing a speculative stage DSL or mutation transaction format. They are
preview descriptions, not machine authorization. The later runner remains the
owner of execution, stage failure retention, and safety checks.
Managed run plan V1 always builds current source; its `skip_build` flag is
therefore exactly `false`. Artifact reuse remains an explicit lower-level
runner option until a later managed-plan version binds the reused artifact.

## Doctor binding

`doctor` binds the Workspace Doctor report format, schema version, canonical
SHA-256, derived status and counts, and sorted blocker IDs. Counts and IDs must
match that report. Doctor blockers force the plan to `blocked`; executing
later requires a fresh plan or an explicit freshness check, not reuse of an
old preview as approval.

The Shell resolves Doctor and the plan a second time immediately before
execution and requires the complete `plan_id` to remain unchanged. The
executor then re-hashes bound capability, catalog, profile, candidate, runner,
Groovy plan, server JAR, Java, and Gradle files. Finally, the delegated runner
repeats the runtime-template safety audit before copying. Any drift requires a
new preview.

## Deterministic arrays and validation

- `runner.arguments` and `effective.jvm_arguments` retain invocation order;
- `mutations`, `stages`, `outputs`, and `retention` retain the declared runner
  order and contain no duplicates;
- Doctor blocker IDs and `limitations` are ascending Unicode code-point order
  without duplicates.

The closed JSON Schema enforces required properties, nullability, item bounds,
whole-item uniqueness, dedicated-server scope, and unavailable-to-blocked
status. A semantic validator additionally verifies digest identities,
canonical ordering, summary counts, status derivation, command equivalence,
region arithmetic, and consistency among mode, diagnostics, JFR, and stages.

Outputs and retention are intentions only. V1 never reclaims another run, uses
a personal world, downloads licensed pack artifacts or a JDK, claims hot
reload, promotes an experimental Cleanroom candidate, or treats a development
run as proof. Changing a required member or its meaning requires a new format
version.
