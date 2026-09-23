# Process Studio Bounded Observed-Effect Comparison V2

Status: incomplete experimental read-only comparison contract

The identity-bearing V1 draft was rejected during adversarial review and was
never admitted as a supported contract. V2 supersedes that draft rather than
mutating it in place; V1 projections, envelopes, comparisons, and ID prefixes
are not accepted as V2 inputs.

Serialized document families:

- `workbench-process-studio-effect-snapshot-projection-v2`;
- `workbench-process-studio-effect-change-envelope-v2`; and
- `workbench-process-studio-bounded-observed-effect-comparison-v2`.

The schemas are
[effect snapshot projection V2](../schemas/bounded-effect-snapshot-projection-v2.schema.json),
[effect change envelope V2](../schemas/effect-change-envelope-v2.schema.json),
and
[bounded observed-effect comparison V2](../schemas/bounded-observed-effect-comparison-v2.schema.json).

## Purpose

V2 performs one deterministic, bounded structural comparison between admitted
baseline and candidate snapshot records carrying the same declared lifecycle
boundary.
It is intended to make large recipe additions, removals, and replacements
reviewable while retaining uncertainty and provenance.

The output is intended to be a bounded observed-effect comparison. The V2
implementation, fixtures, schemas, and focused adversarial review support only
an experimental comparison kernel. The capability remains incomplete because
it has no admitted live family adapter, authenticated capture custody, or
temporal admission path. No current document should be treated as a safety
finding, causal proof, construction approval, playability finding, or release
decision.

## Authority boundary

The authority split is normative:

- Workbench architecture assigns authenticated stage-bound observation to
  Crucible. This fixture slice checks a self-declared Crucible V1 shape and
  content identity but does not authenticate its producer, provenance, or
  custody.
- Each family adapter owns its declared semantic family, semantic version,
  correspondence policy, and fingerprint policy. Its outputs remain opaque to
  Process Studio.
- Process Studio owns closure, alignment, deterministic grouping,
  classification, envelope assessment, and rendering of this comparison.
- Atlas remains the authority for observed and derived game knowledge.
- Blueprints owns convention-aware construction. Profiles own exact pack,
  platform, support, and action policy.

Process Studio has no causality, construction, or action-authorization
authority. Content addressing detects a changed document; it does not make a
forged upstream observation authoritative.

## Source snapshot admission

Each side is a `workbench-crucible-stage-snapshot-v1` document conforming to
the Crucible stage-bound registry/effect snapshot V1 contract. Before an
adapter sees a record, Process Studio:

1. admits only bounded, integer-only ordinary JSON with string object keys and
   the exact Crucible V1 format; the CLI also rejects duplicate object keys and
   a UTF-8 BOM before semantic validation;
2. recomputes the document content identity and requires the exact declared
   Crucible V1 authority and limitation fields, without treating those
   self-assertions as authenticated authority;
3. requires `binding` to contain exactly `pack_profile_id`,
   `platform_profile_id`, `stage`, and `capture`;
4. requires `capture` to contain exactly `capture_id`,
   `comparison_context_id`, `physical_side`, `effect_coverage`, and nullable
   `declared_change_envelope_id`;
5. requires every coverage row to bind exactly one family to a semantic owner,
   adapter, semantic version, correlation policy, fingerprint policy, and
   coverage state;
6. rejects duplicate or unordered coverage families, duplicate source record
   IDs, missing family coverage, contradictory records under `not-observed` or
   `unavailable` coverage, empty observation state, malformed declared V1
   provenance, and empty or non-string effect operations; and
7. recomputes the registry/effect counts and effect-operation summary.

Coverage state is one of `complete`, `partial`, `not-observed`, `unavailable`,
or `failed`. `complete` means complete only for the exact declared family,
context, profile, side, and lifecycle stage. Complete rows for declared
supported families with zero observations are valid. An empty coverage array
is also valid but declares no families; it is not global completeness and says
nothing about an omitted family.

The two projections are comparable only when their comparison context, pack
profile, platform profile, physical side, lifecycle stage, ordered coverage
binding, and numeric bounds are byte-semantically equal. A difference
in family owner, adapter, encoder, correlation policy, or fingerprint policy is
a coverage mismatch, not a recipe change.

## Closed projection

Each admitted source record projects to exactly one record and retains its
declared `source_record_id`. The projection binds:

- the source snapshot, source capture, nullable declared envelope ID, and
  comparison context;
- pack and platform profiles, physical side, and stage;
- ordered family coverage and each adapter actually required by observations
  or an observable declared coverage state; and
- the active byte, source-record, comparison, and expectation ceilings.

An adapter may emit a non-empty ordinary-JSON correlation object and a
lowercase SHA-256 semantic fingerprint. Correspondence must be a stable family
identity admitted by the adapter policy. A changing semantic signature,
fingerprint, display label, source list position, or runtime record ID must not
be invented as correspondence.

A missing or policy-inconsistent adapter binding rejects a family that has
observed records or whose declared state is `complete` or `partial`. A
zero-record family declared `not-observed`, `unavailable`, or `failed` need not
load an adapter, because there is no record to dispatch. Once a family is
bound, an individual record remains `unresolved` if that adapter declines it,
fails to project it, or cannot supply admitted correspondence. An unresolved
record carries its issue and source identity but cannot carry a correlation or
fingerprint. Process Studio does not reinterpret or repair an adapter result.

The GT finite-recipe and Forge crafting implementations are opt-in synthetic
fixture adapters. They are not defaults, production adapters, implicit
Supersymmetry semantics, or universal Minecraft semantics. Their use must be
explicitly requested through the fixture-adapter interface, including
`--fixture-adapters` at the CLI. They require an explicit admitted correlation
object in the descriptor and do not infer correspondence from a registry name,
recipe-map/recipe-ID pair, label, or semantic fingerprint.

## Comparison model

Projected records are ordered and grouped by the tuple:

```text
(family, record_kind, descriptor_kind, operation, canonical_correlation)
```

The comparator assigns exactly one classification:

| Classification | Bounded meaning |
| --- | --- |
| `added` | Exactly one candidate record and no baseline record exist under complete coverage and stable correspondence. |
| `removed` | Exactly one baseline record and no candidate record exist under complete coverage and stable correspondence. |
| `modified` | Exactly one record exists on each side under the same stable correspondence, and the adapter-owned fingerprints differ. |
| `unchanged` | Exactly one record exists on each side under the same stable correspondence, and the adapter-owned fingerprints are equal. |
| `ambiguous` | More than one record on either side claims the same stable correspondence under complete coverage. |
| `unresolved` | Coverage is not complete, adapter projection is unresolved, or admitted correspondence is unavailable. |

`modified` is structural and non-causal. It does not establish which source
declaration, script, mod, reload phase, or external condition caused the
difference.

Every comparison records baseline and candidate snapshot, capture, and source
record IDs together with the available semantic fingerprints. Comparison rows,
arrays, summaries, and content IDs are deterministic. The implementation
evaluates the reverse diff invariant before emitting a result:

```text
added <-> removed
modified <-> modified
unchanged <-> unchanged
ambiguous <-> ambiguous
unresolved <-> unresolved
```

Failure of that invariant rejects the result.

## Declared change envelope binding

A change envelope is owned by the producer of the expected structural change,
not by Process Studio. Its binding contains the exact baseline snapshot and
capture IDs, comparison context, pack and platform profiles, physical side,
and stage; it does not contain a candidate snapshot ID. `state` is `open`
while the declaration is being edited and `closed` when its content may be
used as an internal comparison binding. Its authority claim is limited to
declared expected structural effects and explicitly has no causality or
action-authorization authority. This slice does not authenticate the owner or
prove when the envelope was authored.

Each expectation supplies the exact comparison selector, including non-empty
adapter-owned correspondence, plus nullable exact
`baseline_semantic_fingerprint` and `candidate_semantic_fingerprint` values.
It does not carry a declared classification. Both fingerprints cannot be null.
The expected structural class is derived: baseline null is `added`, candidate
null is `removed`, equal non-null values are `unchanged`, and unequal non-null
values are `modified`. Ambiguity and unresolved evidence cannot be declared as
intended effects. Expectations are canonical, ordered, unique, and bounded.
An open authoring document may carry `allow_unlisted_changes`; closing the V2
envelope requires it to be false. Consequently, every non-`unchanged`
comparison row absent from a closed envelope is reported as an unexpected
observed effect.

The candidate-shaped capture must carry the exact closed envelope ID in its
nullable `declared_change_envelope_id` extension field. A supplied envelope and
candidate declaration must agree exactly. This is an internal consistency
binding, not proof of temporal custody. The emitted comparison embeds the full
admitted envelope, or null for comparison-only use, and also retains its
binding ID. An envelope cannot be assessed when the aligned coverage array is
empty; that request is incomparable.

Assessment retains these closed categories:

- `expected_and_observed_comparison_ids`;
- `unexpected_observed_comparison_ids`;
- `expected_not_observed`;
- `unsupported_or_unobservable`, including incomplete family coverage even
  when that coverage gap produces no comparison row; and
- `expectation_mismatches`, which reopen both the derived expected class and
  the expected and observed before/after fingerprints.

Without an envelope, the assessment and summary state are always
`comparison-only`; unsupported or unobservable evidence remains listed even in
that state. With an exactly bound envelope:

- `unresolved` means an expected or relevant observed effect is ambiguous or
  unobservable within the admitted evidence;
- `mismatch` means expected and observed structure differ without an
  unresolved assessment taking precedence; and
- `matches-declared-envelope` means the comparison rows mechanically match the
  embedded declaration within this exact adapter, coverage, and resource
  bound. It is not an authoritative conformance result.

No state is a pass result or authorization. An envelope with a wrong state,
baseline, capture, context, profile, side, or stage is incomparable and
rejected.

## Determinism and identities

Projection, envelope, comparison-row, and whole-comparison IDs use Crucible's
published `workbench-canonical-json-v2` encoder and domain-separated
`content_id` API with the identity field omitted. Every V2 document, and each
identity-bearing comparison row, carries the exact canonicalizer declaration.
The canonical domain orders object keys by UTF-8 bytes, uses strict Unicode
scalar text and signed 64-bit integers, and rejects non-JSON semantic values.
V2 rejects every floating-point number, including finite values, lone
surrogates, and non-string object keys. The CLI additionally rejects a UTF-8
BOM, duplicate object keys, and non-finite numeric tokens during parse.
Families that require decimal semantics must use an adapter-owner-defined
integer or string representation under the bound semantic/fingerprint policy;
Process Studio does not choose or normalize that representation.

Semantic validators recompute identities, row classification and reason from
coverage/cardinality/fingerprints, the complete assessment from the embedded
envelope and rows, summary counts, canonical order, unique selectors,
adapter/coverage agreement, and pair alignment. JSON Schema describes the
closed serialized shape; cross-document and content-address invariants remain
semantic-validator responsibilities.

Public shape-only validators establish only that a projection, envelope, or
comparison is internally closed and self-consistent. They do not prove that a
projection came from its named snapshot or that a comparison came from its
named sources. Source-replay validation must reproject the raw
baseline/candidate snapshots with the exact adapters and rederive the envelope
assessment and complete result byte-for-byte.

## Resource bounds

The fixed V2 bounds are:

| Resource | Ceiling |
| --- | ---: |
| bytes per input snapshot | 268,435,456 |
| registry plus effect records per snapshot | 500,000 |
| comparison groups | 500,000 |
| change-envelope expectations | 100,000 |
| JSON nesting depth | 64 |
| members in one JSON object or array | 500,000 |
| UTF-8 bytes in one string | 1,048,576 |
| integer range | -9,223,372,036,854,775,808 through 9,223,372,036,854,775,807 |

These hard maxima are fixed and cannot be loosened. Any supported stricter
operational ceilings are recorded in each projection and comparison and must
align exactly. A projection carrying a different policy is incomparable, and
exceeding a bound rejects the operation rather than silently truncating
evidence.

## Read-only boundary and known limits

V2 reads explicit documents and emits a projection or comparison. It does not
launch Minecraft, capture a runtime, edit scripts or recipes, execute a recipe,
test reachability, calculate balance, infer a canonical global recipe map, or
approve a release. An explicitly requested output file is a report artifact,
not a mutation of the target under study.

Registry presence and registration effects do not prove recipe execution,
selection, discoverability, player reachability, balance, or pack support.
Lifecycle observations outside the exact stage are out of scope. Causal
source-to-runtime linkage requires separately admitted evidence and the proper
authority; similarity of two semantic records is not that evidence.
Trusted profile admission, authenticated Crucible provenance, and temporal
custody of snapshot/envelope documents remain future work.
