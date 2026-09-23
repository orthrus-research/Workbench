# Supersymmetry recipe invalidation diagnostic V2

Status: preview product capability over provisional Cleanroom runtime support.

This contract defines a read-only, Supersymmetry-only developer workflow for
reducing two receipt-bound recipe-registration channels without creating a
second recipe truth or approval path. The additive
`workbench-pack-runtime-diagnostic-catalog-v1` selects the exact
Supersymmetry profile. It is not an implicit capability for another pack or
Minecraft version.

The public command is:

```text
workbench runtime-diagnose <workspace> --recipe-invalidations \
  --receipt <candidate-runtime-launch-v3.json>
```

An explicitly selected cold-start comparison adds:

```text
--baseline-receipt <baseline-runtime-launch-v3.json>
```

The baseline is never inferred, persisted, or promoted to a last-green or
release-approved designation. `--json` emits the identity-bound V2 result.

## Authority and maturity

The Supersymmetry Atlas profile owns the recognized GTCEu and Groovy log
grammar, normalized reason identities, aggregation, comparison semantics,
uncertainty, and recommendations. Shell owns only generic project selection,
receipt and evidence custody, profile loading, orchestration, validation, and
presentation.

The capability maturity is `preview`. Its selected Cleanroom runtime support
remains `provisional`; this contract does not make Cleanroom or Supersymmetry a
tested stable release profile. Reports are observations, not normative Atlas
publication, Blueprint authorization, a CI gate, or release approval.

The experimental Groovy V1 diagnostic and comparison formats remain
unchanged. V2 composes their exact results with a new GT recipe-registration
signal channel. It does not reinterpret either V1 identity.

## Receipt and evidence boundary

Each input is a process-bound `workbench-runtime-launch-receipt-v3`. Shell
reopens and verifies the V3 receipt, its bound parent receipt, the exact final
evidence identities, and the session-exit observation before profile code sees
text. The receipt project fields must match the selected Supersymmetry
workspace.

The two channels consume only their declared evidence labels:

- `groovy_postinit` consumes `minecraft-groovy-log`; and
- `gt_startup_registration` consumes `minecraft-latest-log`.

A channel is reduced only from exactly one verified, bounded, text-readable
capture. Missing or oversized evidence is never treated as an empty event set;
this V2 feature requires both declared captures and fails diagnosis when either
is unavailable. A present but grammatically incomplete channel is represented
as uncertainty in the combined result. Changed evidence bytes, malformed
receipt custody, or an unsupported receipt also fail diagnosis.

V3 binds evidence bytes and process observation, but it does not bind the
workspace source revision or the pack-profile document that existed at launch.
It also does not bind the launch-time GTCEu artifact or version. Exact matches
to this profile's retained grammar are observed syntax, not proof of that
runtime version or a tested support envelope.
The current revision and profile are diagnostic context only. A standalone V2
comparison therefore never attributes an observed difference to a source
change. A source/plan-bound developer-feature workflow may retain this report
as subordinate evidence under its own stronger custody, but may not rewrite
the report's claims.

## Groovy postInit channel

`groovy_postinit` is the frozen V1 observer described by
[`recipe-reload-diagnostic-v1.md`](recipe-reload-diagnostic-v1.md). It counts
only complete three-row GT conflict sequences and groups them by observed
script logger and recipe map. A complete cold-start comparison requires one
completed initial `postInit`, no repeated execution, no unbound conflict, no
incomplete frontier, and no truncated group set.

The observed script logger is an attribution label, not proof of a source file
or causal owner.

## GT recipe-registration channel

`gt_startup_registration` recognizes only two profile-declared GTCEu recipe
registration signal grammars in verified `minecraft-latest-log` bytes. It is
not a generic exception counter. A complete observation requires exactly one
GregTech INFO `Registering recipes...` row and exactly one later occurrence of
the V3 receipt checkpoint marker, whose receipt source and verified evidence
label are both `minecraft-latest-log`. Only supported signals strictly between
those rows are counted. Missing, repeated, reversed, or unbound boundary rows
make the channel incomplete rather than turning absent signals into a clean
observation.

The two normalized reason codes and their complete, ordered grammars are:

- `empty-recipe-outputs`: the exact GregTech ERROR signal header for empty
  recipe outputs; the exact same-context `Stacktrace:` logger row; the raw
  `Invalid number of Outputs` exception header; the ordered
  `RecipeMap.postValidateRecipe`, `RecipeMap.addRecipe`, and
  `RecipeBuilder.buildAndRegister` wrapper frames; the next observed owner
  frame; and, within the stack-span bound, the next parsed row as the exact
  same-context GregTech ERROR ore-prefix and material context; and
- `duplicate-furnace-recipe`: the exact GregTech WARN `Invalid Recipe Found`
  signal header; the immediately following raw `Tried to register duplicate
  Furnace Recipe` exception in the bounded admitted syntax; the ordered
  `ModHandler.logInvalidRecipe`, `ModHandler.addSmeltingRecipe`, and
  `ModHandler.addSmeltingRecipe` wrapper frames; and the next observed owner
  frame.

Within those two counted grammars, malformed candidates bearing an exact
target header or the duplicate-furnace exception prefix become incomplete
sequence frontiers. Other RecipeMap cardinality failures, RecipeBuilder
validation failures, ModHandler invalid-recipe forms, and nearby Forge or FML
recipe messages are outside this observer's grammar and are not counted.

The exact six-row GregTech Core fatal invalid-recipe banner is a summary, not
another GT recipe registration signal, and is never added to event counts. An
incomplete banner is a frontier. A complete banner is also an unmatched
frontier when the same capture contains no recognized
`empty-recipe-outputs` sequence, because the admitted grammar did not explain
that summary.

Events are grouped by:

```text
kind | normalized reason code | observed owner class | observed owner method
```

The observed owner is the first bounded stack frame after known GT recipe
reporting and builder internals. It can identify a pack, addon, or GT loader
frame, but it is not causal proof. Source filename, line number, raw exception
message, attempted recipe payload, ore prefix, and material are context only;
they do not participate in group identity. This prevents ordinary source-line
movement and unstable runtime object strings from manufacturing group churn.

Each group retains its count, first and last evidence lines, bounded examples,
and bounded normalized contexts. Counts are GT registration owner-method
counts of recognized signals, not recipe counts: one rejected construction
can emit more than one signal.

## Combined diagnostic

`workbench-supersymmetry-recipe-invalidation-diagnostic-v2` contains exact
top-level source, profile, authority, recommendation, and limitation bindings
plus two channel objects:

```text
channels.groovy_postinit
channels.gt_startup_registration
```

The channels retain their own state, scope, summary, groups, and frontiers.
V2 deliberately has no aggregate group list or aggregate signal count. The
same underlying condition may be forwarded into more than one log, and no
cross-channel deduplication identity has been proven.

The combined state is one of:

- `attention`: at least one supported signal was observed and both channels
  are complete;
- `attention-incomplete`: a supported signal was observed but at least one
  channel remains incomplete;
- `inconclusive`: no supported signal was observed and at least one channel is
  incomplete; or
- `no-supported-signals-observed`: both channels are complete and contain no
  supported signal.

The last state is deliberately not named clear, green, safe, or valid.

## Cold-start comparison

`workbench-supersymmetry-recipe-invalidation-comparison-v2` compares two
explicitly selected V2 diagnostics derived with the same loaded profile.
Custody failures are errors. Structurally valid observations that cannot
support a semantic comparison produce a sealed `incomparable` result with
compatibility findings and no invented delta.

A comparable pair requires:

- distinct V3 launch and receipt identities;
- matching project and selected profile context;
- matching checkpoint kind/source, Java runtime identity, and launcher
  family/host;
- `checkpoint-reached` launch outcomes followed by exact session exit;
- complete, untruncated GT recipe-registration channel reports with no
  incomplete frontiers on both sides; and
- a complete, untruncated initial Groovy `postInit` on both sides, with no
  reload or unbound event.

Payload trees may differ because the caller is comparing two observations.
Their difference is not source-change attribution.

Each channel preserves its own group key and classifies the complete union as
`newly-observed`, `increased`, `decreased`, `no-longer-observed`, or
`same-count`. Groovy resolution-count changes remain Groovy-only evidence.
The V2 comparison has no aggregate summary or group list; it contains the two
channel comparisons separately.

The combined comparison state is one of `more-observed`, `fewer-observed`,
`same-counts`, `same-counts-with-resolution-count-changes`, or `incomparable`.
`more-observed` takes precedence whenever either comparable channel contains a
newly observed or increased group. These are count observations relative to
the explicitly selected baseline, not recipe regressions. Likewise,
`no-longer-observed` does not mean resolved or fixed, and `same-count` does not
mean unchanged recipes.

## Bounds and safe presentation

Evidence text is subject to Shell's receipt-analysis byte bound. Observers also
bound event count, distinct group count, stack depth, examples, contexts, and
frontiers. A complete comparison never consumes truncated groups. Frontier
totals are tracked without retaining an unbounded list.

Identities and human-facing samples have length bounds and reject Unicode
control or format characters. Raw attempted recipe payloads and runtime object
identities are not group keys. Human output shows only bounded examples; the
structured report remains the complete result inside its admitted bounds.

## Claim boundary

V2 does not:

- reconstruct or compare an effective recipe registry;
- detect a silent RecipeMap compilation or lookup rejection for which no
  supported log signal exists;
- provide stable recipe identity or cross-channel event deduplication;
- prove the launch-time GTCEu artifact/version or a supported runtime envelope;
- prove that an observed stack frame or logger caused the event;
- attribute a standalone V3 difference to Java, Groovy, or another source
  edit;
- execute recipes or assess lookup activity, progression, reachability,
  BetterQuesting behavior, JEI/client visibility, or save safety; or
- declare a build green, a recipe fixed, a reload safe, or a release ready.

No source, recipe, runtime, launcher, world, or stored baseline state is
changed by diagnosis or comparison.
