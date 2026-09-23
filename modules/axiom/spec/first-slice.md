# First implementation: definition program to MIXER start

This implementation is a **bounded source model**, not an installed-pack parity
certificate. It constructs the actual upstream Coolants declarations and admits
ordinary MIXER starts under explicitly supplied context. It neither launches
Minecraft nor asks a game/runtime observer to decide validity.

## What is implemented

- Groovy 4.0.30 parsing through conversion, followed by an explicit AST
  interpreter. User bytecode, annotation transforms and arbitrary methods never
  execute. Unknown syntax/API paths fail closed.
- The locked MIXER/BLENDER aliases, `VA`/tier constants, concrete `item`, `ore`
  and `fluid` mappers, int amount assignment, ordinary inputs/outputs,
  nonconsumable item inputs, circuits, duration, EU/t and registration.
- Groovy builder validation, effective MIXER shape 6 item inputs / 1 item output
  / 3 fluid inputs / 2 fluid outputs, and the source-observed Susy field projection
  into BLENDER before original MIXER validation. BLENDER processing is unsupported.
- Ordered ingredient-tree construction and lookup, explicit ordered ore lookup
  facts, greedy allocation, quantity splitting and cached-recipe precedence.
- Ordinary `gregtech:mixer.lv` through `.iv`, physical/ghost input topology,
  individual tank versus aggregate capability fill, output-side admission,
  standard overclock, start power, output fit/void policy, consumption and
  progress-one/reserved-output state. This invokes an idle recipe-search entry;
  it is not the enclosing tick scheduler or processing to completion.

Registry items are explicit ordinary metadata/constant-stack-limit values with
no capabilities or arbitrary item hooks. NBT admits compound, string, byte,
short, int and long; all other kinds are unsupported. Ore members must be
concrete metadata. This is intentionally narrower than arbitrary Forge items.

The imports resolve to reviewed source projections. Axiom does **not** replay
all of `Recipemaps.groovy`, `ModifyRecipeMaps.groovy` or the complete loader.
Helper loops, metaclass mutations, material/Java generation, removals, custom
predicates, cleanroom/properties, chance outputs, other machines and processing
ticks remain unsupported. `coverage` enumerates those limits.

## Important preserved behavior

`stack * n` sets the GroovyScript amount; it does not multiply the old amount.
Unknown ore expansion is missing context, not an empty ore. An explicitly empty
ore has amount zero under GroovyScript's `getAmount` behavior.

Susy's callback happens before validating the MIXER original. Invalid MIXER
field counts can therefore coexist with a registered BLENDER derivative.
An out-of-range `circuitMeta` setter logs an invalid status and omits the circuit;
the Groovy validation branch does not inspect that native setter status. Axiom
retains the warning separately from the resulting construction decision.

Registration and tree reachability are separate: an empty-input Groovy recipe
can be registered without creating a reachable leaf. Tree lookup is not a
priority-sorted list or a backtracking allocation solver. A cached recipe is
checked first and a newly found recipe is cached before later start gates fail.

Full-NBT tree hashing and configuration-only circuit matching differ. A physical
circuit with missing NBT can fail fresh lookup yet match a cached recipe while
initializing its configuration tag. Ghost -1 is absent; zero is a real selector.
Repeated nonconsumable circuit requirements count toward field limits but can
use the same unchanged virtual item. A consumable ore requirement matching that
virtual slot can clear its ghost control through native extraction behavior.

LV tanks hold 8,000 mB, MV 12,000 mB and admitted higher tiers 16,000 mB each.
Aggregate fluid insertion does not spill identical fluid into another empty
distinct tank. Individual tank operations can establish split input contents.
Existing same-fluid output tanks can also provide a split-capacity witness.
Thus an LV empty-output failure for 10,000 mB is not universal impossibility.

Start reserves outputs and sets progress to one. It does not draw energy or
deliver products at that point. A void-policy bypass is not output recovery.

## Standalone and Workbench

Build from the Workbench root:

```sh
python3 tools/build_axiom.py --provision
```

Or supply explicit `--gradle` and `--java-home`. The resulting distribution is
`modules/axiom/jvm/build/install/workbench-axiom-engine` and the versioned ZIP is
copied to the requested new candidate directory. The distribution includes the
engine sources JAR and license/notices. Nothing is published.

With the profile-selected Temurin 25.0.4+7 configured for its standard launcher:

```sh
/path/to/axiom/bin/axiom coverage
/path/to/axiom/bin/axiom check < request.json
/path/to/axiom/bin/axiom query < request.json
```

After installing the separate Core/API/Axiom wheel closure:

```sh
workbench axiom coverage --engine-home /path/to/axiom --java /path/to/jdk/bin/java
workbench axiom check --engine-home /path/to/axiom --java /path/to/jdk/bin/java --request request.json
workbench axiom query --engine-home /path/to/axiom --java /path/to/jdk/bin/java --request request.json
```

The integration checks the installed library inventory and SHA-256 values
before invocation. These checks are preflight identity checks, not a claim of
protection against another process replacing an installation during startup.
Use a trusted, unchanged installation. The Java program identity additionally
includes the engine/parser artifact identity observed by the worker.

Core owns child supervision/cancellation and a temporary private stdin file.
The module owns no process-launch implementation or recipe algorithms.
Standalone evaluation uses a bubblewrap worker with no network or ambient
credentials and read-only Java/classpath mounts. Resource and transport targets
are suspended during MVP development. Existing source structure contracts retain
128 KiB per source file and bounded AST/tree traversal. This is not a claim of a complete OS-level memory quota.
The Java library never executes arbitrary source even without that supervisor.

## Request and result contract

Requests are strict `axiom.request.v1` JSON. Obtain the exact `targetId` from
`coverage.result.targetId`; a stale target is rejected. The top-level fields are:

| Field | Meaning |
| --- | --- |
| `schema`, `targetId` | Protocol and installed immutable source/rule selection |
| `files` | Ordered `{path, text}` entries; all selected source bytes are inline, with no filesystem dependency resolution |
| `registry` | Explicit ordinary item, concrete ore membership, fluid, circuit and ordered ore-lookup context |
| `queryKind` | Query only: `select-and-start`, never implicit named-recipe certification |
| `machine` | Query only: concrete handler contents, energy, OC setting, controls and source-bound cache |
| `loads` | Query only: ordered admitted input/control operations before recipe search |

Registry fields are `scope: "explicit-context"`, `items`, `ores`, `fluids`,
`circuit`, and `oreLookupOrder`. Items declare `id`, `meta`, `maxStack` and
`capabilities: "none"`; ores declare `name` and concrete `{id, meta}` members.
`oreLookupOrder` maps each `id:meta` to every applicable ore name in the observed
lookup order. Array order is semantic, not an interchangeable display order.
An omitted expansion/order is not guessed. Test/demo registries are synthetic;
they are not a recovered Supersymmetry item registry.

Machine fields are `machine`, `entry: "idle-search"`, `inputItems` (6 physical
slots), `inputFluids` (3 tanks), `outputItems` (1), `outputFluids` (2),
`ghostCircuit` (-1..32), `energy`, `overclockTier` (0..machine tier),
`voidItems`, `voidFluids`, `allowInputFromOutputSideItems`,
`allowInputFromOutputSideFluids`, `outputsFull` and `cache` (null or
`{programId, recipeId}`). Empty slots/tanks are null. Item stacks use
`{id, meta, count, nbt?}`; fluids use `{id, amount, nbt?}`. Tags use
`{type, value}`, with compound values mapping names to typed tags.

Loading routes are `ghost-control` with `configuration`, `gui-fluid-tank` with
`slot`/`stack`, `capability-fluid` with `side`/`stack`, and item equivalents
`gui-item-slot`/`capability-item-slot`. Capability `side` is `ordinary` or the
respective channel's `output` side. The upstream item output-side admission
reads the fluid control, which this model preserves. Excess/uninserted input
is reported. Unknown fields/routes or impossible physical shapes are errors.

Results use `axiom.result.v1`. `status` distinguishes accepted, native rejected,
source-error, request-error, requires-context, unsupported, incomplete and
execution-error; `completion` separately states whether evaluation completed.
Exit codes are 0 / 1 / 2 / 3 / 4 for accepted / native-or-source rejection /
request error / unsupported-or-missing-context / incomplete-or-execution failure.
No unsupported operation becomes a native rejection or success.

Definitions retain source path/hash/line/column, generation parent, registration
and reachability. Queries report selection route, ordered stages/effects,
handler allocations, pre-search state and resulting state. No later tick or
output completion is implied. Input loads appear as preceding effects; the
reported `before` state is after those loads and before recipe search.

Every result discloses `wholePackParity: false` and
`installedCompositionQualified: false`. `programId` binds source order/bytes,
the explicit registry, the target lock, admitted native runtime identity and
observed engine/parser artifact identity. A changed helper included in the program, registry, source lock or
engine invalidates cache use. Unmodeled helpers do not become hidden inputs.

## Verification and next boundary

[Conformance](../tests/README.md) separates unit tests, source-method comparison
and outside-checkout installed smoke. The source comparison covers three
methods only, not all downstream predicates or machine behavior.

The next vertical is **source dependency and registry closure**: replace
caller-supplied test context with a reproducible profile-owned target package,
close the real loader/import/helper/material and Java registration paths,
account for removals/collisions across the actual recipe universe, and verify
artifact/source plus active transformations. That gate is necessary before
calling a developer's recipe valid for the real Supersymmetry pack. More
machine families should not precede that authority work.
