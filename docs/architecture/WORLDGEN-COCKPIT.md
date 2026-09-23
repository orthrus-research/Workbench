# Worldgen Cockpit

Status: experimental, implemented dedicated-server workflow

Worldgen Cockpit compares two fresh-world observations over one aligned sample
and reports whether they are equivalent, changed, unstable, or incomparable.
It composes the existing worldgen iteration runner; it does not reimplement
build, provisioning, launch, capture, or viewer handoff.

## Authority and composition

The product-generic engine lives under `modules/worldgen-cockpit`. An explicit
pack profile selects iteration defaults, regions, evidence requirements,
thresholds, and safety bounds. Supersymmetry is one profile, never an implicit
default.

| Evidence | Authority |
| --- | --- |
| Experiment identity, input binding, capture health, and custody | Crucible |
| Exact final block, biome, height, cave, ore, lithology, and fluid state | Strata |
| Causal equality or first divergence | Atlas over admitted Observatory evidence |
| GTCEu definition and controlled trace interpretation | Subsurface Studio |
| Pack-specific requirements and action policy | Selected pack profile |
| Terminal and HTML composition | Workbench |

Workbench presents these layers without converting one owner's evidence into
another owner's claim.

## Commands

```bash
# Preview an identical-input control.
python3 tools/workbench.py worldgen cockpit run \
  --profile supersymmetry --mode fast --show

# Compare two completed iterations without launching Minecraft.
python3 tools/workbench.py worldgen cockpit compare \
  --profile supersymmetry \
  --baseline-report path/to/baseline/iteration-report-v1.json \
  --candidate-report path/to/candidate/iteration-report-v1.json \
  --output .workbench/experiments/worldgen/local-review/report.json

python3 tools/workbench.py worldgen cockpit show --help
python3 tools/workbench.py worldgen cockpit open --help
```

The direct aliases are `workbench cockpit ...` and
`workbench worldgen compare ...`. The high-signal console exposes the same
arguments through `world-studio.cockpit-run`, `-compare`, `-show`, and `-open`.

## Alignment and null control

Before comparison, both sides must agree on the profile identity, Cleanroom
platform, mode, seed, dimension, chunk window, Strata scope, runtime dependency
set, server JAR, Java executable, and intentionally varied subject. Runtime,
world, and final-capture paths must be distinct. A failed alignment gate is
`incomparable`; no delta is calculated across it.

Byte-identical plans and artifacts form an A/A control. If their final state
differs, the result is `unstable`, `behavior_changed` is `null`, and the
delta is never described as a candidate effect. A changed-input pair is not
calibrated until a local A/A control establishes the observed noise boundary.

## Evidence layers and modes

`fast` requires normalized diagnostics, exact final state, and paired spatial
comparison. `debug` additionally requires aligned Atlas causal evidence.
`performance` additionally requires paired JFR summaries and separate
observer-off summaries so generator cost and observation overhead remain
distinct.

Optional evidence is admitted only in aligned pairs and through its owning
validator. Missing required evidence yields partial coverage, not a false
complete result.

The terminal and self-contained HTML reports use one content-addressed JSON
record. They show exact changed chunks and positions plus paired terrain,
biome, cave, ore, lithology, fluid, and accessibility summaries. Final-state
geometry does not identify a carver, deposit, listener, or write owner.

## Custody and safety

Execution creates two disposable runtimes and new worlds. When the first side
builds an artifact, the cockpit freezes that exact file for the second side.
Inputs are copied and rehashed, an existing experiment label is never
overwritten, and generated state remains under ignored `.workbench/` storage.

A crash, failed stage, invalid capture, corrupt binding, or missing required
layer is not a successful comparison. One seed does not establish population
statistics, cross-machine stability, whole-world determinism, visual quality,
or stable profile support.

The exact report, alignment, decision, and nonclaim semantics are defined by
the [Worldgen Cockpit V1 contract](../../modules/worldgen-cockpit/contracts/worldgen-cockpit-v1.md).
