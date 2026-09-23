# Mixin topology and transformation observability

Status: implemented experimental evidence path for the exact Cleanroom
`0.6.8-alpha` candidate.

## Purpose

Workbench keeps three questions separate:

1. Which Mixin components and declarations are present in exact archive bytes?
2. Which lifecycle and transformation stages were observed in one launch?
3. Which bootstrap and service providers were attempted and selected by that
   launch's defining loader?

Static inspection cannot prove runtime behavior. Runtime logs cannot prove
archive contents or final class bytes. Every receipt therefore binds its exact
inputs and retains missing, partial, failed, and unavailable evidence without
turning absence into success.

## Evidence path

```text
exact JAR/ZIP bytes
  -> Project Intelligence topology and conformance receipts
  -> Cleanroom Mixin Doctor policy report

exact controlled launch
  -> Cleanroom profile importers
  -> Crucible runtime receipts
  -> Composite Runtime Snapshot V2

Foundation class-definition seam
  -> final bytes for explicitly requested classes only
```

### Static evidence

[Project Intelligence](../../modules/project-intelligence/README.md) reads
JAR and ZIP bytes without loading archive code. Its
[topology receipt](../../modules/project-intelligence/contracts/mixin-component-topology-receipt-v1.md)
binds archive and entry hashes, manifest routes, Mixin configuration resources,
declared classes, plugins, refmaps, compatibility metadata, embedded package
namespaces, and normalized owner collisions.

Additional static receipts cover:

- [dependency closure](../../modules/project-intelligence/contracts/mixin-dependency-closure-receipt-v1.md),
  including explicit incompleteness;
- [packaged CleanMix annotation-processor conformance](../../modules/project-intelligence/contracts/mixin-ap-compatibility-conformance-receipt-v1.md);
  and
- [compiler and annotation-processor custody](../../modules/project-intelligence/contracts/mixin-compiler-ap-build-receipt-v1.md),
  which binds caller-declared inputs and outputs but does not attest compiler
  execution or reproducibility.

The Cleanroom profile applies
[`cleanroom-mixin-doctor-policy-v1.json`](../../profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-policy-v1.json)
to those facts. Its
[Doctor report](../../profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-report-v1.md)
contains a coverage result for every policy rule. Missing coverage requires
review; it is never an implicit pass.

Static inspection does not decode every target annotation or injection point,
prove target-member compatibility, identify every relocated implementation,
or observe process-only properties. Those limits remain findings or unavailable
coverage rather than guesses.

### Runtime evidence

Cleanroom-specific importers remain under `profiles/platforms/cleanroom/`;
product-generic receipt semantics remain under `modules/crucible/`.

The implemented receipt families are:

- [runtime service receipt V1](../../modules/crucible/contracts/mixin-runtime-service-receipt-v1.md),
  which keeps a reported selection distinct from an independent provider
  enumeration;
- [defining-loader discovery trace V2](../../modules/crucible/contracts/mixin-defining-loader-discovery-trace-receipt-v2.md),
  which records ordered bootstrap and service attempts, bypass properties,
  validity outcomes, failures, and the selected provider;
- [selected service components V1](../../modules/crucible/contracts/mixin-selected-service-components-receipt-v1.md),
  which records the actual service, loader, providers, tracker, audit trail,
  and logger exposed by the selected service;
- [configuration lifecycle V1](../../modules/crucible/contracts/mixin-config-lifecycle-receipt-v1.md),
  which records resource ownership, feature checks, phase gating, preparation,
  promotion, deferral, duplication, rejection, and failure;
- [transformer-chain epoch V1](../../modules/crucible/contracts/mixin-transformer-chain-epoch-receipt-v1.md),
  which records ordered live and delegated chains at observed rebuilds;
- [transformation ledger V1](../../modules/crucible/contracts/mixin-transformation-ledger-v1.md),
  which joins declared configuration facts to observed lifecycle stages without
  inferring missing applications; and
- [final class-definition V1](../../modules/crucible/contracts/mixin-final-class-definition-receipt-v1.md),
  which joins successful Foundation definitions to exact dump bytes for only
  the requested targets.

[Composite Runtime Snapshot V2](../../modules/crucible/contracts/runtime-snapshot-v2.md)
combines semantically valid receipts for one exact launch and profile epoch. It
keeps `observed`, `partial`, `not_observed`, `unavailable`, and `failed`
distinct. Composition does not widen or repair an input receipt.

## Evidence boundaries

CleanMix registration and transformation are progressive. A registered or
prepared configuration does not prove that each target was transformed.
Likewise, `APPLY` is emitted before applicator completion and `GENERATE` before
export and serialization. Audit rows are navigation evidence, not final-byte
proof.

Provider enumeration is not selection evidence unless it observes the same
defining-loader path and selection decisions. A selected provider does not
prove configuration activation, transformer execution, or application health.
A chain entry does not prove that the transformer ran for every class.

Only the final-definition receipt proves successful definition and final bytes,
and only for its named targets. It does not prove method behavior, gameplay,
whole-process coverage, or compatibility with another runtime epoch.

The candidate
[`transformer-toolchain-lock-v1.json`](../../profiles/platforms/cleanroom/candidates/0.6.8-alpha/transformer-toolchain-lock-v1.json)
binds expected transformation-critical artifacts. It does not prove that a
particular launch was provisioned from, loaded, or selected every locked
artifact.

## Commands

Static inspection and policy evaluation:

```bash
python3 tools/inspect_mixin_artifacts.py --help
python3 tools/inspect_mixin_dependency_closure.py --help
python3 tools/inspect_mixin_ap_compatibility.py --help
python3 tools/assemble_mixin_compiler_ap_build_receipt.py --help
python3 profiles/platforms/cleanroom/tools/run_mixin_doctor.py --help
```

Exact-profile runtime imports:

```bash
python3 profiles/platforms/cleanroom/tools/import_cleanmix_runtime_service.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_defining_loader_discovery_trace.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_service_components.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_config_lifecycle.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_transformer_chain.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_audit.py --help
python3 profiles/platforms/cleanroom/tools/import_foundation_final_definitions.py --help
```

Generic assembly:

```bash
python3 modules/crucible/tools/assemble_mixin_runtime_service_receipt.py --help
python3 modules/crucible/tools/assemble_mixin_transformation_ledger.py --help
python3 modules/crucible/tools/assemble_runtime_snapshot.py --help
```

Write generated receipts, reports, logs, traces, class dumps, and provisioned
artifacts only to mutable Workbench state: ignored `.workbench/` storage in a
source checkout or the declared external state root for an installed suite.

## Authority and custody

- Project Intelligence owns exact static component and declaration facts.
- The Cleanroom profile owns candidate-specific policy, import semantics, and
  runtime epoch declarations.
- Crucible owns controlled observation, receipt validation, and composition.
- Atlas may interpret admitted evidence while preserving its stated limits.
- Blueprints may gate construction only on declared admitted evidence.
- Manuals may teach the workflow but cannot authorize code or reinterpret a
  receipt.

Observers must not add providers, alter descriptors or bypass properties,
request retransformation, or create a second selection route. A producer may
fail open toward the observed application only when it fails closed toward its
receipt. Recurrent Complex receives no special adapter or registration path;
its standard lifecycle effects are observed like those of any other mod.
