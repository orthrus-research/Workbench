# Crucible Mixin defining-loader discovery trace receipt V2

Status: implemented experimental runtime evidence contract

## Purpose

This contract records one ordered service-discovery execution observed around
the Mixin runtime's own defining-loader iterator. It answers which bootstrap
and service providers were attempted, how those attempts ended, and which
service object was selected during one exact controlled launch.

V2 is additive to the
[service-provider enumeration evidence V1](mixin-service-provider-enumeration-evidence-v1.md).
V1 independently enumerates the thread-context classloader and identity-sorts
the complete provider set. V2 preserves execution order from the iterator
that the instrumented `MixinService` code already uses. Neither format may be
relabelled as the other.

The generic receipt and semantic parser live under `modules/crucible/`.
Platform profiles own exact byte substitutions, source hashes, launch
fixtures, and imports. The first exact importer observes CleanMix 0.7.0 on the
Cleanroom `0.6.8-alpha` candidate; those product and platform versions are not
generic defaults.

## Receipt surface

`format` is fixed to
`workbench-crucible-mixin-defining-loader-discovery-trace-receipt-v2` and
`schema_version` is `2`. The receipt retains:

- `session`: capture, launch, platform profile, logical side, and exact
  candidate/toolchain lock hashes;
- `inputs`: exact hash, size, and label bindings for the raw trace, both locks,
  observer agent, Mixin engine, selected-provider artifact, launch log, and
  fixture result;
- `observation`: the exact substituted class input/output hashes, its code
  source, Java version, and defining-loader/parent identities;
- `bypass_properties`: ordered bootstrap and service property observations and
  whether discovery used `ServiceLoader` or a system-property class;
- `ordered_attempts`: contiguous attempt IDs with ordered event projections,
  construction, bootstrap return/class-name, service-name, validity,
  selection, terminal outcome, and typed failures where present;
- `selection`: the one selected provider and exact measured source artifact;
- `failures`, independent start/end `health`, derived `summary`, and explicit
  evidence `boundaries`.

The closed Draft 2020-12 schema is
[`mixin-defining-loader-discovery-trace-receipt-v2.schema.json`](../schemas/mixin-defining-loader-discovery-trace-receipt-v2.schema.json).
It rejects unknown fields at every receipt object boundary. Schema validation
establishes structure only. Admission also requires the V2 semantic parser,
which recomputes identity, checks event ordering and projections, and verifies
derived summary and boundary constants.

## Exact evidence binding

The exact-profile importer must fail closed unless all of these bindings hold:

- the observed target input bytes equal the `MixinService.class` entry in the
  measured Mixin-engine artifact;
- the observed output bytes equal the instrumented payload in the measured
  observer-agent artifact;
- the target and provider code-source URIs resolve to the supplied measured
  artifacts;
- provider classes and their Java service descriptors exist in those exact
  provider bytes; and
- the raw trace is complete, ordered, internally consistent, and independently
  ends in declared health.

These are local evidence-integrity checks, not remote attestation. The lock
hashes frame evaluation but do not independently prove that every locked
artifact was loaded by the launch.

## Identity and canonicalization

`canonicalization_id` is `workbench-canonical-json-v1`: UTF-8 JSON, sorted
object keys, compact separators, retained array order, and no non-finite
numbers. `receipt_id` is
`crucible-mixin-defining-loader-discovery-trace:sha256:<lowercase-hex>`, where
the digest covers the canonical complete receipt except `receipt_id`.

Changing an input binding, attempt/event order, selected provider, failure,
health result, summary, or boundary therefore changes the semantic identity.

## Observation and non-perturbation boundary

The implemented observation mechanism is an opt-in Java instrumentation agent
guarded by the exact original `MixinService` class hash. It adds observation
calls around the existing iterator and provider calls. It does not enumerate
with the thread-context loader, add a service provider or descriptor, mutate a
bypass property, or introduce another selection route. A rejected hash or an
observer write failure cannot produce a healthy receipt.

`selected_service_proved_for_this_launch` means only that this exact observed
discovery selected the retained object while observation ended healthy. V2
does not prove configuration admission, mixin application completion,
transformer-chain custody, or final defined class bytes. Those require their
own additive evidence formats.

The generic contract contains no Recurrent Complex packages, service entries,
reflection, registration, or lifecycle behavior. External structure
decoration remains an independent Forge participant.
