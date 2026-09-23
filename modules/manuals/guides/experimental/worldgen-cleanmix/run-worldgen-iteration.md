# Run one World Studio iteration

Status: experimental working guide

Use this for the normal edit-build-fresh-world-observe loop. The runner builds
the current mod, provisions a disposable Cleanroom server, generates a bounded
window, summarizes structured diagnostics, validates optional Strata output,
and records one iteration report.

## Authority boundary

This is a development workflow. Its report records the inputs, stages, output
paths, and exact failure; it is not a causal proof or compatibility decision.

## Preflight

Provide a compatible runtime template and, when physical-state capture is
needed, a Strata checkout. Workbench may discover an unambiguous template
under `.workbench/`; explicit environment variables are also supported:

```bash
export WORKBENCH_WORLDGEN_RUNTIME_TEMPLATE=/path/to/cleanroom-runtime
export WORKBENCH_STRATA_ROOT=/path/to/strata
```

The runner copies the template and never edits it in place.

## Run

From the repository root:

```bash
python3 tools/workbench.py worldgen dev \
  --profile supersymmetry \
  --seed 8675309 \
  --region=-24,-4,16,16
```

Use `--mode fast` with a small region while fixing build or launch failures.
Use `--mode performance` only when you need the bounded World Studio JFR
events. `--no-open` keeps the checked viewer handoff without launching a
browser.

For an intentional comparison, pass a baseline log produced from the same
seed, plan identity, mod set, world type, generator, and chunk window:

```bash
python3 tools/workbench.py worldgen dev \
  --profile supersymmetry \
  --seed 8675309 \
  --region=-24,-4,16,16 \
  --compare /path/to/baseline.log \
  --no-open
```

Semantic differences are an inspectable result; malformed output, an unsafe
runtime, or an incomplete launch is a failed stage.

## Read the result

Open `.workbench/iterations/worldgen/<label>/iteration-report-v1.json`.
`failure.stage` points to the first failed boundary:

- `preflight`: missing or ambiguous runtime, toolchain, profile, or Strata.
- `build`: compilation, dependency, or remapping failure.
- `provision` or `configure`: unsafe input, stale destination, invalid mod, or
  malformed plan.
- `capture`: server failure, crash, or incomplete save.
- `summarize` or `performance`: malformed diagnostics or JFR.
- `handoff` or `open_viewer`: invalid manifest or local viewer failure.

Do not delete the failed run before inspecting its stage log. Use a new label
after fixing the cause. For a closed causal question, continue with
[Observe an exact Cleanroom run](observe-exact-cleanroom-worldgen-run.md).
