# Imported-source provenance

The Workbench source root was assembled from selected prior development work
and public upstream evidence. Its build and validation do not depend on an
earlier repository, branch, or local evidence store.

[`MIGRATION-MANIFEST.json`](../../MIGRATION-MANIFEST.json) records each retained
external import group, its source identity, its public destination, and whether
the material was copied or adapted. Identity-bearing records keep their exact
bytes when current validators depend on them; semantic changes use a new
format.

Only source, contracts, compact evidence, and profiles required by the current
product are retained. Game files, credentials, dependencies, caches,
toolchains, worlds, runtime captures, and generated proofs remain outside the
repository. Provisioned or generated state belongs under ignored `.workbench/`
storage or another explicit external store.

## Versioned-name transition

Python implementation paths now use stable names. Remaining versioned records
and schema names identify wire contracts, metadata formats or retained evidence;
they are not parallel supported implementations. Native package manifests
carry component versions. Release artifacts and tags remain externally
versioned. Do not rename an identity-bearing record without updating its
contract and consumers, or rewrite historical evidence during source cleanup.

## Final public baseline

The [Core and module architecture](../architecture/CORE-AND-MODULES.md)
defines the implemented ownership, package layout, native version metadata and
acceptance criteria. Core, API, modules and profiles are separate distributions;
the old bundled Core packaging path has been removed. This status update does
not alter the source-import identities recorded by the migration manifest.

Final publication requires a reviewed clean revision, a successful clean-checkout
build and full validation, verified redistribution/license records, and a passing
secret-scan receipt for the exact exported tree. Reconcile the chosen project
license and accompanying notices with the destination repository before the
final import. Use `tools/prepare_public_export.py` to create the clean-root
snapshot; do not import the source repository's historical branches or tags.

Creating a migration branch on a staging repository does not change the declared
Orthrus Research destination, prove that it exists, or authorize a final release.
