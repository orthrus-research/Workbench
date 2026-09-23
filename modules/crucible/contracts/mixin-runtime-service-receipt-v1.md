# Crucible Mixin runtime service receipt v1

Status: experimental capture contract

## Purpose

The Mixin runtime service receipt binds runtime-reported service observations
and, when a probe actually performed one, an independent enumeration of
available `IMixinService` providers.  It exists to answer two different
questions without collapsing either into the other:

1. Which service name did the running Mixin subsystem report using?
2. How many distinct provider implementations did a bounded enumeration find?

A repeated boot line, an initialization message, or a subsystem banner is not
a provider enumeration.  Conversely, finding exactly one provider does not
prove that the runtime selected it.  V1 therefore has separate
`observations` and `provider_enumeration` surfaces and separate summary states.

Crucible owns this runtime capture and its evidence custody.  Platform
profiles may import exact log or probe formats into the generic observation
set.  Atlas may interpret an admitted receipt later; this contract does not
make a platform policy decision.

## Bound scope

Every receipt binds exactly one controlled launch through:

- Crucible session, launch, platform profile, and logical side;
- the SHA-256 of the exact candidate transformer-toolchain lock used to frame
  receipt evaluation;
- both the canonical ID and the exact file SHA-256 of the Project Intelligence
  component/topology receipt;
- exact hashes and sizes for every referenced runtime artifact;
- exact hashes and sizes for every referenced evidence source; and
- ordered service observations plus one explicit enumeration state.

`component_topology_receipt_id` is the receipt's canonical semantic identity.
`component_topology_receipt_sha256` is the SHA-256 of the exact receipt file
placed in custody.  They are intentionally distinct bindings.

The candidate lock is referenced by hash.  V1 does not alter, copy, or extend
an identity-bearing candidate-lock format. The binding establishes the
artifact expectations used by the receipt; it does not by itself prove that
the launch was provisioned from that lock or loaded every artifact named by
it. Launch-provisioning provenance needs its own session evidence.

## Generic observation-set input

The CLI accepts
`workbench-crucible-mixin-runtime-service-observation-set-v1` with exactly
these top-level fields:

- `format`;
- `session`;
- `artifacts`;
- `evidence`;
- `observations`;
- `provider_enumeration`; and
- `limitations`.

The session contains `session_id`, `launch_id`, `profile_id`, `side`,
`candidate_toolchain_lock_sha256`, `component_topology_receipt_id`, and
`component_topology_receipt_sha256`.

Artifacts contain `artifact_sha256`, `size_bytes`, `role`, and `label`.
Evidence records contain `source_sha256`, `size_bytes`, `kind`, and `label`.
Every hash used by an observation or provider entry must resolve to one of
those exact bindings.

Evidence kind is closed to `runtime_log`, `runtime_probe`, and
`service_loader_enumeration`.  Service reports may use the first two;
provider enumeration requires the dedicated service-loader evidence kind.

`source_artifact_sha256` retains only the code-source artifact named by that
observation.  For example, a Mixin subsystem banner may name the artifact that
contains `MixinEnvironment` while the selected service implementation lives in
a different platform artifact.  A reported service name and subsystem source
therefore establish neither an implementation class nor a provider artifact.
Only a separately captured provider entry may make that class/artifact binding.

Observation sequence numbers are contiguous from one.  V1 observation kinds
are:

- `initialization_report`: a service announced initialization; this is context
  and is never counted as selected-service evidence;
- `selection_report`: the runtime explicitly reported a selected or booted
  service name; and
- `subsystem_report`: a Mixin subsystem banner reported its current service
  name, version, environment, and source artifact.

All observations require `sequence`, `kind`, `service_name`,
`evidence_sha256`, and `source_record`.  They may retain the exact reported
`service_class`, `subsystem_version`, `environment`, `source_artifact_sha256`,
`logger`, `level`, `thread`, and `wallclock`.  A subsystem report requires its
version, environment, and source artifact hash.

## Provider enumeration

`provider_enumeration.state` is exactly one of:

- `not_enumerated`: no bounded enumeration was performed;
- `enumerated`: the listed provider set is the complete result of the declared
  bounded enumeration; or
- `failed`: an enumeration was attempted but did not produce an admissible
  complete provider set.

Only `enumerated` may contain providers.  Each provider binds its implementation
class, reported service name, and provider artifact hash.
The referenced artifact must carry the `service_provider` role; a subsystem
code-source artifact carrying `mixin_runtime` cannot be promoted to provider
custody by matching a reported name.
`enumerated` and `failed` bind the exact
`java.util.ServiceLoader.iterator` mechanism, raw evidence SHA-256, semantic
provider-evidence ID, and a source record equal to that ID.  `enumerated` has a
null failure.  `failed` retains a typed failure and discards any partially
iterated provider list.  `not_enumerated` has an empty provider list and null
mechanism, evidence, evidence ID, source record, and failure.

The semantic evidence format is
[`mixin-service-provider-enumeration-evidence-v1.md`](mixin-service-provider-enumeration-evidence-v1.md).
Its complete raw bytes remain in Crucible custody and are bound by the receipt's
`service_loader_enumeration` evidence record.

The summary's `enumerated_provider_uniqueness` is derived only for an
`enumerated` result: `none`, `exactly_one`, or `multiple`.  It is `unknown` for
both `not_enumerated` and `failed`.
`enumerated_distinct_service_name_count` and
`duplicate_enumerated_service_names` expose duplicate reported names without
using those names as provider identity or collapsing multiple implementations.

## Reported selection

Only `selection_report` and `subsystem_report` observations contribute to
`reported_service_names`.  The derived `reported_selection_state` is:

- `not_reported` when neither kind appears;
- `one_name_reported` when all such reports name the same service; or
- `conflicting_names_reported` when they name more than one service.

`one_name_reported` deliberately says nothing about provider uniqueness.
Repeated identical reports remain multiple evidence records and do not become
an enumeration.

## Identity and canonical form

The machine representation is
[`mixin-runtime-service-receipt-v1.schema.json`](../schemas/mixin-runtime-service-receipt-v1.schema.json).
`receipt_id` has the form
`crucible-mixin-runtime-service-receipt:sha256:<lowercase-hex>`.  Its digest is
the SHA-256 of canonical JSON for the entire receipt except `receipt_id`.
Canonical JSON uses UTF-8, sorted object keys, compact separators, retained
array order, and no non-finite numbers.

The builder sorts artifact, evidence, provider, limitation, and reported-name
sets deterministically while retaining observation sequence order.  The
semantic parser recomputes the receipt identity, all derived summary fields,
all cross-references, and all mandatory limitations.
The CLI rejects duplicate JSON object keys before constructing the observation
set so parser last-key-wins behavior cannot hide conflicting inputs.

## Explicit boundaries

- A reported service name is not an enumerated provider set.
- A reported service name or subsystem code source does not establish the
  service implementation class or provider artifact.
- An enumerated provider set is not proof of runtime selection.
- A failed enumeration publishes no partial provider set and leaves provider
  uniqueness unknown.
- Log absence is not proof that a service or provider was absent.
- An artifact hash binds bytes but does not itself prove those bytes were
  selected or executed.
- The receipt contains no transformation outcome, final-class, or final-byte
  field.  It never proves that any Mixin transformation completed or survived
  into the host classloader.
- The receipt does not authorize a Blueprint or a release.
- The generic contract contains no Recurrent Complex integration or special
  lifecycle path.
