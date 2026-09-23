# Blueprints executable engine contract v1

Status: accepted semantic and structural contract

Contract ID: `BLUEPRINTS-EXECUTABLE-ENGINE-V1`

This contract turns the
[executable product charter](executable-product-charter-v1.md) into exact
records and fail-closed lifecycle rules. It defines meaning only. It does not
implement a standards compiler, planner, renderer, simulator, target mutator,
history store, proof exporter, or CLI.

## Authority boundary

Executable authority comes from:

1. this versioned engine contract;
2. structurally admitted, immutable standards compiled under their own
   accepted contract;
3. the exact request, target, plan, and run identities;
4. current Atlas evidence admitted by the selected standards;
5. the universal engine gates plus every selected-standard gate; and
6. target-state compare-and-swap at application time.

The following are never executable authority:

- AI output;
- source frequency or similarity;
- a source example by itself;
- a Manual;
- an unadmitted standard file;
- an unavailable Atlas result treated as empty;
- a failed or skipped required gate; or
- a developer’s unrecorded intent.

No admitted standard means no plan, candidate, simulation, release, patch,
application, verification, or proof.

## Contract records

The v1 record set is:

| Record | Schema | Purpose |
| --- | --- | --- |
| canonical request | `blueprints-request-v1.schema.json` | Intake-independent intent, parameters, output mode, consent, and exact target |
| immutable plan | `blueprints-plan-v1.schema.json` | Selected standards, effective parameters, Atlas checks, paths, operations, and gates |
| executable run | `blueprints-run-v1.schema.json` | Candidate, simulation, release, application, verification, invalidation, and causal failures |
| portable proof | `blueprints-proof-v1.schema.json` | Verified identities and exportable evidence without private payloads |
| simulation environment | `blueprints-environment-lock-v1.schema.json` | Approved isolator, dependency objects, commands, and resource limits |
| private simulation evidence | `blueprints-simulation-evidence-v1.schema.json` | Gate outcomes, bounded command evidence, isolated manifests, and cleanup closure |
| exact release bundle | `blueprints-release-bundle-v1.schema.json` | Ordered before/after bytes and modes bound to candidate, target, and operations |
| local history manifest | `blueprints-history-manifest-v1.schema.json` | Causal records, content-addressed artifacts, privacy, and retention state |
| portable proof export | `blueprints-proof-export-v1.schema.json` | Proof plus only the canonical payloads explicitly admitted for export |

All nine schemas are closed: undeclared fields are invalid. A structurally
valid record may still be semantically invalid. The independent semantic
validator enforces this contract’s cross-record and state-dependent rules.

Implementation details for the last three records are fixed by the
[release, application, and proof contract](release-application-proof-v1.md).

## Canonical JSON and identity

Canonical JSON is UTF-8 JSON with:

- Unicode strings normalized to NFC;
- object keys sorted by Unicode code point;
- arrays retained in their contract-defined order;
- no insignificant whitespace;
- no duplicate object keys;
- lowercase JSON literals;
- finite base-ten numbers only;
- the shortest lossless JSON number representation; and
- no trailing newline in the hashed byte sequence.

For a record identity, remove only the identity field named below, serialize
the remaining complete record as canonical JSON, calculate SHA-256, and append
the lowercase hexadecimal digest to the prefix.

| Identity | Record projected for hashing | Prefix |
| --- | --- | --- |
| target state | request `target` without `target_state_id` | `blueprints-target-state:sha256:` |
| request | request without `request_id` | `blueprints-request:sha256:` |
| plan | plan without `plan_id` | `blueprints-plan:sha256:` |
| candidate | candidate without `candidate_id` | `blueprints-candidate:sha256:` |
| simulation | simulation without `simulation_id` | `blueprints-simulation:sha256:` |
| release | release without `release_id` | `blueprints-release:sha256:` |
| patch | release `projections.patch` without `patch_id` | `blueprints-patch:sha256:` |
| application | application without `application_id` | `blueprints-application:sha256:` |
| verification | verification without `verification_id` | `blueprints-verification:sha256:` |
| run snapshot | run without `run_id` | `blueprints-run:sha256:` |
| proof | proof without `proof_id` | `blueprints-proof:sha256:` |

An admitted standard uses:

```text
blueprints-standard:sha256:<sha256-of-complete-canonical-compiled-standard>
```

Its `standard_sha256` is the same digest suffix. This contract fixes the
binding but does not define the standard’s contents or admission compiler.

Every edit creates a new identity. Identity fields are never trusted merely
because they have a valid prefix.

## Canonical request

Interactive intake, CLI flags, and supplied JSON must compile to the same
request bytes when they express the same intent. Intake provenance is
therefore not part of the canonical request.

Request parameters:

- are sorted lexicographically by `name`;
- have unique names;
- contain only developer-supplied values;
- do not contain derived, allocated, or defaulted values; and
- preserve JSON value types.

Requested variant IDs are unique and sorted. An empty list delegates selection
to standard priority and specificity. `output_mode` is exactly one of:

- `instructions`;
- `patch-bundle`; or
- `direct-apply`.

`allow_direct_apply` must be true when `output_mode` is `direct-apply`.
Compliant revisions require both an engine proposal and explicit
`accept_compliant_revision` consent; the original request is never silently
rewritten.

### Target state

The target state binds:

- one logical repository identity;
- an exact 40-character Git revision;
- whether the current worktree is dirty; and
- a canonical manifest digest of every tracked, staged, unstaged, and
  permitted untracked path that participates in the run.

The manifest includes path, kind, mode, and content identity. It excludes only
paths excluded by the engine’s accepted local-state contract. This contract
does not define that separate implementation boundary.

A dirty worktree is permitted and protected. It is not normalized to the
commit and may not be overwritten.

## Plan admission

A `ready` plan requires:

- one admitted primary standard;
- zero or more admitted compatible component standards;
- exact standard identity/digest agreement;
- no unresolved selection tie;
- every effective parameter accepted;
- all required Atlas invariants present and passing;
- `relevant_drift: none`;
- at least one normalized authorized path;
- a canonical ordered operation manifest;
- a canonical ordered validation-stage list; and
- no blocked reason.

A `blocked` plan contains the standards and evidence that were actually
resolved, no candidate, and one or more causal `blocked_reasons`. A missing
standard produces no plan record at all because no standard can authorize the
plan shape. Its operation array is empty whenever synthesis did not complete;
the semantic validator, rather than the structural schema alone, enforces that
ready plans have operations and no blocked reasons while blocked plans have at
least one reason.

Effective parameters are unique and sorted by name. They state their standard
class, value, origin, and whether the developer accepted a compliant revision.
The plan, not the request, is the first record allowed to contain derived,
allocated, defaulted, or optional effective values.

Primary and component standards are sorted primary-first and then by
`standard_id`. Variant and rationale IDs are sorted. Remaining ties set
`developer_choice_required: true`, make the plan `blocked`, and prevent a
candidate.

### Atlas admission and drift

Every standard pins an accepted Atlas baseline and declares critical
invariants. The plan records baseline, current result, each invariant result,
and whether relevant drift is blocking.

- `fail` or `unavailable` on a required invariant blocks the plan.
- Relevant drift blocks the plan.
- Non-impacting drift may be reported outside the critical invariant set.
- Atlas reports observed truth; it does not approve a standard.

## Authorized paths and operations

Authorized paths and operation paths are normalized repository-relative POSIX
paths.

They:

- are NFC;
- contain no empty, `.`, or `..` segment;
- contain no backslash or absolute prefix;
- do not name `.git` or another engine-protected path;
- do not traverse a symlink outside the bound repository;
- are permitted by every controlling standard; and
- are sorted lexicographically after normalization.

Authorized directory prefixes end in `/`. Every operation path must fall
under at least one authorized prefix. Operation ordinals are contiguous from
zero, paths are unique, and the list is sorted by normalized path before
ordinal assignment.

`create` and `update` require a content digest. `delete` requires
`content_sha256: null`. Candidate and release manifests must reproduce this
same ordered operation set.

## Candidate boundary

A candidate is internal and sealed. Its run record may expose only:

- identity bindings;
- the content-manifest digest;
- a local content-addressed sealed locator; and
- the edit generation.

It may not expose source text, patch bytes, generated assets, placement text,
or a filesystem path. Possession of a candidate ID or local sealed object does
not authorize release or application.

Candidate construction requires a `ready` plan. Its target and standard IDs
must exactly equal the plan bindings.

Editing a request, plan, or candidate:

1. creates a new identity;
2. emits an `invalidate` event;
3. moves the run to `invalidated`;
4. names every old candidate, simulation, release, patch, application,
   verification, and proof identity in `invalidated_ids`; and
5. makes those identities inadmissible to every later phase.

Downstream identities may not be copied into the new generation.

## Simulation admission

Simulation binds the exact candidate and approved environment lock. Gate
ordinals are contiguous from zero and correspond exactly to the plan’s
required validation stages.

The simulation is `passed` only when every required universal and
selected-standard gate is `passed`. `failed` and `unavailable` both prevent
release. Skipping a required gate is equivalent to `unavailable`.

The universal baseline includes:

- schema and standard validity;
- standard composition and parameter validity;
- authorized-path and identity/allocation collision checks;
- byte-identical deterministic regeneration;
- target-state consistency;
- pinned formatter stability;
- isolated compilation;
- the target’s existing tests; and
- generated invariant, collision, determinism, and placement tests.

Standards may add runtime, presentation, integration, or formed-world gates.
Those gates use disposable fixtures, never a developer’s live world.

## Release and output projections

`generate` does not synthesize new code. It releases the exact candidate that
already passed simulation.

A release requires:

- the candidate bound to the current plan;
- one `passed` simulation for that exact candidate;
- an unchanged target state;
- an operation digest equal to the plan/candidate manifest; and
- no invalidation.

One release deterministically produces three projections over the same
ordered operations:

- placement instructions;
- a patch bundle; and
- the direct-application diff.

The patch identity is recomputable from its bundle and operation digests. All
three projection digests must bind the same `operations_sha256`. A request’s
`output_mode` selects delivery behavior, not different code.

Failed, unavailable, missing, stale, or invalidated simulation state exposes:

- no release record;
- no patch identity;
- no source or asset bytes;
- no instructions;
- no direct diff; and
- no proof.

## Target application transaction

Only `direct-apply` may execute the v1 `apply` phase. `instructions` and
`patch-bundle` stop in `released`; their external realization is not silently
treated as a Blueprints application or verification.

Direct application:

1. requires explicit request consent;
2. binds the release’s baseline `expected_target_state_id`;
3. recomputes the complete `observed_target` immediately before mutation;
4. rejects when observed and expected identities differ;
5. confirms every path remains authorized;
6. previews the exact released diff;
7. applies all operations as one transaction;
8. records the complete post-state identity; and
9. either succeeds completely or restores the exact pre-state.

Both `observed_target` and `post_target` retain the full recomputable target
record, not merely an asserted ID. An application is `applied` only when:

- the expected and observed target identities are equal;
- `atomic` is true;
- `post_target` is present; and
- `rollback` is `not-needed`.

A rejected application has `post_target: null` and no verification.
If rollback fails, the run remains `application-rejected`, emits a critical
causal error, and Blueprints must not claim the target is unchanged.

## Verification and proof closure

Verification binds the exact applied release, application, and post-target
state. It passes only when every required post-application check passes.

A `release-validated` proof exists as soon as any output mode reaches
`released`. It closes request through release, the patch identity, all passing
gates, the baseline target, local history, and the exportable artifact set. It
sets application, verification, applied-target, and verified-target fields to
`null` and makes no target-mutation claim.

A `target-verified` proof exists only for a `verified` direct-apply run. It
extends the release proof and closes:

- request, plan, standards, candidate, simulation, release, patch,
  application, verification, and run identities;
- baseline, applied, and verified target identities;
- every required passing gate;
- retained local manifest and retention policy; and
- the exported artifact set.

Proof artifacts declare `exportable` or `local-private`. A local-private
artifact:

- may be named by logical ID and digest in the compact proof;
- must set `included_in_export: false`;
- must not appear in `export.artifact_ids`; and
- must not expose a locator, path, payload, log, world, cache, or private
  content.

The `export` slot is `null` until export occurs. Its manifest digest covers the
portable artifact manifest, not the proof containing that digest, so proof
identity is acyclic. An export manifest must set `excludes_private: true`.
Proof records live in local history; portable export is optional. Instructions
and patch bundles may export `release-validated` proof from `released`; direct
application may export only `target-verified` proof from `verified`.

## CLI state machine

The persisted states are:

`initialized`, `planned`, `simulation-failed`, `simulated`, `released`,
`application-rejected`, `applied`, `verification-failed`, `verified`, and
`invalidated`.

Every phase admits only these transitions:

| Phase | Admitted predecessor | Successor on success | Successor on handled failure |
| --- | --- | --- | --- |
| `init` | no run | `initialized` | no persisted run |
| `plan` | `initialized`, `simulation-failed`, `application-rejected`, `verification-failed`, `invalidated` | `planned` | predecessor unchanged |
| `simulate` | `planned` | `simulated` | `simulation-failed` |
| `generate` | `simulated` | `released` | `simulated` with causal error and no release |
| `apply` | `released` in `direct-apply` mode | `applied` | `application-rejected` |
| `verify` | `applied`, `verification-failed` | `verified` | `verification-failed` |
| `history` | any persisted state | predecessor unchanged | predecessor unchanged |
| `export-proof` | `released` for `instructions`/`patch-bundle`; `verified` for `direct-apply` | predecessor unchanged | predecessor unchanged with causal error and no export |
| `invalidate` | any state except `initialized` and `invalidated` | `invalidated` | not applicable |

Every persisted command attempt appends one event. Event sequences are
contiguous from zero. `from_state` equals the preceding event’s `to_state`;
the first `init` event uses `from_state: null`. `history` and successful
`export-proof` use `result: no-op` because they do not change executable
state.

Illegal predecessor, illegal output mode, missing authority, stale identity,
or invalidated input fails before the phase and cannot fabricate a transition.

The current run `state` equals the last event’s `to_state`. State-dependent
closure is:

| State | Required records | Forbidden records |
| --- | --- | --- |
| `initialized` | request/target | plan, candidate, simulation, release, application, verification |
| `planned` | plan, candidate | simulation, release, application, verification |
| `simulation-failed` | plan, candidate, failed simulation | release, application, verification |
| `simulated` | plan, candidate, passed simulation | release, application, verification |
| `released` | plan, candidate, passed simulation, release | application, verification |
| `application-rejected` | release, rejected application | verification |
| `applied` | release, applied application | verification |
| `verification-failed` | release, applied application, failed verification | none of the prior records |
| `verified` | release, applied application, passed verification | none of the prior records |
| `invalidated` | invalidation event and invalidated IDs | every invalidated downstream record |

The run schema uses nullable record slots so every state has one stable shape.
Semantic validation enforces this table.

## Causal failure semantics

Errors are structured and deterministic. Each names:

- a stable uppercase code;
- the phase;
- a human-readable summary from accepted templates;
- affected record identities; and
- a retained evidence digest.

An error never contains candidate code. Multiple errors are sorted by phase,
code, and affected identity. The minimum known conflict boundary is reported;
unknown or unavailable evidence is not rewritten as an empty result.

## Contract fixtures

`executable-engine-examples-v1.json` contains one synthetic verified
direct-apply lifecycle plus fail-closed mutations. It is not an admitted
standard, implementation example, generated feature, runtime result, or
independent semantic validator.

The fixtures cover:

- structural schema rejection;
- identity drift;
- missing standard authority;
- unauthorized traversal;
- failed-gate release leakage;
- divergent output operations;
- stale-target application;
- retained downstream IDs after a manual edit;
- illegal state transitions; and
- private artifact export.

## Implementation and conformance boundary

This contract establishes the record semantics and schema surface. Separate,
versioned contracts govern the corresponding implementation boundaries:

- [implementation standards](implementation-standard-v1.md) govern standard
  admission and compilation;
- [planning and sealed synthesis](planner-and-sealed-synthesis-v1.md) govern
  canonical intake, planning, allocation, and rendering;
- [isolated simulation](isolated-simulation-v1.md) governs build and runtime
  checks;
- [release, application, and proof](release-application-proof-v1.md) govern
  target mutation, history, and proof export;
- the [core CLI interface](core-cli-interface-v1.md) governs executable library
  and command behavior; and
- the [engine conformance proof](engine-conformance-proof-v1.md) defines the
  independent semantic validation boundary.

Passing the synthetic contract fixtures proves only that the contract surface
is internally testable. It does not admit a standard or prove that a real
feature can be generated.
