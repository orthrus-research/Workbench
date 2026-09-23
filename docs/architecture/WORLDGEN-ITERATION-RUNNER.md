# Worldgen iteration runner

Status: experimental, implemented dedicated-server workflow

The worldgen iteration runner shortens the daily edit-to-inspection loop:

```text
source or exact artifact
  -> frozen plan and profile
  -> fresh disposable Cleanroom runtime and world
  -> aligned diagnostics and Strata capture
  -> retained summary and local viewer handoff
```

It is an orchestrator, not a new evidence authority.

## Ownership

| Concern | Owner |
| --- | --- |
| Input binding, fresh runtime, staged execution, and report lifecycle | Crucible |
| Exact platform requirements | Selected Cleanroom profile |
| Pack defaults and policies | Explicit pack profile |
| Generator implementation and diagnostics | World Studio fixture |
| Final physical state and renderer handoff | Strata |
| Optional causal capture and interpretation | Crucible Observatory and Atlas |

Generic runner code lives under
`modules/crucible/src/workbench_crucible_worldgen_iteration/`. The current
Supersymmetry defaults live in
`profiles/packs/supersymmetry/worldgen/worldgen-iteration-profile-v2.json`.
Generated state belongs under `.workbench/iterations/worldgen/`.

## Commands

```bash
python3 tools/workbench.py worldgen dev --profile supersymmetry
python3 tools/workbench.py worldgen dev --profile supersymmetry --mode debug --no-open
python3 tools/workbench.py worldgen dev --help
```

The product command supports a profile or exact profile file, runtime template,
Strata checkout, Java and Gradle commands, plan, seed, aligned region, mode,
heap, timeouts, and viewer behavior.

The normal path builds one production-remapped artifact. `--artifact` binds an
exact existing JAR and skips compilation; `--skip-build` is an explicit
diagnostic reuse path. These choices are mutually exclusive and recorded in
the report. Supplying `--observatory-artifact` installs that exact observer and
requests the additive same-run capture; it does not turn the iteration report
into a causal interpretation.

## Staged lifecycle

The runner writes a stage as `running` before work, then atomically records
`complete` or `failed`:

1. `preflight` resolves and hashes the profile, runtime template, plan, tools,
   seed, and chunk window.
2. `build` selects one exact production artifact.
3. `provision` copies a clean runtime to independent files and excludes worlds,
   logs, caches, crashes, captures, and prior experiments.
4. `configure` creates a new world, freezes the plan, installs the artifact,
   and validates runtime requirements.
5. `capture` launches Cleanroom, requests the aligned Strata region, stops the
   server, and validates the captured package.
6. `summarize` validates structured diagnostics; optional comparison and JFR
   stages remain distinct from final-state capture.
7. `handoff` validates the exact local viewer target and optionally opens it.

The
[Worldgen iteration report V1](../../modules/crucible/contracts/worldgen-iteration-report-v1.md)
retains exact inputs, stage outcomes, failures, artifacts, and a reproduction
command. It is an operational development record, not a proof bundle,
Blueprints approval, or release gate.

## Freshness and reload boundary

Generator classes, the frozen plan, and already generated chunks cannot be
truthfully hot-reloaded. Every normal iteration therefore receives a fresh
runtime and a new world. The runner refuses to reuse an existing iteration
label or world path, and disposable files must not share mutable hard links
with the source template.

The selected profile declares Java and runtime requirements. The runner records
Java and Gradle independently and does not install host packages or download
licensed pack artifacts. A mod already present in the runtime passes through
the standard Forge lifecycle; the generic runner does not import, configure,
or adapt it.

## Safety and nonclaims

- A crash, invalid capture, corrupt package, missing readiness marker, unclean
  stop, or failed required stage makes the iteration fail.
- Exact state belongs to Strata; causal claims require admitted Observatory
  evidence and Atlas interpretation.
- A same-seed comparison is bounded to its declared scope and telemetry
  exclusions. It does not prove whole-world determinism.
- The implemented workflow is dedicated-server only and requires compatible,
  already provisioned runtime and Strata inputs.
- Reports, runtimes, worlds, logs, captures, recordings, and viewers remain in
  ignored local storage.

For aligned two-sided experiments, use the
[Worldgen Cockpit](WORLDGEN-COCKPIT.md).
