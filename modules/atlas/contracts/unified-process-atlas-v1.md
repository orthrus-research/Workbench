# Unified Process Atlas v1 contract

Status: active answer and artifact contract

The Unified Process Atlas joins generated runtime mechanics, runtime
presentation, pinned source, quest data, and curated interpretation without
collapsing those evidence layers into one authority. Machine-readable JSON is
the primary answer contract. Markdown and later interfaces are deterministic
projections of the same JSON result.

Contract artifacts:

- [Acceptance questions v1](../data/acceptance-questions-v1.json)
- [Prerequisite expansion v1](prerequisite-expansion-v1.md)
- [Infrastructure expansion v1](infrastructure-expansion-v1.md)
- [Target-parameterized query contract v1](atlas-target-query-contract-v1.md)
- [Acceptance-question schema v1](../schemas/lexicon/corpus-question-v1.schema.json)
- [Answer schema v1](../schemas/lexicon/corpus-answer-v1.schema.json)
- [Query-instance schema v1](../schemas/atlas-query-instance-v1.schema.json)
- [Query-result schema v1](../schemas/atlas-query-result-v1.schema.json)
- [Exact corpus-link schema v1](../schemas/lexicon/corpus-link-v1.schema.json)
- [Quest-graph record schema v1](../schemas/lexicon/quest-graph-record-v1.schema.json)
- [Current production data boundary](../data/production/README.md)

## Scope

Version 1 proves a chemistry and process-chain vertical slice around stable,
exact identities. It must answer developer and player questions about
provenance, recipes, machines, quantities, alternatives, profile differences,
process routes, reusable requirements, byproducts, recycling, and quest
context.

The larger purpose is a version-bound explanatory model of how Forge/FML,
GTCEu, SusyCore, admitted dependencies, and pack-authored Groovy,
configuration, resources, structures, and quests assemble the final game.
Player projections answer what can be done and what it requires. Developer
projections retain declaration, ownership, lifecycle, enforcement, mutation,
and extension seams so they can explain why the observed game behaves that
way.

The contract does not require a web interface, manually copied runtime recipes,
or fuzzy display-name matching. It does not treat a quest prerequisite as a
mechanical prerequisite unless separate runtime or pinned-source evidence proves
that relationship.

## Fidelity and explanatory purpose

The Atlas distinguishes five stages of a truthful explanation:

1. an exact versioned identity and operation context;
2. an observed runtime, presentation, quest, or artifact record;
3. an exact pinned-source declaration or enforcement semantic when the
   observation needs interpretation;
4. a named derivation policy for any computed statement; and
5. curated player or developer wording that cites the preceding evidence.

No later stage may invent an earlier one. Runtime state establishes what the
assembled snapshot contains; pinned source establishes what the selected
implementation means; neither substitutes for the other. A source possibility
is not an observed active state, and a runtime field name is not sufficient
proof of its semantics.

Every derived assertion retains its inputs and policy. For example, numeric
recipe EU/t may be reported directly from runtime mechanics, but a named
voltage tier also requires the exact pinned GTCEu tier mapping used by this
version. Forge may be the lifecycle, registry, capability, configuration, or
dimension substrate without becoming the owner of a GTCEu or SuSy rule.

`not-applicable`, `not-observed`, and `unresolved` are distinct. Only positive
evidence may establish `not-applicable`; absence of a captured relation never
becomes “not required.” Truncation, missing source semantics, unknown condition
kinds, and unobserved formed-world state remain visible to both audiences.

The infrastructure expansion applies this contract first to power, voltage,
machine structures and hatches, capabilities, dimensions, environment, and
effective configuration. The same truth-path discipline is the intended basis
for broader teaching about how Supersymmetry and Forge construct the game.

## Acceptance-question records

Each record has exactly these fields:

- `id`: stable audience-prefixed question ID;
- `question`: human-readable question whose semantics are under test;
- `audience`: `developer` or `player`;
- `query`: the query operation expected to answer it;
- `selector`: exact typed `kind` plus stable `key`;
- `required_fields`: unique response paths that must be present;
- `required_evidence_bases`: evidence bases that must occur in the answer;
- `status`: `active`, `deferred`, or `retired`.

A dot in a `required_fields` entry addresses a nested object field. Absence is
not equivalent to an empty result: a required field must be present even when
its value is an empty collection or an explicit unresolved object.

Active IDs are unique across the suite. Their `DEVELOPER-` or `PLAYER-` prefix
must agree with `audience`. A selector is authoritative only after exact
resolution returns one candidate. Multiple candidates produce an ambiguity
result; they are never reduced by display name, row order, or shortest ID.

## Response invariants

Every successful or fail-closed answer is one JSON object. It contains:

- `question_id` and `query` identifying the requested contract;
- `target` containing the requested selector and its exact resolution;
- `snapshot_id` naming the evidence snapshot;
- `profile_scope` listing every profile and physical side consulted or known to
  be missing;
- `evidence` containing stable evidence records used by returned assertions;
- `result_status` containing one of `answered`,
  `answered-with-profile-gap`, `ambiguous`, `unresolved-identity`,
  `unresolved-evidence`, `unsupported-query`, or `no-mechanical-route`;
- every path listed by the acceptance question's `required_fields`.

Every factual assertion references at least one evidence ID. Every referenced
evidence ID resolves inside the response or its immutable corpus bundle. An
assertion without evidence is returned only as explicitly `unresolved`; it is
not rendered as a definitive statement.

`unresolved-evidence` means the exact target identity resolved, but at least
one evidence basis required by the acceptance question did not. The response
retains the resolved target and names each missing basis in `known_gaps`; it
must not be rendered as an answered result.

Generated runtime facts remain generated facts. Curated interpretation may cite
them but does not copy them into manually maintained claims merely to change
search or rendering behavior. Runtime execution and HEI/JEI presentation remain
separate predicates and separate evidence records.

Collections are ordered deterministically by stable semantic key and then by
stable observed ID. Ordering never depends on source enumeration, SQLite row
order, or dictionary insertion order. Empty results use empty arrays or explicit
status objects rather than omitted required fields or `null` placeholders.

## Pagination contract

Queries returning pageable collections include a `page` object with:

- `limit`: requested and enforced positive page size;
- `offset`: non-negative starting offset;
- `returned`: number of rows in this page;
- `total`: total rows matching the resolved selector and scope;
- `truncated`: `true` when `offset + returned < total`.

The query applies deterministic ordering before `offset` and `limit`. Invalid
limits or offsets fail before querying. A caller cannot infer completeness from
`returned`; it must inspect `total` and `truncated`. Pagination truncation does
not imply that a process traversal hit one of its structural bounds.

## Truncation contract

Bounded traversals include a `truncation` object even when no limit is hit:

```json
{
  "truncated": false,
  "reasons": [],
  "limits": {
    "max_depth": 8,
    "max_routes": 50,
    "max_alternatives_per_slot": 25,
    "max_visited_nodes": 10000
  }
}
```

When `truncated` is `true`, `reasons` is non-empty and names every enforced
boundary that affected the result, such as `max-depth`, `max-routes`,
`max-alternatives-per-slot`, or `max-visited-nodes`. Returned routes remain
valid partial routes. Page completeness remains governed only by the separate
`page` object. Unresolved leaves, direct and indirect cycles, procedural-rule
boundaries, and missing executable-machine evidence are represented explicitly;
they are not silently discarded or converted into fabricated finite recipes.

Input alternatives within one slot remain alternatives. Distinct input slots
remain jointly required. Non-consumables remain requirements without becoming
consumed quantities. Chanced outputs retain their chance and are never rendered
as guaranteed yield.

## Evidence labels

- `runtime-mechanics`: accepted normalized runtime behavior, including executable
  recipes, quantities, machine relationships, and procedural rules.
- `runtime-presentation`: HEI/JEI category, wrapper, or visibility evidence that
  does not by itself prove execution.
- `pinned-source`: declaration, ownership, mutation, registration, or
  implementation evidence from an exact pinned source revision.
- `quest-data`: quest graph, task, reward, text, localization, or build-time
  rewrite evidence.
- `curated-interpretation`: human explanation tied to explicit source, runtime,
  quest, or generated evidence IDs.
- `unresolved`: a known assertion or link that has not been safely resolved or
  proven.

`required_evidence_bases` is a minimum set, not permission to suppress other
relevant bases. Profile gaps and unresolved identities stay visible even when
some required evidence is present.

## Audience projections

Developer projections retain declaration, ownership, lifecycle, exact recipe
semantics, executable-machine evidence, profile differences, and full
provenance. Player projections may hide implementation noise but retain exact
quantities, alternatives by slot, reusable requirements, chance, byproducts,
quest semantics, evidence IDs, snapshot, profile scope, and known gaps.

The executable-machine projection is derived only from explicit execution
mechanics; consultation or presentation relationships never enter its machine
list. Recipe details union exact producer and consumer occurrences and retain
all consumed, reusable, and output slots. Ingredient and reusable-requirement
views are lossless per-route projections and must match the same bounded route
target, items, and truncation state.

The implemented byproduct view follows the same rule: it projects every
retained route, including routes with no additional output slot, and defines a
byproduct only as a mechanical co-output rather than economic waste. Its
separate recycling traversal follows exact `consumes` and `may_consume`
occurrences forward from deduplicated co-output alternatives. Reusable
`requires` edges do not advance that traversal. A downstream use becomes a
verified closure only through an exact return to the final target, a retained
route subproblem, or an observed forward cycle; stage-local chances are never
multiplied into an inferred yield. The full contract is recorded in the
[byproducts and recycling expansion](byproducts-recycling-expansion-v1.md).

The prerequisite view is derived from that same bounded route DAG. Each route
becomes one operation retaining its exact production proof and whose
requirements are an outer `AND`: one execution-proven machine-choice group plus
one group for every consumed and reusable slot. Linked alternatives become
exact upstream dependency edges. Roots, subproblems, cycles, unresolved leaves,
boundaries, and truncation are preserved. Unknown player inventory and
infrastructure remain assumptions, not claims, and quest ordering remains
non-mechanical.

Row classification is evidence-derived, not inferred from display position.
Lookup-active native recipes are `runtime-executable`; inactive native rows are
`category-only`; explicit non-finite process owners are `procedural`; and
client wrapper rows linked by exact `reconciles_to` edges are
`presentation-only`. A class absent from one exact target slice has a zero
observed count; it is not evidence that the class cannot exist elsewhere.

Profile comparison requires all four v1 observations together: COMMON client,
COMMON dedicated, CLIENT_JEI/HEI client, and OFFLINE. Each scope reports exact
target availability independently from scope-owned mechanics and reconciled
presentation. A missing target makes mechanics unavailable; it never becomes
an authoritative empty set. HEI presentation may still be observed through an
exact `reconciles_to` edge when the HEI profile has no material node.

Pairwise target, mechanics, and presentation results are separately
`equivalent`, `different`, or `not-comparable`. Observation IDs and scopes are
removed only for semantic hashing; the original records remain in evidence.
Corresponding logical rows expose complete field-level deltas, while
left-only and right-only rows remain explicit. A digest mismatch without an
exact row or field delta is invalid.

Verification confidence is a bounded audit rather than a numeric or global
score. It reports exact-identity, snapshot-binding, requested-profile coverage,
required-evidence-basis, and quest-guidance-link checks. Its status is
`verified`, `verified-with-known-gaps`, or `unresolved`, and its evidence IDs
must equal the union of those checks. It never implies cross-version validity,
profile equivalence, a complete mechanical route, or mechanical force behind a
quest prerequisite unless a separate projection proves those claims.

An explicit client-presentation `reconciles_to` edge may carry its HEI source
record into a COMMON projection when that edge targets an embedded in-scope
runtime record. Both records retain `runtime-presentation` evidence. No other
out-of-scope record is admitted, and reconciliation never becomes execution
evidence.

Both projections derive from the same query result. A renderer cannot add a
mechanical claim, upgrade presentation evidence to execution evidence, or
reinterpret a quest ordering edge as a mechanical gate.

## Acceptance execution

An active question passes only when its selector resolves according to the exact
identity rules, every `required_fields` path is present, every
`required_evidence_bases` value occurs in referenced evidence, and response
invariants hold. Deferred questions remain versioned but do not count as active
coverage. Retired questions remain historical and are not executed.

The prerequisite and infrastructure expansions record the staged semantics for
route readiness, machine construction, and infrastructure readiness. Route
readiness, exact bounded machine construction, and the player/developer
infrastructure pair are active acceptance questions. Infrastructure answers
retain partial status wherever formed-world state, an unsupported predicate,
or a bounded traversal prevents exact closure.

The byproducts and recycling expansion implements the final active v1 question:
lossless co-output projection, exact root deduplication, fair bounded forward
consumption, route/final/cycle closure, semantic recomputation, and exact
runtime evidence closure. All sixteen active Atlas v1 questions are supported
by the answer bridge.

Acceptance results report their explicit result status; an ambiguity, profile
gap, unsupported query, or missing route is data, not a reason to manufacture a
definitive answer.
