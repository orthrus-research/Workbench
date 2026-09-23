# Crucible Mixin transformation ledger v1

Status: experimental capture contract

## Purpose

The Mixin transformation ledger records ordered, typed observations from one
controlled launch.  It preserves the difference between a statically declared
configuration, runtime registration and preparation, an attempted Mixin
application, a config plugin callback, CleanMix post-processing, and the bytes
ultimately retained by the host classloader.

The ledger is transport and custody, not interpretation.  Crucible admits the
observations and their evidence.  Atlas may later answer causal questions over
an admitted ledger, while Sentinel may present findings authorized by an exact
platform policy.

## Identity and scope

Every ledger binds exactly one:

- launch and Crucible session;
- platform profile and logical side;
- Project Intelligence component/topology receipt SHA-256; and
- ordered observation stream.

The component receipt is referenced by its canonical SHA-256.  It is not
copied into the ledger, and the ledger does not extend or reinterpret an
identity-bearing platform candidate lock.

## Observation stages

V1 admits these stages without treating them as interchangeable:

1. `artifact_discovered`: a source artifact was inventoried;
2. `config_declared`: a manifest statically declared a configuration;
3. `config_registered`: a dedicated runtime lifecycle probe observed the
   configuration being submitted to the Mixin platform;
4. `config_prepared`: a dedicated runtime lifecycle probe observed the
   configuration being prepared for its phase;
5. `apply_started`: the runtime selected a mixin and began its applicator;
6. `plugin_post_apply_seen`: an `IMixinConfigPlugin.postApply` callback was
   observed for the named config;
7. `mixin_postprocess_seen`: CleanMix reported its distinct class
   post-processing operation;
8. `generated`: the Mixin class generator reported a generated class;
9. `final_class_defined`: the host retained exact final bytes for a class;
10. `final_member_attributed`: a final class member carried bounded attribution
   such as a validated `MixinMerged` annotation; and
11. `failure`: a named transformation or class-definition failure was
    captured.

In particular, a CleanMix audit `APPLY` entry maps only to `apply_started`.
CleanMix `POSTPROCESS` maps only to `mixin_postprocess_seen`; it is not a config
plugin `postApply` callback.

## Evidence rules

Each observation cites one immutable source digest and an evidence kind.  The
original log, receipt, callback record, crash report, or class dump remains in
ignored Crucible custody.  A ledger never embeds unrestricted log text.

The V1 stage-to-evidence relation is closed:

| Stage | Admissible evidence kinds |
| --- | --- |
| `artifact_discovered` | `artifact_scan` |
| `config_declared` | `manifest` |
| `config_registered` | `config_lifecycle_probe` |
| `config_prepared` | `config_lifecycle_probe` |
| `apply_started` | `cleanmix_audit`, `mixin_stage_export` |
| `plugin_post_apply_seen` | `plugin_callback` |
| `mixin_postprocess_seen` | `cleanmix_audit` |
| `generated` | `cleanmix_audit` |
| `final_class_defined` | `foundation_final_bytes` |
| `final_member_attributed` | `foundation_final_bytes` |
| `failure` | `crash_report` |

A manifest is static declaration evidence only. It cannot establish that the
platform submitted or prepared a configuration. Likewise, selecting a Mixin
runtime service establishes neither lifecycle event, so the separate runtime
service receipt is not a transformation-ledger evidence kind. Runtime
registration and preparation require a purpose-built
`config_lifecycle_probe` record.

CleanMix audit records can support only the positive operations named in the
table. They cannot establish a failure, and neither can the mere occurrence of
a plugin callback. V1 admits a `failure` only when a retained crash report
names the transformation or class-definition failure.

The generic assembler rejects duplicate JSON object keys before validating an
observation set. A last-key-wins parse is not an admissible way to resolve
conflicting lifecycle claims.

A `.mixin.out` export therefore supports only the conservative lower-bound
`apply_started` observation. It remains intermediate and can never support a
final-definition or final-member stage. `foundation_final_bytes` observations
must include the exact final class SHA-256.

An observation may state only `observed`, `failed`, or `unknown`. Final class
and final-member stages require `observed`; an `unknown` outcome cannot assert
or increment either final observation. V1 has no
generic `succeeded` outcome because no single intermediate callback proves the
complete transformer chain succeeded.

## Ordering and completion

Observation sequence numbers are contiguous from one and define ledger order.
For the same mixin/config/target correlation, lifecycle stages cannot move
backward.  `failure` may occur at any point.  Wall-clock timestamps and thread
names are optional context and never determine canonical order.

The launch state is `complete`, `crashed`, or `incomplete`.  `complete` means
the declared capture procedure reached its clean terminal seal; it does not
mean every `apply_started` mixin is individually proven successful.  A crash
or incomplete launch may still retain useful prefix evidence.

## Canonical representation

The machine representation is
[`mixin-transformation-ledger-v1.schema.json`](../schemas/mixin-transformation-ledger-v1.schema.json).
The ledger ID is `crucible-mixin-ledger:sha256:<digest>`, where `<digest>` is
the SHA-256 of canonical JSON excluding the `ledger_id` field.  Canonical JSON
uses UTF-8, sorted object keys, compact separators, and no non-finite numbers.

## Explicit boundaries

- The ledger does not make `cleanmix.log` or `.mixin.out` final-byte truth.
- It does not infer that absence from a best-effort log is known absence.
- It does not count inactive phase proxies as active transformation authority.
- It does not authorize a Blueprint or a release.
- It introduces no Recurrent Complex integration, adapter, import, reflection,
  package heuristic, or special lifecycle path.
