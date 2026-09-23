# Manual implementation-guide contract v1

Status: active semantic contract with focused fixture validation

Contract ID: `MANUALS-IMPLEMENTATION-GUIDE-V1`

Requirement policy: [requirement-policy-v1.json](requirement-policy-v1.json)

Contract examples: [contract-examples-v1.json](../examples/contract-examples-v1.json)

## Purpose

This contract defines the canonical machine-readable meaning of a Manual:
an evidence-backed recipe for implementing one exact feature family under one
version and authority scope.

It answers how to reproduce an intended feature without treating one source
example, runtime observation, convention, or generated explanation as a
universal requirement.

This contract does not itself publish a material, fluid, item, block, recipe,
or machine guide. Its examples are synthetic contract fixtures. They exercise
meaning and invalid states but establish no implementation-family truth.

## Authority boundary

A Manual retains the evidence basis used by Atlas and adds an
`authority_role` describing what that exact record can support in this
contract. Roles do not upgrade their underlying basis.

In particular:

- pinned source occurrence is not automatically an API contract or enforced
  lifecycle rule;
- runtime presence proves an observation in the named scope, not how to
  reproduce it;
- presentation presence is not execution authority;
- placed-world observation describes one fixture unless a closed-world rule
  establishes broader meaning;
- an example remains `example-only` unless separate normative evidence
  supports a requirement; and
- a derivation or explanation cannot manufacture mechanical authority.

The exact role-to-basis admissions and requirement thresholds are defined by
`MANUALS-REQUIREMENT-POLICY-V1`.

## Canonical JSON and identity

`manual-canonical-json-v1` encodes JSON as UTF-8 with object keys sorted by
Unicode code point, compact separators, JSON literals, finite numbers, and no
trailing newline. Arrays retain their declared semantic order. Strings are not
case-folded or Unicode-normalized.

SHA-256 digests use lowercase hexadecimal.

### Request identity

A request has:

- `format`: `susy-manual-request-v1`;
- `schema_version`: `1`;
- the contract and requirement-policy IDs;
- `request_id`;
- an exact `implementation_family` selector;
- a structured intended `outcome`;
- a `version_scope`;
- one or more profile/physical-side scopes where runtime or presentation
  observation is requested; and
- requested evidence authorities.

`request_id` is:

```text
manual-request:sha256:<digest>
```

The digest is over the canonical request object with only `request_id`
removed. Description wording is identity-bearing; changing the requested
outcome, family selector, version, profile, side, or authority changes the ID.

An omitted authority is not the same as an unavailable authority. The request
must name the required and optional bases separately.

### Manual identity

A family result has:

- `format`: `susy-manual-family-v1`;
- `schema_version`: `1`;
- contract and policy IDs;
- `manual_id`;
- the complete request;
- exact family identity and title;
- result status;
- prerequisites;
- ordered steps;
- expected observations;
- examples;
- validation checks;
- known unknowns; and
- a complete evidence array.

`manual_id` is:

```text
manual:sha256:<digest>
```

The digest is over the canonical family result with only `manual_id`
removed. Any semantic or evidence change creates a different Manual
identity.

## Version and scope

`version_scope` names exact applicable versions and revisions. For the current
product family it carries Minecraft and Forge versions, pack version and
commit, and exact source-set and runtime-snapshot identities when those
authorities are used.

A missing runtime snapshot is represented explicitly, not as an empty
observation set. A source-only Manual may leave runtime identity
unavailable, but any expected runtime result then remains unobserved or
unresolved.

Every evidence binding carries the scope appropriate to its basis:

- pinned source: repository/source identity, revision, path or symbol/span;
- runtime mechanics or presentation: snapshot, profile, and physical side;
- placed-world observation: snapshot, world/fixture, dimension, and location;
- quest data: exact quest-data revision and node or edge identity; and
- curated derivation or explanation: policy and exact input evidence IDs.

## Ordered implementation steps

`steps` is ordered by a contiguous zero-based `ordinal`. Source enumeration or
object-map order never supplies step order implicitly.

Each step contains:

- `step_id`;
- `ordinal`;
- stable `action`;
- lifecycle stage and physical side;
- exact inputs and produced artifacts;
- zero or more requirement statements;
- supporting evidence IDs;
- omission/failure semantics; and
- a step status.

`step_id` is `manual-step:sha256:<digest>` over the canonical step object
with only `step_id` removed. The object retains `ordinal`.

An input or artifact is an exact typed reference, not prose. A later step may
refer to an earlier produced artifact by exact artifact ID. All references
must resolve, and dependency edges must point to lower ordinals. A lifecycle
stage name is version-bound; similarly named hooks on different loaders or
physical sides are not interchangeable.

Step status is one of:

- `specified`: its meaning and cited support close in the named scope;
- `partial`: some required field or support remains bounded and is named;
- `unavailable`: a required authority artifact is unavailable;
- `unresolved`: available authorities do not close one exact meaning; or
- `not-applicable`: a positive family or variant rule excludes the step.

## Requirement statements

Requirements belong to steps. A step may have multiple requirements because
compile, runtime, identity, presentation, and integration obligations are
different claims.

Each statement contains:

- `requirement_id`;
- one policy `classification`;
- `modal`;
- exact `statement`;
- `scope`;
- `condition`;
- `support_evidence_ids`; and
- `omission`.

`requirement_id` is `manual-requirement:sha256:<digest>` over the canonical
object `{"step_ordinal": <ordinal>, "requirement": <requirement>}`, where the
nested requirement has only `requirement_id` removed.

The eight classifications are:

- `compile-required`: needed for the implementation to compile or link against
  the pinned API;
- `runtime-required`: needed for registration, lifecycle completion, or
  executable runtime behavior;
- `identity-required`: needed for unique, stable, or collision-free identity;
- `presentation-required`: needed for the explicitly requested client-facing
  presentation outcome;
- `integration-required`: needed for the explicitly requested interoperability
  or downstream feature;
- `conditional`: required only when its exact predicate holds;
- `conventional`: an evidence-backed ecosystem practice that is not enforced;
  and
- `optional`: an evidence-backed extension whose omission preserves the
  defined core outcome.

The first five use modal `must`. `conditional` uses `must-when` and supplies a
nonempty condition with an exact `predicate_id`, human statement, typed
inputs, and `when_true_class` drawn from the first five classes.
`conventional` uses `should`; `optional` uses `may`. No other modal/class pair
is valid.

The policy’s `support_any` field is an OR of role combinations; every role in
one chosen combination must be represented among the cited evidence.
Role admission is necessary but not sufficient: evidence subject, version,
lifecycle, physical side, profile, and closed scope must match the exact
statement. Evidence outside the admitted combinations may enrich a statement
but cannot authorize its modal.

## Omission and negative claims

Every requirement carries an `omission` object with:

- `status`;
- scoped `outcome`;
- exact evidence IDs; and
- `closed_scope`.

Omission status is:

- `proven`: closed source or controlled omission proof establishes the result;
- `observed`: a named controlled fixture observed the result;
- `expected`: normative enforcement supports an expectation not independently
  observed;
- `unknown`: available evidence does not close the outcome; or
- `not-applicable`: a positive rule excludes omission analysis.

`proven` and `observed` require `closed_scope: true` and supporting
`omission-proof` evidence. `expected` requires normative source evidence and
must not be worded as an observation. `unknown` is the mandatory state when
the available evidence cannot authorize a negative statement.

Absence from a source search, runtime graph, client presentation, or one world
fixture does not prove impossibility outside the explicitly closed scope.

## Evidence bindings

Every top-level evidence binding has:

- exact `id`;
- shared `basis`;
- source `authority`;
- policy `authority_role`;
- `record_kind`;
- exact scope;
- complete immutable `record`;
- `record_sha256`; and
- optional `derived_from` evidence IDs.

`record_sha256` is SHA-256 of the canonical `record` object. Evidence IDs are
unique. Every cited ID resolves exactly once, every derivation input resolves,
and every top-level evidence record is used. An `unresolved` label describes a
result state and never satisfies a requirement threshold.

## Examples

An example binds one exact implementation and its evidence. It carries an
`example_id`, exact family/version binding, relevant step IDs, evidence IDs,
and `normative: false`.

An example may illustrate an already supported requirement. It cannot be the
sole reason a requirement uses `must`, `must-when`, or `may`. Repetition or
frequency does not promote examples into contracts.

Synthetic fixtures use `fixture: true`. Their roles exercise the contract
shape only and cannot be published as family evidence.

## Expected observations and validation

Expected observations state what a correctly implemented family should expose
under exact source, runtime, presentation, integration, or world scopes. Each
observation has a stable ID, basis, scope, expected status, typed selector,
supporting evidence IDs, and verification method.

An expected observation is not an observed result. A validation record must
report one of `pass`, `fail`, `unavailable`, `not-observed`, `ambiguous`,
`unresolved`, or `truncated` and cite the exact evidence used.

The family result status is:

- `resolved`: all outcome-defining requirements and validations close;
- `partial`: a usable bounded result retains named gaps;
- `unavailable`: a required authority artifact is unavailable;
- `unresolved`: available authorities cannot select one exact family or
  meaning; or
- `invalid`: the document violates the contract.

## Known unknowns

`known_unknowns` preserves each unavailable, not-observed, ambiguous,
unresolved, or truncated question with its scope, affected IDs, evidence IDs,
and consequence. Unknowns are data, not prose footnotes.

A family result may be `partial` with unknowns. It may not be `resolved` while
an unknown affects an outcome-defining requirement or validation.

## Deterministic guide projection

Canonical JSON is authoritative. A guide renderer conforming to this contract
must:

- preserve family, version, profile, and side scope;
- preserve step order and exact IDs;
- project modal language only from the policy-admitted classification;
- distinguish expected from observed results;
- retain evidence and unknown boundaries; and
- produce identical Markdown from identical canonical input.

The renderer may improve navigation and explanation. It may not change
requirements or conceal unavailable evidence.

## Contract validation boundary

The focused policy and contract-shaped fixtures:

- exercise every requirement class;
- recompute request, Manual, step, requirement, and evidence digests;
- reject unsupported authority promotion;
- reject a conditional without a predicate;
- reject example-only evidence as normative support;
- reject disconnected evidence; and
- reject a closed-world negative without omission proof.

Contract examples are not JSON-Schema validation and are not accepted
implementation guides. They validate the semantic policy boundary only.
