# Atlas

Atlas is Workbench's read-only game-knowledge module. It answers
questions about recipes, materials, resources, progression, source locations,
runtime observations, and world generation while preserving the scope and
uncertainty of the underlying evidence.

Atlas never authorizes a source edit, profile action, or release. Blueprints
owns construction, Crucible owns runtime capture and evidence custody, and a
pack profile supplies pack-specific interpretation.

The [Axiom–Atlas integration vision](../../docs/product/AXIOM-ATLAS-VISION.md)
describes proposed native-check integration and its separate release boundary.
The [integration boundaries](../../docs/architecture/ATLAS-INTEGRATION-BOUNDARIES.md)
describe the shared API contracts and optional adapters. Atlas does not require
Pack Program Studio, Material Semantics or Shell for its independent recipe
queries. Crucible supplies its runtime evidence readers. Shell adds retained
construction-plan assessment; selected profiles supply their own integrations.

## Current surfaces

- Exact, parameterized producer, consumer, prerequisite, infrastructure,
  byproduct, recycling, and route queries.
- Immutable categorical graph publications with canonical JSONL authority and
  a rebuildable SQLite search index.
- Recipe search, inspection, bounded or complete finite impact analysis, proposed-change
  assessment, and exact before/after runtime comparison.
- Material and fluid classification with independent identity, composition,
  properties, flags, forms, and runtime-occurrence lenses.
- Worldgen listener/write queries and first-divergence analysis over sealed
  Crucible bundles.
- Experimental stopped-world Anvil fingerprints and semantic block deltas.

Profile-specific adapters and current source locks live under `profiles/`.
Selected resources resolve through the installed profile API. Atlas also
packages the exact historical Supersymmetry V3 lock with its fixed catalog,
so that read-only historical queries work without a profile installation.
Source caches and generated knowledge files default beneath Core's selected
user state root (`atlas/sources/legacy-forge` and `atlas/knowledge`). Existing
checkout-local `.workbench/atlas` files stay in place and can be reopened by
passing their paths explicitly to the source, mutation, and normalization
tools. Static normalization also accepts `--source-root` and `--pack-root`
through the selected profile adapter when validating older source bytes.

For the historical Supersymmetry V3 source lock, the standalone diagnostics
can use `--pack-profile supersymmetry` to read the admitted profile's declared
`source-lock` resource. `--source-lock /path/to/source-lock.json` selects an
explicit retained file instead. A selected profile that is unavailable fails
without falling back to the packaged historical copy. Source-authority file verification
with `--pack-profile` also requires `--source-root` for the provisioned source
cache; its pack source is resolved beneath that same root unless `--pack-root`
is supplied.


## Audit captured recipes for dead ends

```text
workbench atlas recipes audit-dead-ends /path/to/graph
workbench atlas recipes audit-dead-ends /path/to/graph --json
workbench atlas recipes audit-dead-ends /path/to/graph --csv
```

The audit scans every captured GT recipe for missing producer/use candidates,
unused co-products, both-sided gaps and structural cycles. Accepted alternatives,
reusable requirements, chance outputs and unknown lookup/matching states remain
distinct. JSON contains full evidence references; CSV contains every recipe row.
The default display is a summary. No Minecraft launch or profile is needed to
read an admitted graph, including one imported from your own supported capture.

External acquisition, terminal uses and other recipe families remain outside
this first audit. Findings are review candidates; missing captured links do not
prove impossible acquisition or useless output. See the
[dead-end audit contract](contracts/atlas-recipe-dead-ends-v1.md).

## Share and review a completed scan

An Atlas scan archive carries a completed graph, its original observation
payloads and input binding, and its saved audit. Import it on another Windows
or Linux machine without installing the pack, Java, Shell or a pack profile.
Atlas verifies the archived data and its relationships before publishing the
imported directory.

```text
workbench atlas scans import "branch-scan.zip" --destination "Imported scan"
workbench atlas scans show "Imported scan"
workbench atlas scans audit "Imported scan" --summary --json
workbench atlas scans audit "Imported scan" --finding both-sides-candidate --limit 25 --json
workbench atlas scans audit "Imported scan" --text circuit --offset 25 --limit 25 --json
workbench atlas recipes search "Imported scan/graph" copper --json
workbench atlas scans export "Imported scan" --output "shared-scan.zip"
```

`audit` reads saved findings; it does not run the evaluator again. Its default
output is a summary. Filters or explicit paging select stored recipe rows,
while the original coverage and complete audit totals remain visible. The
result identifies a different installed audit policy when applicable.

Import and export require new destinations. Re-export preserves the original
envelope identity and payload bytes; local import receipts remain separate.
The archive is a historical snapshot: content verification does not authenticate
its publisher, reproduce its original execution, or match it to the current
pack environment. See the [completed scan contract](contracts/atlas-completed-scan-v1.md)
for contents, filters, provenance and these boundaries. The original capture
owner provides `workbench capture recipes export` when Shell is installed.

## Retained initialization observations

Atlas can import an explicitly selected retained Axiom snapshot through the
optional Supersymmetry observation adapter. The importer uses Core's custody and
read leases and Axiom's historical request/schema admission. It does not execute
initialization again. Axiom and Shell remain unnecessary for querying a published
observation graph.

```bash
workbench atlas observations import-snapshot /path/to/check-attempt \
  --pack-profile supersymmetry --output /path/to/new-observation-graph --json
workbench atlas observations context /path/to/observation-graph --json
workbench atlas observations search /path/to/observation-graph iron --json
workbench atlas observations inspect /path/to/observation-graph NODE_ID --json
workbench atlas observations relationships /path/to/observation-graph NODE_ID --json
workbench atlas observations evidence /path/to/observation-graph NODE_ID \
  --snapshot /path/to/check-attempt --pack-profile supersymmetry --json
```

Paired snapshots require an explicit `--side baseline` or `--side candidate`;
ordinary snapshots use `single`. Import requires the profile's `observations`
extra: Axiom 0.1.1 and Core 0.1.4 or later in their respective 0.1 series.
Disabled or unavailable integrations fail explicitly without disabling queries.

The [initialization node families](contracts/atlas-initialization-node-families-v1.md)
cover retained lifecycle/source/configuration, material and generated-content
inventories, witnessed properties, GT lookup/category state, crafting values and
order, furnace storage, ore mutations, diagnostics and other captured families.
Fingerprints remain fingerprints; stored callbacks and selectors remain stored
observations. Neither import completion nor original capture coverage means native
initialization succeeded, a matcher executed, or gameplay is possible.

The [V3 graph authority](contracts/atlas-categorical-observation-bundle-v3.md)
preserves the original producer and initialization scope. The
[observation query contract](contracts/atlas-observation-query-v1.md) defines
exact selections, relationship pages, evidence references and optional original
record resolution. Existing finite GT impact remains a separate capability;
native object-reference cycles do not become recipe dependency cycles.

For linked queries, open a [JSONL observation session](contracts/atlas-observation-session-v1.md):

```bash
workbench atlas observations session /path/to/observation-graph
```

Send requests through a pipe or file using the versioned session protocol.
Atlas verifies the graph once, then reuses the view for search, inspection,
relationships and evidence. Each request binds the graph identity returned by
the ready record. File changes invalidate the view and require a fresh open.
The command uses Core's existing process lifetime and needs no Shell or daemon.

The first family-specific observation analysis is
[crafting reference exposure](contracts/atlas-crafting-reference-exposure-v1.md):

```bash
workbench atlas observations crafting-exposure /path/to/observation-graph NATIVE_VALUE_NODE_ID --json
```

It finds stored crafting definitions that refer to the selected native value,
with exact reference paths and cycle-safe traversal of that catalog. It follows
the finite recorded graph by default; explicit `--max-depth` and `--max-nodes`
bounds disclose any unexplored frontier. Shared object references explain
structural exposure; they do not predict ingredient matching or the result of an
edit. Capture gaps and the original native outcome stay visible.

## Recipe queries

In VS Code use **Workbench: Search Atlas Recipes**; in IntelliJ use
**Tools → Workbench → Search Atlas Recipes**. Choose a captured graph, search
for an item, fluid, recipe or machine, then select a result. Follow incoming
or outgoing relationships directly, use the previous/next page controls, or
inspect captured values and evidence. Input selectors preserve alternatives,
quantities, reusable inputs and unresolved matching as recorded. A representative
stack is labeled separately from an accepted alternative.

Reopen the last selection from the same action after restarting the editor.
Reopening verifies the graph identity and refuses changed evidence. The browser
shows one neighborhood at a time; its page count is not a claim that the whole
prerequisite route is complete. Already visited nodes are marked as references
on the current path. They do not prove a deadlock or a craftable cycle.

The same paged API is available independently of the IDE and Shell:

```bash
workbench atlas recipes browse /path/to/graph SELECTION_ID --limit 50 --offset 0 --json
```

Pass `--expect-graph GRAPH_SET_ID` when following a previous result. The response
retains exact node properties, relationship values and evidence, the graph
context, and an explicit next offset. Complete prerequisite derivation remains
available through `routes`; browsing does not discard or rewrite graph evidence.
The native browser reuses a verified process-local graph session for successive
clicks. File changes invalidate the session. See the
[browse and session contract](contracts/atlas-recipe-browse-session-v1.md).

Use the Workbench CLI with an explicit graph root or source checkout:

```bash
pixi run --locked --no-config workbench atlas recipes context /path/to/input
pixi run --locked --no-config workbench atlas recipes search /path/to/input copper_sulfate
pixi run --locked --no-config workbench atlas recipes inspect /path/to/input SELECTION_ID
pixi run --locked --no-config workbench atlas recipes routes \
  /path/to/verified-graph RESOURCE_SELECTION_ID --json
pixi run --locked --no-config workbench atlas recipes impact \
  /path/to/verified-graph RECIPE_SELECTION_ID --max-depth 4 --max-nodes 500
pixi run --locked --no-config workbench atlas recipes impact \
  /path/to/verified-graph RECIPE_SELECTION_ID --exploration complete-finite --json
pixi run --locked --no-config workbench atlas recipes compare-runtime \
  /path/to/before-graph /path/to/after-graph --max-depth 4 --max-nodes 2000
```

A source checkout can establish source occurrences, not runtime presence. A
verified runtime graph can establish its captured recipe identities and
relationships, not general playability or progression reachability. Every
result retains its frontiers, unknowns, and evidence gaps. Use `--json` when
another tool needs those fields.

### Recipe routes for quest preparation

`routes` follows the producers of one exact item or fluid, then the producers
of their inputs, through the complete finite captured graph. Each recipe keeps
its jointly required input slots and the alternatives within each slot. The
report retains batch quantities, fluids, reusable tools, recipe conditions,
machine/map observations, and ordered chance outputs. Shared dependencies are
linked once; cycles include an exact edge witness and unknown starting supply.

The result supplies evidence for planning quests. It does not choose recipes,
balance a requested quantity, evaluate chance yields, order quests, or prove
craftability. Missing producers and unsupported matching remain explicit.
Optional `--max-depth`, `--max-resources`, and `--max-recipes` limits report any
unexplored frontier; omitting them requests complete finite exploration.

The independent Python API is
`derive_recipe_routes(view, resource_selection_id, options=RecipeRouteOptions(...))`,
using the same verified `open_recipe_health` view. It accepts a cancellation
callback. See the [captured route contract](contracts/atlas-captured-recipe-routes-v1.md).

### Recipe-impact exploration modes

`impact` defaults to bounded V1 analysis with depth 4 and 500 visited nodes.
`--exploration complete-finite` explicitly selects V2: it exhausts the admitted
finite dependency structure without depth, node or cycle-work limits. Do not
combine this mode with `--max-depth` or `--max-nodes`. Both modes require an exact
observed recipe in a verified graph.

The Python API uses the same verified view:

```python
from pathlib import Path
from workbench_atlas_recipe_health import open_recipe_health

with open_recipe_health(Path("/path/to/verified-graph")) as view:
    bounded = view.impact("RECIPE_SELECTION_ID", max_depth=4, max_nodes=500)
    complete = view.complete_impact("RECIPE_SELECTION_ID")
```

`complete_impact` also accepts `check_cancelled`, a callback that raises when
Core requests cancellation. Cancellation or failure produces no successful
completed report.

**Complete exploration does not establish complete evidence.** V2 reports have
no traversal frontiers; incomplete input matching, unknown recipe activity and
other evidence gaps remain explicit. Cycles are summarized as strongly connected
components: groups whose nodes have dependency paths to one another. Each
component retains all members and one closed witness using actual graph edges.
That witness does not enumerate every cycle. A cycle or an alternative producer
does not establish usable supply, bootstrap resources or gameplay reachability;
viability remains unknown.

To prepare a graph from a retained capture, select its pack-owned adapter and
the exact input manifest bound by the capture:

```bash
workbench atlas recipes import-capture /path/to/capture \
  --pack-profile supersymmetry --input-manifest /path/to/input-manifest.json \
  --output /path/to/new-graph --json
```

The Supersymmetry adapter projects finite GT recipes and machine/map bindings.
It retains quantities, chance metadata, reusable inputs and exact resource tags;
unsupported input matching remains explicit. Other recipe families and runtime
behavior are outside this projection. Import requires the optional profile;
querying the resulting graph uses Atlas's independent installation.
See the [capture-adapter contract](contracts/atlas-recipe-capture-adapter-v1.md).

If a graph's derived index is missing or stale, rebuild it without changing
the canonical graph streams:

```bash
pixi run --locked --no-config workbench atlas recipes index \
  /path/to/graph --max-source-bytes 8589934592 --max-index-bytes 4294967296
```

See the [index-operation contract](contracts/atlas-recipe-index-operation-v1.md),
[bounded recipe-impact contract](contracts/atlas-recipe-impact-report-v1.md),
[complete finite recipe-impact contract](contracts/atlas-complete-recipe-impact-report-v2.md), and
[runtime-comparison contract](contracts/atlas-runtime-recipe-comparison-v1.md)
for the exact result semantics.

## Worldgen and saved-state analysis

The Worldgen Observatory query packages consume sealed Crucible evidence. The
Anvil observers instead compare exact stopped-world state. Neither source can
infer generator causality that its records did not observe.

```bash
python3 modules/atlas/tools/query_worldgen_observatory.py --help
```

## Layout

- `contracts/`: versioned question and result semantics.
- `schemas/`: machine-readable record schemas.
- `examples/`: compact contract examples.
- `data/`: generic capability and policy data.
- `src/workbench_atlas/`: core query implementation.
- `src/workbench_atlas_categorical_graph/`: immutable graph reader and
  derived-index support.
- `src/workbench_atlas_recipe_health/`: recipe search and analysis.
- `src/workbench_atlas_worldgen/`: bounded Worldgen Observatory queries.
- `tests/`: focused contract and implementation tests.
- `tools/`: explicit command-line entry points for offline operations.

Run the module tests from the repository root:

```bash
python3 -m unittest discover -s modules/atlas/tests -p 'test_*.py'
```
