# Mixin AP compatibility conformance receipt V1

## Purpose

This additive receipt compares the CLASS-retained annotations in one exact
JAR/ZIP with the annotation-processor compatibility map packaged in that same
archive. It answers a narrow question: **does the packaged metadata contain
exactly the class defaults and explicit member overrides implied by the
packaged Mixin bytecode under the bound policy?**

It does not change the Mixin topology receipt V1, the Cleanroom Doctor report
V1, a candidate lock, or a transformer toolchain lock.

The generic implementation is policy-driven. The initial exact policy is
[`cleanmix-ap-compatibility-policy-v1.json`](../../../profiles/platforms/cleanroom/mixins/cleanmix-ap-compatibility-policy-v1.json),
which binds CleanMix 0.7.0 source revision
`24898e32adc735fe48f4a3732b1831bd566c4626`.

## Pinned CleanMix semantics

At the bound source revision:

- `@Mixin` and `@Compatibility` use CLASS retention;
- every annotation-processor-registered Mixin gets a class metadata key;
- an unannotated Mixin class gets the AP's latest behavior default, `0.6.0`;
- an explicit class `@Compatibility` replaces that default;
- explicit method, constructor, and field annotations add member keys and
  override the enclosing class at runtime;
- method keys are `class::name(descriptor)return`, with constructors named
  `<init>`;
- field keys are `class::name:descriptor`; and
- the AP writes `cleanmix_version_compatibility.json` to `CLASS_OUTPUT`.

Primary sources are
[`Compatibility.java`](https://github.com/CleanroomMC/CleanMix/blob/24898e32adc735fe48f4a3732b1831bd566c4626/src/main/java/org/spongepowered/asm/mixin/Compatibility.java),
[`CompatibilityManager.java`](https://github.com/CleanroomMC/CleanMix/blob/24898e32adc735fe48f4a3732b1831bd566c4626/src/ap/java/org/spongepowered/tools/obfuscation/CompatibilityManager.java),
and
[`CleanroomUtil.java`](https://github.com/CleanroomMC/CleanMix/blob/24898e32adc735fe48f4a3732b1831bd566c4626/src/main/java/org/spongepowered/asm/mixin/CleanroomUtil.java).

## Inputs and scan boundary

The producer accepts:

1. exact bytes plus a stable label for one regular archive; and
2. exact bytes for a V1 AP-compatibility policy.

It inventories the archive with the existing bounded ZIP safety rules. It
parses JVM constant pools, class/field/method tables, and both visible and
invisible annotation attributes without loading code. It selects classes that
carry the policy's Mixin annotation descriptor, reads their Compatibility
annotations, and constructs the expected metadata map.

Malformed class files, malformed Compatibility annotations, non-matching class
paths, duplicate logical Mixin classes, and duplicate expected member keys fail
closed before a receipt is published. Multi-release variants that declare the
same logical Mixin class are deliberately unsupported until a runtime-selection
policy is bound.

## Conformance decisions

The metadata resource retains its exact SHA-256, byte size, parse state, and
invalid-entry facts. Valid AP output versions must be canonical
`major.minor.patch` values with each component from 0 through 999. Annotation
versions are normalized the same way CleanMix's AP normalizes them.

When declarations exist, all of the following are nonconformant:

- the metadata resource is missing or invalid JSON;
- the JSON root is not an object;
- an entry key or version is invalid or noncanonical;
- a required class-default or member-override key is missing;
- an undeclared key is present; or
- a declared key has the wrong version.

An archive with neither Mixin declarations nor the metadata resource is
`not-applicable`. A metadata resource without packaged Mixin declarations is
nonconformant rather than silently accepted.

The receipt publishes deterministic declarations, expected entries, actual
entries, and issues. Each top-level child record and the receipt itself is
content-addressed with canonical JSON V1. The semantic validator recomputes
all derivable expected entries, issues, counts, and disposition. The bound
validator rescans the exact artifact and policy bytes and requires the rebuilt
receipt to match.

## Explicit limits

This is packaged-output conformance, not compiler reproducibility. It does not
prove:

- that either advertised annotation processor executed;
- which compiler, processor JAR, options, diagnostics, or incremental-build
  state produced the archive;
- refmap or obfuscation-mapping correctness;
- runtime classpath order, configuration source ownership, or which metadata
  resource CleanMix selects; or
- configuration preparation, Mixin application, or final transformed bytes.

Those facts remain compiler/build receipt, runtime-service, and Crucible
custody respectively.

## CLI

```bash
python3 tools/inspect_mixin_ap_compatibility.py \
  --policy profiles/platforms/cleanroom/mixins/cleanmix-ap-compatibility-policy-v1.json \
  --output .workbench/mixin-ap-compatibility.json \
  path/to/mod.jar
```

The output is published atomically. The command returns exit status `2` after
publication when the receipt is nonconformant. `--allow-nonconformant` is
available only for evidence-collection workflows that intentionally retain a
negative receipt.
