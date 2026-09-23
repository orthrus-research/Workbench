# Worldgen Qualifier V1 contract

Status: experimental working vertical slice

## Outcome

V1 answers a bounded acceptance question: **do byte-identical world-generation
inputs remain equal across every required observed cell, stage, and final-state
domain in this exact pack-owned matrix?**

The result is one `workbench-worldgen-qualification-report-v1`. It is an
orchestration and gate record, not a new evidence authority.

## Inputs and custody

An executable matrix binds:

- one exact qualification profile and referenced Cockpit profile;
- one frozen subject Groovy plan;
- one exact subject artifact, either supplied or built by the first cell and
  copied once before later cells;
- declared suite, intent, seeds, regions, pair orders, heaps, and pair
  repetitions; and
- exact runtime mod JARs discovered from a completed iteration plus any
  explicitly supplied risk JARs.

Every cell is a Worldgen Cockpit identical-input control: two independent
disposable runtimes, two fresh worlds, the same plan and artifact bytes, and an
aligned seed, dimension, mode, and chunk window. The incremental qualification
session remains `incomplete` until acquisition, cross-comparison, risk scan,
gate evaluation, JSON retention, and HTML retention complete. Existing labels
are never replaced.

All generated evidence stays under ignored `.workbench` storage.

Before execution, the plan checks its selected axes, automatic evidence
acquisition, and declared runner capabilities against the requested intent. A
known-unreachable acceptance is `attention` and refuses to launch Minecraft
unless the caller explicitly supplies `--allow-inconclusive` to retain partial
evidence despite the listed gaps.

## Perturbation matrix

V1 can execute and observe:

- independent JVM and fresh-world repetition;
- baseline-first and candidate-first pair order;
- multiple exact seeds and chunk regions;
- multiple JVM heap sizes as an allocation/GC-pressure proxy; and
- repeated pairs per identical cell.

For matching seed, dimension, mode, and capture scope, V1 also compares one run
from each cell. This prevents a heap-specific or order-specific stable result
from passing merely because both runs inside each individual cell agree.

The profile separately declares capabilities that are partial or unsupported.
The initial runner cannot command chunk traversal order, replay a warm mutable
cache, or wait on a pack-defined scheduled-tick/quiescence checkpoint. It has
only partial stage checkpoint coverage unless paired admitted Observatory
evidence is supplied. Requiring any of these capabilities yields
`inconclusive`; their absence cannot be inferred away.

## Domain gates

Every A/A comparison is reduced into independently visible gates:

| Gate | Exact observed signal |
| --- | --- |
| Semantic | normalized generator and population records |
| Terrain | changed surface-height columns |
| Biomes | changed biome columns |
| Lithology | Strata-classified lithology block transitions |
| Caves | cave-space block transitions and paired geometry |
| Ore | Strata-classified ore block transitions |
| Fluids | physical-fluid transitions |
| Decoration | structures, vegetation, and otherwise uncategorized block transitions |
| Settled final | every compared final block position |

The selected intent decides which gates and authorities are required, but every
intent includes the settled-final gate. A byte-identical delta outside a
developer's narrow subsystem remains a reproducibility failure; it is not
discarded as irrelevant noise.

Ore and release intents require stronger stage/causal evidence. Final ore
blocks show exact state, geometry, and correlation. They do not identify the
deposit definition, selector, write owner, RNG lane, or first divergence.
Subsurface Studio and Atlas remain responsible for those answers.

## Generic static risk screening

V1 reads bounded Java class constant pools from exact SHA-256-bound JARs. The
generic rules find class-scoped candidates for:

- unordered collection iteration combined with seeded RNG selection;
- identity hashing or identity-map iteration;
- clock, UUID, thread-local, secure, or global entropy in generation-shaped
  classes;
- unsorted filesystem, reflection, or service enumeration;
- asynchronous execution in generation-shaped classes;
- weak-reference or GC-sensitive cache iteration;
- host locale, timezone, or encoding defaults; and
- unordered parallel floating-point reductions.

ZIP traversal, encrypted members, symlink members, duplicate names, nested
archives, class count, class size, total uncompressed size, malformed class
files, and finding truncation are explicit coverage boundaries. A malformed or
uninspected byte surface makes coverage `partial`.

A match is `class-constant-pool-cooccurrence`: an investigation lead, not proof
that a class loads, a route executes, or the risk causes a delta. A pack may
disposition only the exact tuple of JAR SHA-256, class name, and rule ID, with a
nonempty rationale. Artifact upgrades invalidate that disposition naturally.

## Decision precedence

The evaluator applies this precedence:

1. a critical observed A/A or same-scope cross-perturbation delta produces
   `rejected-unstable-critical`, even when other evidence is missing;
2. misalignment, changed-input evidence, unknown domains, missing authorities,
   insufficient seeds, unsupported required perturbations, partial required
   byte coverage, or unreviewed high-risk findings produce `inconclusive`;
3. complete empirical final-state requirements produce
   `accepted-empirical`; and
4. complete profile-required stage evidence and perturbations can produce
   `accepted-exact` inside the declared matrix.

The schema reserves `accepted-bounded` and `accepted-patched` for future
contracts that explicitly bind equivalence relations or a patched exact
artifact. V1 never emits either state by guessing a tolerance or recognizing a
filename.

`failed` is reserved for an acquisition or evaluation failure. A crash, corrupt
capture, invalid input, exceeded safety bound, or failed required stage is not
an inconclusive successful run.

## Authority and non-claims

- Crucible owns experiment custody and runtime health.
- Strata owns admitted final block state.
- Atlas owns admitted causal and first-divergence answers.
- The pack profile owns pack-specific matrix and gate requirements.
- Workbench composes these answers and never establishes a second truth or
  approval path.

Qualification does not claim whole-world or all-seed determinism, source-to-JAR
reproducibility, production compatibility, visual quality, same-process causal
provenance when upstream evidence lacks it, or stable Cleanroom release support
for an experimental platform profile.
