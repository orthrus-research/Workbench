# Worldgen Cockpit V1 contract

Status: experimental working vertical slice

## Outcome

Worldgen Cockpit V1 answers one bounded question: what differs between two
fresh-world observations over the same declared Cleanroom sample, and which
available authority can support each part of that answer?

It produces one content-addressed
`workbench-worldgen-cockpit-report-v1` and an optional self-contained HTML
projection. The report remains useful without the HTML projection.

## Admitted inputs

Each side must be a successful `workbench-worldgen-iteration-report-v1` with
complete preflight, build, provision, configure, capture, summarize, and
handoff stages. V1 reopens and rehashes the report-bound profile, frozen plan,
and subject artifact. It validates the diagnostic summary, exact Strata V1 or
V2 manifest and tiles, and checked viewer handoff before comparison.

Optional pairs are admitted only together:

- Worldgen Observatory bundles and one common comparison scope whose seed,
  platform, dimension, and fingerprint chunks align with the iteration;
- GTCEu definition and impact inventories, plus optional controlled traces;
- observer-off JFR summaries.

Supplying a path is not evidence admission. The owning Crucible, Atlas,
Subsurface Studio, or JFR validator still controls acceptance.

## Alignment gate

Before any delta is computed, both sides must agree on:

- iteration profile identity and exact file hash;
- Cleanroom platform profile;
- mode, seed, dimension, and requested chunk region;
- Strata world seed, dimension, and exact captured window;
- runtime mod identities and hashes excluding the intentionally varied subject
  artifact;
- Cleanroom server JAR hash and Java executable hash; and
- distinct runtime and final-capture paths.

Failure yields `incomparable`. No semantic, final-state, statistical, causal,
or performance verdict is calculated across a failed alignment gate.

## Comparison and reproducibility states

The subject plan and artifact hashes define the input relation:

- `identical-input-control`: both are byte-identical;
- `before-after`: at least one differs; or
- `incomparable`: the alignment gate failed.

Report status means:

- `equivalent`: no semantic or exact final-state difference was observed;
- `changed`: a changed-input pair differs in the aligned scope;
- `unstable`: an identical-input control differs, which is a reproducibility
  failure and not a candidate effect; or
- `incomparable`: the pair failed alignment.

An A/B report is `not-calibrated` until an identical-input control establishes
the local run-to-run noise boundary. V1 reports this limitation rather than
subtracting an invented noise model.

## Evidence layers

The report keeps these layers separate:

| Layer | Owner | V1 answer |
| --- | --- | --- |
| Semantic | World Studio diagnostics retained by Crucible | Whether normalized generator/chunk records differ after the declared telemetry exclusions |
| Final state | Strata | Exact block, height, biome, cave-space, ore, lithology, and physical-fluid differences in captured chunks |
| Statistical | Workbench presentation over aligned Strata observations | Paired spatial distributions, practical threshold flags, quantiles, and outliers for one seed |
| Subsurface | Subsurface Studio over exact Crucible and Strata inputs | GTCEu definition/impact/trace-aware ore and fluid comparison when supplied |
| Causal | Atlas over admitted Observatory bundles | Bounded equality or first divergence for one common fingerprint scope aligned to the experiment seed, platform, dimension, and captured window |
| Performance | JFR and the World Studio summarizer | Paired stage and sampling latency deltas and profile-owned regression flags |
| Observer overhead | JFR | Observer-on versus separately supplied observer-off deltas for each side |

`complete-for-mode` means every evidence layer required by the selected pack
profile and mode is present and not unresolved. It does not mean whole-world,
cross-seed, general compatibility, or causal completeness outside the selected
scope.

## Exact final-state comparison

V1 compares every Y=0..255 block position in every aligned chunk. Missing
dense sections mean air under the admitted Strata contract. It also compares
all 256 height and biome columns per chunk.

Changed block pairs are classified through pack-owned state semantics as ore,
lithology, cave-space, physical fluid, or other block. Geometry derives final
cave components, entrances, water intersections and access; ore components,
material mass, vertical bands and cave access. These are final-state geometric
facts. They do not identify a carver, deposit instance, write owner, or reason.

## Modes

- `fast`: semantic, exact final-state, and paired spatial comparison.
- `debug`: fast evidence plus Atlas causal evidence.
- `performance`: fast evidence plus JFR performance and observer-overhead
  evidence.

The pack profile owns default sample windows, evidence requirements, practical
thresholds, and safety bounds. Supersymmetry is one explicit profile and is
never a generic default inside the module.

## Custody and mutation

`run --show` and `run --json` are inert. Execution creates a fresh ignored
experiment root, freezes both plans, profiles, supplied artifacts and optional
evidence, then delegates each world to the existing Crucible iteration runner.
It never reuses a world. A newly built first-side artifact is copied once and
that exact copy is supplied to the second side.

The incremental session remains `incomplete` until both iterations, comparison,
report write, and review write finish. A failed stage remains failed with its
error and retained paths. An existing experiment label is never overwritten.

## Authority and non-claims

Workbench orchestrates and derives presentation; it does not create a second
truth or approval path. Atlas remains causal authority, Strata final-state
authority, Crucible controlled-experiment authority, and the selected pack
profile pack-specific semantic authority.

V1 does not claim:

- that final blocks identify their generator;
- that one seed estimates a population or statistical significance;
- that a changed A/B pair exceeds runtime noise without a control;
- that a JFR delta from one process is a stable benchmark;
- that scope-aligned optional controlled evidence was emitted by the same
  process unless an upstream identity explicitly binds it; or
- that experimental Cleanroom `0.6.8-alpha` is a stable supported profile.
