# Worldgen iteration report V1

`workbench-worldgen-iteration-report-v1` is the operational record written by
`workbench worldgen dev`. It is deliberately a development report, not a
Crucible proof receipt, Atlas interpretation, Blueprints approval, or release
gate.

The report exists so a developer can answer five practical questions after a
successful or failed run:

1. Which exact profile, Java, Gradle, source artifact, Groovy plan, seed, and
   chunk window were selected?
2. Which stage ran, why did it run, and how long did it take?
3. Which stage failed and what is the retained actionable error?
4. Where are the disposable runtime, logs, summaries, Strata package, and
   checked viewer handoff?
5. What command reproduces the requested iteration?

## Lifecycle

The top-level `status` is `running`, `failed`, or `complete`. Each ordered
stage is written as `running` before work begins and then atomically rewritten
as `complete` or `failed`. A failed stage carries an exception type and
message, and the top-level `failure.stage` names it. This makes a process
termination or build failure visible rather than presenting a generic success.

V1 stages currently include:

- `preflight`: resolve and identify inputs without mutating a runtime;
- `build`: resolve one exact production-remapped World Studio artifact,
  compiling current source by default or retaining an explicit artifact/skip
  selection in stage detail;
- `provision`: construct a new runtime while excluding prior worlds and run
  residue;
- `configure`: select the fresh world, install the current mod, and freeze one
  plan;
- `capture`: run Cleanroom and request the aligned Strata sample;
- `summarize`: validate and reduce World Studio diagnostics;
- optional `compare` and `performance` stages;
- `handoff`: validate the exact local Strata deep link; and
- optional `open_viewer`: start the local renderer and retain its PID and stop
  command.

## Boundaries

- Generated reports, runtimes, worlds, logs, captures, and recordings remain
  below ignored `.workbench/iterations/worldgen/` storage.
- Runtime files are copied to independent inodes. A disposable target must not
  be able to mutate its source template through a shared archive hard link.
- Existing worlds, logs, caches, crash output, prior Strata output, and
  retained experiment directories are not copied into the new runtime.
- The selected Groovy source is copied once into `groovy/postInit`; other
  scripts are retained unless they also configure `mods.worldStudio`.
- The runner does not import, configure, call, or adapt Recurrent Complex. If
  its stock artifact exists in the template, it passes through as an ordinary
  Forge participant and `generate-structures=true` remains enabled.
- A passing report means this bounded developer iteration completed. It does
  not establish whole-world determinism, compatibility for every installed
  mod, visual quality, or causal attribution.

Adding or changing V1 meaning requires a new format version. New optional
developer stages may be added only if older consumers can continue treating
the ordered `stages` array and top-level lifecycle fields as opaque operational
detail.
