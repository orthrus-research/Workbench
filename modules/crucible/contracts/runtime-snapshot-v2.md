# Crucible Composite Runtime Snapshot V2

This contract gives one exact launch a single content-addressed snapshot ID
without creating a second runtime truth. The snapshot is a manifest over
semantically validated typed receipts. Crucible owns capture and composition;
Atlas owns interpretation; Blueprints owns construction gates.

V1 receipt formats remain verbatim. V2 never repairs, widens, or silently
reinterprets an older receipt. A receipt enters `receipt_bindings` only after
its own versioned semantic validator passes, and every binding must match the
snapshot's launch, platform profile, physical side, declared session or capture
identity, and applicable candidate/toolchain locks.

## Runtime epoch and capabilities

`epoch` is a profile-owned adapter declaration. Generic Crucible code retains
only its ID, adapter version, explicit version axes, and stable capability
names. Cleanroom-, CleanMix-, Foundation-, MixinBooter-, and LaunchWrapper-
specific class ownership therefore stays under `profiles/platforms/cleanroom/`.

Every capability declared by the epoch has exactly one snapshot row:

- `observed`: complete evidence for the adapter's bounded projection;
- `partial`: valid evidence with an explicit coverage boundary;
- `not_observed`: the epoch declares the capability, but this capture did not
  observe it;
- `unavailable`: the profile epoch adapter declares no producer for the
  capability; or
- `failed`: the observer attempted the capability and retained a failure
  receipt.

`unavailable` and `not_observed` are not synonyms. Evidence-backed rows bind
receipt IDs and a semantic fingerprint. The fingerprint covers only the
profile adapter's stable capability projection, not volatile launch identity
or the entire runtime.

## Failure and crash behavior

`capture_health` is independent of application success. A crashed or incomplete
launch may still publish a valid snapshot when observer start, terminal health,
retained errors, and missing capabilities are explicit. A healthy observer
cannot publish errors or an unfinished capture.

## Cross-epoch comparison

The V2 comparison function joins stable capability names rather than concrete
implementation classes. Each result is `equivalent`, `changed`, `unavailable`,
or `not-observed`. Equality is bounded semantic-fingerprint equality and never
claims whole-runtime equivalence or a cause for a change.

## Assembly

The checked assembler accepts a transient V2 plan containing the runtime
identity, profile epoch, receipt file labels, capability coverage, and observer
health. It semantically validates each known receipt, computes raw-file and
bounded semantic hashes, resolves capability rows by receipt role, and refuses
to replace an existing output:

```bash
python3 modules/crucible/tools/assemble_runtime_snapshot.py \
  --plan .workbench/evidence/runtime-snapshot-plan-v2.json \
  --output .workbench/evidence/runtime-snapshot-v2.json
```

Schema: [`../schemas/runtime-snapshot-v2.schema.json`](../schemas/runtime-snapshot-v2.schema.json).
