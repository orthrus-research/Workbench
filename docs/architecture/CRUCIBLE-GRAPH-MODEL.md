# Crucible graph model

Status: current public architecture

## Purpose

Crucible turns exact, admitted evidence into deterministic immutable graph
artifacts. It owns capture mechanics, canonical validation, bounded
materialization and shard assembly, dependency-impact and equivalence checks,
and isolated revision-pinned query execution. It does not decide what a game
fact means or whether a proposed source change is allowed.

The retained public core is store-independent. It does not provide a general
graph repository, append-transaction coordinator, publication-proof workflow,
moving-reference store, or index. A caller that persists or publishes graph
artifacts owns those integration mechanics and must preserve the identities
and authority boundaries defined here.

The authority split is fixed:

- Atlas owns observed and derived mechanical meaning, including causal and
  query semantics.
- Blueprints owns construction plans and construction approval.
- An explicitly selected platform or pack profile owns profile-specific
  meaning, support state, adapters, and action policy.
- Crucible owns controlled observation and faithful execution of exact
  authority-owned recipes.
- Workbench Shell and clients orchestrate and present those results without
  creating another authority or approval path.

Supersymmetry is one explicit pack profile. It is never an implicit default in
the product-generic graph model. Historical Forge evidence applies only to its
recorded Forge profile; it is not Cleanroom evidence.

This document summarizes the durable model. The executable contracts and
schemas under [`modules/crucible`](../../modules/crucible/README.md) define the
exact implemented record shapes.

## Invariants

1. Every semantic graph output is derived from exact immutable inputs through
   an exact authority-owned recipe.
2. Evidence, admissions, revisions, and graph objects are immutable. A
   correction creates new records.
3. Full and incremental materialization over the same declared inputs must
   produce identical semantic bytes and identities.
4. Runtime nondeterminism is captured as evidence or modeled semantics. It
   never permits nondeterministic materialization.
5. If an external integration exposes a mutable name such as `current`, it is
   only a convenience reference. It is not evidence, graph identity, support
   status, or authority.
6. Any external indexes and caches are disposable derivations. Losing one
   cannot change graph meaning.
7. Missing, unavailable, unresolved, bounded, truncated, inapplicable, and
   conflicted states remain distinct. None is silently converted to empty.
8. Profile, target, side, runtime, world, dimension, seed, region,
   configuration, and time scopes are explicit. A nearby scope is never used
   as a fallback.
9. Identity-bearing records retained from an earlier format remain byte exact
   and read-only. They are inputs to explicit adapters, not alternate public
   workflows.

These invariants implement
[Decision 0005](../decisions/0005-immutable-evidence-and-materialized-graphs.md)
and use the service boundary from
[Decision 0006](../decisions/0006-embedded-kernel-and-local-service.md).

## Data flow and authority

```text
exact source or runtime artifacts
              |
              v
bounded capture and canonical record validation
              |
              v
caller-supplied evidence, admission, and evidence-set revision
              |
              +---- exact authority-owned recipe
              +---- exact schema, ontology, and profile inputs
              v
bounded materialization + deterministic shard assembly
              |
              v
immutable graph records and object descriptors
              |
              v
caller-supplied revision pins -> isolated owner query port
```

Transport validation is Crucible-owned. It may decode a declared framing,
validate schema and ordering, bind exact capture scope, retain raw bytes, and
classify loss, truncation, completion, or crash residue. It must not infer
aliases, causal relationships, semantic equivalence, category membership, or
global absence.

Semantic normalization is owned by Atlas or by an exact profile within its
declared authority. Crucible may execute that normalization only through the
exact registered recipe. The
[Atlas causal provenance contract](../../modules/atlas/contracts/atlas-causal-provenance-contract-v1.md)
defines the required evidence closure for factual graph records.

## Shared V2 canonical byte domain

Current semantic records use `workbench-canonical-json-v2`. The value domain
is closed to `null`, booleans, Unicode strings, signed 64-bit integers, ordered
arrays, and objects with unique string keys. Floating-point values are not
allowed in identity-bearing records; schemas use canonical decimal strings,
ratios, or integer-plus-unit representations where needed.

Canonical JSON is UTF-8 without a byte-order mark or insignificant whitespace.
Object keys are sorted by unsigned UTF-8 bytes, strings are not Unicode
normalized, integers use minimal decimal form, and schemas define ordering for
set-like arrays. Duplicate keys, unknown fields, malformed Unicode, excessive
nesting, and noncanonical bytes fail validation.

A semantic record identity is calculated from the complete closed record with
only its top-level `id` omitted:

```text
SHA-256(
  UTF8("workbench-content-v2\n") ||
  UTF8(kind) || 0x0a ||
  canonical_json(body_without_id)
)
```

The textual ID is `<kind>:sha256:<lowercase digest>`. The validator always
recomputes it. A content ID proves identity in this byte domain; it does not
prove authority, correctness, completeness, freshness, or support.

Opaque objects retain their raw SHA-256 and byte length. Canonical NDJSON
shards contain already ordered canonical records, one per line. A descriptor
binds the schema, partition, ordering policy, record count, byte length, raw
digest, and key bounds. Compression is transport only; uncompressed canonical
bytes define the object.

Cross-language conformance vectors live in
[`modules/crucible/conformance/canonical-v2`](../../modules/crucible/conformance/canonical-v2/README.md).

## Evidence and admission

Raw artifacts may be retained even when incomplete or invalid. Retention does
not admit them as evidence. Generated captures, worlds, graphs, indexes, jobs,
and service state belong under ignored `.workbench/` storage or another
explicit private store, never in the source tree.

An evidence record binds its exact source bytes, producer and normalizer,
capture environment, applicable context, observation payload, coverage,
completion state, limitations, and provenance. Domain schemas may add closed
payloads but may not weaken common scope and provenance requirements.

Admission is a separate immutable decision over exact evidence. Its outcome
is `admitted`, `rejected`, or `quarantined`, and it binds the validator,
policy, profile inputs, diagnostics, and any prior decision it supersedes.
Only an admitted decision selected by an evidence-set revision is eligible for
ordinary materialization. Revalidation creates a new decision; it never edits
the earlier one or upgrades an incomplete capture to complete. The V2 record
library validates these relationships; it does not provide a persistent append
transaction.

An evidence-set revision pins the exact ledger heads, effective admission
selection, immutable partitions, context, coverage, conflicts, limitations,
and unresolved frontiers used by a build. Materialization pins one revision
before reading; evidence appended later cannot leak into the operation.

The exact record families are defined by the
[immutable graph record contract](../../modules/crucible/contracts/crucible-immutable-graph-record-family-v2.md).

## Graph objects and revisions

The common graph records are:

- `node`: one exact subject at a declared semantic level;
- `edge`: one directed or explicitly symmetric relationship;
- `property`: a typed value that is not part of subject identity;
- `evidence-link`: the closed support for a fact or derivation;
- `refinement`: correspondence between subjects at different resolutions;
- `frontier`: an explicit unresolved, unavailable, bounded, or truncated
  continuation boundary; and
- `conflict`: incompatible applicable evidence or interpretations retained
  without silent selection.

Each record has a content ID. Nodes also have stable subject IDs derived from
their exact identity tuples, allowing joins across revisions without making
mutable properties part of subject identity. Derived records retain source
graph record IDs and transitive evidence reachability.

A graph revision represents one graph family, category, resolution, purpose,
and exact context. Its manifest binds:

- authority owner, recipe owner, materializer, and custodian;
- recipe, implementation, schema, ontology, profile, and adapter identities;
- evidence-set and input graph revision IDs;
- context, target, runtime, world, configuration, and applicable support scope;
- deterministic partition and total-order policies;
- ordered object descriptors and per-kind counts and roots;
- evidence state, coverage, limitations, conflicts, and frontiers; and
- the validation requirements applied before publication.

Partitioning is recipe-defined and identity-bearing. Cross-partition edges are
explicit. Repartitioning is a new recipe and therefore a new revision.

A graph-set revision publishes compatible graph revisions that are intended to
be queried together. It binds the member order, exact context and evidence
roots, category and resolution membership, refinement and join maps,
compatibility decisions, limitations, and aggregate root. The graph set is the
atomic query target; membership alone never implies that independently scoped
graphs are compatible.

## Recipes, categories, and resolutions

A materialization recipe is an immutable semantic contract owned by Atlas or
an exact profile. It declares the complete input kinds and selectors, output
family and schemas, category and resolution rules, ordering and partitioning,
dependency footprints, evidence and conflict propagation, resource bounds,
and deterministic failure behavior. It also binds its exact implementation
and dependency closure. A source path or version label alone is insufficient.

Materialization may read only declared immutable inputs. Clock time, locale,
filesystem enumeration, scheduling, network state, randomness, and mutable
environment state cannot become hidden semantic inputs. External or stochastic
behavior must first be captured and admitted as evidence.

Categories are stored recipe-defined projections, not UI filters. Categories
may overlap, but the recipe must define membership, cross-category edges, and
evidence propagation. Resolutions are declared semantic granularities. Every
lossy aggregation emits refinement mappings back to its source subjects and
preserves conflicts, limitations, and frontiers.

The executable graph layer is deliberately bounded. The V2 materializer runs
one qualified recipe over explicit immutable inputs, while the V2 kernel
assembles deterministic shards, plans conservative dependency impact, and
compares supplied clean and incremental assemblies. These operations return
artifacts and receipts; they do not store or publish them.

## Incremental materialization

A clean assembly defines the result. Incremental work is only an optimization.
Every output partition carries a dependency footprint over evidence,
admissions, source graphs, recipes, schemas, ontologies, profile adapters,
parameters, and cross-partition lookups.

Impact calculation compares exact immutable inputs and closes changes
transitively. Unknown or unmatched changes widen to a safe partition or full
rebuild; under-invalidation is never accepted. Unaffected partitions may be
reused only by exact identity.

An integration that publishes an incremental result must:

1. pin the complete new input closure;
2. calculate and validate the impact set;
3. build changed objects create-new;
4. validate rebuilt and reused objects together;
5. prove byte-for-byte equivalence with the corresponding clean build;
6. persist immutable objects and manifests in dependency order; and
7. expose a convenience reference only after its own atomic publication check.

If equivalence or any required check fails, the candidate result is rejected.
The retained kernel does not mutate a store or reference, so a failed check
cannot make that candidate visible through Crucible itself.

## Queries, indexes, and references

A query receives one caller-supplied graph-set ID, publication identity,
role-keyed graph revision IDs and exact canonical rows for its complete
lifetime. Results bind those identities, the query implementation, parameters,
input closure, inspected rows, and canonical result bytes.

The query kernel validates and isolates the supplied rows and immutable object
bytes. It does not resolve a moving reference, fetch a publication proof,
validate a graph repository, or consult an index. The caller must validate the
relationship among its graph-set, graph-revision, and publication identities
before invocation.

The bounded read boundary is specified by the
[revision-pinned query contract](../../modules/crucible/contracts/crucible-revision-pinned-query-kernel-v2.md).

## Runtime and worldgen semantics

Runtime determinism and graph-build determinism are separate. World generation
may be seeded, order-sensitive, contextual, probabilistic, externally
scheduled, or unresolved. The graph build is still deterministic over its
exact evidence and profile inputs.

Worldgen uses three authority-preserving layers:

- Crucible produces faithful capture-occurrence and stability structure;
- Atlas owns generative rule interpretation; and
- Atlas owns realized-world interpretation over exact observed instances.

The selected profile owns specialized predicates and applicability. Joins
require exact rule, actor, configuration, profile, runtime/world epoch, and
occurrence identities. A final block match or repeated same-seed result alone
is not causal proof. See the
[Worldgen Observatory architecture](WORLDGEN-OBSERVATORY.md).

## Integration and failure behavior

The retained core creates and validates immutable values but does not make
them visible through a repository or moving reference. A persistence layer may
store content-addressed objects create-new, but publication ordering, atomic
visibility, crash recovery, and reference updates remain that integration's
responsibility.

Failures are explicit:

| Condition | Result |
| --- | --- |
| Malformed artifact or identity mismatch | retain or quarantine exact bytes; do not admit |
| Missing recipe, schema, profile, ontology, or object | fail unavailable; never borrow another version |
| Materialization crash or validation failure | return no accepted candidate artifact |
| Full/incremental mismatch | reject the incremental candidate; do not replace any accepted artifact |
| Conflicting evidence | retain all sides and emit a conflict |
| Missing evidence | preserve its exact unavailable, unresolved, bounded, or truncated state |
| Corrupt supplied object, descriptor, record, or manifest | reject the supplied closure |
| Protected action with insufficient profile support | fail the action gate without hiding the graph |

## Public entry points and verification

Discover current Crucible-facing product actions through the generated catalog:

```bash
workbench capabilities crucible
```

The module overview lists active runtime, storage, snapshot, Mixin, and
worldgen commands. For repository verification, run:

```bash
python3 -m unittest discover -s modules/crucible/tests -p 'test_*.py'
```

The current contracts most directly governing graph mechanics are:

- [ContextRef and InputBinding V2](../../modules/crucible/contracts/crucible-context-and-input-binding-v2.md)
- [immutable graph records V2](../../modules/crucible/contracts/crucible-immutable-graph-record-family-v2.md)
- [bounded computed graph materializer V2](../../modules/crucible/contracts/crucible-bounded-computed-graph-materializer-v2.md)
- [deterministic graph shard kernel V2](../../modules/crucible/contracts/crucible-deterministic-graph-shard-kernel-v2.md)
- [revision-pinned query kernel V2](../../modules/crucible/contracts/crucible-revision-pinned-query-kernel-v2.md)
