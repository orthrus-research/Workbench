# Offline artifact inputs

Status: implemented exact artifact-byte capture and non-executing binary
inspection. This does **not** establish installed composition or recipe parity.

Source declarations alone cannot supply the registry. Axiom now accepts the
actual artifact bytes referenced by an explicit candidate/side/optional
selection. This is an independent, local data operation: it does not initialize
Forge, define mod classes, construct a fake world, or query a game harness.

## Capture

First prepare an ordinary [target request](source-targets.md) with an explicit
`composition.side` and optional choices. Then:

```sh
python3 tools/build_axiom_artifacts.py \
  --target /inputs/source-target.zip \
  --request /inputs/client-request.json \
  --engine-home /tools/axiom \
  --java-home /tools/jdk \
  --artifact-root /inputs/local-pack-files \
  --output .workbench/axiom/artifacts/client.zip
```

The artifact root must contain the declared output paths, such as `mods/foo.jar`.
The tool does not search for a Minecraft installation or download anything. It
asks the Java engine to inspect the exact source request, then copies only that
engine's selected declarations. Every input must match its declared pack hash.
The original index inconsistency remains visible; capture does not repair it.

Missing artifacts fail capture unless `--allow-partial` is explicitly supplied.
An existing artifact with the wrong bytes always fails, including partial mode.
Empty captures, symlinks, unsafe paths, and overwriting an existing candidate
are rejected. Captured bytes are hashed again while writing; candidates are
atomically promoted without clobbering existing files.

## Bundle identity

An `axiom.artifacts.v1` ZIP contains only:

- `manifest.json`: source-target, candidate and composition identities; side and
  optional choices; selected metadata/output paths; each provided artifact's
  size and SHA-256.
- `blobs/<sha256>`: unchanged original artifact bytes, deduplicated by content.

Members are stored without additional compression; nested JARs keep their
original compression, signatures and license files. ZIP timestamps and ordering
are reproducible. No originating machine paths or credentials are recorded.
The bundle identity hashes its exact manifest bytes; each content-addressed
member is independently checked. Engine identity still belongs to the result
and must be included when caching an analysis.

These are local/ignored dependency inputs, **not Workbench release artifacts**.
Nothing in capture authorizes redistribution or relicenses third-party code.
The Axiom engine distribution does not include these mod JARs.

## Inspect independently or through Workbench

```sh
/tools/axiom/bin/axiom target \
  --target /inputs/source-target.zip --artifacts /inputs/client.zip \
  < /inputs/client-request.json

workbench axiom target --profile supersymmetry \
  --engine-home /tools/axiom --java /tools/jdk/bin/java \
  --target /inputs/source-target.zip --artifacts /inputs/client.zip \
  --request /inputs/client-request.json
```

The source request must match the bundle's candidate, side and optional choices.
Changing a helper, configuration, source file, or selected dependency cannot
reuse a stale bundle. Java re-derives the selected declarations from the source
package; it does not trust the capture tool's selection assertions.

The isolated worker receives both archives read-only. Neither archive is
extracted onto the worker filesystem. All provided artifact hashes are verified
before any binary declarations are inspected. Java streams each nested JAR and
uses the ASM reader already bundled in the pinned Groovy parser. No mod JAR is
placed on the Java classpath and no mod class is loaded or initialized.

`artifactInspection` reports:

- Verified provided bytes and explicit missing selected artifacts.
- JAR entry/class counts and ordered member-content identities.
- Main manifest attributes, including coremod/transformer declarations.
- Direct Forge `@Mod` and loading-plugin annotations, plus direct implementations
  of the admitted coremod, transformer and MixinBooter interfaces.
- Direct automatic event-subscriber and annotated event/lifecycle method counts;
  [detailed declarations](registration-inputs.md) include signatures, priority and
  side metadata without claiming registration, dispatch order or activation.
- Conventional mixin JSON, `mcmod.info`, service descriptors and access-transformer
  resources, with their original entry names and hashes.
- Embedded JARs and multi-release class variants as unresolved inputs.
- Duplicate binary class names without arbitrarily selecting a provider.

Case-insensitive manifest-name collisions suppress the aggregate manifest rather
than choosing the last ZIP entry. Detailed resources retain each parsed candidate.
Malformed manifests and class-entry/binary-name disagreements have explicit
diagnostics; these are inspection gaps, not inferred native loader rejections.
Unreadable class headers cannot contribute a successful class-provider discovery.

This is declaration discovery, not complete mod discovery, class verification,
inheritance closure or injection application. Other annotations and custom
resource discovery remain unmodeled. Method bodies are not evaluated; incomplete
or unsupported class/JSON forms remain explicit. JAR signatures are not verified.

Default output is compact. Add `artifactPaths` with 1–16 provided descriptor
paths to include decoded resource details for those artifacts, for example:

```json
"artifactPaths": ["mods/susycore.pw.toml", "mods/gregtech-ce-unofficial.pw.toml"]
```

All provided artifacts are still inspected; detail filters do not change their
identities or coverage. The first 128 duplicate names are displayed, with the
full duplicate count and a truncation flag. Duplicate reporting does not impose
an invented classloader precedence.

## Bounds and qualification

Bundles are limited to 2 GiB and 2,000 artifacts; each artifact is at most
256 MiB. Nested JAR processing limits expansion to 1 GiB per JAR and 4 GiB total,
100,000 entries per JAR, 250,000 unique binary names, 4 MiB per class, 1 MiB per
metadata entry and 64 MiB per other entry. These are archive intake contracts.
Worker execution and transport targets are temporarily suspended for MVP.
Native resource termination remains incomplete evaluation, not recipe rejection.

`providedArtifactBytesVerified: true` means exactly that. Even
`selectedArtifactCoverage: "complete-declared-selection"` does not establish a
complete installed classpath: platform bytes, transitive/embedded libraries,
mod discovery, loader order, source/build correspondence, active transformations,
and registry/material initialization still need qualification. The enclosing
source composition retains `artifactBytesVerified: false` for that broader
composition, and all results retain `installedCompositionQualified: false`.

## Evidence and next boundary

`tools/axiom_artifact_smoke.py` copies the engine and both input archives outside
the checkout, compares class-file counts with Python's independent ZIP reader,
and compares the three principal mod annotations with the JDK's `javap` reader.
It also independently compares 41 annotated event/lifecycle methods in five
principal registration classes, including generic signatures and explicit sides.
It tests stale-side rejection, detail identity, unchanged source bytes, and an
optional installed Workbench/profile handoff. Java tests use classes whose
initializers would have an observable effect if executed, and verify that
inspection does not initialize them. Capture tests cover mismatches, missing
artifacts, deterministic output and no-clobber/path boundaries.

The pinned default-client source selection has been captured offline: 189 exact
artifacts, 427,695,126 bytes, with no missing selected declarations. This is
concrete artifact identity evidence, not a game-run observation.

The next work is platform/library and loader/transformation closure, connecting
the actual binary declarations to exact source/build inputs, then real
registry/material construction. No synthetic registry substitution is implied.
