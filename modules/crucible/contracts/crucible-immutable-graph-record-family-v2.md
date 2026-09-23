# Crucible immutable graph record family V2

Status: accepted executable immutable-graph contract and conformance slice.
Canonical encoding, closed record validation, relational publication
validation, retained fixtures, and the synthetic clean-rebuild proof are
admitted. No persistent store, materializer, service, profile admission, or
product runtime capability is admitted by this slice.

## Purpose

This contract defines the smallest common V2 record family needed to admit
immutable evidence and publish one deterministic categorical graph. It is the
contract slice authorized by the accepted
[Crucible graph model](../../../docs/architecture/CRUCIBLE-GRAPH-MODEL.md).
Domain contracts may add new versioned record types, but they may not weaken
the identities, authority boundaries, provenance closure, or failure behavior
defined here.

The structural schemas are:

- [shared definitions](../schemas/crucible-v2-common.schema.json);
- [immutable object descriptor](../schemas/crucible-object-descriptor-v2.schema.json);
- [evidence record](../schemas/crucible-evidence-record-v2.schema.json);
- [admission record](../schemas/crucible-admission-record-v2.schema.json);
- [immutable ledger entry](../schemas/crucible-ledger-entry-v2.schema.json);
- [evidence-set revision](../schemas/crucible-evidence-set-revision-v2.schema.json);
- [graph record](../schemas/crucible-graph-record-v2.schema.json);
- [graph revision](../schemas/crucible-graph-revision-v2.schema.json);
- [graph-set revision](../schemas/crucible-graph-set-revision-v2.schema.json);
- [materialization recipe](../schemas/crucible-materialization-recipe-v2.schema.json);
- [dependency manifest](../schemas/crucible-dependency-manifest-v2.schema.json);
  and
- [reference event](../schemas/crucible-reference-event-v2.schema.json).

All schemas use JSON Schema Draft 2020-12. The shared schema is a definition
library, not an instance record: its root accepts only the empty object. A
validator must register every `workbench://` schema locally and must never
retrieve an unknown schema over a network.

## Normative boundary

The words **must**, **must not**, **required**, **should**, and **may** are
normative.

This family owns generic identity, custody, admission, revision,
materialization, dependency, and reference mechanics. It does not define:

- Atlas semantic interpretation or category meaning;
- profile-specific platform, pack, or world meaning;
- Blueprint applicability or approval;
- `ContextRef`, `InputBinding`, jobs, transports, or capability negotiation,
  which are specified by separate contracts and runtime modules;
- append transactions, compare-and-swap storage, and crash recovery, which are
  outside this record family and are not provided by the V2 core; or
- a supported Cleanroom or Supersymmetry profile.

An unresolved referenced kind is unavailable. A caller must not infer its
contents from a filename, nearby revision, display label, or V1 artifact.

## Closed semantic domain

Every record in this family is a closed semantic object. Every object-shaped
subvalue has `additionalProperties: false`. Extensible or domain-owned payloads
are immutable exact objects reached through an `object-descriptor` ID; they
are never open JSON maps embedded in a common record.

Every top-level record has these required fields:

| Field | Meaning |
| --- | --- |
| `kind` | The lowercase domain-separated identity kind |
| `format` | Exact record-format identifier |
| `schema_version` | Integer `2` |
| `schema_id` | Exact local `workbench://` structural schema identifier |
| `canonicalizer` | Exactly `workbench-canonical-json-v2` |
| `id` | Recomputed content ID for the complete record body |

The schema is necessary but not sufficient validation. JSON Schema cannot
prove byte identity, graph closure, reference existence, authority, canonical
array order, root calculations, or the equality between an ID suffix and the
bytes it names.

### Semantic and operational separation

All fields accepted by these record schemas are semantic for that record and
are included in its identity, except the top-level `id` field itself. There is
no generic `metadata`, `extensions`, or `annotations` escape hatch.

The following values must not appear in a semantic record unless a future
domain contract explicitly makes the observed value part of evidence:

- publication or access time;
- local path, cache path, or temporary filename;
- host name, process ID, thread ID, or worker assignment;
- job or transport request ID;
- elapsed time, throughput, memory use, retry count, or progress;
- mutable reference-head location; and
- compression choice or other replaceable transport representation.

Those values belong in an operational sidecar keyed by the immutable record
ID. A sidecar may be replaced or removed without changing semantic identity.
It must not supply missing evidence or authority.

A `reference-event` is an immutable audit record about an operational
transition, so the event's actor, expected target, new target, reason, and
sequence are deliberately identity-bearing. The mutable reference head and
its filesystem or database representation are not part of the event.

## Canonical bytes and identity

The canonical value subset and encoder are exactly those reserved by the
graph model:

- `null`, booleans, Unicode strings, signed 64-bit integers, semantic arrays,
  and closed objects only;
- no floating-point number, non-finite number, or integer outside the signed
  64-bit range;
- UTF-8 without a byte-order mark or Unicode normalization;
- exact V2 string escaping, UTF-8 key ordering, minimal integers, and no
  insignificant whitespace; and
- array order is always semantic; and
- no more than 256 nested array or object containers, with the outermost
  container at depth one.

The canonical body is the complete schema-valid record with only top-level
`id` omitted. Its ID is:

```text
<kind>:sha256:hex(
  SHA-256(
    UTF8("workbench-content-v2\n") ||
    UTF8(kind) || 0x0a ||
    canonical_json(body_without_id)
  )
)
```

No other field may be removed from identity. In particular, limitations,
coverage, support state, authority, scope, conflict, frontier, and dependency
fields remain identity-bearing even when empty.

### Semantic roots

Every `semantic_root`, `aggregate_semantic_root`, `footprint_root`, history
root, partition root, and per-kind record root uses one algorithm. Let
`canonical_json(value)` mean the exact V2 encoding above. Then:

```text
root_v2(domain, value) = hex(
  SHA-256(
    UTF8("workbench-semantic-root-v2\n") ||
    UTF8(domain) || 0x0a ||
    canonical_json(value)
  )
)
```

The result is the 64-character lowercase hexadecimal digest stored in a root
field. `omit_fields(object, names)` below means the JSON object containing
every key/value pair from `object` except keys exactly present in the JSON
string array `names`; it does not recursively omit fields. Domain strings and
projections are exact:

| Root field | Domain | Canonical `value` projection |
| --- | --- | --- |
| Admission partition `semantic_root` | `evidence-set-revision/effective-admissions/partition` | `{"partition_key":"<key>","record_ids":["<admission-record-id>",...],"shard_ordinal":<integer>}` in exact shard order |
| Evidence partition `semantic_root` | `evidence-set-revision/effective-evidence/partition` | `{"partition_key":"<key>","record_ids":["<evidence-record-id>",...],"shard_ordinal":<integer>}` in exact shard order |
| Non-null `effective_admission_root` | `evidence-set-revision/effective-admissions` | `[{"partition_key":"<key>","record_count":<integer>,"semantic_root":"<sha256>","shard_ordinal":<integer>},...]` in exact admission-partition order |
| Non-null `effective_evidence_root` | `evidence-set-revision/effective-evidence` | `[{"partition_key":"<key>","record_count":<integer>,"semantic_root":"<sha256>","shard_ordinal":<integer>},...]` in exact evidence-partition order |
| Non-null evidence history root | `evidence-set-revision/history/<bucket>` | `["<content-id>",...]`, a canonically sorted unique JSON array selected for that exact bucket by the pinned ledger heads and selection policy |
| Evidence-set `semantic_root` | `evidence-set-revision/aggregate` | `omit_fields(record,["id","semantic_root"])` |
| Graph partition `semantic_root` | `graph-revision/partition/<record-kind>` | `{"partition_key":"<key>","record_ids":["<graph-record-id>",...],"shard_ordinal":<integer>}` in exact shard order |
| Non-null graph `record_roots.<kind>` | `graph-revision/records/<record-kind>` | `["<graph-record-id>",...]`, one JSON array in canonical partition/shard/record order |
| Graph `aggregate_semantic_root` | `graph-revision/aggregate` | `omit_fields(record,["id","aggregate_semantic_root"])` |
| Dependency `footprint_root` | `dependency-manifest/footprint` | `omit_fields(footprint,["footprint_root"])` |
| Dependency `semantic_root` | `dependency-manifest/aggregate` | `omit_fields(record,["id","semantic_root"])` |
| Graph-set `aggregate_semantic_root` | `graph-set-revision/aggregate` | `omit_fields(record,["id","aggregate_semantic_root"])` |

`<record-kind>` is the exact hyphenated graph body value, including
`evidence-link` and `refinement-mapping`. `<bucket>` is exactly one of the four
history property names. A nullable root is `null` if and only if its selected
ID sequence is empty. For effective membership this means the corresponding
count is zero and its partition array is empty. Non-null root inputs are never
deduplicated or reordered while hashing; validators first require their
declared canonical order.

History bucket selection is exact under the pinned ledger and admission
selection policy. `rejected` and `quarantined` contain the admission-record IDs
with those outcomes. `superseded` contains each prior admission ID named by a
later admission plus each evidence ID named by `corrects_record_ids` or
`supersedes_record_ids`. `conflicted` is the canonical ID array returned by the
exact selection policy's conflict output over those pinned entries. Each
bucket is deduplicated and sorted by content-ID UTF-8 bytes before hashing; an
implementation must not use traversal order.

Partition roots prove membership and order. The containing aggregate also
binds the object descriptor, count, key bounds, and partition root, so replacing
shard bytes, descriptor metadata, or partition framing changes or invalidates
the revision. Content IDs in a projection are themselves validated before a
root is accepted. No Merkle-library default, JSON text as received, or host
collection order may substitute for this algorithm.

### Required validation order

A conforming reader validates in this order and fails closed at the first
invalid boundary:

1. Parse UTF-8 JSON while rejecting a BOM, duplicate keys, malformed Unicode,
   floating-point tokens, non-finite values, and integers outside signed
   64-bit range. The ordinary Python `jsonschema` integer type accepts values
   such as `1.0`; therefore Draft validation alone is not a canonical-domain
   check.
2. Resolve only locally registered schema resources and validate the complete
   closed Draft 2020-12 schema.
3. Validate all cross-field, ordering, relation, scope, and authority rules in
   this contract.
4. Re-encode the body with the V2 canonicalizer and recompute the record ID.
5. Resolve referenced immutable objects and validate their type, bytes,
   digest, and required transitive relations before admission or publication.

Default JSON encoders, permissive parsers, online `$ref` retrieval, and
schema-only validation do not conform.

### Array order

Arrays are never silently reordered during validation. Builders may construct
canonical arrays before calculating identity; readers reject a noncanonical
order.

The following arrays preserve declared order:

- `validator_ids`, in execution order;
- `diagnostics`, in contiguous `ordinal` order starting at zero;
- graph `partitions`, ordered by partition key, record-kind order, and shard
  ordinal;
- evidence-set `admission_partitions` and `evidence_partitions`, each ordered
  by partition-key UTF-8 bytes and then shard ordinal, and `ledger_heads`,
  ordered by ledger-namespace UTF-8 bytes;
- graph-set `members`, which are the graph-set query order;
- graph-set `join_graphs` and `refinement_graphs`, in owner-policy order;
- recipe `derivation_steps`, in canonical topological order;
- recipe `output_contracts`, in declared production order; and
- dependency `footprints`, in output-partition production order.

Every other array whose schema declares `uniqueItems: true` is a canonical
set. Its items are sorted by their canonical item bytes. A validator rejects
duplicates and noncanonical set order. For source bindings, the declared key
is role, object-descriptor ID, then canonical locator bytes. Domain schemas may
declare additional ordered arrays only in a new versioned contract.

The graph record-kind order is exactly: `node`, `edge`, `property`,
`evidence-link`, `refinement-mapping`, `frontier`, `conflict`. This order, not
the textual order of a schema enum and not lexical sorting, controls graph
partition order and per-kind root traversal. The corresponding
`record_counts`/`record_roots` properties are respectively `nodes`, `edges`,
`properties`, `evidence_links`, `refinement_mappings`, `frontiers`, and
`conflicts`.

## Record relationship

```text
exact bytes
  -> object descriptor
     -> evidence record
        -> admission record
           -> immutable ledger-entry chain
              -> evidence-set revision

recipe + evidence-set revision + optional input graph revisions
  -> dependency manifest + graph records in canonical shards
     -> graph revision
        -> graph-set revision
           -> successful reference event
```

All arrows point to already-published immutable objects. A graph revision
references its dependency manifest; the dependency manifest deliberately does
not reference the resulting graph revision, avoiding an identity cycle.
Subject-to-subject graph edges use stable subject IDs and likewise do not
create content-record cycles.

A relational validator must resolve each ID to the expected record kind. A
generic `contentId` lexical match never proves that the target exists or has
the required kind.

### Exact reference kinds and authority adapters

Every schema leaf that carries a content-ID reference resolves to a typed
definition with the normative annotations `x-workbench-reference-kinds` and
`x-workbench-reference-class`. A dynamic reference additionally declares
`x-workbench-reference-kind-source`. A registry must reject a C01 schema if a
runtime content-ID leaf does not resolve to exactly one such classification.
The record's own top-level `id` and a storage `object_id` are identities, not
references, and are the only exceptions.

The exact reference-kind table is:

| Reference fields | Required kind or closed kind set | Class/scope |
| --- | --- | --- |
| Object, schema, payload, result, selector-object, parameter-object, value, summary, continuation, constraint, frontier, and shard descriptor fields | `object-descriptor` | C01 |
| Evidence provenance/correction/supersession, graph evidence inputs, evidence links, and dependency evidence IDs | `evidence-record` | C01 |
| Admission prior/partition/dependency IDs | `admission-record` | C01 |
| Ledger predecessors and heads | `ledger-entry` | C01 |
| Evidence-set parents and graph/dependency/alignment evidence-set inputs | `evidence-set-revision` | C01 |
| Graph sources, body links/conflicts/refinement node sources, frontier/conflict lists, and dependency graph-record IDs | `graph-record` | C01 |
| Graph parents/inputs/members and source graph partitions | `graph-revision` | C01 |
| Recipe references | `materialization-recipe` | C01 |
| Dependency parent/revision binding | `dependency-manifest` | C01 |
| Reference-event predecessor | `reference-event` | C01 |
| Admission candidate and ledger entry target | exactly the sibling `candidate_kind` or `entry_record_kind` | C01, dynamic |
| Reference-event old/new target | exactly sibling `new_target_kind`, from `evidence-set-revision`, `graph-revision`, or `graph-set-revision` | C01, dynamic |
| Node, edge, property, refinement, frontier, and conflict subject IDs | exact recipe subject kind | subject identity, dynamic |
| `context_ref_id` and alignment context inputs | `context-ref` | external |
| `owner_authority_id`, `owner_revision_id`, and `authority_adapter_id` | `authority`, `owner-revision`, and `authority-adapter` respectively | external, one binding |
| Every policy, compatibility rule, order policy, selector policy, invalidation policy, and mapping policy | `policy` | external under the field-specific authority binding |
| Recipe and step implementation references | `implementation` | structural external identity, then semantic validation under exact `recipe_owner` |
| Materializer/custodian component and implementation references | `component` and `implementation` | structural external capability registry; never silently re-authorized as graph semantics |
| Producer and transport normalizer | `producer` and `transport-normalizer` | external under evidence authority |
| Validators and supporting receipts | `validator` and `validation-receipt` | external under decision owner |
| Profile adapter bindings | `profile-adapter` plus the binding's `profile_authority` | external under that exact profile authority |
| Profile support bindings | `profile-revision`, `profile-adapter`, and each `support-decision`, plus the binding's `profile_authority` | external under that exact profile authority |
| Ontologies and semantic validation requirements | `ontology` and `validation-requirement` | external under semantic `authority_owner` |
| Tools | `tool` | structural external registry |
| Merge recipe and scope selector | `merge-recipe` and `selector` | external under evidence-set authority |
| Full/incremental conformance cases | `conformance-case` | external under recipe owner |
| Compatibility decision | `compatibility-decision` | external under the alignment's `decision_authority` |
| Reference actor, tool, and authorization | `actor`, `tool`, and `authorization-decision` | external under `authorization_authority` |
| Inline derivation predecessor and graph-record step | `derivation-step` | embedded in the exact recipe |
| Dependency `input_components[*].component_id` | sibling `component_kind` from the closed union of `object-descriptor`, `implementation`, `policy`, `ontology`, `profile-revision`, `profile-adapter`, `tool`, and `component` | mixed; exact sibling kind and item `authority_binding` |
| Diagnostic supporting records | any registered C01 top-level record kind | C01 |

An authority binding is exactly `owner_authority_id`, `owner_revision_id`, and
`authority_adapter_id`; all three are identity-bearing external content IDs.
The external resolver returns the exact canonical record bytes and the adapter
that validated them. Core recomputes the external record ID and kind. The
returned adapter must equal the exact adapter in the applicable authority
binding. The publisher must use pinned, owner-approved adapter trust roots to
validate the binding itself; an unscoped or self-declared adapter result is
unavailable, not authoritative.

Core never implements Atlas selection, profile compatibility, category
meaning, or state-reduction policy. After generic closure it invokes the
exact owner adapter/policy boundary for these required rules:
`canonical-total-order`, `admission-validation`,
`evidence-assertion`, `evidence-selection-history`, `derivation`,
`graph-record-semantics`, `dependency-closure`, `dependency-semantics`,
`recipe-definition`, `graph-category-semantics`, `graph-materialization`,
`graph-set-composition`, `compatibility-rule-application`,
`compatibility-alignment`, `state-reduction`, and
`reference-authorization`. A missing hook, false result, or typed diagnostic
rejects publication. Each named hook is the whole-record attestation for its
normative owner role and receives that role's complete immutable projection;
approval of a referenced artifact or another record cannot be replayed as
approval of this projection. In particular, Workbench's composition owner
cannot manufacture a member compatibility result.

Every relation callback is an untrusted application boundary. Before invoking
the external-reference resolver or an owner-policy validator, core MUST build a
detached request from the already pinned primitive fields, canonical record
bytes, external-reference results, blob bytes, authority scope, and trusted
adapter registration. It MUST NOT expose a caller-owned expectation, request,
record wrapper, mutable mapping, or nested value to the callback. In
particular, each `PolicyValidationRequest` is an isolated whole-request
snapshot; a policy callback may attest its rule and exact projection but may
not rewrite the record, resolved-record view, external-reference view, object
bytes, authority scope, or adapter binding that it is being asked to approve.

After any such callback returns, core MUST resnapshot the isolated request and
compare it with the exact pre-call shape. Mutation through a retained alias,
nominally frozen object, nested value, or custom mapping is a typed relation
failure and rejects publication even when the callback returns approval or
diagnostics. Resolver results are separately snapshotted and revalidated from
canonical bytes. Exact `True` is the only direct owner-policy approval; a
validated diagnostic iterable is processed only as diagnostics, never by
truthiness. A callback exception, malformed result, mutation, or truthy
substitute for `True` cannot authorize publication, and mutation of the
isolated copy cannot change later validation.

Scope selection is path-specific and uses this exact division. Under
`recipe_owner` are `implementation.implementation_id`, every
`derivation_steps[*].implementation_id`,
`accepted_inputs.scope_compatibility_rule_id`,
`policies.total_order`, `policies.partition`, `policies.deduplication`,
`policies.dependency_footprint`, `policies.invalidation`,
`policies.deterministic_failure`, `full_build_conformance_case_ids`, and
`incremental_equivalence_case_ids`. Under `authority_owner` are
`policies.identity`, `policies.category_membership`,
`policies.category_overlap`, `policies.cross_category_edges`,
`policies.resolution_aggregation`, `policies.refinement_mapping`,
`policies.evidence_propagation`, `policies.conflict_propagation`,
`policies.frontier_propagation`, and every ontology binding. A graph
revision's `partition_policy_id` and `total_order_policy_id`, and a dependency
manifest's `invalidation_policy_id`, use `recipe_owner`; a refinement body's
`mapping_policy_id` uses `authority_owner`. Admission validation is under
`decision_owner`; evidence-set admission selection and scope selection are
under its `authority_owner`. Graph
materializer/custodian component identities are structural capability-registry
facts, not grants of graph semantic authority. A graph's `authority_owner`
cannot silently replace its distinct `recipe_owner`, and neither can replace a
profile-owned support or compatibility decision.

`profile_adapter_bindings` contain an exact `profile_adapter_id` and
`profile_authority`. `profile_support_bindings` additionally contain an exact
`profile_revision_id` and nonempty `support_decision_ids`. Each profile record
is independently adapter-validated under that binding's exact profile
authority. The state-reduction owner hook proves its ContextRef applicability;
the graph or composition owner cannot attest profile support.

## Immutable object descriptor

An `object-descriptor` binds exact stored bytes. Its `object_id` is:

```text
workbench-blob-v2:sha256:<sha256-of-exact-raw-bytes>
```

The suffix of `object_id` must equal `sha256`, and `byte_length` must equal the
length of the exact bytes. The store must verify both on every untrusted read.
Content identity does not authorize public disclosure or cross-privacy-domain
deduplication.

The four representations are:

- `exact-bytes`: opaque bytes. All described-record, schema, ordering, count,
  order-authority, and key fields are `null`.
- `canonical-json-value`: one canonical closed JSON value under an exact bound
  schema and schema object descriptor. `described_record_kind` is null,
  `canonical_item_count` is `1`, and ordering fields are null. The value need
  not have a record envelope, content ID, or `kind`; `media_type` is exactly
  `application/json`.
- `canonical-json-record`: one complete canonical V2 JSON record. Its exact
  schema URI, schema object descriptor, and top-level record kind are bound;
  `canonical_item_count` is `1`, ordering policy/authority are `null`, and
  `media_type` is exactly `application/json`.
- `canonical-ndjson-shard`: one or more canonical records of one top-level
  kind, each followed by one line feed. Schema bytes, total-order policy,
  the policy's exact `total_order_authority`, positive item count, and
  inclusive minimum/maximum record keys are bound; `media_type` is exactly
  `application/x-ndjson`.

For every canonical NDJSON shard, core validates the complete bytes, line-feed
framing, canonical record rows, exact bound schema and kind, digest, byte
length, and item count. It then invokes the required `canonical-total-order`
hook under the descriptor's exact `total_order_authority` over the complete
ordered row sequence. That owner hook validates the declared policy's key
construction, strict row order, uniqueness, and inclusive minimum/maximum
keys; core has no generic fallback for an unknown order policy. Known C01
partition types additionally validate their normative keys mechanically. A
graph partition descriptor's order policy and authority must equal the
containing graph revision's `total_order_policy_id` and `recipe_owner`;
admission and evidence partition descriptors' order authorities must equal the
containing evidence-set revision's `authority_owner`.

The schema object descriptor referenced by a canonical representation must
itself describe the exact schema bytes as `exact-bytes`. It must hash the same
schema whose `$id` equals `described_schema_id`. Empty shards are omitted. A
compressed copy is a transport sidecar; it cannot replace the descriptor of
the authoritative uncompressed bytes.

## Evidence record

An `evidence-record` is one authority-owned observation or statement over
exact payload and source objects. It binds:

- exact authority owner and owner revision;
- evidence kind and future exact `ContextRef`;
- producer and Crucible transport-normalizer implementations;
- one or more role-bearing source locators;
- a closed domain payload through an object descriptor;
- evidence state, capture completion, loss, truncation, and coverage;
- provenance, correction, and supersession links;
- explicit V1 import bindings and migration manifest when applicable; and
- exact limitations.

Transport normalization may validate framing, ordinals, bytes, scope binding,
completion, loss, and schema. It must not infer aliases, causation, category
membership, lifecycle meaning, or global absence. Those semantics belong to
Atlas or an exact selected profile and enter through an owned recipe.

Every locator is discriminated and closed. Byte ranges use a positive length;
record ordinals are zero-based; JSON Pointers follow RFC 6901 escaping;
coordinate bounds are inclusive and require each minimum coordinate to be no
greater than its maximum. A locator must resolve within the exact bytes named
by its object descriptor.

Capture fields must agree. Examples include:

- `completion = complete` requires `loss = none`, `truncated = false`, and a
  coverage state compatible with the evidence-kind contract;
- `evidence_state = truncated` requires `truncated = true` and explicit
  coverage; and
- unavailable, incomplete, failed, or lossy input may be retained, but it
  cannot be upgraded to complete by transport normalization.

Correction and supersession targets must be prior evidence records under a
compatible authority and scope. They change a later effective view; they never
alter or delete the target.

The required `evidence-assertion` hook under `authority_owner` attests the
complete evidence record, including producer, sources, payload, capture state,
provenance, corrections, supersession, legacy bindings, and limitations.

### V1 bindings

V1 bytes, fields, canonicalization, and IDs remain verbatim. A V2 evidence
record importing V1 must:

- retain the original format and original ID as text, without parsing it as a
  V2 ID;
- bind the exact V1 bytes through an object descriptor;
- bind the original validator, exact V2 `profile_adapter_binding`, and
  field-mapping manifest;
- retain omissions and limitations; and
- bind the import group's exact `MIGRATION-MANIFEST.json` bytes through
  `migration_manifest_object_descriptor_id`.

If `legacy_bindings` is empty, the migration-manifest field must be `null`. If
it is nonempty, the field must resolve. The wrapper receives a new V2 ID and
must never impersonate the V1 artifact.

## Admission record

An `admission-record` evaluates exactly one object descriptor or evidence
record. It is separate from the candidate and binds the decision owner,
context, validator execution sequence, exact evaluated schemas, policies,
profile-adapter bindings, diagnostics, limitations, validation-result object, and
supporting receipts.

`candidate_kind` must match the resolved target kind. The ordered validators
must all resolve to exact implementations. Diagnostics have unique contiguous
ordinals. A validator crash, unknown authority, unknown schema, unresolved
policy, or reference failure cannot produce `admitted`.

The only outcomes are:

- `admitted`, with `eligible_for_materialization = true`;
- `rejected`, with eligibility false; or
- `quarantined`, with eligibility false.

An admitted incomplete record remains incomplete and is eligible only for a
recipe whose exact domain policy accepts that incomplete state. Admission
cannot manufacture semantic closure. Revalidation appends a new admission
record and points `prior_admission_record_id` at the earlier decision; it never
rewrites the earlier record.

After core validates the candidate, schema, references, discriminator,
diagnostic mechanics, and outcome/eligibility coupling, it invokes the
required `admission-validation` hook under the exact `decision_owner`. The
owner hook must bind the complete admission record, including candidate,
validator sequence, evaluated schemas, policies, receipts, validation-result
object, outcome, and eligibility. A receipt or validation result accepted for
another candidate, outcome, or validation closure cannot be replayed.

## Immutable ledger entry

A `ledger-entry` closes the semantic chain named by an evidence-set ledger
head. It binds one exact `ContextRef`, ledger namespace, contiguous zero-based
ordinal, previous entry, and one evidence or admission record. Entry zero has
`previous_entry_id = null`; every later entry names the immediately preceding
ledger entry. `entry_record_kind` must match the resolved `entry_record_id`.

A relational validator walks from a head to ordinal zero and proves that every
entry has the same context and namespace, each predecessor ordinal is exactly
one lower, every predecessor ID resolves, and no cycle or duplicate ordinal
exists. Each entry's context must equal the context of its resolved evidence or
admission record. The head's declared ordinal must equal the resolved head
entry's ordinal. Ledger order establishes custody order only; it does not
establish runtime causality.

This immutable record does not define a mutable ledger head, writer lease,
multi-entry transaction, compare-and-swap algorithm, branch publication, or
crash-recovery protocol. A storage implementation must provide those mechanics
without changing ledger-entry identity. A partial or uncommitted append never
becomes an evidence-set head.

## Evidence-set revision

An `evidence-set-revision` is the immutable snapshot-isolation boundary read by
materialization. It binds exact ledger heads, parent revisions, admission
selection policy, partitioned effective admitted decisions and evidence,
their exact counts and aggregate membership roots, scope selector,
retained-history roots, coverage, limitations, an optional pre-materialization
evidence-frontier object, and aggregate root. It never repeats all effective
record IDs inline in the manifest.

A relational validator must prove that:

1. every row in `admission_partitions` resolves as an admission, has outcome
   `admitted`, is materialization-eligible, and targets exactly one row in
   `evidence_partitions`;
2. the selection policy deterministically chooses one effective admission per
   evidence record without hiding rejected, quarantined, superseded, or
   conflicted history;
3. every effective admission and evidence row occurs in one of the complete
   ledger chains walked from the pinned heads;
4. every partition object, bound record schema, count, min/max key, and root
   validates; evidence shard rows are in strictly increasing evidence-record
   ID UTF-8 byte order, while admission shard rows are in strictly increasing
   candidate evidence-record ID UTF-8 byte order, with no duplicate row or
   candidate;
5. ledger namespaces are unique and their heads and ordinals exist;
6. a child includes all parent history plus valid appended correction,
   retraction, admission, or conflict records;
7. a revision with zero or one parent has `merge_recipe_id = null`; a
   multi-parent revision binds an exact deterministic merge recipe; and
8. effective counts equal the exact shard totals; each effective root is
   `null` exactly when its count and partition array are empty, otherwise it is
   recomputed over the ordered partition summaries specified above; and
9. the aggregate root is recomputed over the canonical ordered partitions,
   effective roots, and history roots.

For an admission partition, `minimum_record_key` and `maximum_record_key` are
the first and last exact `candidate_record_id`. For an evidence partition they
are the first and last exact evidence-record `id`. Empty shards are omitted;
ordinals are contiguous from zero within each partition key. This shape keeps
the evidence-set manifest proportional to shard count rather than capture
size while retaining exact membership and invalidation roots.

If `evidence_frontier_object_descriptor_id` is non-null, it resolves to a
canonical JSON control record of kind `evidence-frontier` under an exact bound
domain schema. It describes unresolved or omitted evidence before graph
materialization; it must not reference graph frontier records or otherwise
invert the evidence-to-graph dependency.

Evidence appended after the named heads cannot leak into a build using this
revision. Absence from a later effective view is not deletion unless an
admitted correction or retraction explicitly changes the view.

## Graph record

A `graph-record` has one of seven closed bodies:

| Body | Identity-bearing role |
| --- | --- |
| `node` | Exact subject identity tuple and stable subject ID |
| `edge` | Directed or explicitly symmetric predicate between subjects |
| `property` | Typed value object attached to one subject |
| `evidence-link` | Exact evidence support for another graph record |
| `refinement-mapping` | Fine subjects contributing to one coarse subject |
| `frontier` | Bounded, truncated, unavailable, or unresolved boundary |
| `conflict` | Incompatible applicable records retained without selection |

The record envelope binds owner, graph family, category, resolution,
`ContextRef`, logical key, recipe and derivation step, keyed evidence inputs
and exact source-contract-selected graph inputs, qualifiers, evidence state,
and limitations. An evidence input pairs `input_contract_key` with one exact
evidence record. A source input pairs `input_contract_key`, `source_kind`,
`contract_key`, nullable exact predecessor step, and one graph record. At least
one exact evidence or source input is required. Roles remain recoverable even
when two input contracts admit the same evidence kind or graph coordinates.
A derived record must keep transitive reachability to primary evidence.

`logical_key` is the complete recipe-produced total-order key for the graph
record; it is not a display label or partial prefix. Within each canonical
graph shard, `logical_key` values must be unique and in strictly increasing
unsigned UTF-8 byte order. The core validates that order while the exact recipe
owns construction of the key and its semantic components. Two records with
the same logical key in one shard are invalid even when their content IDs
differ.

A node's `subject_id` is recomputed from the exact closed identity tuple in
`subject_identity_object_descriptor_id`, using the exact `subject_kind` and
`subject_identity_schema_object_descriptor_id` selected by its output
contract's matching `node_kind`. The descriptor representation is
`canonical-json-value`, not a nested identity-bearing record. The V2 content
algorithm uses `subject_kind` as its domain kind over that closed value.
Mutable properties never enter that tuple. The same subject
may therefore retain its subject ID while its node record ID changes.

An edge binds both stable subject IDs and exact source/target node-record IDs,
plus direction, predicate, context, qualifiers, evidence, derivation, and
limitations through the enclosing record. Each endpoint record must resolve
to a node whose subject matches the corresponding subject ID and must belong
to the current candidate revision or a declared compatible input graph.
Both endpoint record IDs must also occur in the edge envelope's flattened
`source_graph_inputs[*].graph_record_id` set.
Symmetric endpoints must be stored in recipe-declared canonical order.
Parallel edges are allowed only when an identity-bearing occurrence,
qualifier, evidence, derivation, or endpoint record differs.

Property values use exact schema and value object descriptors rather than an
open inline value. A property also binds `subject_node_record_id`, which must
resolve to a node with the same subject and belong to the current candidate or
a compatible input graph; it must also occur in the property envelope's
flattened source inputs. An evidence link's supporting evidence must also
occur in the flattened evidence inputs. A refinement mapping binds one closed coarse
endpoint and one or more closed fine endpoints. Every endpoint pairs a stable
subject ID with an exact source node-record ID, exact source graph-revision ID,
and family/category/resolution coordinates. The source revision must contain
that node, the node must match all endpoint coordinates, subject, and context,
and the node ID must occur in the envelope's flattened source inputs. The
source revision must be a declared compatible graph input. Coarse and fine
resolutions must differ; parallel subject and node arrays are forbidden. The
mapping also binds omitted conflicts and its exact aggregate summary. A
bounded or truncated frontier requires `omitted_at_least >= 1`;
an unavailable or unresolved frontier may use `null`. Conflict inputs must
resolve under compatible graph scope and stay queryable even after a later
revision supersedes their interpretation.

`derivation_step_id` must name one inline step in the exact recipe. One step
`output_contracts` entry must name the exact keyed recipe output contract that
matches the graph record's family/category/resolution and contain its body
kind. Every evidence input key and evidence kind must match one declared step
input contract. Every source input must byte-for-byte select a closed source
contract of kind
`accepted-graph-input`, `predecessor-output`, or `local-output`, including its
input key, source kind, contract key, predecessor ID, and allowed record kind.
A predecessor source names its
exact earlier step ID; these references are the derivation DAG edges. Core
validates these bindings and DAG mechanics; the recipe owner validates
semantic derivation through the required `derivation` policy hook.
The semantic `authority_owner` independently attests the complete graph-record
interpretation through `graph-record-semantics`; derivation approval by the
recipe owner cannot authorize the graph's subject, edge, property, category,
evidence, refinement, frontier, or conflict meaning.

## Graph revision

A `graph-revision` publishes one graph family, category, resolution, purpose,
and exact context. It binds four distinct roles:

- semantic `authority_owner`;
- `recipe_owner`;
- exact Crucible `materializer`; and
- exact storage `custodian`.

It also binds the recipe and implementation, canonical parameter values,
schemas, ontologies, exact profile-support bindings, tools, evidence-set and graph inputs,
parents, partition and order policies, dependency manifest,
canonical shards, counts, roots, evidence state, coverage, completeness,
support, limitations, frontiers, conflicts, and semantic validation
requirements.

`input_graph_bindings` is the canonical set of exact
`graph_input_contract_key`/`graph_revision_id` pairs. Every key names one
accepted graph-input contract in the exact recipe, and the revision must match
that contract's family, category, resolution, allowed row kinds, context, and
compatibility requirements. Keys are unique, but two keys may bind distinct
same-coordinate revisions. An `accepted-graph-input` graph-record source
binding is closed only by the exact revision bound under its own contract key;
matching coordinates or membership in a differently keyed revision is not
closure.

At least one evidence-set or input-graph revision is required. For every
partition, the validator must:

- resolve the object descriptor as a canonical NDJSON shard;
- verify partition key, shard ordinal, record kind, count, min/max key, raw
  bytes, and semantic root;
- validate every graph record and require matching owner, family, category,
  resolution, context, and recipe;
- enforce the recipe's total order and partition policy; and
- prove that all cross-partition references resolve in declared inputs or the
  complete candidate revision.

Per-kind counts equal the sum of records in the shards. A per-kind root is
`null` exactly when its count is zero. The aggregate root is recomputed over
the complete ordered partition and per-kind roots. Listed frontier and
conflict IDs must resolve to records in the revision. Coverage, completeness,
support, and limitations must be conservative reductions under the recipe's
exact policies.

Core rejects a graph-record ID appearing more than once, duplicate logical
keys even when their record IDs differ, duplicate physical partition triples,
and overlapping or nonmonotonic shard key ranges within one partition key and
record kind. After those mechanics and complete pinned row closure pass, core
invokes the required `graph-materialization` hook under the exact
`recipe_owner`, including for an empty-output graph. The recipe owner validates
the complete revision and rows against its exact partition, deduplication, and
global output policies; a shard-local order check or per-record derivation
decision is not a substitute. Core separately invokes the required
`graph-category-semantics` hook over that same complete pinned revision and
rows under `authority_owner`, which validates category membership, overlap,
cross-category edges, resolution aggregation, refinement, evidence/conflict/
frontier propagation, and other semantic policy results. Neither owner may
attest the other's decision.

The dependency manifest must describe the same recipe, implementation,
context, evidence sets, graph inputs, and
`parameter_values_object_descriptor_id` as the revision.
Every object and required semantic check must pass before the revision
manifest is published. Build worker, elapsed time, temporary paths, and
whether the build was full or incremental are operational sidecars; they do
not change the revision ID.

## Graph-set revision

A `graph-set-revision` is the atomic query boundary for compatible graph
revisions. Its ordered members bind category and resolution to exact graph
revision IDs. The set additionally binds its composition owner, purpose,
context, evidence roots, exact profile-support bindings, compatibility rule,
its distinct exact `compatibility_rule_authority`, exact constraints, aligned
join and refinement graph revisions, coverage,
completeness, support, limitations, conflicts, and aggregate root.

Every categorical member and every `join_graphs` or `refinement_graphs`
auxiliary member contains an identity-bearing `alignment`. It binds an exact
external `compatibility_decision_id`, its `decision_authority`, the exact
compatibility rule and constraints, the member and target contexts, the member
and target evidence-set revision arrays, an `exact` or `policy-compatible`
result for context and evidence, and outcome `compatible`. The inline values
are the exact decision inputs and result, not a substitute for the external
decision artifact.

The validator must prove:

- every member's declared category and resolution match its graph revision;
- each alignment's rule and constraints equal the graph-set values, its member
  inputs equal the resolved graph revision, and its target inputs equal the
  graph set;
- `exact` requires byte-equal context or evidence-set arrays;
- `policy-compatible` requires the pinned owner/profile adapter to validate
  the external compatibility-decision artifact under the alignment's exact
  `decision_authority`; missing or unscoped validation is unavailable;
- join and refinement revisions are members or declared compatible auxiliary
  revisions and preserve authority at every endpoint;
- profile, runtime epoch, world instance, world epoch, dimension, and other
  required scopes are equal or explicitly aligned by the compatibility rule;
- aggregate state never upgrades a weaker member state; and
- the aggregate semantic root covers the complete ordered membership and
  compatibility inputs.

Presence in one set does not make two revisions comparable or causal. A graph
set is not a universal ontology and cannot transfer authority to its
composition owner. In particular, Workbench cannot mint a compatibility
decision merely because it owns composition mechanics.
The composition owner also cannot authorize the top-level compatibility rule;
that rule resolves only under `compatibility_rule_authority`, while each
decision resolves under its alignment's `decision_authority`.
The required `graph-set-composition` hook under `composition_owner` attests the
complete set membership and composition projection. The separate required
`compatibility-rule-application` hook under `compatibility_rule_authority`
attests the top-level rule and complete comparison inputs/results; each member
decision remains independently attested by `compatibility-alignment` under its
own `decision_authority`.

## Materialization recipe

A `materialization-recipe` is an immutable semantic contract owned by Atlas or
another exact authority. Crucible may own a recipe for faithful controlled
capture projection, but not for Atlas interpretation or Blueprint approval.

The recipe binds:

- name, semantic version, owner, and semantic authority;
- exact implementation closure: implementation ID, executable and/or source
  tree, dependency lock, and runtime environment;
- an exact inline derivation DAG whose steps bind a content ID, stable step
  key, implementation, closed evidence/source input contracts, and keyed
  output contracts; predecessor-output source contracts are the exact DAG
  edges;
- accepted evidence kinds and keyed graph input contracts with exact
  family/category/resolution/record-kind coordinates, plus scope compatibility;
- exact selector, parameter schema, and canonical parameter values;
- every schema, ontology, profile adapter, and tool;
- ordered output family/category/resolution contracts, each with exact node
  kind to subject-kind and subject-identity-schema bindings; and
- exact policies for identity, order, partitioning, deduplication, category
  membership/overlap/cross-edges, resolution/refinement, evidence/conflict/
  frontier propagation, dependencies, invalidation, and deterministic
  failure.

Every top-level output contract has a unique `output_contract_key`. A step
output names one of those keys and a nonempty subset of its permitted graph
record kinds. A source input contract names either a keyed accepted graph
input, an exact predecessor step output, or a local output from the same step.
Its record kinds must be a nonempty subset of the named contract. This makes
external inputs, inter-step edges, and intra-step record links unambiguous.
Two accepted graph input contracts may deliberately have identical graph
coordinates and record-kind sets, for example baseline and candidate roles;
their distinct `graph_input_contract_key` values preserve that meaning.
Coordinate equality never permits substituting one keyed role for another.

For each step, let `step_body` be the complete closed step object with only
`derivation_step_id` omitted. Its ID is
`content_id("derivation-step", step_body)` under the V2 content algorithm.
Step, input-contract, and output-contract keys and IDs are unique in their
declared scopes. `derivation_steps` is the deterministic topological order:
every predecessor-output source names an earlier step, and concurrently ready
steps are ordered by unsigned UTF-8 bytes of `step_key`. A missing predecessor,
self-edge, cycle, coordinate mismatch, output-kind mismatch, or undeclared
input is invalid.
Step implementation IDs resolve under the recipe owner's exact adapter and
must be included in dependency closure.

A semantic version or source path is not an implementation identity. At least
one executable or source-tree object must resolve, and the dependency lock and
runtime environment must close that implementation. Wall clock, locale, host
enumeration, process scheduling, mutable environment, network state, and
random sources are forbidden inputs. If they matter to the subject, they must
first become admitted evidence.

After core validates the recipe's closed structure, references, policy scopes,
output coordinates, step IDs, and derivation DAG, it invokes the required
`recipe-definition` hook twice over the exact same immutable recipe: once
under `recipe_owner` for implementation and construction mechanics and once
under `authority_owner` for semantic interpretation. Both exact owners must
approve the assembled recipe. Approval of an implementation, policy, or prior
recipe cannot be replayed as approval of a different recipe record.

The record shape requires at least one declared full-build conformance-case ID.
Incremental publication is forbidden when `incremental_publication_enabled` is
false. Enabling it requires one or more exact incremental-equivalence case IDs.
Core validates these declarations and their referenced identities; it does not
execute a full-build workflow or infer that any named case passed.

## Dependency manifest

A `dependency-manifest` records deterministic dependency footprints for one
candidate materialization without pointing back to the resulting graph
revision. It binds its exact semantic `authority_owner`, exact `recipe_owner`,
recipe, implementation, context, evidence sets, keyed input graph bindings,
parameter values, all other keyed input component bindings, optional prior
manifest, invalidation policy, partition footprints, and roots.

Each footprint names a nonempty canonical set of exact output physical
partitions as `partition_key`, `shard_ordinal`, and `record_kind`, then lists all
evidence records, admission records, exact evidence and admission partitions,
keyed source graph records, exact keyed source graph partitions, and component
keys on which it depends. Every source graph record or partition repeats the
exact `graph_input_contract_key` whose top-level binding closes it.
Evidence/admission partition references include shard ordinal; source graph
partitions also include record kind. A partition key, coordinate, or
record-kind set alone is never an exact dependency.
Its root is recomputed over those canonical lists.
The footprint array is empty exactly when the candidate graph has no output
partitions; every present footprint retains a nonempty `output_partitions`
set. Global recipe, context, evidence/input, parameter, and component bindings
remain identity-bearing even for an empty or known-absent graph.

Every top-level `input_components` item binds a unique semantic key and role,
exact component kind and ID, and a nullable exact authority binding.
Implementation, policy, ontology, profile-revision, and profile-adapter
components require the applicable non-null authority binding. A C01 object
descriptor requires null. Structural tool and component registry identities
may be null or explicitly authority-bound. `component_kind` is schema-coupled
to `component_id`; footprint `component_keys` must resolve exactly one declared
binding. Bare mixed ID arrays are forbidden because they lose role and adapter
scope.

In addition to recipe-owner `dependency-closure`, the required
`dependency-semantics` hook under `authority_owner` attests the complete
semantic dependency projection. Neither hook can replay approval of one
component, footprint, or prior manifest against another dependency record.

`dependency_state = exact` asserts record- or partition-level closure under
the recipe. `conservative-broad` permits deliberate over-invalidation, never
under-invalidation. A missing dependency invalidates the candidate. An input
whose dependency closure cannot be proven must broaden the footprint to a
safe partition, graph, or complete-input boundary.

The manifest aggregate root covers all exact global inputs and ordered
footprints. Reverse-dependency indexes are disposable operational derivations;
they may accelerate impact analysis but may not add or omit dependencies.

## Reference event

A `reference-event` records one successfully committed compare-and-swap
transition to an evidence-set, graph, or graph-set revision. It binds exact
context, namespace, name, sequence, prior event, expected prior target, new
target kind and ID, `authorization_authority`, actor, tool, authorization
decision, and reason.

Sequence zero creates a reference and requires both prior fields to be null.
Every later event requires a previous event and expected old target. The
validator must prove a contiguous sequence, identical namespace/name/context,
that the prior event's new target equals the expected old target, and that the
new target resolves to `new_target_kind` under the same compatible context.

Only a successful transition receives this record. A compare-and-swap loss is
an operational failure or branch proposal, not a committed reference event.
The current target is derived from the valid chain head. Resolving a reference
pins that immutable target for the operation; later events do not alter the
pinned read.

After core validates the immutable event and predecessor mechanics, it invokes
the required `reference-authorization` hook under the exact
`authorization_authority`. The owner hook binds the complete transition,
including context, reference namespace/name, sequence, prior event, expected
old target, new kind/target, actor, tool, authorization decision, and reason.
An authorization decision accepted for another actor, sequence, or old/new
target cannot be replayed.

A reference does not confer support, completeness, authority, approval, or
freshness. Those states come from its immutable target and the exact owning
authority.

## Cross-record publication validation

Before an evidence-set, graph, graph-set, or reference event becomes visible,
the publisher must perform all applicable checks below:

1. Validate canonical domain, closed schema, semantic rules, and recomputed ID
   for every candidate record.
2. Resolve every referenced object from the pinned store/privacy scope and
   verify exact bytes, kind, digest, and length.
3. Resolve every external content ID through a pinned adapter result, verify
   its exact kind/content ID, and require the adapter and owner scope selected
   by the applicable authority binding; unscoped, mismatched, or missing
   external results fail unavailable.
4. Prove owner, context, profile, world, runtime epoch, and support bindings;
   unknown or missing required bindings fail unavailable.
5. Prove provenance closure without treating similarity, shared seed, shared
   path, timestamp proximity, or final-state match as causation.
6. Recompute all counts, partition roots, effective membership roots, history
   roots, dependency roots, and
   aggregate roots from canonical members.
7. Prove that every shard record is in exactly its recipe-declared partition
   and canonical order.
8. Run every required owner policy hook over the complete pinned candidate;
   no generic core default may replace a missing semantic decision.
9. Reject dangling, wrong-kind, cross-context, stale, or cyclic content-record
   references.
10. Publish referenced immutable objects first and the containing manifest
   last.
11. Advance a convenience reference only after complete target validation and
   a successful compare-and-swap.

For identical evidence-set revisions, input graph revisions, recipe,
parameters, profiles, and parents, full and incremental builds must produce
identical record bytes, partition boundaries, shard bytes, roots, graph
revision ID, and graph-set revision ID. A mismatch rejects the incremental
candidate and leaves the previous reference unchanged.

## Failure semantics

- Unknown fields, duplicate keys, floats, invalid Unicode, wrong array order,
  unresolved local schemas, wrong IDs, or digest mismatch are invalid.
- Missing objects, policies, authorities, profiles, validators, recipes, or
  dependencies are unavailable; no nearby object may be substituted.
- Rejected and quarantined candidates remain addressable for audit but cannot
  enter the effective evidence view.
- Incomplete evidence may be admitted only when its exact owner policy allows
  diagnostic use, retaining incomplete state and limitations.
- Conflicts remain explicit records. A recipe cannot silently choose a winner.
- Empty evidence and known absence are different. `unavailable`, `unresolved`,
  `bounded`, `truncated`, `inapplicable`, and `conflicted` remain distinct.
- A failed build publishes no graph manifest. A failed reference transition
  leaves the prior target unchanged.
- Corruption of one referenced object invalidates the complete containing
  revision; partial query results must not masquerade as that revision.

## Versioning and compatibility

Once a retained artifact uses one of these format IDs, any semantic change to
its fields, canonical rules, array ordering, identity, or cross-record meaning
requires a new format version. V1 schemas and canonicalizers are not modified
or redirected. A V2 reader may expose V1 only through an explicit adapter and
new V2 wrapper as described above.

Conformance for this record family uses fixed valid and malicious fixtures,
cross-language canonical vectors, offline `$ref` resolution, recursive
closed-object schema checks, ID and root recomputation, wrong-kind and
missing-reference failures, V1 identity preservation, and deterministic
synthetic graph validation. Those checks validate record mechanics only; they
do not establish a persistent store or end-to-end publication workflow.
