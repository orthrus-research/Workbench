# Ultimate Runtime Graph producer

The Ultimate Runtime Graph producer is the profile-owned Cleanroom mod used
to collect exact Supersymmetry runtime observations. The current producer
version is `0.16.0`. The producer is experimental evidence infrastructure;
its output does not by itself establish profile support, construction
admission, action authority, execution proof, or release qualification.

## Build

The build uses the repository-managed Gradle 9.6.1 distribution and a Java 25
toolchain while targeting Java 8 bytecode. It compiles against exact ignored
copies of GroovyScript, GTCEu, Supersymmetry, Supercritical, BetterQuesting,
AE2, Techguns, Industrial Renewal, RSGauges, ReFinedTools, McJtyLib,
OpenComputers, Biomes O' Plenty, Recurrent Complex, IvToolkit, Cave Generator,
and Had Enough Items.

From the repository root, provide every dependency property and run
`remapJar`:

```bash
WORKBENCH_ROOT="$PWD"
PRODUCER_ROOT="$WORKBENCH_ROOT/profiles/packs/supersymmetry/atlas/probes/ultimate-runtime-graph-producer"
GRADLE="$WORKBENCH_ROOT/.workbench/toolchains/ide-validation-v1/gradle-9.6.1/bin/gradle"

"$GRADLE" --no-daemon --console=plain -p "$PRODUCER_ROOT" \
  -PworkbenchGroovyScriptJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/groovyscript.jar" \
  -PworkbenchGregTechJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/gregtech.jar" \
  -PworkbenchSuSyJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/supersymmetry.jar" \
  -PworkbenchSupercriticalJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/Supercritical.jar" \
  -PworkbenchBetterQuestingJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/BetterQuestingUnofficial.jar" \
  -PworkbenchAppliedEnergisticsJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/ae2-uel.jar" \
  -PworkbenchTechGunsJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/techguns.jar" \
  -PworkbenchIndustrialRenewalJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/IndustrialRenewal.jar" \
  -PworkbenchRsGaugesJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/rsgauges.jar" \
  -PworkbenchRfToolsJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/refinedtools.jar" \
  -PworkbenchMcJtyLibJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/mcjtylib.jar" \
  -PworkbenchOpenComputersJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/OpenComputers.jar" \
  -PworkbenchBiomesOPlentyJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/BiomesOPlenty.jar" \
  -PworkbenchRecurrentComplexJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/RecurrentComplex.jar" \
  -PworkbenchIvToolkitJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/IvToolkit.jar" \
  -PworkbenchCaveGeneratorJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/cavegenerator.jar" \
  -PworkbenchJeiJar="$WORKBENCH_ROOT/.workbench/fixtures/<fixture>/mods/HadEnoughItems.jar" \
  clean remapJar
```

The build fails closed when a dependency property is absent or does not name a
regular file. Outputs stay under
`.workbench/build/supersymmetry/ultimate-runtime-graph-producer/` and the JAR
task uses reproducible file ordering and timestamps.

## Capture mechanics

The producer is inert unless the required `workbench.runtimeGraph.*` system
properties are present. Capture and compatibility-only modes are exact
Booleans and are mutually exclusive. A capture writes only to an absolute,
absent output beneath an existing directory. Category failures are retained
independently; successful publication uses an atomic same-parent staging move
and an empty `.capture-complete` marker.

The current dedicated-server profile samples three checkpoints:

- `material-manager-frozen`
- `server-started`
- `post-start-end-tick`

Each adapter must complete twice with byte-identical canonical output, no
unsupported values, no adapter diagnostics, and a clean process exit. The
producer keeps raw runtime observations separate from Atlas projection. It
does not assemble graphs in the game process and does not depend on the
historical `dev.susycodex` producer, `GraphData`, SQLite projection, or V1
node/edge identities.

Version `0.16.0` retains registry alternatives and defaults, Forge fluid
ownership and world defaults, smelting overlap and lookup selection, and
bounded serialized item/fluid values. Serialized NBT is preflight-bounded by
depth, node, text, and cumulative sample budgets, then checked again after
canonical projection. Subtype-provider output is bounded before capture and
reports deterministic truncation. Provider-internal allocations and behavior
remain outside custody. Groovy removals and backup-before-remove events remain
non-final tombstone observations.

## Compatibility mechanics

FML capture distinguishes mod-directory candidates from runtime attachment,
active-container ownership, loaded coremods, tweakers, mixin metadata, and
non-mod libraries. A shadowed input JAR therefore remains an explicit
`present-unattached` candidate rather than being presented as its active
implementation.

The exact Supersymmetry target requires a narrow provisional Recurrent Complex
overlay. Its authority is
`profiles/packs/supersymmetry/atlas/runtime-graph/cleanroom-compatibility-overlays-v1.json`;
the current shell launch path validates and applies that authority and retains
the application receipt. Captures must bind the rewritten artifact, receipt,
and provisional profile state.

UniversalModCore 1.2.2 can request a Forge chunk ticket after Forge clears the
ticket map during server shutdown. The producer's dedicated-server bridge is
armed by `FMLServerStoppingEvent`, runs strictly between Forge and
UniversalModCore callback priorities, and removes only the identical temporary
empty collection it inserted. The exact failure binding and mutation boundary
are declared in
`profiles/packs/supersymmetry/atlas/runtime-graph/cleanroom-runtime-compatibility-shims-v1.json`.
The Workbench shell owns compatibility-only launch preparation and validation;
this directory intentionally has no standalone Python shutdown or capture
runner.

Raw numeric registry IDs and ore-dictionary ordinals are launch-scoped
observations. GT item-material sequences retain runtime order because GTCEu's
fallback selects the first element. Hidden-class VM addresses are removed
from executable type and captured-field names, while executable behavior
bodies remain explicitly uncaptured.

Generated captures and their admission records belong under ignored
`.workbench/` storage and are not public source inputs.
