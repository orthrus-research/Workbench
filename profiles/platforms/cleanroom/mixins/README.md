# Cleanroom mixin policy

The versioned JSON policy in this directory is the reusable static policy for
consumer mods targeting Cleanroom's native CleanMix stack. It maps scanner
artifact facts to accept, review, or reject findings and records the
primary-source evidence behind registration, phase, compatibility, ownership,
and embedded-package rules.

Candidate directories bind the policy to exact platform and artifact inputs.
Neither the policy nor a candidate toolchain lock is a runtime receipt: they do
not prove installed bytes, selected services, plugin decisions, or transformed
class output.

Implemented assets:

- [`cleanroom-mixin-doctor-policy-v1.json`](cleanroom-mixin-doctor-policy-v1.json)
  contains 41 rules across all nine scanner fact roots, including exact
  `requiredFeatures` normalization and bounded plugin/connector dependency
  closure;
- [`Cleanroom Mixin Doctor report V1`](cleanroom-mixin-doctor-report-v1.md)
  defines deterministic findings and explicit per-rule coverage; and
- [`cleanroom-mixin-doctor-report-v1.schema.json`](cleanroom-mixin-doctor-report-v1.schema.json)
  validates the report envelope.

The separate
[`cleanmix-ap-compatibility-policy-v1.json`](cleanmix-ap-compatibility-policy-v1.json)
binds CleanMix 0.7.0 annotation descriptors, metadata resource, member-key
encoding, behavior default, and exact source revision for the generic Project
Intelligence packaged AP-conformance verifier. It does not modify either
Doctor report version or claim compiler/runtime evidence.

[`cleanmix-compiler-ap-build-policy-v1.json`](cleanmix-compiler-ap-build-policy-v1.json)
binds the exact published CleanMix 0.7.0 processor archive, both standard
annotation-processor service providers, and required refmap, mapping, and
compatibility-output custody for the generic Project Intelligence build
receipt. Complete custody is not compiler-process attestation or
reproducible-build proof.

The
[`cleanmix-0.7.0-runtime-regression-matrix-v1.json`](cleanmix-0.7.0-runtime-regression-matrix-v1.json)
asset binds the distribution, source, compatibility-behavior, exact candidate,
phase-fix, and PR 3 identities for 12 fresh-JVM case specifications. It is an
executable specification whose original identity and creation-time harness
state remain unchanged. The additive X01 runner now executes all nine P0 rows;
its ignored, content-addressed report is the runtime observation, not a rewrite
of this V1 asset.

The profile-owned
[`cleanroom-runtime-analysis-epochs-v1.json`](cleanroom-runtime-analysis-epochs-v1.json)
catalog keeps Cleanroom, CleanMix, Foundation, and Mixin distribution versions
as separate axes. It records the source-grounded transition from the exact
`0.5.17-alpha` MixinBooter/LaunchWrapper seam, through Cleanroom-owned service
selection with inherited LaunchWrapper providers, to the exact
`0.6.8-alpha` CleanMix `0.7.0` Foundation-provider seam. Only the last epoch is
candidate-bound. Cleanroom `0.7.x` remains an unresolved target until an exact
release/source/artifact lock and adapter are admitted.

For the candidate-bound epoch, the exact-hash observer now implements ordered
service discovery, selected-service component capture, native config
admission/phase custody, transformer-chain rebuild epochs, and Foundation final
class-definition bytes. The chain
capture retains ordered live/delegated entries, exact implementation artifacts,
refresh causes, and the separate provider/Foundation exclusion layers. The
component capture
observes Cleanroom-owned Foundation providers without installing or enumerating
a second service and treats the still-present LaunchWrapper/Foundation bridge
as measured runtime structure, not as erased legacy behavior.

Run the fresh-JVM P0 matrix with:

```bash
python3 profiles/platforms/cleanroom/tools/run_cleanmix_p0_matrix.py \
  --output .workbench/evidence/cleanmix-runtime-conformance/x01-execution
```

The runner refuses an existing destination, launches a physical client for the
client row and dedicated servers for the other rows, and content-addresses the
complete execution report. That immutable X01 report retains its creation-time
XPASS and review state. X02 resolved the XPASS as a false first-entry oracle
and closed the handler claim as `historical_defect_not_reproduced` using direct
application joined to Foundation final bytes. The bounded M2 project is
complete; Matrix V2 and multi-epoch implementations of candidate-bound
observers remain separate work.

Run the exact profile evaluator with:

```bash
python3 profiles/platforms/cleanroom/tools/run_mixin_doctor.py \
  --fail-on reject \
  --output .workbench/cleanroom-mixin-doctor.json mod.jar
```

Partial or unavailable rule coverage forces review. It cannot silently become
an accept result. The retained fixture evaluates 34 rules and leaves seven
bytecode/runtime/dependency-closure rules explicitly unavailable.
