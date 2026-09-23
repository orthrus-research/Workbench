# Worldgen Cockpit

Worldgen Cockpit is the product-generic worldgen comparison engine. It runs
or consumes two completed Worldgen Iteration V1 experiments, rejects
misaligned evidence, and turns the accepted pair into one terminal decision
surface plus a self-contained local review.

The first experimental vertical slice provides:

- baseline and candidate runs in separate disposable Cleanroom runtimes and
  fresh worlds;
- one exact seed, dimension, mode, chunk window, runtime dependency set,
  Cleanroom server, and Java identity;
- byte-bound plans, profiles, subject artifacts, iteration reports, Strata
  manifests and shards, summaries, logs, and optional evidence;
- semantic World Studio comparison and exact final-block comparison;
- clickable and terminal changed-chunk heatmaps, exact changed-position
  samples, block-state deltas, and robust spatial outliers;
- paired height, biome, cave-space, ore-mass, lithology, fluid, component,
  entrance, depth-band, and accessibility measurements;
- optional Atlas first-divergence answers, GTCEu Subsurface Studio comparison,
  JFR regression analysis, and separately reported observer overhead; and
- an explicit identical-input control. If byte-identical plans and artifacts
  produce different final state, the result is `unstable`, never a candidate
  effect.

Run from the repository root:

```bash
# Preview the complete inert plan. With no side-specific input, this is an A/A
# reproducibility control and the first artifact is built once for both sides.
python3 tools/workbench.py worldgen cockpit run \
  --profile supersymmetry \
  --mode fast \
  --show

# Compare two plans while holding the built artifact constant.
python3 tools/workbench.py worldgen cockpit run \
  --profile supersymmetry \
  --mode fast \
  --baseline-plan path/to/baseline.groovy \
  --candidate-plan path/to/candidate.groovy

# Recompose already completed iterations without launching Minecraft.
python3 tools/workbench.py worldgen cockpit compare \
  --profile supersymmetry \
  --baseline-report path/to/baseline/iteration-report-v1.json \
  --candidate-report path/to/candidate/iteration-report-v1.json \
  --output .workbench/experiments/worldgen/local-review/report.json
```

`fast` requires semantic, exact final-state, and paired spatial evidence.
`debug` additionally requires a separately admitted Atlas causal comparison.
The selected Observatory cohort must match the iteration seed, platform,
dimension, and captured chunk window; this alignment does not invent
same-process provenance.
`performance` additionally requires paired JFR summaries and observer-off JFR
summaries so production regression and observation overhead remain separate.
Missing mode evidence produces `partial`, not a false complete result.

The direct aliases are `workbench cockpit ...` and
`workbench worldgen compare ...`. The high-signal console exposes the same
arguments through `world-studio.cockpit-run`, `-compare`, `-show`, and `-open`
wizards.

See the [architecture](../../docs/architecture/WORLDGEN-COCKPIT.md), the
[V1 contract](contracts/worldgen-cockpit-v1.md), and the
[report schema](schemas/workbench-worldgen-cockpit-report-v1.schema.json).

This is a bounded experimental dedicated-server slice. It does not yet provide
multi-seed inference, automatic Observatory capture, automatic observer-off
runs, an embedded Strata 3D diff, production GTCEu trace emission, or
integrated/client comparison.
