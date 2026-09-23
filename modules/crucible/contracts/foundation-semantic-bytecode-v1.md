# Foundation semantic-bytecode comparison contract V1

## Purpose and authority

This contract defines a comparison-only fingerprint for complete Foundation
final-class dump generations. Foundation's exact bytes and exact whole-dump
manifest remain the custody authority. A semantic-bytecode digest never
replaces an exact class SHA-256, binds an actor, establishes code provenance,
or authorizes a hook.

V1 addresses one observed source of cross-process byte inequality:
SpongePowered Mixin writes a random session UUID into the `sessionId` element
of method-level, runtime-visible `MixinMerged` annotations. It grants no
general permission to remove annotations, debug metadata, constant-pool rows,
or other nondeterministic bytes.

## Content-addressed policy

The implementation publishes the complete policy material and its canonical
JSON SHA-256. Its policy ID has the form:

`workbench-semantic-bytecode-policy:sha256:<policy-sha256>`

Two dumps may be compared semantically only under the identical policy ID.
Policy V1 accepts class-file major versions 45 through 69 and performs one
length-preserving rewrite:

1. parse the complete class-file and constant pool;
2. locate a `RuntimeVisibleAnnotations` attribute owned by a method;
3. locate annotation descriptor
   `Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;`;
4. require exactly one `sessionId` element with string tag `s` and a canonical
   lowercase, hyphenated UUID value;
5. require every class-file reference to that `CONSTANT_Utf8` row to be an
   admitted `MixinMerged.sessionId` value reference; and
6. replace the 36 UTF-8 data bytes with
   `00000000-0000-0000-0000-000000000000` before deriving the semantic class
   SHA-256.

Sharing one UUID constant among multiple admitted `MixinMerged.sessionId`
elements is safe and is counted. Sharing it with any constant-pool row,
attribute, annotation element, bytecode operand, or other parsed reference is
unsafe and rejects the class. A target annotation in any other location is
also rejected.

Unknown attributes remain byte-for-byte valid when no rewrite occurs. A class
that would be rewritten is rejected if it contains an unknown attribute,
because V1 cannot prove that opaque payload does not share the target constant.
Malformed constant pools, modified UTF-8, annotations, bytecode, standard
attributes, path/class identities, trailing bytes, unsafe sharing, and
ambiguous element shapes are admission failures. A failure produces no
semantic manifest.

## Receipts

A semantic manifest retains, for every class:

- dump-relative path and exact size;
- exact final-byte SHA-256 from the independently constructed Foundation
  manifest;
- policy-normalized semantic SHA-256;
- normalized UTF-8 constant count; and
- admitted annotation-reference count.

It also retains Foundation's exact whole-dump manifest SHA-256, aggregate
counts, the full policy material and ID, and two separate content addresses:

- `semantic_dump_sha256` hashes an ordered view containing only the policy ID,
  paths, sizes, and semantic class digests; and
- `manifest_sha256` hashes the complete receipt, including exact identities.

Consequently, equivalent runs may have different exact Foundation and receipt
manifest hashes while sharing one semantic-dump hash. This distinction is
intentional and mandatory.

The comparison receipt lists every input manifest, reports whether exact
Foundation manifests are pairwise distinct and whether semantic dump hashes
are all equal, and content-addresses that report. It does not infer that the
runs, generated worlds, mod sets, or behaviors are otherwise equivalent.

## Storage boundary

The parser, contract, and generic CLI live under `modules/crucible/`. Candidate
invocations, generated manifests, and comparison receipts belong under ignored
`.workbench/` evidence custody. Platform profiles may cite them but do not own
or redefine this policy.
