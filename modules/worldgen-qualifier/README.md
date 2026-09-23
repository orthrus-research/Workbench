# Worldgen Qualifier

Worldgen Qualifier turns the Worldgen Cockpit null-control rule into a reusable
acceptance workflow for modded Minecraft generators. It is product-generic:
the module owns safe orchestration and fail-closed evaluation, while an
explicit pack profile owns the matrix, intent, domain, risk, and assurance
requirements. Supersymmetry is one profile, never an implicit default.

The working V1 slice provides:

- `workbench qualify run`: preview or execute a matrix of
  byte-identical A/A controls in independent fresh Cleanroom JVMs and worlds;
- seed, heap, pair-order, and repetition cells, plus cross-cell comparisons for
  matching seed and capture scope;
- exact Strata final-state fingerprints and Cockpit semantic, block, height,
  biome, cave, lithology, ore, fluid, decoration, statistical, causal, and
  performance evidence gates;
- `development`, `terrain`, `caves`, `ore`, `decoration`, and `release`
  decision intents;
- a bounded exact-JAR class scanner for unordered RNG selection,
  identity-sensitive ordering, ambient entropy, filesystem/reflection order,
  asynchronous generation, GC-sensitive caches, host defaults, and
  non-associative parallel reductions;
- exact profile dispositions for static findings, without treating a static
  match as runtime causation; and
- retained content-addressed JSON, a concise terminal verdict, an HTML gate
  review, incremental session state, source bindings, and reproduction command.

Preview the smallest practical qualification:

```bash
python3 tools/workbench.py worldgen qualify run \
  --profile supersymmetry \
  --suite smoke \
  --intent development \
  --show
```

Execute it by omitting `--show`. A rejected or inconclusive qualification
returns exit 1; an operational or malformed-input failure returns exit 2.
If required coverage is already known to be unavailable, preview is marked
`ATTENTION` and execution refuses before launching Minecraft. The explicit
`--allow-inconclusive` override exists only when retaining partial evidence is
worth the declared cost.

Assess an already retained A/A Cockpit control:

```bash
python3 tools/workbench.py worldgen qualify assess \
  --profile supersymmetry \
  --suite smoke \
  --intent decoration \
  --cockpit-report path/to/worldgen-cockpit-report-v1.json
```

Screen any exact mod JAR independently:

```bash
python3 tools/workbench.py worldgen qualify scan \
  --profile supersymmetry \
  --jar path/to/mod.jar
```

`accepted-empirical` means every required exact observation agreed inside the
declared matrix. It does not mean all seeds, machines, profiles, traversal
orders, or time. `accepted-exact` additionally requires the profile's admitted
stage evidence and perturbation coverage. Unknown, partial, unsupported, and
unreviewed required evidence never becomes an allowed noise bucket.

See the [architecture](../../docs/architecture/WORLDGEN-QUALIFIER.md), the
[V1 contract](contracts/worldgen-qualifier-v1.md), and the
[qualification report schema](schemas/workbench-worldgen-qualification-report-v1.schema.json).
