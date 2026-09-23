# Crucible ContextRef and InputBinding V2

Status: executable context-and-input contract. This contract defines immutable
execution context and immutable semantic-input bindings. It does not define selectors,
catalog storage, mutable references, service dispatch, jobs, or transports.

Normative schemas:

- `workbench://schemas/crucible/crucible-context-ref-v2.schema.json`
- `workbench://schemas/crucible/crucible-input-binding-v2.schema.json`
- `workbench://schemas/crucible/crucible-context-v2-common.schema.json`

The schemas are closed Draft 2020-12 resources loaded only from the finite
context registry. No network retrieval or alternate schema ID is allowed.

## Identity

Both records use `workbench-canonical-json-v2`. Their ID is the shared
domain-separated record content ID over the complete record with only the
top-level `id` member omitted:

```text
sha256("workbench-content-v2\n" || kind || "\n" || canonical-body)
```

The exact ID prefixes are `context-ref:sha256:` and
`input-binding:sha256:`. Unknown members are rejected before an ID is minted.
The validator snapshots caller-owned input before schema, semantic, or identity
validation.

Names, labels, aliases, physical paths, endpoints, process IDs, credentials,
and a mutable `current` selector are not identity fields. A service may use
them to resolve a record, but may not add them to or substitute them for a
resolved record.

## ContextRef

The exact record header is:

- `kind`: `context-ref`
- `format`: `workbench-crucible-context-ref-v2`
- `schema_version`: `2`
- `schema_id`:
  `workbench://schemas/crucible/crucible-context-ref-v2.schema.json`
- `canonicalizer`: `workbench-canonical-json-v2`

A ContextRef binds:

- one store and one exact workspace registration revision;
- one exact platform-profile revision and, when selected, one exact
  pack-profile revision;
- each profile's exact adapter, authority binding, support decisions, and
  declared support state;
- sorted source, dependency, and artifact lock sets;
- an optional exact installed runtime and runtime epoch, including side and
  sorted configuration locks;
- an optional world instance, creation receipt, save lineage, and world epoch;
- world seed and generator configuration only in the explicitly
  `applicable` world-generation alternative;
- an optional dimension instance and registry/provider identity;
- an optional half-open chunk region;
- at most one experiment, fixture, action, and capture scope, in that order;
  and
- exact privacy and resource-budget classes.

Optional scopes use closed discriminated objects. There are no open objects or
nullable identity placeholders. A selected world requires a selected runtime
and repeats its exact `runtime_epoch_id`. A selected dimension requires a
selected world and repeats its exact `world_epoch_id`. A chunk region requires
a selected dimension and repeats its exact `dimension_instance_id`.

Chunk bounds are half-open on both axes:

```text
minimum_chunk_inclusive.x <= x < maximum_chunk_exclusive.x
minimum_chunk_inclusive.z <= z < maximum_chunk_exclusive.z
```

Both intervals must be nonempty. The words `inclusive` and `exclusive` are
part of the identity-bearing field names; a consumer must not reinterpret them
as block coordinates or closed bounds.

`support_state` is one of `experimental`, `provisional`,
`tested-supported`, or `unsupported`. Runtime availability is deliberately not
a support state. An unavailable profile record, adapter, decision, or
validation port makes publication unavailable; it must not be encoded as a
different ContextRef support value.

Support decision IDs do not self-authorize. Each platform or pack profile
binding supplies an exact profile authority triple:

```text
owner_authority_id
owner_revision_id
authority_adapter_id
```

The pinned external registry must validate every profile revision, adapter,
authority record, and support decision under that exact triple. A mandatory
profile-support port then attests that the complete decision set applies to
the exact profile revision, adapter, state, and ContextRef.

## InputBinding

The exact record header is:

- `kind`: `input-binding`
- `format`: `workbench-crucible-input-binding-v2`
- `schema_version`: `2`
- `schema_id`:
  `workbench://schemas/crucible/crucible-input-binding-v2.schema.json`
- `canonicalizer`: `workbench-canonical-json-v2`

`context_ref_id` is mandatory and must equal the exact ContextRef validated in
the same publication. InputBinding never embeds or reconstructs context
fields.

The record has separate closed arrays for evidence-set revisions, graph
revisions, graph-set revisions, materialization recipes, schema descriptors,
ontologies, adapters, policies, and query-index descriptors. Each member has
an `input_key`; keys are globally unique across every array. Each array is a
canonical set ordered by the canonical bytes of its complete members.

A schema binding pairs a schema URI with the exact object descriptor that
declares that URI. Ontology and policy bindings carry their exact authority
binding. Adapter bindings are discriminated by adapter kind and carry their
exact authority binding. An `authority-adapter` binding must name the same
adapter as its authority triple.

The binding does not assert that an input is applicable. A mandatory
application-supplied input-applicability port validates all exact revisions,
owners, profile/context compatibility, and policy-specific constraints over
the complete pinned ContextRef and InputBinding.

## Publication validation and trust boundary

Local seal/load/validate functions prove strict JSON-domain, schema, generic
semantic, and content-ID invariants. They are prepublication validation and do
not prove that external records exist or apply.

`validate_context_publication` additionally requires all of these fail-closed
ports:

1. A pinned external-reference registry returning canonical, content-addressed
   records of the exact expected kind and, where declared, exact authority
   scope.
2. A profile-support validator for each selected platform or pack profile.
3. A whole-context binding validator that attests the complete ContextRef
   composition, including store/workspace registration, profile and lock
   applicability, operation scopes, privacy class, and resource-budget class.
4. A context-ancestry validator for the exact runtime, world, creation receipt,
   save lineage, world epoch, dimension, and region chain.
5. An input-applicability validator over the complete ContextRef/InputBinding
   pair and pinned external records.

The external registry is an application trust boundary. Its callback cannot
self-declare authority: returned canonical bytes and content IDs are
revalidated, and declared authority metadata must exactly match the
expectation. Missing records, missing ports, port exceptions, false decisions,
or scope mismatches produce typed relation failures.

The external-reference resolver and all four boolean validation families
(profile support, whole-context binding, context ancestry, and input
applicability) receive detached requests constructed from pinned primitive
fields, canonical ContextRef/InputBinding bytes, exact authority scope, and a
fresh immutable external-reference view. No callback receives the caller's
expectation, record wrapper, request object, mapping, or nested value. The
external resolver's returned record is separately snapshotted, content-ID and
kind validated, and authority-scope checked before it enters the pinned view.

After each callback returns, the validator MUST resnapshot the isolated request
and compare it with the exact pre-call shape. Mutation through
`object.__setattr__`, a retained alias, a nested value, or a custom mapping is a
typed relation failure even if the callback returns approval. A boolean port
accepts only the exact boolean value `True`; `False`, a truthy non-boolean,
mutation, a malformed result, or an exception rejects the publication. A
callback can therefore observe the exact proposed binding but cannot redirect
an external lookup, rewrite one request for a later port, or manufacture
applicability by mutating its isolated copy.

The generic validator mechanically enforces repeated parent IDs. The
whole-context port proves the non-ancestry bindings form one applicable
context, while the ancestry port proves that the referenced immutable runtime
and world records contain the same chain. This prevents a syntactically
consistent ContextRef from joining a workspace registration to the wrong
store, locks to the wrong workspace/profile, a world epoch from one runtime, a
dimension from another world, or a region from another dimension.

## Isolation requirements

Implementations and conformance fixtures must demonstrate that otherwise
similar records remain different when any of these exact bindings differ:

- store or workspace registration;
- platform/pack profile revision, adapter, support decision, or authority;
- installed runtime or runtime epoch;
- world instance, creation receipt, save lineage, or world epoch;
- dimension instance, registry, provider, or numeric dimension; or
- source/dependency/artifact/configuration locks and applicable semantic input
  revisions.

In particular, equal seeds do not identify equal worlds. Equal labels do not
identify anything because labels are absent from both schemas. No omitted
scope may be filled from a neighboring ContextRef.
