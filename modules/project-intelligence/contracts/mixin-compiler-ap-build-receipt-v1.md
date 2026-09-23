# Mixin compiler/AP build receipt V1

## Purpose

This additive receipt records exact caller-declared custody around one Mixin
compiler and annotation-processor invocation. It binds the compiler executable,
runtime identity, processor archives and their standard service entries,
ordered options, diagnostics, sources and other inputs, and generated refmap,
mapping, and CleanMix compatibility outputs.

The receipt answers **which exact bytes and declarations were placed in one
invocation-custody set?** It does not answer **can an independent party
reproduce the outputs from those inputs?**

This contract does not modify the topology, dependency-closure, or packaged AP
compatibility receipt V1 identities. It also does not modify a Cleanroom
candidate lock or transformer-toolchain lock.

## Input boundary

The generic producer accepts a `MixinCompilerAPInvocationInput` and exact bytes
for one V1 policy. The invocation retains caller order for every sequence for
which order can affect compiler behavior:

1. one compiler executable and one exact compiler-identity capture;
2. one or more runtime artifacts and one exact runtime-identity capture;
3. annotation-processor archives in processor-path order;
4. compiler options and annotation-processor options as separate ordered
   string arrays;
5. one exact diagnostics capture, including a valid empty capture;
6. source files in declared compilation order;
7. other typed input artifacts, including classpath and mapping inputs; and
8. typed generated outputs.

Every file slot has a stable label and logical name. Exact bytes produce a
SHA-256 and byte size. A caller can deliberately declare an absent file; its
record has `present: false`, null hash and size, and a content identity for the
absent slot. Missing required custody produces a negative receipt rather than
being silently omitted.

The producer does not retain workstation paths. Generated receipts, specs, and
captures belong under ignored `.workbench/` storage or another declared
external evidence store.

## Processor and output facts

Every declared processor archive is hashed in full and inspected without
loading its code. The producer applies the existing bounded ZIP inventory
rules, reads the policy-selected
`META-INF/services/javax.annotation.processing.Processor` member, and records
its exact hash, size, parse state, and sorted ServiceLoader provider list.
Malformed archives fail closed before publication. A missing or malformed
service entry remains a deterministic negative fact.

The initial exact
[`CleanMix 0.7.0 compiler/AP policy`](../../../profiles/platforms/cleanroom/mixins/cleanmix-compiler-ap-build-policy-v1.json)
requires:

- the exact published CleanMix `0.7.0` archive SHA-256 already bound by the
  Cleanroom transformer-toolchain lock;
- `MixinObfuscationProcessorInjection` and
  `MixinObfuscationProcessorTargets` in the processor service entry;
- at least one exact compiler runtime artifact and source; and
- present `refmap`, `mapping`, and `cleanmix-compatibility` output kinds, with
  the compatibility output logically named
  `cleanmix_version_compatibility.json`.

The processor declarations and compatibility-output behavior are bound to
CleanMix revision `24898e32adc735fe48f4a3732b1831bd566c4626`. The relevant
primary sources are the
[`Processor` service descriptor](https://github.com/CleanroomMC/CleanMix/blob/24898e32adc735fe48f4a3732b1831bd566c4626/src/ap/resources/META-INF/services/javax.annotation.processing.Processor),
[`SupportedOptions.java`](https://github.com/CleanroomMC/CleanMix/blob/24898e32adc735fe48f4a3732b1831bd566c4626/src/ap/java/org/spongepowered/tools/obfuscation/SupportedOptions.java),
and
[`CompatibilityManager.java`](https://github.com/CleanroomMC/CleanMix/blob/24898e32adc735fe48f4a3732b1831bd566c4626/src/ap/java/org/spongepowered/tools/obfuscation/CompatibilityManager.java).

Output bytes are not parsed by this contract. Refmap and mapping semantics are
separate checks. The packaged AP compatibility conformance receipt separately
compares compiled annotations with the compatibility JSON.

## Custody decisions

`summary.custody_state` is `complete` only when all generic and policy-required
slots are present and the processor bindings conform. Otherwise it is
`partial`, and every reason appears as a content-addressed issue. Complete
custody does not assert that compilation succeeded: an invocation that emits
complete outputs and non-empty diagnostics can still have complete custody.

The receipt explicitly fixes these evidence boundaries:

- caller-declared single-invocation custody is recorded;
- exact declared bytes are bound;
- compiler process execution is not attested;
- source-to-output causality is not proved;
- reproducible-build equivalence is not proved; and
- runtime Mixin behavior is not observed.

Environment variables, filesystem state outside declared inputs, locale,
network access, process scheduling, and time are outside V1. A later
reproducibility contract would require a hermetic execution description and an
independent deterministic rerun; it must not reinterpret this V1 receipt.

## Identity and validation

Canonical JSON V1 content-addresses every file, service entry, processor,
classified input, classified output, option set, issue, invocation, and final
receipt. The semantic validator rejects unknown fields and recomputes child
identities, policy-derived issues, all counts, custody state, and the final
receipt identity.

The bound validator additionally rebuilds the receipt from the original
`MixinCompilerAPInvocationInput` and exact policy bytes. This is the check that
detects substitution of a source, processor, diagnostics capture, option, or
generated output even if an attacker recomputes otherwise well-formed content
identities.

## CLI

The CLI consumes a strict
`workbench-mixin-compiler-ap-build-input-v1` JSON document. Relative file paths
are resolved from the specification's directory. A file entry has exactly
`label`, `logical_name`, and `path`; classified inputs and outputs add `kind`.

```json
{
  "annotation_processor_options": [
    "-AoutRefMapFile=example.refmap.json",
    "-AoutSrgFile=example.srg"
  ],
  "compiler": {
    "compiler_identity_capture": {
      "label": "javac-version",
      "logical_name": "captures/javac-version.txt",
      "path": "captures/javac-version.txt"
    },
    "executable": {
      "label": "javac",
      "logical_name": "jdk/bin/javac",
      "path": "inputs/javac"
    },
    "runtime_artifacts": [
      {
        "label": "java-runtime",
        "logical_name": "jdk/bin/java",
        "path": "inputs/java"
      }
    ],
    "runtime_identity_capture": {
      "label": "java-version",
      "logical_name": "captures/java-version.txt",
      "path": "captures/java-version.txt"
    }
  },
  "compiler_options": ["-source", "8", "-target", "8"],
  "diagnostics": {
    "label": "diagnostics",
    "logical_name": "captures/javac-diagnostics.bin",
    "path": "captures/javac-diagnostics.bin"
  },
  "format": "workbench-mixin-compiler-ap-build-input-v1",
  "inputs": [
    {
      "kind": "mapping-input",
      "label": "mcp-srg",
      "logical_name": "mappings/input.srg",
      "path": "inputs/input.srg"
    }
  ],
  "invocation_label": "example-main-compile",
  "outputs": [
    {
      "kind": "refmap",
      "label": "refmap",
      "logical_name": "example.refmap.json",
      "path": "outputs/example.refmap.json"
    },
    {
      "kind": "mapping",
      "label": "mapping",
      "logical_name": "example.srg",
      "path": "outputs/example.srg"
    },
    {
      "kind": "cleanmix-compatibility",
      "label": "compatibility",
      "logical_name": "cleanmix_version_compatibility.json",
      "path": "outputs/cleanmix_version_compatibility.json"
    }
  ],
  "processors": [
    {
      "label": "cleanmix-ap",
      "logical_name": "processor-path/cleanmix-0.7.0.jar",
      "path": "inputs/cleanmix-0.7.0.jar"
    }
  ],
  "schema_version": 1,
  "sources": [
    {
      "label": "example-mixin",
      "logical_name": "src/example/ExampleMixin.java",
      "path": "inputs/ExampleMixin.java"
    }
  ]
}
```

Run:

```bash
python3 tools/assemble_mixin_compiler_ap_build_receipt.py \
  --policy profiles/platforms/cleanroom/mixins/cleanmix-compiler-ap-build-policy-v1.json \
  --output .workbench/mixin-compiler-ap-build.json \
  .workbench/mixin-compiler-ap-build-input.json
```

The receipt is published atomically. A partial receipt is still published and
then returns status `2`. `--allow-partial` is reserved for evidence collection
that intentionally retains an incomplete invocation-custody set.
