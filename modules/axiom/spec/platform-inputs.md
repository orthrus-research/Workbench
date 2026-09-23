# Explicit platform inputs

Status: bootstrap metadata selection, offline byte capture and binary declaration
inspection are implemented. Platform activation, transformations, event dispatch
and registry construction are **not qualified**.

A pack's Forge declaration does not implicitly select Workbench's Cleanroom
platform. Axiom accepts a separate profile-owned platform package and records
that choice without asserting equivalence to Forge.

## Selection authority

The Cleanroom profile exposes exact `runtime-toolchain.json` bytes through
`workbench.axiom_targets`. The bootstrap hash identifies the original ZIP.
The short artifact lock strengthens selected hashes; it is not a complete
classpath. Java reads original `mmc-pack.json` and selected patch JSON files.

Currently admitted: an explicit Linux x86_64 client with a target Java major
advertised by the bootstrap. Metadata selection describes a target Java major; it is not authority to select
Axiom's worker JDK. The worker separately requires the exact profile-pinned runtime.
The selected Cleanroom 0.6.12-alpha bootstrap's 160 declarations resolve to 115
files: 114 ordered classpath entries and one native-extraction archive. The
Minecraft JAR is last in the metadata-derived classpath. No native/rendering
library is executed; assets, credentials and game arguments are not evaluated.
Capturing the bootstrap does not implement every setting in `instance.cfg`.

Selection is independently implemented from these pinned Prism 11.1.0 sources,
not copied launcher implementation:

- [Component order](https://github.com/PrismLauncher/PrismLauncher/blob/ea87ffcfbc22c3bb37c75b97160fe836aeb130be/launcher/minecraft/PackProfile.cpp).
- [Active library merge and classpath order](https://github.com/PrismLauncher/PrismLauncher/blob/ea87ffcfbc22c3bb37c75b97160fe836aeb130be/launcher/minecraft/LaunchProfile.cpp).
- [Library/native paths](https://github.com/PrismLauncher/PrismLauncher/blob/ea87ffcfbc22c3bb37c75b97160fe836aeb130be/launcher/minecraft/Library.cpp).
- [OS rules](https://github.com/PrismLauncher/PrismLauncher/blob/ea87ffcfbc22c3bb37c75b97160fe836aeb130be/launcher/minecraft/Rule.cpp) and
  [runtime classifiers](https://github.com/PrismLauncher/PrismLauncher/blob/ea87ffcfbc22c3bb37c75b97160fe836aeb130be/launcher/RuntimeContext.h).

Rules precede merging; the last applicable rule decides. Ordinary and native-map
libraries merge separately. Equal versions retain the first matching declaration.
Unequal active versions report unsupported instead of guessing Prism's version
ordering; none occur in this selected Linux bootstrap. Maven coordinates own
storage paths, not optional download-path overrides. A `natives-linux` classifier
alone does not imply native extraction. Cached hints are not patch authority,
and `suggests` is not an equality constraint. Other hosts, server arrangements,
agents and unknown metadata forms do not receive favorable defaults.

This does not establish effective defining classloaders, mod order, source/build
correspondence or active transformations.

## Capture and portable inspection

```sh
python3 tools/build_axiom_platform.py \
  --policy profiles/platforms/cleanroom/runtime-toolchain.json \
  --bootstrap /inputs/cleanroom-0.6.12-alpha.zip \
  --library-root /inputs/libraries \
  --side client --os linux --architecture x86_64 --java-major 25 \
  --engine-home /tools/axiom --java-home /tools/jdk \
  --output .workbench/axiom/platform/cleanroom-linux-client.zip

/tools/axiom/bin/axiom platform --platform /inputs/platform.zip

workbench axiom platform --profile cleanroom \
  --engine-home /tools/axiom --java /tools/jdk/bin/java \
  --platform /inputs/platform.zip
```

Capture does not download, execute sources or discover a runtime. It asks Java
for library selection, copies only those paths, checks declared SHA-1/size and
available policy SHA-256 pins, and asks Java to verify the final bundle before
atomic no-clobber promotion. Missing files require `--allow-partial`; wrong bytes
always fail. Metadata-only partial packages report every selected file missing.

The deterministic STORED ZIP contains `manifest.json`, original `policy.json`,
original `bootstrap.zip`, and `blobs/<sha256>`. This is local/ignored dependency
input, not a public Workbench release artifact. Original licenses remain attached;
Axiom does not relicense, initialize or add these JARs to its own classpath.

`platformId` binds the manifest, policy, bootstrap, explicit selection and
provided bytes. Optional detail requests use `axiom.platform-request.v1`, exact
`platformId`, and `libraryPaths` with 1–16 provided Maven-layout paths. Filters
do not change identity. Duplicate/unsafe paths, unreferenced members, altered
hashes and unsupported forms fail closed. Worker isolation and existing
time/heap/output bounds apply; JAR expansion is separately bounded. Native
archives are verified but never extracted.

## Joining source, mod and platform inputs

```sh
workbench axiom target --profile supersymmetry \
  --engine-home /tools/axiom --java /tools/jdk/bin/java \
  --target /inputs/source.zip --artifacts /inputs/mods.zip \
  --platform /inputs/platform.zip --platform-profile cleanroom \
  --request /inputs/client-request.json
```

The source request must explicitly select the same physical side.
`inputCompositionId` binds the source candidate, mod selection, optional mod
bundle, platform and actual engine identity. Workbench verifies both installed
policy owners before and after evaluation; Core retains process/cancellation
ownership. No bridge implements metadata or recipe algorithms.

Combined output retains platform identity, coverage, missing inputs, component
and classpath/native order, plus requested `artifactDetails`. Full declarations
and binary inventories remain available through `axiom platform`, avoiding bulk
duplication within the one-MiB protocol. An optional source-request `platform`
object carries an exact-platform detail request; stale identities fail.

## Annotation definitions and verification

Binary inspection counts annotation types. Detailed `annotationTypeDeclarations`
retain original class identity, members, descriptors, flags, required/default
distinction, exact typed defaults and Java meta-annotations. Defaults are not
applied to unresolved usages; `annotationDefaultsResolved` stays false.

`tools/axiom_platform_smoke.py` checks actual inputs outside the checkout,
independently compares hashes and ZIP class counts, compares selected Cleanroom
event annotation members/defaults with JDK `javap`, and optionally compares
standalone and installed Workbench results including combined requests.
Synthetic tests cover selection/custody/unsupported cases. Neither test category
is whole-loader or recipe parity proof.

Next: source/build and active-transformation identity, inherited/programmatic
subscriptions, actual dispatch, and independent material/registry execution.
See [registration inputs](registration-inputs.md). Complete declared library
bytes are a prerequisite, not completion of these execution requirements.
