# Crucible Worldgen Observatory capture contract v1

Status: experimental generic capture contract;
no Cleanroom probe or Atlas admission is implied

Contract ID: `WORKBENCH-CRUCIBLE-WORLDGEN-OBSERVATORY-CAPTURE-V1`

This contract defines the loss-aware runtime record and bundle boundary for
observing world generation in a controlled Crucible fixture. It does not define
where a particular Cleanroom version is instrumented, interpret captured
mechanics, authorize construction, or create a second World Studio truth
store.

Crucible owns the exact experiment, raw records, capture health, and bundle
publication state. Atlas may later normalize and interpret a retained bundle.
Blueprints may consume gate results without owning capture semantics. Sentinel
may diagnose a bundle only against a separately declared rule or invariant.
World Studio projects these records and authorities without reclassifying
them.

Machine-readable artifacts:

- `worldgen-observatory-record-v1.schema.json`;
- `worldgen-observatory-bundle-v1.schema.json`; and
- the focused standard-library contract tests under
  `modules/crucible/tests/test_capture_contract.py`.

## Exact run boundary

One run binds one capture plan, mode, fixture, platform profile, physical side,
runtime Java, mappings, transformed runtime, mod set, configuration set, world
identity, seed, world type, generator options, and declared dimensions. A null
pack profile means that no pack profile applies; it never means an inferred
default pack.

The run ID is content-addressed over the canonical run object without its
`run_id`. Canonical JSON is UTF-8 with sorted object keys, no insignificant
whitespace, no non-finite numbers, and no trailing newline. Runtime records
repeat the run ID and relevant world, dimension, and chunk scope so a detached
record cannot silently acquire another context.

Exact platform bindings belong to platform profiles. Pack-specific selectors,
fixtures, expected behavior, and diagnostic policies belong to pack profiles.
This generic contract contains neither Supersymmetry assumptions nor a list of
version-specific hook locations.

## Capture modes

Every run declares exactly one mode:

- `summary`: bounded counters, phase results, and checkpoint digests;
- `trace`: lifecycle, call, event, and attribution spans with summarized hot
  paths;
- `forensic`: selected per-call, per-write, and per-access detail; or
- `lossless-fixture`: a bounded controlled fixture in which any dropped record
  prevents completed publication.

Selectors and budgets are part of `capture_plan_sha256`. Changing either while
running requires an append-only `capture_control` record. Such a change remains
observable and may split later comparisons; it cannot rewrite earlier scope.

`lossless-fixture` means zero dropped records, no `dropped_detail` record, no
truncated record coverage, and a zero dropped count in the final summary. It
does not mean that a crashed process completed. A crashed lossless fixture is
still incomplete even if nothing was dropped before the crash.

## Append-only record envelope

Records are retained in increasing, contiguous `ordinal` order beginning at
zero. An occurrence is identified by `(run_id, ordinal)`. Publication never
renumbers or deletes an earlier occurrence.

Every record contains:

- `scope`: run, world, dimension, and chunk qualification;
- `causality`: trace, active span, parent span, root trigger, and explicit
  cross-trace or cross-chunk links;
- `actor`: exact, ambiguous, unbound, or Workbench attribution, including
  transformed bytecode identity when exact;
- `order`: thread-local sequence, Lamport value, optional tick and monotonic
  timestamp, and explicit happens-after ordinals;
- `outcome`: entered, returned, threw, canceled, rejected, observed, or
  incomplete state without suppressing exceptions;
- `coverage`: capture mode, selector, retained-detail state, cumulative drop
  count, and limitations; and
- a record-type-specific `payload`.

The admitted record types are:

| Type | Purpose |
| --- | --- |
| `capture_control` | Start, stop, flush, or recorded selector change |
| `probe_health` | Exact hook binding, application, reachability, or failure |
| `span_enter` | Begin one generator, phase, event-listener, or operation span |
| `span_return` | Normal terminal result for an entered span |
| `span_throw` | Exceptional terminal result for an entered span |
| `event_dispatch` | Event bus post or listener boundary and state transition |
| `decision` | Stable rule input and selected, admitted, rejected, or skipped outcome |
| `rng_observation` | RNG stream summary or selected exact call observation |
| `block_write` | Primer, world, chunk, or direct-storage write attempt/result |
| `chunk_access` | Chunk lookup, load, generate, populate, save, or unload request |
| `checkpoint` | Canonical phase-local generated-state digest |
| `dropped_detail` | Exact affected type/range/count and backpressure reason |
| `diagnostic` | Capture-health observation, never an undeclared policy finding |

Typed payload schemas are closed. Human-readable log text is not a substitute
for a typed record.

## Ordering and causality

`ordinal` is append order, not a claim that concurrent game operations have a
total mechanical order. Exact order within one thread follows
`thread_sequence`. `happens_after` and causal links state additional observed
ordering. Monotonic time and tick values support navigation and performance
analysis but do not establish causality by themselves.

An entered span has one globally unique `span_id`. It terminates exactly once
with either `span_return` or `span_throw`. A terminal record without an earlier
matching enter is invalid. Spans on one thread are properly nested. Async work
opens a new span and uses an explicit causal link; it does not pretend that a
thread-local stack crossed an unobserved handoff.

A synchronous child inherits its parent's trace and root trigger. An ordinary
record on a thread with an active span cites that active span; clearing the span
identity is not an out-of-band escape hatch. A thrown outcome retains the
exception class and a digest of its message, including listener throws.

A completed bundle has no open span. Incomplete crash residue may retain open
spans, but the residue and summary must enumerate the same exact set. Existing
terminal pairs remain validated even in an incomplete residue.

Actor binding has evidentiary meaning:

- `exact` binds a mod/container, code-source digest, mapped method, and
  transformed-class digest;
- `workbench` identifies the capture adapter itself with the same exact code
  binding requirements;
- `ambiguous` retains the candidates without selecting one; and
- `unbound` makes no owner claim.

Ambiguous and unbound actors leave every exact-binding field null. Probe-health
records are emitted as `workbench` actors and are admitted as exact binding
evidence only after the candidate-owned hook tuple and original/transformed
class digests match the raw binding receipt.

A stack sample or namespace resemblance may be displayed as a lead, but it
does not promote `ambiguous` or `unbound` attribution to exact causation.

Nested block-write observations share a `write_chain_id` only for one exact
dimension, position, generating chunk, and target chunk. The target chunk is
derived from the block position, and the record chunk is the generating chunk.
A completed logical chain has exactly one terminal storage observation. API
and storage actors remain distinct evidence; a terminal callee is not silently
promoted to the logical initiator.

## Coverage, budgets, and backpressure

Coverage is never inferred from the absence of records. Each record states
whether its detail is complete, sampled, aggregated, truncated, or unavailable.
The final summary states the bundle-wide coverage and exact dropped count.

When a non-lossless writer reaches a declared budget, it may retain aggregate
counts and append `dropped_detail`. That record identifies the affected record
type, first and last lost producer sequence, exact drop count, and reason.
The capture then remains sampled or truncated and cannot be presented as
lossless. A lossless fixture aborts and publishes incomplete residue rather
than dropping detail and claiming success.

Instrumentation or writer failure is a capture failure, not evidence that the
observed generator succeeded or failed. Generator exceptions are recorded and
remain generator exceptions; a probe must not consume, replace, or reinterpret
them.

## Semantic fingerprints

A checkpoint records the digest of one canonical semantic generated state. A
bundle-level semantic fingerprint binds that checkpoint to:

- its exact comparison scope;
- dimension, chunk, and observed stage;
- a versioned canonicalization policy;
- the included semantic domains; and
- the resulting semantic-state SHA-256.

Semantic canonicalization excludes at least wall-clock time, monotonic time,
thread identity and sequence, Lamport counters, performance metrics, and
runtime object identity. It uses semantic registry/resource identities rather
than treating numeric registry assignments as stable meaning.

The fingerprint ID is content-addressed over the complete fingerprint without
its ID. Equal fingerprints establish equal canonical state only for the
declared domains, canonicalizer, scope, and checkpoint. One fingerprint does
not prove determinism. Determinism requires a comparison of independently
executed, exact-scope runs and preservation of any sampling or coverage
limitations.

## Completion seal and crash residue

A bundle has exactly one publication state.

### Completed

A completed bundle:

- ends after an appended `capture_control` stop record;
- has balanced spans;
- has internally consistent counts and bindings;
- has at least one semantic fingerprint;
- retains all declared limitations;
- includes no crash residue; and
- contains a completion seal over the canonical run manifest, records, and
  semantic fingerprints.

The capture ID is content-addressed over the contract ID, run ID, the three
component digests, record count, and last ordinal. Altering any sealed byte or
semantic limitation invalidates publication.

### Incomplete

An incomplete bundle has no completion seal. It retains a content-addressed
crash residue with the reason, last complete ordinal, exact open spans, and
recoverability state. Reasons include process crash, forced termination, probe
failure, I/O failure, budget exhaustion, fixture abort, and unknown failure.

Incomplete residue is useful diagnostic material. It must never masquerade as
a completed capture, a passing Crucible result, or Atlas evidence closure.

## Safety and authority limits

Captures remain local by default and belong in ignored `.workbench/evidence/`
storage. Disposable worlds belong in `.workbench/fixtures/`. Credentials,
chat, arbitrary player data, and unrestricted tile-entity payloads are outside
this contract.

This contract establishes only what an exact observer retained in one exact
experiment. It does not establish that an unobserved hook was absent, that a
registered generator affected final blocks, that one mod caused a change when
attribution is ambiguous, or that behavior generalizes across profiles,
sides, worlds, seeds, chunks, or capture modes.

## Validator obligations

A conforming validator fails closed on at least:

- unknown or extra closed-schema fields;
- run, scope, or mode mismatch;
- non-contiguous append ordinals or invalid thread-local order;
- forward `happens_after` references;
- terminal spans without enters, duplicate terminals, improper nesting, or
  unreported open spans;
- detached active-span records, nested trace/root changes, or suppressed throw
  identity;
- exact actor attribution without exact code binding;
- non-Workbench probe-health assertions or probe tuples that do not match the
  candidate binding receipt;
- block positions, generation chunks, target chunks, or terminal counts that
  disagree within a write chain;
- lossless mode with any dropped or truncated detail;
- a fingerprint that does not bind an exact checkpoint or excludes required
  semantic nondeterminism fields;
- component, run, fingerprint, residue, or seal digest drift;
- a completed publication without a stop record, balanced spans,
  fingerprints, or completion seal; and
- an incomplete publication containing a completion seal or inconsistent
  crash residue.
