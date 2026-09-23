# Workbench Prism/MultiMC client launch V2

Status: experimental executable contract

V2 preserves the V1 launch operation and adds an explicit, identity-bound
compatibility overlay for a disposable launcher projection. It is selected
only when `--compatibility-patch` is supplied; an ordinary launch remains a V1
receipt.

## Compatibility patch boundary

Archive-entry patches use
`workbench-runtime-compatibility-patch-v1`. They bind one
projection-relative archive and entry to exact pre-patch SHA-256 identities,
require a positive byte-match count, and perform a length-preserving
replacement. Text configuration experiments use the separate
`workbench-runtime-compatibility-text-patch-v1` format. They bind the complete
target file hash and one exact UTF-8 replacement with an expected match count;
replacement length may differ. Additive experiment artifacts use
`workbench-runtime-compatibility-file-overlay-v1`. They bind a regular source
file beside the specification to an exact hash and require a new, absent
projection-relative target. Workbench rejects absolute paths, traversal,
symbolic links, target drift, archive-entry drift, invalid UTF-8, oversized
inputs, existing overlay targets, and unexpected match counts.

The patch is applied only after the portable projection has been copied and
verified. The canonical Packwiz materialization and source checkout are never
rewritten. The retained V2 receipt records the patch ID, specification hash,
target path, operation kind, and the applicable before/after file, JAR, and
archive-entry hashes. File overlays also retain the normalized source path,
hash, and size.

## Retained identity

An overlay launch emits:

- `workbench-runtime-launch-receipt-v2`, schema version `2`; and
- `workbench-runtime-launch-result-v2`, schema version `2`.

The V2 launch identity includes the complete patch records, so two otherwise
identical launches with different overlays cannot share an identity. The
result remains a runtime observation, not a stable dependency decision or an
Atlas interpretation.
