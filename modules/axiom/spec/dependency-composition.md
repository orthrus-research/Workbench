# Dependency composition

Status: implemented **captured declaration inspection**, not installed-mod,
registry, transformation, or recipe-processing qualification.

`axiom target` independently parses the actual candidate's `pack.toml`, index,
and every captured artifact descriptor. It no longer reports only the eight
dependencies named in the initial research lock. No file is downloaded, mod
class loaded, or Minecraft process started by inspection.

## What is established

The `composition` result binds its candidate source identity, physical side,
and explicit optional-file choices. It reports:

- Pack and platform version declarations, with raw source identity.
- Index hash agreement and individual captured index-member hashes.
- Missing members, duplicated portable paths, preserved-input assumptions,
  and unsupported aliases/hash/download forms.
- Every captured `mods/**/*.pw.toml`, plus captured metadata explicitly named
  by the index, including unindexed declarations. It retains the descriptor's
  path/hash, output path, side, optional defaults and exact artifact locator/hash.
- Loose files under the artifact directory, including directly supplied JARs.
  Their bytes are identified, not executed or certified as valid mod artifacts.
- Captured configuration count/content identity and source mixin resources.
- Reference-source associations for pinned dependencies, and explicit drift
  when a candidate changes the corresponding artifact identity.

A source archive member is not necessarily indexed. An indexed descriptor is
not evidence that its referenced JAR was acquired. An acquired JAR would still
need source correspondence, loader/dependency resolution, and active code
transformation checks before it could qualify registry construction.

In particular, mixin resource presence does not prove that a loader queued that
configuration or applied its injections. Resources are listed by conventional
`mixin.*.json`/`mixins.*.json` names in captured `src/main/resources`; custom
resource names, coremods, generated manifests, activation code, platform classes,
and sources in other mods remain part of the unresolved composition boundary.

## Explicit side and optional choices

Add the following to an ordinary exact-base target request:

```json
{
  "schema": "axiom.target-request.v1",
  "targetId": "axiom-source-target:sha256:<exact returned digest>",
  "overlays": [],
  "composition": {
    "side": "client",
    "options": {
      "mods/cleanroom-relauncher.pw.toml": true
    }
  }
}
```

`side` is `client` or `server`. Omission leaves selection unspecified. Client
means the physical client, including its integrated server; server means the
dedicated server. Side membership and optional enablement are separate.
Without an override, optional declarations use their declared default. Required
dependencies cannot be disabled using this field. Unknown, non-optional, or
malformed option keys are request errors. No installation is performed.

Rows say `included-declaration`, `excluded-declaration`, or `unspecified`, not
"active mod." A descriptor outside the index remains `not-indexed` even when
the requested side/options would include it. Changing side/options changes
`compositionId` but not `candidateId`. Changing configuration, a helper, Java
source, metadata, or deleting a file changes candidate and composition identity.
Result reuse must also bind the engine identity.

## Supported format and limits

TomlJ 1.1.1 parses TOML; Java implements the bounded Packwiz metadata contract.
The installed parser and transitive dependency JARs are hash-locked, included in
engine identity, and retain their license notices in the distribution.

This reader admits explicit `packwiz:1.1.0`, SHA-256/SHA-512/SHA-1/MD5 content
hashes, HTTP(S) URL locators and exact CurseForge metadata locators. Hexadecimal
hashes are case-insensitive. It does not infer CurseForge URLs or update to a
newer artifact. Canonical nested relative paths and ordinary special characters
are supported; absolute paths, traversal, aliases and overlapping output/source
paths are not admitted. Murmur2 and other download modes remain unsupported.

TOML documents have a 1 MiB bound and 48-level container/value and dotted-path
guards. The lexical guard skips comments and quoted strings; TomlJ remains the
syntax authority. Exceeding a bound is incomplete, not proof of invalid TOML. Indexes have a
20,000-entry bound; diagnostic output has a 2,000-record bound. Existing target
archive, worker time/heap and 1 MiB transport bounds still apply. Oversized
stdout/stderr terminates the worker immediately and retains the original
`incomplete`/`transport.byte-bound` result.

`declarationInventoryComplete` only means all discovered captured/index-named
artifact metadata was parsed. `metadataConsistent` also requires no detected
metadata/integrity/context diagnostics. Neither means registry closure.
Every composition result separately retains `artifactBytesVerified: false`,
`activeTransformationsResolved: false`, `registryConstructed: false`, and
`installedCompositionQualified: false`.

## Pinned-pack evidence

For the locked Supersymmetry 0.1.16.15 source target, all 190 artifact descriptors
parse: 184 declare both sides and six declare client only. The source capture
contains 2,054 configuration files and 52 conventional mixin resources across
the three mod source repositories. Only three artifact declarations have a
captured reference-source repository; none has qualified binary equivalence.

The committed index is still one LF byte and does not match the hash declared
in `pack.toml`. Inspection reports that inconsistency and does not regenerate,
repair, or silently substitute an installable index.

`tools/axiom_composition_conformance.py` compares every actual descriptor with
Python's independent TOML parser, checks index and mixin identities, tests
client/server and optional selections, and optionally compares installed
Workbench/profile results with standalone Java outside the checkout. It runs in
the required Axiom CI lane. Java tests separately cover malformed/duplicate TOML,
stale hashes, missing sources, changed dependencies, unsafe paths, collisions,
resource bounds and transport termination. These are metadata/transport checks,
not an alternate game harness or whole-pack parity proof.

The next composition work is explicit verified artifact/platform capture and
source/build/transform correspondence, followed by real registry/material
bootstrap and phased construction. The full architecture remains the objective.

[Offline artifact inputs](artifact-inputs.md) now capture and independently verify
the provided selected mod bytes. They remain a separate result from complete
installed-composition qualification; platform and activation closure stay open.

Format references: [Packwiz pack](https://packwiz.infra.link/reference/pack-format/pack-toml/),
[index](https://packwiz.infra.link/reference/pack-format/index-toml/),
[mod metadata](https://packwiz.infra.link/reference/pack-format/mod-toml/),
[download modes and empty-field defaults](https://github.com/packwiz/packwiz/blob/main/core/mod.go),
and [TomlJ](https://github.com/tomlj/tomlj).
