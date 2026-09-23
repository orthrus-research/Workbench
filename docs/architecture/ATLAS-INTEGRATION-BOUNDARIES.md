# Atlas integration boundaries

Status: implemented source/package boundary; installed and release qualification
remain specific to the tested workflows and component combination. Atlas no
longer requires Pack Program Studio or Material Semantics to install or load.

## Independent Atlas workflows

Atlas owns read-only knowledge queries, evidence admission, comparison and
impact interpretation within declared evidence models. Its base installation,
module discovery and independent queries must work without Pack Program Studio or Material Semantics installed or
enabled. Integrations add capabilities through explicit APIs or adapters.

An API boundary must also be an installation boundary: importing a small helper
from a product package still couples Atlas to that package's dependencies and
initialization. Moving the import into a function or an `optional-dependencies`
section alone does not establish independence.

Every Atlas integration must declare the exchanged contract, its version, its
owner, required providers and failure behavior. In-process interfaces and
versioned retained records are sufficient; an API does not require a network
service. Dependencies remain explicit and acyclic.

## Ownership at the boundary

| Integration | Producer or service responsibility | Atlas responsibility |
| --- | --- | --- |
| Pack Program Studio | Parse and normalize source, validate its original declaration records, preserve source provenance | Query admitted declarations and interpret their relationships |
| Material Semantics and profiles | Supply explicit domain policies with their identities and interpretation versions | Validate admitted policy inputs and classify observed graph data within those policies |
| Crucible | Capture runtime evidence and validate its original bundles and observations | Consume documented readers or adapters; preserve lifecycle, side, coverage and evidence bindings |
| Axiom | Execute native initialization and capture original outcomes | Admit the actual initialization scope through a versioned adapter and provide supported analyses |
| Blueprints | Validate and apply construction plans | Assess an owner-validated projection without becoming a construction authority |
| Core | Discovery, process/service lifecycle, storage, cancellation, recovery and cleanup | Declare and use those services through the Workbench API |
| Shell, Runtime Explorer and IDE clients | Compose and present declared capabilities | Return owner-bound results without requiring clients to duplicate Atlas algorithms |

The same rule applies to future integrations. The [Axiom–Atlas vision](../product/AXIOM-ATLAS-VISION.md)
remains a separate integration direction, not the definition of Atlas's entire MVP.

## Contracts and adapters

### Retained initialization observations

`atlas observations import-snapshot` selects the optional profile-owned
`workbench.observation_graphs` API-1 adapter. The Supersymmetry adapter requires
enabled Axiom 0.1.1 and Core 0.1.4 readers, then injects Axiom's historical
admission policy into Core's retained snapshot reader. Core verifies custody,
archive/input identities, exact saved source membership and read leases. Axiom
validates its original request, program and intent contracts. The shared port
lives in `workbench_api.retained_snapshots`; neither side imports Atlas.
Core registers the `core` entry in `workbench.retained_snapshot_providers`.
The API resolves that explicitly selected provider, checks its distribution,
version and host availability, and admits its API-1 reader. The profile consumes
this port without importing Core implementation modules. Custody and leases
remain owned by Core; provider discovery does not open or initialize evidence.

Atlas projects the admitted report into a separately versioned V3 graph with its
actual initialization side, source/context bindings, original outcome and
coverage. It owns node-family interpretation and exact relationship queries.
Querying or rebuilding that graph requires neither Axiom nor the profile. Reopening
original evidence explicitly selects the optional adapter and the matching snapshot.
No import or evidence read launches a native worker or rewrites a retained result.

The adapter preserves complete membership fingerprints, selected full witnesses,
GT lookup/category distinctions, crafting alias references and both stored
orderings, furnace storage, and the other captured observation families. Missing
details remain missing. Native object references and stored input descriptors do
not become executed matchers, source causation, recipe dependency edges or proof
of usable alternatives. See the [node-family contract](../../modules/atlas/contracts/atlas-initialization-node-families-v1.md).

### Shared domain contracts

Atlas consumes narrow public input contracts for declarations, source
locations, material policy inputs and evidence readers. Producer-specific adapters
validate the original producer records and translate them into those contracts.
Atlas independently checks the inputs required for its own analysis. An adapter
must not replace original evidence validation with an unchecked “validated” flag.

Adapters retain original producer identity, record format, exact input and policy
digests, source bindings, profile, side, lifecycle scope, coverage and unresolved
conditions where applicable. A translation cannot upgrade static declarations
into runtime observations or infer unsupported correspondence between captures.

Parsing, compiler integration and policy generation remain with their domain
owners. Generic host interfaces belong in `workbench-api`; domain algorithms and
product implementations do not move there to conceal a dependency. Contract
validators must be independently importable from the producer implementation.
Atlas's own graph-query errors and internal serialization helpers do not require
Material Semantics to load.

The independently importable contracts in `workbench-api` are:

- `source_locations`: exact byte bindings, portable paths and UTF-16 coordinates;
  Pack Program Studio retains parsing and compatibility exports.
- `source_declarations`: the existing V1 declaration and navigation record
  validators and identity encodings. V1 retains its original producer authority;
  reading a retained record does not require that producer to be installed.
- `material_classification`: immutable API-1 policy data and structural admission.
  Material Semantics generates domain policies; Atlas applies admitted policies
  to observations. Existing policy JSON and digests retain their V1 meaning.

Material policy admission also accepts the explicit legacy V1 snapshot contract:
the exact policy object, its recomputed digest, and supported structural rules.
This supports already-produced policies from older providers. Live policy
generation with the full Supersymmetry integration uses profile and Material
Semantics 0.1.1 or later; adapting a snapshot does not repair older producer code
that requires its own concrete Python class.

These contracts require API 0.1.1 or later in the admitted 0.1 series. Atlas,
Pack Program Studio, Material Semantics and Shell declare that minimum explicitly.
PPS's source-normalizer identity includes the extracted declaration and location
contract bytes. Changed producer/contract code therefore requires a fresh search;
historical feeds remain readable with their original bindings.

Use explicit adapter injection at existing composition points and existing
admitted profile extension discovery where it applies. An optional adapter may
depend on both endpoints; Atlas's base package must not depend back on it. A
profile-specific contribution must remain owned by the selected profile. No
implicit default provider, pack, or filesystem import path is selected.

Static provenance normalization uses the selected profile's
`workbench.provenance_primitives` API-1 adapter to run the complete original
source-index and mutation validators. Its `static` CLI requires `--pack-profile`
separately from the evidence-audience `--profile`. The library also accepts an
explicit primitive-validation adapter. Missing or incompatible adapters stop
normalization with an actionable error. Reading or checking an already-normalized
V1 artifact remains available without the profile or its source parsers installed.

Retain legacy readers for existing formats. Owner names embedded in current
formats cannot silently become generic provider names under the same format.
Changes to identity-bearing meanings require an explicit version transition.

## Availability and installation

An absent, disabled, incompatible or broken provider makes only the workflows
requiring it unavailable. The result identifies the missing integration; unrelated
Atlas queries and Core lifecycle operations remain usable. Missing integration
must not become an empty successful answer or a weaker substitute analysis.

Package requirements, module requirements, profile admission and command routing
must agree. Atlas now owns `workbench atlas recipes`: context, search, inspect,
impact, runtime comparison and index rebuilding use its declared dependency
closure, without Shell or a source-normalizer profile. Results still require the
corresponding source or runtime evidence.

`assess-plan` additionally requires an owner-validated construction plan. Shell
rebuilds the retained plan through its owning profile against current source bytes
and refuses a stale plan before opening the selected graph. This check does not
make a historical graph current or prove recipe execution. Shell provides the
longer `atlas recipes assess-plan` route and supplies Atlas's
API-1 plan-assessment adapter explicitly. Atlas alone reports that integration as
unavailable. The fixture-backed `check`, `why` and `impact` commands resolve an
explicit admitted `workbench.semantic_projections` profile extension.

`atlas recipes import-capture` resolves an explicit `workbench.recipe_graphs`
API-1 profile extension. Crucible checks retained capture custody, the selected
profile interprets domain records, and Atlas constructs and reopens the resulting
categorical graph. The Supersymmetry profile requires Crucible 0.1.1 for this
reader. Atlas's base query installation does not require the projection adapter.
The [capture-adapter contract](../../modules/atlas/contracts/atlas-recipe-capture-adapter-v1.md)
keeps capture import separate from native execution and support qualification.

Atlas registers the parent `atlas` namespace. An older Shell's existing
`atlas recipes` route can therefore coexist through Core's longest-prefix
dispatch. Disabling Shell leaves Atlas's independent route available; a current
Shell claims only the more specific plan-assessment route.

Shell and the full Supersymmetry profile retain their own PPS/MS requirements
for their source-generation and composition workflows. They are optional to the
independent Atlas recipe workflow; installing those broader packages deliberately
selects their dependency closure. Crucible remains an Atlas dependency supplying
documented runtime evidence readers. Further packaging changes must follow the
actual workflow rather than claiming every profile feature is independent.

## Complete finite recipe-impact integration

The existing recipe-impact route retains bounded V1 behavior by default. An
explicit mode selects the additive V2 contract:

```bash
workbench atlas recipes impact /path/to/verified-graph RECIPE_SELECTION_ID \
  --exploration complete-finite --json
```

The corresponding API is `view.complete_impact(selection_id, check_cancelled=None)`
on a view returned by `workbench_atlas_recipe_health.open_recipe_health`.
Complete mode cannot be combined with depth or node bounds. Atlas owns finite
propagation and cycle analysis; adapters supply evidence and clients present the
result. Core continues to own cancellation and process lifecycle. Cancellation
or failure cannot return a successful completed report.

V2 separates exhausted exploration from incomplete evidence. Its empty traversal
frontiers mean all admitted finite dependencies were explored. They do not
resolve incomplete selector matching, unknown lookup activity, unobserved
acquisition paths or gameplay viability. Cycle components retain all mutually
reachable members and one closed witness of actual graph edges. A witness is
not every possible cycle, and a cycle or alternative producer does not establish
bootstrap supply or a usable replacement.

V2 clients must preserve exact graph, search and recipe-selection bindings and
show evidence completeness separately from exploration completion. Large member
lists may be expanded lazily while the full report remains available. Clients
must explicitly support V2; translating it into V1 by dropping components or
uncertainty is not compatible behavior. See the
[complete finite report contract](../../modules/atlas/contracts/atlas-complete-recipe-impact-report-v2.md)
for the exact versioned fields and the
[Atlas README](../../modules/atlas/README.md#recipe-impact-exploration-modes)
for both CLI and Python examples.

## Acceptance

- Install built Atlas artifacts outside the checkout without Pack Program Studio
  and Material Semantics, using normal declared dependency resolution. Discover
  Atlas and exercise the admitted independent queries with no source-path leaks.
- Exercise each optional adapter through its public contract, including missing,
  disabled, incompatible, duplicate and failing provider cases where applicable.
- Preserve graph/result semantics, original source and evidence bindings, policy
  hashes, bounds, uncertainty and refusal behavior. A changed producer code
  identity may correctly require a fresh source search; retain historical records.
- Prove that malformed records, digest mismatches, unsupported policy versions and
  incompatible profile/side/lifecycle scopes still refuse the affected analysis.
- Verify the selected installed CLI and IDE workflows against the actual package
  and profile combination. Capability inventory and checkout tests alone do not
  establish an MVP installation.

See [Core and module separation](CORE-AND-MODULES.md) for host ownership and
[shared source intelligence](SOURCE-NAVIGATION.md) for the existing source flow.
