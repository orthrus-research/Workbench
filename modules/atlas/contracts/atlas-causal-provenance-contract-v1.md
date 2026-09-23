# Atlas source-to-runtime causal provenance contract v1

Status: active

This contract defines how Atlas may connect a revision-bound source fact to one
exact final runtime record. It preserves declarations, configuration,
registration, lifecycle execution, copying, mutation, removal, replacement,
profile reconciliation, and final observation as separate evidence-typed
steps. A namespace, package name, call site, similar row, or final-state
coexistence never substitutes for a causal transition.

Contract artifacts:

- [Provenance-request schema v1](../schemas/atlas-provenance-request-v1.schema.json)
- [Provenance-result schema v1](../schemas/atlas-provenance-result-v1.schema.json)
- [Provenance-policy schema v1](../schemas/atlas-provenance-policy-v1.schema.json)
- [Causal provenance policy v1](../data/atlas-provenance-policy-v1.json)
- [Machine-checked examples v1](../examples/atlas-provenance-contract-examples-v1.json)
- [`atlas_causal_provenance_contract.py`](../src/workbench_atlas/atlas_causal_provenance_contract.py)

The policy object is part of request identity. A stable policy ID cannot change
node classes, relation direction, causal strengths, lifecycle order, closure,
negative-statement authority, or pagination semantics beneath an existing
request.

## Exact request and result identity

A provenance request binds:

- one accepted M1 `query_instance_id`;
- one snapshot and exact final profile/physical-side scope;
- zero or more explicitly admitted supporting scopes for exact
  `reconciles_to` records;
- one final runtime node ID, normalized kind, complete record SHA-256, and
  immutable capture-evidence ID;
- one source lock;
- the complete provenance-policy digest; and
- global path, node, relation, and evidence-record bounds.

`request_id` is
`atlas-provenance-request:sha256:<lowercase-hex>`. The digest is SHA-256 over
the canonical JSON bytes for the complete request except `request_id`.
Canonical JSON uses sorted object keys, UTF-8, no insignificant whitespace,
and no trailing newline.

`result_id` uses the same rule and the
`atlas-provenance-result:sha256:` prefix over the complete result except
`result_id`. Nodes, relations, paths, evidence records, and permitted negative
statements are independently content-addressed under their declared prefixes.
Every ID-bearing child therefore fails if a caller changes its evidence,
strength, lifecycle stage, endpoint, wording, or scope and retains an old ID.

Supporting scopes do not widen the final claim. They only admit exact
cross-profile identity records needed to reach the request's final scope. A
node from any other snapshot or scope is rejected.

## Exact source-span identity

A `source-span` identity retains:

- `source_lock_id`, `source_id`, canonical repository, revision, and tree;
- repository-relative path and exact symbol;
- one-based inclusive line start and end;
- zero-based inclusive byte start and end;
- complete file SHA-256 and exact span-byte SHA-256; and
- the versioned extraction-policy ID.

The digest is over the exact inclusive byte interval
`bytes[byte_start:byte_end + 1]`. Line numbers and symbols are navigation
metadata inside the identity; they cannot compensate for a byte, file,
revision, tree, or policy mismatch. `SRC-PACK` and upstream sources resolve
through the single [active source lock](../../../profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json), including its locked pack revision and tree.
The existing
[infrastructure source-authority registry](../data/infrastructure-source-authorities-v1.json)
may be reused as citation input, but its line-level citation alone is not an M4
span until P01 supplies the missing tree, byte interval, span digest, and
extraction-policy binding.

Static occurrence is intentionally weaker than execution. A source span can
prove that code or a script text exists in one revision. It does not prove that
the selected configuration admitted it, that Groovy compiled or ran it, that a
call site was reached, or that its effect survived later mutation.

## Node classes

| Node class | Exact role |
| --- | --- |
| `source-span` | Revision-, tree-, file-, symbol-, line-, byte-, and extraction-policy-bound source bytes |
| `declared-object` | A stable semantic declaration derived from exact source bytes, before runtime admission |
| `configuration-entry` | An exact selected configuration key/value bound to its source span |
| `script-operation` | One ordered operation in one evidenced stage execution |
| `lifecycle-event` | One evidenced occurrence and ordinal in the versioned lifecycle model |
| `runtime-state` | An immutable intermediate, after, tombstone, or reconciled runtime state |
| `final-runtime-record` | The request-bound normalized final record and its capture evidence |
| `explanation` | Player or developer wording derived after validation, never mechanical evidence |

Class-specific `identity` objects have exact fields. A script operation retains
its source span, stage-execution identity, operation index, and operation
digest. A configuration entry retains its source span, exact key path, and
selected-value digest. Intermediate and final runtime identities retain exact
normalized node IDs, state or record digests, and capture-evidence IDs.

## Relation direction and causal strength

Every relation points from an earlier input, actor, or derivation input to its
result. Reversing an edge changes its meaning and ID.

| Predicate | Meaning | Permitted strength |
| --- | --- | --- |
| `declares` | Exact source bytes define a semantic object | `ownership` |
| `registers` | A declaration, operation, or lifecycle event contributes or causes a runtime registration | `participation`, `causation` |
| `loads_configuration` | An exact selected value is consumed at a lifecycle boundary | `participation`, `causation` |
| `invokes` | A call site, operation, or lifecycle event reaches another operation or event | `possibility`, `participation`, `causation` |
| `copies` | A declared or runtime predecessor is copied into a separately identified runtime state | `transformation`, `causation` |
| `transforms` | A declared or runtime predecessor produces a non-identity after-state | `transformation`, `causation` |
| `mutates` | An exact runtime before-state produces an exact changed after-state | `transformation`, `causation` |
| `removes` | An exact runtime predecessor produces an exact tombstone or post-removal state | `transformation`, `causation` |
| `replaces` | An exact runtime predecessor produces an exact installed successor | `transformation`, `causation` |
| `reconciles_to` | Exact typed identity connects accepted records across profiles | `identity` |
| `observed_as_final` | An exact runtime state is present as the selected immutable final record | `observation` |
| `derives_explanation` | Validated facts produce audience wording | `derivation` |

The complete policy additionally fixes permitted endpoint classes, required
evidence record kinds for every permitted strength, lifecycle requirements,
closure roles, and precise meanings. Attaching an unrelated evidence record
cannot promote a relation.

The strength words are not a confidence ladder:

- `ownership` identifies what exact source declares; it does not prove runtime
  presence.
- `possibility` represents static reachability only and can never close a
  causal path.
- `participation` establishes that an evidenced input took part but does not
  make it a sufficient cause.
- `transformation` requires exact predecessor and after-state identity.
- `causation` requires accepted execution plus exact transition endpoints.
- `identity`, `observation`, and `derivation` reconcile, terminate, or present
  a path; they do not create source ownership.

Structural similarity cannot produce `copies`, `transforms`, `mutates`,
`removes`, or `replaces`. Namespace, mod ID, package, class name, translation,
display text, recipe-map membership, and adjacent ordering cannot promote a
relation to ownership or causation. Mutation, removal, and replacement
operations belong in bound transition evidence; they cannot replace the exact
before-state endpoint.

## Lifecycle model

The lifecycle policy is a profile-scoped partial order, not a single global
integer. It records these verified Forge/Cleanroom/Supersymmetry boundaries
from the hash-bound predecessor snapshot in the repository's
[migration manifest](../../../MIGRATION-MANIFEST.json):

1. mod construction;
2. Forge `NewRegistry`;
3. Groovy `preInit`;
4. mod pre-init;
5. ordinary Forge registry events;
6. Forge initialization;
7. the Groovy `init` hook;
8. mod post-init;
9. Groovy `postInit`;
10. load complete and registry freeze;
11. optional later server startup; and
12. final observation.

Offline artifacts use `offline-artifact-load` followed by final observation
and are never described as having passed through Forge. `static-source` is a
named non-execution boundary and cannot by itself establish a lifecycle
occurrence.

The model orders stage kinds. Every emitted lifecycle event or transition must
also carry occurrence-specific evidence. In particular:

- configured root and lexicographic file order are retained separately from
  Forge stage order;
- an empty pack `init` root is not an execution occurrence;
- one observed mod order is not generalized when dependencies or priorities do
  not guarantee it;
- process readiness is not evidence that every Groovy operation completed;
  and
- reload paths require their own evidenced stage occurrences rather than reuse
  of startup events.

A path whose evidenced lifecycle stages reverse or cross an inapplicable
profile boundary is invalid.

## Evidence closure

Every node and relation cites one or more immutable evidence records. Each
record carries its authority, record kind, complete record object, recomputable
record SHA-256, and a content-addressed evidence ID. The result contains every
referenced record exactly once and rejects unused evidence.

A source-span evidence record contains the complete source-span identity, not
only a line citation. Script-operation and lifecycle-event nodes require
runtime stage-execution evidence bound to the exact class and identity digest.
When a relation relies on that stage evidence, the stage and the executed
operation or event endpoint must agree, and the evidence carries the exact
identity digests of both relation endpoints. Merely proving that the enclosing
Groovy or Forge stage ran does not prove that a particular operation ran or
that it affected a particular runtime object.

The v1 authorities are:

- `pinned-source` for source-lock-, revision-, tree-, and byte-bound records;
- `runtime-mechanics` for accepted lifecycle, transition, reconciliation, and
  final-capture records; and
- `derived-validation` for a validator result that makes no new mechanical
  claim.

An edge may cite multiple authorities. Evidence membership alone does not
raise causal strength; the predicate policy and exact endpoints still govern.
Every result must contain exactly one `final-runtime-record` node equal to the
request endpoint.

A closed path:

- starts at an exact `source-span` or `configuration-entry`;
- is edge-connected with no repeated node or relation;
- follows a valid lifecycle order;
- contains at least one `transformation` or `causation` edge;
- contains no `possibility` edge; and
- ends through `observed_as_final` at the request-bound final record.

`declares` plus `observed_as_final` is not a closed causal path. A source call
site plus a similar final row is not a closed causal path. A profile
`reconciles_to` edge preserves identity but cannot manufacture a causal
transition.

`derives_explanation` is retained outside causal paths. Its endpoints and
`causal-validation` evidence must still resolve and remain fully used, but it
cannot alter closure or supply a mechanical transition.

## Result status and closure

Status equals the machine-derived closure state:

- `closed`: at least one closed path exists, no frontier or closure reason
  remains, and nothing is truncated;
- `partial`: retained evidence-typed paths have named open frontier nodes and
  reasons, without a request-bound truncation;
- `bounded`: at least one global request bound stopped traversal, the frontier
  and minimum omitted count remain explicit, and emitted paths retain their own
  validity;
- `unresolved`: available records do not establish a closed path and the exact
  failure reason remains visible; and
- `unavailable`: a required authority artifact is unavailable, without a
  negative game claim.

Reason codes distinguish dynamic script analysis, missing source spans,
unobserved transitions, unresolved lifecycle order, unresolved identity
reconciliation, unavailable evidence, and each global bound.

V1 uses one bounded result and no pagination cursor. The request fixes global
counts for paths, nodes, relations, and evidence records. A bounded result
reports emitted counts, the bound reached, and `omitted_at_least >= 1`.
Truncation never means completion. Resumable provenance traversal is not
defined by C01 and cannot reuse an M2 continuation token without a later
versioned contract.

## Permitted negative statements

Free-form negative claims are forbidden. A result may emit only the exact
policy-bound wording and authorities for:

- `not-found-in-closed-source-scope`: requires a complete revision-bound source
  index under a named extraction policy and says nothing outside that scope;
- `not-observed-in-final-capture`: requires exact final-capture absence
  evidence and says nothing globally or across profiles;
- `removed-before-final`: requires a closed path containing an exact removal
  transition; and
- `no-closed-causal-path`: reports only what this exact result and its bounds
  failed to establish.

`unavailable` never means absent. A missing source index cannot support
`not-found`. A final capture without a matching record cannot prove that a
script never created and later removed it. Namespace mismatch cannot prove
non-ownership. Similarity cannot prove non-copying or copying.

## Example semantics

The examples are production-shaped contract fixtures, not accepted production
evidence. They use the diluted-oil pilot vocabulary to exercise:

- one exact M1-bound request;
- `closed`, `partial`, `bounded`, `unresolved`, and `unavailable` results;
- immutable source, stage, final-runtime, and derived-validation evidence;
- a policy-bound `no-closed-causal-path` statement; and
- mutations of request identity, source bytes, evidence bytes, causal strength,
  lifecycle data, endpoints, path connectivity, aggregate status, negative
  wording, truncation, and byte-range validity.

The validator recomputes every identity, verifies exact evidence use and path
connectivity, checks lifecycle compatibility and global bounds, and rejects all
declared invalid mutations for their expected fail-closed reason.

## Compatibility and package boundary

This contract does not change Atlas V1, M1 query defaults, M2 continuation,
runtime graph normalization, or bridge output. It only supplies the versioned
M4 vocabulary and validators consumed by later packages.

C01 deliberately does not:

- hydrate or index source repositories;
- parse Groovy operations or configuration files;
- capture runtime execution;
- add runtime graph node kinds or predicates;
- query causal paths;
- generate player/developer bridge projections; or
- claim that the contract examples occurred in production.

P01 must verify source bytes and build exact symbol/span indexes. P02 must
extract pack operations while retaining dynamic unresolved boundaries. N01
owns shared runtime normalization. P03 owns graph traversal and final causal
closure over captured evidence.
