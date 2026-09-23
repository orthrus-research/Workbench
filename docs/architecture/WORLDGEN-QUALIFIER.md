# Worldgen Qualifier

Status: experimental, implemented dedicated-server workflow

Worldgen Qualifier applies the Cockpit's identical-input rule across a bounded,
pack-owned perturbation matrix. It asks whether one exact plan and artifact
remain equal for every required observed cell, domain, and evidence layer.

The result is an orchestration and gate record, not a new evidence authority.

## Authority

- Crucible owns experiment identity, runtime health, and custody.
- Strata owns admitted final physical state.
- Atlas owns admitted causal and first-divergence answers.
- Subsurface Studio owns GTCEu definition and controlled-trace interpretation.
- The selected pack profile owns matrix axes, intent, domain requirements,
  risk dispositions, safety bounds, and acceptance policy.
- Workbench composes those answers without creating another truth or approval
  path.

## Commands

```bash
# Preview a bounded matrix without launching Minecraft.
python3 tools/workbench.py worldgen qualify run \
  --profile supersymmetry --suite smoke --intent development --show

# Assess an already retained A/A Cockpit report.
python3 tools/workbench.py worldgen qualify assess \
  --profile supersymmetry --suite smoke --intent decoration \
  --cockpit-report path/to/worldgen-cockpit-report-v1.json

# Screen one exact mod JAR for generic nondeterminism risks.
python3 tools/workbench.py worldgen qualify scan \
  --profile supersymmetry --jar path/to/mod.jar

python3 tools/workbench.py worldgen qualify show --help
python3 tools/workbench.py worldgen qualify open --help
```

The direct alias is `workbench qualify`. The high-signal console exposes
`world-studio.qualifier-run`, `-assess`, `-scan`, `-show`, and `-open`.

## Matrix and gates

Each cell is a Cockpit A/A control: two independent JVMs, two fresh worlds,
and identical plan and artifact bytes. The matrix can vary seed, pair order,
heap size, region, and repetition. Matching scopes are also compared across
cells so a perturbation-specific result cannot pass only because the two runs
inside one cell agree.

Domain gates remain separately visible for semantic diagnostics, terrain,
biomes, lithology, caves, ore, fluids, decoration, and every compared final
block. The selected intent decides which gates and evidence owners are
required, but missing or unsupported required coverage stays inconclusive.
`--allow-inconclusive` authorizes retaining partial evidence; it does not turn
that evidence into acceptance.

The exact-JAR scanner reports bounded investigation leads for unordered RNG
selection, identity-sensitive ordering, ambient entropy, filesystem or
reflection order, asynchronous work, cache sensitivity, host defaults, and
parallel numeric reduction. A static match does not prove that a class loads,
a route executes, or a finding caused a delta. Profile dispositions bind the
exact JAR hash, class, rule, and rationale.

## Outcomes and safety

The evaluator applies fail-closed precedence:

1. an observed critical A/A or cross-perturbation delta is rejected as
   unstable;
2. misalignment, changed-input evidence, unknown domains, missing authorities,
   insufficient coverage, or unresolved required risks are inconclusive;
3. complete empirical final-state requirements can be accepted inside the
   declared matrix; and
4. the stronger exact outcome additionally requires every profile-mandated
   stage and perturbation observation.

A crash, corrupt input, invalid capture, exceeded bound, or failed required
stage is an operational failure, not an inconclusive successful run. Execution
never reuses a world or overwrites an existing label, and all generated state
remains under ignored `.workbench/` storage.

Acceptance is bounded to the exact matrix. It does not claim all-seed or
whole-world determinism, source-to-JAR reproducibility, production
compatibility, visual quality, same-process causality without an upstream
identity, or stable support for an experimental platform profile.

The exact inputs, decision precedence, and nonclaims are defined by the
[Worldgen Qualifier V1 contract](../../modules/worldgen-qualifier/contracts/worldgen-qualifier-v1.md).
