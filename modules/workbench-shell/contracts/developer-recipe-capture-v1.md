# Developer-local recipe capture

`workbench capture recipes` connects a developer's saved pack checkout to a
selected prepared server, then produces a retained Atlas dead-end audit. The
checkout can be an unreleased branch with local saved changes. The workflow is
experimental; native game acceptance is specific to the selected platform, JDK
and runtime.

## Select, review, prepare and run

Install Workbench Core, Shell, Atlas, Crucible, Project Intelligence and the
selected recipe-capture profile. The Supersymmetry profile currently supports
only its pinned original Forge 1.12.2 / Forge 14.23.5.2860 / Java 8 binary model.
The prepared server must contain the matching original Minecraft, Forge,
GregTech, GroovyScript and Susy-Core artifacts. Other selected libraries and mods,
including local-built JARs, are inventoried and retained. A different source
branch does not qualify different required binaries or Java versions.

Choose an existing server environment and a compatible **JDK** containing both
`java` and `javac`. Core stores their locations per user, workspace and recipe
profile in `recipe-fixtures-v1.json` under the platform's Workbench configuration
directory (`WORKBENCH_CONFIG_HOME` can select another configuration directory).
The registry stores locations, not a claim that the bytes are compatible. The
profile verifies Java, artifact digests and Forge's launch classpath when the
selection is inspected or used. Workbench does not download a pack runtime for
this flow. The selected JDK is used for compilation and game execution; the
profile's build and game Java records remain distinct.

Register locations once, then inspect them without launching a game:

```text
workbench capture recipes fixtures set --workspace "<checkout>" --pack-profile supersymmetry --runtime "<prepared-server>" --java-home "<jdk-home>"
workbench capture recipes fixtures inspect --workspace "<checkout>" --pack-profile supersymmetry --json
```

An inspector error names the incompatible or missing input. To recover, repair
the selected server/JDK and register those locations again, or supply
`--runtime` and `--java-home` on one `plan` command. An explicit plan with both
overrides also works if the user registry is damaged. Neither registration nor
inspection changes pack files or admits a native capture.
Contained JDK directory/file aliases are copied as ordinary files. Broken links,
escaping links and cycles are rejected. On Windows, Core uses verified ASCII
8.3 path aliases when Java cannot use the selected path directly; if the volume
does not supply one, select a shorter ASCII JDK or retained-state path.
Core handles long retained filenames at Windows filesystem boundaries without
changing the paths stored in evidence. Use a user-owned state directory whose
ancestors allow directory lookup; Java 8 also checks those ancestors when
opening JAR files. The installed default is under the user's local application
data directory.

Replace the quoted placeholders below with your own absolute paths. Keep the
checkout, prepared server, JDK and retained-state directories in separate trees.
The commands use the same flags on Linux and Windows:

```text
workbench capture recipes plan --workspace "<checkout>" --pack-profile supersymmetry --state-root "<capture-state>" --heap-mib 8192 --json
```

`--runtime` and `--java-home` override the registered locations for this plan.
Core can also use the matching Setup JDK if the fixture registry supplies no
Java home. Planning observes saved source, probes Java, inventories the selected runtime and
JDK, and retains a new attempt. Read its `attempt_id`, exact `id`, source revision,
dirty state, location provenance, artifact bindings, source-root replacement
policy and heap selection.
Planning starts no game process. The default heap is 16384 MiB; select a value
appropriate for the target environment and available memory.

```text
workbench capture recipes prepare "<attempt_id>" --confirm "<request.id>" --state-root "<capture-state>" --json
```

Preparation verifies that the selected inputs still match the reviewed request,
copies them into Core custody and compiles the profile-packaged observer. It
stages the exact packaged Java sources with relative compiler arguments, retaining
both original and staged identities so the Java 8 compiler need not receive the
installation's Unicode source path. It
replaces the complete `groovy`, `config`, `resources` and `scripts` roots from
the saved checkout, including roots now absent from it. This removes stale or
deleted template recipes. The profile also excludes `cache/groovy`, where its
pinned GroovyScript build stores generated compiled scripts, so the isolated
runtime rebuilds them from the selected source. Launch planning rejects a
retained compiled-script cache. Original Git file modes stay in the source binding.
POSIX copies preserve those modes; Windows physical executable bits follow the
host's `.exe`, `.com`, `.bat` and `.cmd` filename semantics. Source bytes and paths
remain exact across that mode projection. Compilation success is not game
initialization or capture admission. Review the resulting prepared record before
execution.

Read and accept the [Minecraft EULA](https://www.minecraft.net/en-us/eula) for the
isolated execution, then supply that acceptance explicitly:

```text
workbench capture recipes run "<attempt_id>" --confirm "<prepared.id>" --accept-eula --state-root "<capture-state>" --json
```

Execution creates a separate runtime copy, writes `eula=true` there and applies
the profile's recorded capture settings: loopback address, an ephemeral server
port, query/RCON disabled, a separate capture world and the tick watchdog
disabled for the finite matcher scan. Other server properties, including an
explicit `online-mode` value, are preserved. The original selected environment
and saved checkout remain input evidence.

Core retains the process outcome and complete output. A successful process exit
and an admitted sealed capture are separate requirements. Crucible verifies the
capture; the selected profile projects a new graph; Atlas writes the complete
audit. Cancellation remains available while the process and analysis run. The
recorded process policy has no timeout or output-byte limit.

## Reopen, cancel and iterate

```text
workbench capture recipes show "<attempt_id>" --state-root "<capture-state>" --json
workbench capture recipes cancel "<attempt_id>" --state-root "<capture-state>" --json
```

`show` verifies the retained saved source. For a completed attempt it checks the
request, preparation, launch, runtime lock, protocol, input manifest and audit;
the complete capture and observer-build file inventories; and the linked process
identity, successful exit, complete output and graph identity. Changed evidence
fails verification. Reopening starts no game process and needs no current live
checkout or runtime. These integrity checks establish retained content and its
bindings; they do not repeat the original game execution.

`cancel` requests cancellation and retains available evidence; its acknowledgement
is not a claim that the active process has already stopped.

Save an edit and create a new plan to capture again. Source, environment, Java or
profile drift invalidates preparation/execution of an earlier plan. Failed and
completed attempts remain separate; an attempted preparation or run cannot be
reused to certify changed inputs.

A completed record supplies the audit path, summary, coverage and graph
projection identity. The retained graph can also be queried independently:

```text
workbench atlas recipes audit-dead-ends "<retained-graph>" --json
workbench atlas recipes audit-dead-ends "<retained-graph>" --csv
```

## Share a completed scan

Export a successfully completed attempt to a new archive outside its retained
directory. Export checks the original local custody before copying the selected
data; it does not launch the game or evaluate the audit again.

```text
workbench capture recipes export "<attempt_id>" --state-root "<capture-state>" --output "<new-scan.zip>" --json
```

The archive contains the original sealed capture, graph/index, input, audit and
provenance records. It omits the runnable server, JDK, source checkout, world,
observer binaries and process output. Their recorded identities remain historical
declarations; importing the archive cannot verify those omitted bytes or claim
local game execution. Original absolute paths are preserved as provenance and
are never followed during import.

On another machine, install Core and Atlas with their declared dependencies:

```text
workbench atlas scans import "<scan.zip>" --destination "<new-scan-directory>"
workbench atlas scans show "<scan-directory>"
workbench atlas scans audit "<scan-directory>" --limit 25
```

The receiving machine needs neither the game nor the pack checkout or profile.
Use the imported graph path reported by `show` with the existing Atlas recipe
commands. `scans audit` reads the completed report with explicit filtering and
paging; `recipes audit-dead-ends` instead evaluates a graph again. An imported
scan is historical evidence with no current-target match claim. Its object ID
identifies the exact observation and payloads; a pack version or branch name is
not enough to establish equivalence. See the
[completed-scan contract](../../atlas/contracts/atlas-completed-scan-v1.md).

## Evidence and owner boundaries

Project Intelligence observes tracked and nonignored untracked saved files,
including deleted tracked paths from the same stable observation. Core owns
private copies, process execution, cancellation and retention. The selected pack
profile owns source-root mapping, compatibility, observer sources, launch policy
and recipe projection through API version 1 of `workbench.recipe_captures` and
the `workbench.recipe_graphs` adapter. Shell composes those owners. Atlas reads
the resulting graph and remains independently
usable without a game launch.

The retained request uses `workbench-developer-recipe-capture-request-v1`; the
prepared record uses `workbench-developer-recipe-capture-prepared-v1`; the outcome
uses `workbench-developer-recipe-capture-result-v1`. Runtime locks, observer source
and build identities, Java identities, protocol settings and exact input bytes
remain bound to that attempt. Preparation and execution errors record their
failed phase and keep the available evidence.

The current observer covers finite GT recipes, item/fluid values, machines and
qualified matching relationships. It does not establish crafting/smelting or
other-mod completeness, external acquisition, useful terminal endpoints,
transitive supply viability or gameplay completion. Missing producers or uses
remain structural candidates; cycle components alone do not prove a trap. See
the [Atlas audit contract](../../atlas/contracts/atlas-recipe-dead-ends-v1.md) and
[source-input contract](../../../profiles/packs/supersymmetry/atlas/developer-recipe-capture-input-v1.md).

The Windows Java 8 launcher and native capture path need their own acceptance,
including Unicode paths. Python preparation, unit tests and graph queries do
not establish that qualification. Modern Java 21/25 and changed pack binaries
need additional profile compatibility work.

The synthetic Forge Java contract tests use Core's same fixture registry when
available. They locate the Minecraft server library by the profile's SHA-256,
without assuming a particular installation path. For one test invocation,
`WORKBENCH_TEST_JAVA` and `WORKBENCH_FORGE_TEST_SERVER_JAR` remain exact overrides
for the Java executable and Minecraft server JAR. Missing fixtures skip those
tests with recovery guidance; an explicitly selected wrong library fails its
digest check. The optional original SussyPatches contract also searches the
registered runtime by its fixture SHA-256; `WORKBENCH_FORGE_TEST_SUSSYPATCHES_JAR`
overrides it for one test run. A passing synthetic test is not a native Forge
capture.
