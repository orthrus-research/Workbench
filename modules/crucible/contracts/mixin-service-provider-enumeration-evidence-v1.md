# Crucible Mixin service-provider enumeration evidence v1

Status: experimental runtime evidence contract

## Purpose

This contract records one bounded Java `ServiceLoader` enumeration of
`org.spongepowered.asm.service.IMixinService` in a launched process.  It is
the evidence source for the independent `provider_enumeration` surface in a
Mixin runtime-service receipt.

Provider enumeration is not runtime selection.  The probe records every
provider implementation returned by the chosen context classloader, but it
does not ask Mixin which implementation it selected and never turns a reported
Mixin service name into a provider class or artifact attribution.

## Probe mechanism

V1 fixes these mechanism fields:

- `probe_id`: `workbench-mixin-service-provider-enumerator-v1`;
- `mechanism`: `java.util.ServiceLoader.iterator`;
- `service_interface`: `org.spongepowered.asm.service.IMixinService`; and
- `class_loader_kind`: `thread_context`.

Iteration instantiates each provider and calls its public `getName()` method.
The provider source is taken from that implementation class's protection-domain
code source, not from `MixinEnvironment`, a subsystem banner, or the reported
service name.  The probe requires a regular non-symlink source artifact and
records its file URI, SHA-256, and size.

## Complete and failed observations

`state` is `enumerated` or `failed`.

- `enumerated` contains the complete provider set observed after the iterator
  reached exhaustion and has `failure: null`.
- `failed` contains no providers and has a typed failure.  A partially iterated
  set is deliberately discarded.

Failure stages are `service_interface_load`, `service_loader_create`,
`service_loader_iteration`, `provider_name`, `provider_code_source`, or
`artifact_measurement`.

Providers are identified by implementation class plus exact provider artifact
SHA-256.  `service_name` is a reported attribute, not provider identity.
Different implementations may report the same name; such duplicate names are
retained and summarized without collapsing the provider count.

## Session and identity

Each evidence document binds the same session, launch, profile, logical side,
candidate transformer-toolchain lock SHA-256, component-topology receipt ID,
and component-topology receipt file SHA-256 used by the enclosing runtime
service receipt.

`evidence_id` has the form
`crucible-mixin-service-provider-enumeration:sha256:<lowercase-hex>`.  Its
digest is SHA-256 over canonical JSON for the complete document except
`evidence_id`.  Canonical JSON uses UTF-8, sorted object keys, compact
separators, retained array order, and no non-finite numbers.  Provider rows are
sorted by implementation class, provider artifact SHA-256, and service name.

The raw evidence file remains in ignored Crucible custody.  A runtime-service
receipt binds its complete bytes by SHA-256 and size as well as its semantic
`evidence_id`.

## Admission boundary

The semantic parser recomputes identity, summary, ordering, and boundary
constants.  An exact platform importer additionally re-hashes each source
artifact, resolves the recorded file URI to the supplied artifact, and checks
that the provider implementation class and Java service descriptor exist in
those exact bytes.

This is evidence integrity, not remote attestation.  It does not establish
which service Mixin selected, whether provider initialization beyond
construction succeeded, whether a transformation ran, or which bytes survived
into a final class definition.
