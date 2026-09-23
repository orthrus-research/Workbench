# Native developer environment preparation

The default workflow remains developer-authored IDE edits, saved-candidate checks,
and source-linked diagnostics. Environment preparation is a separate operation:
it resolves dependencies but never runs Minecraft or grants qualification.

## Responsibilities

| Owner | Responsibility |
| --- | --- |
| Project Intelligence | Capture exact saved source bytes and file modes |
| Pack profile | Client dependencies, optional defaults, source roots and admission |
| Platform profile | Pinned bootstrap, installer, Java, launcher protocol and platform artifacts |
| Packwiz / installer | Refresh the private source copy and acquire pack files |
| Prism | Resolve launcher libraries, assets and natives, then supply its launch handoff |
| Core | Private storage, process custody, downloads, verification, image publication and cleanup |
| Shell / IDE clients | Selected Work Session, exact consent, progress, cancellation and reopening |

Profiles provide declarative requirements through their packaged check interfaces;
they do not import Core or implement process/storage ownership. The API contains
pure Packwiz dependency and Prism protocol contracts. Core does not interpret
recipes or game observations.

## Prepare, then check

Both native IDE clients offer **Prepare with Packwiz and Prism** in the existing
saved-check image picker. The equivalent CLI is:

```bash
workbench context run SESSION_ID -- checks plan-environment \
  --prism /path/to/native/PrismLauncher \
  --packwiz /path/to/native/packwiz \
  --java /path/to/platform-jdk/bin/java \
  --accounts /path/to/PrismLauncher/accounts.json \
  --seed /optional/existing/mods
workbench context run SESSION_ID -- checks prepare-environment ENVIRONMENT_ID \
  --confirm ENVIRONMENT_REQUEST_ID
workbench context run SESSION_ID -- checks environment-show ENVIRONMENT_ID
workbench context run SESSION_ID -- checks environment-cancel ENVIRONMENT_ID
workbench context run SESSION_ID -- checks environment-recover ENVIRONMENT_ID \
  --confirm ENVIRONMENT_REQUEST_ID
```

Use the returned exact IDs. Planning captures source, declares downloads and
account-copy effects, and binds executable hashes. Execution rechecks source,
profile requirements, tools and consent. Attempts execute at most once. The
command envelope succeeding does not imply `result.state == "ready"`.

Preparation honors the selected checkout, not a moving upstream release. Check
its `pack.toml`, Git revision and dependency metadata before choosing a baseline.
An upgrade requires selecting the new pack revision and preparing a new image;
never overwrite an immutable image or mix newer mod JARs into older source.

Select trusted, already-installed native Prism, Packwiz and a complete platform
JDK. This slice does not silently discover launcher roots or automatically
install those three tools. The current Cleanroom policy admits Prism 11.1.0 and
Temurin 25.0.4+7, with the release metadata's `-LTS` suffix recognized. The
bootstrap and Packwiz installer are downloaded from exact packaged locks. A
local executable hash identifies the selected tool, not its entire installed
distribution; keep the selected installation intact during preparation.

The active provisional runtime is Cleanroom 0.6.12-alpha with CleanMix 0.7.2.
Its stable-named `runtime-toolchain.json` pins the platform JAR itself as well as
the transformer libraries, independently of historical candidate evidence.
Supersymmetry 0.1.16.15's Fugue requires Cleanroom 0.6.10-alpha or newer;
updating the pack while retaining the former 0.6.8-alpha platform is insufficient.

Core refreshes only a private saved-source copy. It validates every resulting
index entry before passing it to the pinned installer, with an empty separate
launcher-metadata directory. Client/server selection and declared optional
defaults are explicit; an omitted optional default is false. Seed directories
contribute only files matching the selected artifact hashes. Undeclared files,
preserve-mode entries, output collisions, ignored dependency metadata and hash
mismatches fail closed. Source is never refreshed in place.

Prism receives a fresh, owner-private data root and the pinned bootstrap. Only
the explicitly selected account store is copied, with mode 0600; the original
launcher configuration and accounts are untouched. Preparation may authenticate
or refresh that private copy online. Hooks, Java selection, memory, environment
and native workarounds are overridden in the fresh instance.

Prism has no prepare-only CLI. Core instead supplies a wrapper which receives
the exact Java argument array and LauncherPart stdin protocol after resolution.
The wrapper never executes Java. It admits the known platform command, verifies
the locked `NewLaunch.jar`, rejects unknown parameters and external library
paths, and replaces all account fields with fixed offline placeholders before
writing a handoff. Core supervises the unique Prism process group; no existing
launcher peer or detached game process is adopted.

Core combines the verified pack payload, libraries, assets and natives into an
immutable image, with image-relative arguments and sanitized `workbench-launch.txt`.
The complete JDK is copied; contained JDK file symlinks are flattened into
independent files, while escaping links and directory links are rejected. The
platform and pack re-admit the assembled image before atomic publication.

Runtime images now use metadata format `workbench-runtime-image-v2`, including
the optional launch-input member. Old image records are not adapted or shown in
the current picker; prepare or import a new image. Saved checks execute the
image directly through Core's durable launch gate, preserving Prism's entrypoint
protocol without launching Prism or copying accounts again.

## Reuse and recovery

Dependency identity includes parsed Packwiz metadata, versions, side/default
decisions, ignore policy and conservative auxiliary inputs. It excludes derived
index hashes, the four projected source roots and demonstrably unindexed Markdown.
Script/config additions, edits and deletions can therefore reuse the same image.
Unknown non-source inputs remain conservative dependencies. A matching image is
reused only after its content manifests and current profile admission verify.

All preparation state is under the selected private `developer-checks/` store.
Requests and results link into the existing Work Session; there is no new session
database. Core retains lifecycle evidence while discarding installer/Prism child
output at this boundary. It removes copied account files after verified closure
and moves disposable files to same-filesystem recoverable private trash. Prism's
own private files are never included in the runtime image or public source.
Each preparation phase has a separate request-bound progress history using
Core's current observation contract. Progress collection never reads the
discarded installer/Prism output or interprets account data as game evidence.

Cancellation closes the exact owned process group. Interrupted or uncertain
custody blocks cleanup; reopening offers explicit recovery. Never infer closure
from a dead frontend or force-delete its private files. A recovered attempt does
not become successful. Partial image copies from an interrupted publication
remain managed temporary storage for the existing storage cleanup workflow.

## Evidence and limits

Real-tool tests exercised side filtering, omitted optional defaults, hash
rejection, cached reuse, saved loose-file update/deletion and paths with spaces.
The production path prepared the Supersymmetry client through a real Prism
no-game handoff and removed copied accounts after closure. Those observations
were made on WSL2 using Linux executables, not a qualified native Linux host.

The original real saved-source observations used Supersymmetry 0.1.16.12 at
`bca8d0eee22c73f026a69601802eb7517c8c5879`, not the current upstream release.
They proved compiler-failure detection and execution of a manually corrected
script, but that older pack failed during Recurrent Complex initialization.
The [saved-check documentation](SAVED-CANDIDATE-CHECKS.md) links the released
upstream fix. Those historical failures must not be presented as an unfixed
defect in current Supersymmetry or as successful runtime qualification.

Supersymmetry 0.1.16.15 at `3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`
has also been prepared through real Packwiz/Prism. Its initial check against
0.6.8-alpha correctly failed Fugue's declared platform dependency. A fresh
image now binds the updated 0.6.12-alpha platform policy.
The installed SusyCore 0.1.118 artifact contains the upstream argument-slot fix.
Preparation and artifact inspection remain distinct from startup validation.

An offline **account** does not imply network isolation: trusted game/mod code
may still access the network or other resources available to the OS user.
Disposable storage is not a security sandbox. Native Linux runtime qualification,
other host platforms, server parity, gameplay and recipe-specific correctness
remain separate acceptance work. Guided generation is not a prerequisite.

Upstream boundaries were checked against the [Prism 11.1.0 release](https://github.com/PrismLauncher/PrismLauncher/releases/tag/11.1.0),
its [command-line interface](https://prismlauncher.org/wiki/getting-started/command-line-interface/),
[custom commands](https://prismlauncher.org/wiki/help-pages/custom-commands/), and
the [Packwiz installer documentation](https://packwiz.infra.link/tutorials/installing/packwiz-installer/).
