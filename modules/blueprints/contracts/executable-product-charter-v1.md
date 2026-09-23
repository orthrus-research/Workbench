# Blueprints executable product charter v1

Status: current product contract

Charter ID: `BLUEPRINTS-EXECUTABLE-PRODUCT-CHARTER-V1`

## Objective

Blueprints is a program a developer runs to construct the approved
implementation of a requested feature for an explicitly selected profile.

The developer supplies parameters. Blueprints:

1. binds the exact target revision and worktree state;
2. compiles all intake forms into one canonical request;
3. selects and composes admitted implementation standards;
4. derives or allocates permitted parameters;
5. checks Atlas evidence and target drift;
6. synthesizes a sealed candidate in isolation;
7. simulates every universal and standard-required gate;
8. releases exact code and placement only after all required gates pass;
9. optionally applies the approved diff atomically;
10. verifies the resulting target state; and
11. retains a local proof history that may be exported.

“Best” means conforming to the manually authored Supersymmetry standard for
the exact feature family and variant. Frequency, source similarity, examples,
AI output, and community convention do not define the standard.

## Non-goals

Blueprints does not:

- use AI for intake, planning, selection, synthesis, explanation, or review;
- infer or promote standards from existing implementations;
- produce code when no admitted standard applies;
- release partially validated or best-effort candidates;
- modify an unauthorized repository or integration surface;
- patch upstream dependencies unless an admitted standard explicitly permits
  that repository;
- overwrite local worktree changes;
- use a developer’s live Minecraft world;
- treat a Manual as executable authority; or
- silently migrate a generated implementation to a newer standard.

## Core records

### Request

Interactive questions, CLI parameters, and JSON all compile into the same
versioned canonical request. Standards classify parameters as:

- `required`;
- `derived`;
- `allocated`;
- `defaulted`; or
- `optional`.

Derived, allocated, and defaulted values appear in the plan preview.

### Standard

An admitted standard is a structurally valid YAML file in the caller-selected,
profile-owned registry root. Placement in that selected root is the approval
act; Blueprints has no central built-in registry or secondary approval
workflow.

Authoring YAML compiles into canonical JSON for identity, validation, and
execution. Each immutable standard version declares:

- feature family and mandatory core template;
- approved variants;
- maintainer priority and specificity;
- required, derived, allocated, defaulted, and optional parameters;
- permitted target repositories and integration surfaces;
- deterministic templates and formatters;
- component standards, compatibility, and precedence rules;
- critical Atlas baseline and drift invariants;
- universal-baseline additions;
- compilation, runtime, presentation, integration, and world gates;
- test and disposable-fixture definitions;
- explanation and diagnostic templates;
- idempotent reconciliation and migration rules; and
- checked-in executable hooks where declarative behavior is insufficient.

Checked-in hooks are trusted through repository admission. They are restricted
to selected repositories, isolated build directories, and the approved
dependency cache.

### Plan

A plan binds:

- request and standard versions;
- selected primary and component variants;
- exact target revision and worktree digest;
- all effective parameters and allocations;
- authorized paths;
- critical Atlas evidence and drift classification;
- candidate file operations;
- validation stages; and
- reasons for every deterministic selection.

When approved variants remain tied after maintainer priority and specificity,
planning stops for explicit developer selection.

### Candidate

The engine may render code internally to simulate it, but the candidate stays
sealed. It is not developer output and cannot be applied.

A developer may edit a plan or sealed candidate through the supported
workflow. The edit becomes a new candidate and invalidates every downstream
simulation, released patch, application, verification, and proof record.

### Released implementation

Code becomes releasable only when every universal and selected-standard gate
passes. A successful release contains:

- all required source;
- registration and configuration changes;
- assets and localization;
- migrations where required;
- standard-provided feature tests;
- engine-generated invariant, collision, determinism, and placement tests;
- developer documentation;
- exact file-placement metadata; and
- a machine-readable proof manifest in local run history.

### Proof

Proof stays in `.workbench/blueprints/` and is not committed into target
repositories. Developers may export a portable bundle containing the compact
manifest and permitted non-private evidence.

Local history uses content-addressed deduplication. Compact manifests remain
by default; bulky builds, logs, worlds, and caches follow configurable
retention.

## Standard selection and composition

Standards are manually authored. Existing pack and first-party source may be
inspected for conformance, conflicts, allocation systems, or examples, but it
cannot create a standard.

When a request has no admitted standard or variant, Blueprints fails closed
and releases no code.

A primary standard composes approved component standards. Selection uses:

1. admitted compatibility;
2. maintainer-defined priority;
3. specificity to the canonical request; and
4. explicit developer choice only for a remaining tie.

Explicit compatibility and precedence rules are the only conflict resolution.
An undeclared composition conflict blocks generation.

## Atlas and drift

Each standard pins its accepted Atlas evidence baseline and declares critical
invariants. Every run queries the current Atlas target state.

- Relevant invariant drift blocks generation.
- Non-impacting drift is reported separately.
- Atlas supplies observed truth but never approves a standard.
- Missing Atlas authority is `unavailable`, not an empty target state.

Before defining a new allocation ledger, Atlas must locate and model any
identity-allocation mechanism already present in Supersymmetry. Standards use
existing authoritative allocation systems where available. Otherwise they
declare allocation pools backed by one deterministic reservation ledger that
never silently reuses identities.

## Parameters and reconciliation

When parameters violate a standard, Blueprints explains every conflict and
may offer a compliant revision. It continues only after explicit acceptance.
Standards cannot be bypassed.

When an equivalent identity or feature already exists, the standard’s
idempotent reconciliation rules determine whether Blueprints:

- reports exact equivalence;
- offers an approved update;
- requests a different identity; or
- rejects an ambiguous or incompatible collision.

## Target and application safety

Every run binds an explicit repository revision plus the complete current
worktree state. Local modifications participate in simulation and are
protected from silent overwrite.

Blueprints supports three output modes selected by an execution parameter:

- placement instructions and exact code for manual application;
- a complete reviewable patch bundle; and
- direct worktree application.

All modes use the same released implementation. Long-form teaching remains a
Manuals concern.

Direct application:

1. validates in an isolated worktree;
2. previews the exact released diff;
3. rechecks the target revision and worktree digest;
4. applies atomically only to authorized paths; and
5. aborts without partial writes when the target changed or application
   failed.

## Simulation pipeline

Simulation is staged:

1. static Atlas and source compatibility;
2. standard, composition, parameter, path, and allocation validation;
3. deterministic candidate regeneration;
4. pinned repository formatters;
5. isolated compilation;
6. existing and generated tests;
7. runtime registration and behavior where required;
8. client presentation or integration where required; and
9. formed-world testing where required.

Every implementation must pass the universal safety baseline:

- schema and standard validity;
- authorized-path enforcement;
- identity and allocation collision checks;
- byte-identical deterministic regeneration;
- target-state consistency;
- pinned formatter stability;
- isolated compilation;
- the target’s existing test suite; and
- generated invariant, collision, determinism, and placement tests.

Standards add stricter gates. Runtime and formed-world checks use
standard-defined disposable fixtures built from the exact target and candidate,
never a developer’s world.

The isolated environment may acquire only dependencies admitted by its
environment lock. Blueprints verifies exact dependency identities and caches
them content-addressably.

## Code style and explanations

Approved templates generate code. The target repository’s pinned formatters
then run, and repeat output must be byte-identical.

Human-readable plans, rationales, and errors come from maintainer-authored
standard templates plus deterministic universal engine messages. There is no
AI-generated explanation.

Failure output is a structured causal report showing:

- failed standards and invariants;
- conflicting parameters;
- affected source and target locations;
- blocked simulation stages;
- relevant evidence;
- the minimal known conflict boundary; and
- compliant revisions available for acceptance.

Failure output contains no releasable code.

## Standard evolution

Generated implementations and proofs remain pinned to the exact standard
version used.

When a newer standard exists, Blueprints reports conformance differences and
may offer an explicit migration defined by admitted migration rules. It never
silently rewrites existing implementations.

## CLI and implementation stack

The first implementation is a deterministic Python core and CLI that reuses
Atlas. Pinned Java/Gradle adapters provide compilation and Minecraft fixture
execution. Later Gradle and graphical adapters call the same core behavior.

The CLI lifecycle is:

- `init`;
- `plan`;
- `simulate`;
- `generate`;
- `apply`;
- `verify`;
- `history`; and
- `export-proof`.

`generate` releases an already simulated sealed candidate; it does not create
unvalidated code.

## Profile-selected standards

The module ships no implicit standard. Each profile owns its registry,
templates, evidence bindings, and allocation ledger. A standard may use the
complete CLI lifecycle only when its declared gates genuinely qualify that
path; planning-only or compile-only output is not a releasable implementation.

## Manuals boundary

[Manuals](../../manuals/README.md) may explain admitted standards and successful
Blueprint behavior. Manuals is not an input to standard selection, generation,
simulation, or validation and cannot waive an executable gate.
