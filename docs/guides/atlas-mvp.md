# Atlas MVP usage guide

Atlas helps pack developers inspect captured recipes, find missing producer or
consumer links, and trace item/fluid relationships while designing progression
and quests. A completed scan is a shareable data object for one exact captured
environment. Another developer can review it without initializing Minecraft.

The initial release target is **Linux x64**, including use inside a WSL2 Linux
distribution. Native Windows installation is outside this release. Core owns local
installation, processes and storage. Atlas owns graph queries and recipe
analysis; profiles provide explicit game-specific adapters. Axiom and the other
Workbench modules are optional integrations, not prerequisites for reading a scan.

## 1. Choose your installation

| Your task | Select these components | Additional inputs |
|---|---|---|
| Import and review a completed scan | Atlas, with Core, API and Crucible dependencies | A completed-scan ZIP from a trusted source |
| Capture your own saved pack branch | Core, Shell and the Supersymmetry profile, with their dependencies | Compatible prepared server, saved pack checkout and a supported JDK |
| Use an IDE client | The relevant native components plus the VS Code or IntelliJ plugin | A configured Core executable |

A scan ZIP and a Workbench installation kit are different objects. Keep shared
scans separate from the installer. Do not treat a plain ZIP of arbitrary graph
files as a completed scan; import requires the versioned manifest and original
evidence records.

### Obtain or build a wheelhouse

Use a reviewed Linux x64 wheelhouse for your Python minor version. It contains
the Python packages, dependency wheels, installer
and a hash manifest. Installation from it is offline. Python 3.12–3.14 with
`venv` is a prerequisite and is not bundled. Run the same Linux instructions
inside WSL2 when using a Windows computer; keep the checkout, installation and
state in the Linux filesystem.

If no matching installation kit has been published, build one from the public
source on the intended host. Install Git and Pixi **0.75.0**, then run:

```text
git clone --branch master https://github.com/orthrus-research/workbench.git
cd workbench
pixi run --locked --no-config -e release python tools/build_native_distribution.py --component workbench-atlas --output .workbench/build/atlas-wheelhouse
```

For capture workflows, select the larger assembly instead:

```text
pixi run --locked --no-config -e release python tools/build_native_distribution.py --component workbench-core --component workbench-shell --component workbench-profile-supersymmetry --output .workbench/build/capture-wheelhouse
```

The locked `release` environment supplies the build Python and pip. The build
may download dependencies; it selects the declared Workbench dependency closure
and creates a target-specific wheelhouse. Every output directory must be new.
Read its `wheelhouse.json` for the exact Python minor and components. The current
Pixi release environment selects Python 3.14; use Python 3.14 to install its kit.

### Install on Linux x64

```bash
python3.14 /path/to/atlas-wheelhouse/install_workbench.py /path/to/atlas-wheelhouse --destination "$HOME/.local/share/workbench/atlas-mvp"
"$HOME/.local/share/workbench/atlas-mvp/bin/workbench" --version
```

Use that executable in subsequent examples, or add its `bin` directory to your
own `PATH`. The installer never overwrites an existing destination. For an
upgrade, install to a new directory and select its executable explicitly.

Confirm the installed commands:

```text
workbench modules list --json
workbench atlas scans --help
workbench atlas recipes --help
```

## 2. Import and review a shared scan

Use paths that belong to your machine. The destination must not already exist:

```text
workbench atlas scans import "branch-scan.zip" --destination "Imported scan"
workbench atlas scans show "Imported scan"
workbench atlas scans audit "Imported scan" --summary
```

Import verifies archive paths, member hashes and the captured graph/report
bindings before publishing the destination. A failed import produces no usable
partial scan. Keep your notes outside the imported directory because its exact
inventory is verified. Original path strings remain historical provenance;
Atlas never follows them to the original developer's machine.

`show` identifies the original observation, graph, saved audit policy and
coverage. Its graph path is the input for the recipe commands below. Matching
the scan to your current checkout remains unassessed: a branch name or pack
version label alone does not prove that two environments match.

Scan commands currently verify the complete included evidence on each open.
Large scans can take minutes even for a small page; paging bounds the returned
rows, not verification cost. No game, graph projection or audit evaluation runs
when reading the saved findings.

## 3. Find dead-end candidates

```text
workbench atlas scans audit "Imported scan" --finding missing-producer-candidate --limit 25
workbench atlas scans audit "Imported scan" --finding no-output-use-candidate --limit 25
workbench atlas scans audit "Imported scan" --finding both-sides-candidate --limit 25
workbench atlas scans audit "Imported scan" --text circuit --lookup-state active --offset 0 --limit 25 --json
```

`--text` searches recipe/map identities and related item/fluid identifiers and
names. Use `--json` for the complete machine-readable page. Follow a non-null
`page.next_offset` with the same filters and limit. The unfiltered original
summary, coverage and audit policy remain in every page.

| Finding | Meaning within the captured domain |
|---|---|
| `missing-producer-candidate` | A required input lacks a supported local producer link. |
| `no-output-use-candidate` | No output has an accepted local use. |
| `both-sides-candidate` | Both of those conditions apply. |
| `stranded-output-candidate` | At least one output has no accepted local use. |
| `structural-cycle` | The recipe participates in a recorded dependency cycle. |

These categories overlap. Missing recipe links are review candidates, not proof
that an item is unobtainable or useless. Mining, loot, trades, equipment,
building and quest uses may lie outside the captured domain. Unsupported
matching remains uncertain. A cycle does not establish an initial supply or a
deadlock; follow alternatives and retain unresolved dependencies.

## 4. Follow producers, consumers and recipe values

Use the graph path reported by `show`. Copy a `selection_id` from search;
do not construct one from a display name:

```text
workbench atlas recipes context "Imported scan/graph" --json
workbench atlas recipes search "Imported scan/graph" water --json
workbench atlas recipes inspect "Imported scan/graph" "<selection_id>" --json
workbench atlas recipes browse "Imported scan/graph" "<selection_id>" --offset 0 --limit 25 --json
workbench atlas recipes routes "Imported scan/graph" "<item-or-fluid-selection_id>" --json
```

Inspection and relationship pages retain observed quantities, duration, energy,
reusable inputs, chance outputs and evidence references where supported. For
quest preparation, inspect the target item/fluid, choose a producer, follow each
required input and record alternatives and unresolved sources. Preserve machine
and configuration requirements. Route traversal concerns the finite captured
graph; it does not prove practical player completion or create a quest book.

The graph search display is bounded; it is not the full audit inventory. Use the
saved audit for complete finding totals and its page metadata for filtered rows.
`workbench atlas recipes audit-dead-ends GRAPH --json` or `--csv` explicitly
evaluates a graph again; `workbench atlas scans audit` reads the saved report.

## 5. Capture your own branch

Install the capture assembly and read the
[developer-local capture contract](../../modules/workbench-shell/contracts/developer-recipe-capture-v1.md).
The current Supersymmetry capture profile supports its pinned **Forge 1.12.2 /
Forge 14.23.5.2860 / Java 8** binary model. Java 21/25 environments need a
separately supported profile; selecting a different Java executable does not
qualify them. Reading a completed scan needs no Java.

Select separate absolute paths for your saved checkout, matching prepared
server, compatible JDK containing `java` and `javac`, and retained state:

```text
workbench capture recipes plan --workspace "<checkout>" --runtime "<prepared-server>" --java-home "<jdk>" --pack-profile supersymmetry --state-root "<capture-state>" --heap-mib 8192 --json
workbench capture recipes prepare "<attempt_id>" --confirm "<request.id>" --state-root "<capture-state>" --json
```

Review each returned record before the next step. Use the actual IDs it returns.
The checkout may contain saved changes on an unreleased branch; unsaved editor
buffers are not inputs. Planning binds the selected source, runtime and JDK.
Preparation verifies and copies them, applies the saved source roots and builds
the observer. Choose heap and disk capacity for your environment; preparation
retains inputs and execution creates a separate runtime copy.

After reading and accepting the Minecraft EULA, run the prepared attempt:

```text
workbench capture recipes run "<attempt_id>" --confirm "<prepared.id>" --accept-eula --state-root "<capture-state>" --json
workbench capture recipes show "<attempt_id>" --state-root "<capture-state>" --json
```

From another terminal, request cancellation if needed:

```text
workbench capture recipes cancel "<attempt_id>" --state-root "<capture-state>" --json
```

Cancellation retains available evidence. A successful server exit alone is not
an admitted scan; the completed result must verify the capture and report its
graph, audit and coverage. Changed source or runtime inputs require a new plan.
Failed attempts are retained and are not reused for changed inputs.

## 6. Share the completed work

Export from a successfully completed local attempt:

```text
workbench capture recipes export "<attempt_id>" --state-root "<capture-state>" --output "<new-scan.zip>" --json
```

Recipients can also forward an imported object:

```text
workbench atlas scans export "Imported scan" --output "forwarded-scan.zip" --json
```

The ZIP includes the original capture data, graph/index, saved audit and provenance
records. It omits the game, mods, JDK, world and original execution output.
Re-export preserves the portable manifest identity and original payload bytes;
compressed ZIP bytes may differ across platforms. Hash verification establishes
content integrity, not publisher authentication. Original provenance can contain
absolute paths and environment identifiers, so review it before sharing.

Store exports under ignored `.workbench/exports/` or outside your source checkout.
A successful export does not make the full retained execution attempt disposable;
Core-managed recoverable recipe-attempt cleanup is not yet exposed in this MVP.

## Current scope and troubleshooting

- The first audit covers the finite captured GT recipe domain. Other recipe
  families, external acquisition/terminal uses, transitive supply, chance
  feasibility and machine execution remain incomplete or unassessed.
- A missing producer/use is not an empty-success whole-pack diagnosis. Read the
  coverage and unresolved counts alongside the candidate list.
- If import rejects an archive, obtain the complete exported object. Do not edit
  its manifest, remove payloads or rewrite paths to force acceptance.
- If installation/import/export refuses an existing destination, choose a new
  one. Existing installs and observations are intentionally retained.
- If capture reports an incompatible runtime or Java version, select an admitted
  environment. No profile silently substitutes a release for your branch.
- The commands are usable independently of the IDE plugins. See
  [IDE clients](../../clients/README.md) for configuring their Core executable;
  a dedicated visual scan-audit journey is still future work.

Reference: [completed-scan format and API](../../modules/atlas/contracts/atlas-completed-scan-v1.md),
[Atlas recipe queries](../../modules/atlas/README.md), and
[support](../../SUPPORT.md). Workbench's source license is in [LICENSE](../../LICENSE);
upstream material keeps its recorded licenses and notices.
