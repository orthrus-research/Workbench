# Crucible worldgen graph V2

Status: implemented experimental proving boundary. Hosted World Studio
integration is outside this contract.

## Purpose and authority

This boundary turns two exact, fresh Cleanroom worldgen controls into one
immutable, multi-owner graph set without moving semantic authority into
Crucible.

- Crucible owns execution identity, capture validation, same-run joins,
  capture-health, occurrence, stability, deterministic materialization,
  storage, publication, and bounded pinned reads.
- Atlas owns the generative and realized branch recipes and their claim
  meanings. They enter Crucible through explicit owner ports.
- Strata owns the final-state observation cited by each same-run join.
- The selected platform and pack profiles own applicability, experimental
  support, and action policy.
- Workbench Shell and clients may compose the records but cannot strengthen a
  fact or create another approval path.

The graph family is `workbench.worldgen`, the graph-set family is
`workbench.worldgen.graph-set.v1`, and the proving purpose is
`crucible.m4.w01.proving`.

## Exact proof input

`load_worldgen_proof_bundle` reopens and validates:

- two prelaunch `workbench-worldgen-execution-envelope-v2` records with the
  same run plan and distinct execution, process, runtime, world-instance,
  save-lineage, and world-epoch identities;
- each control's complete population capture audit, causal trace, same-run
  causal/final-state join, and terminal seal;
- one canonical A/A stability receipt that names both exact joins;
- the profile-owned experimental action-policy bytes named by each input
  binding;
- the current feature source digest and unchanged historical V1 prototype
  pattern and Observatory schema digests; and
- exact profile authority and applicability bindings.

Control similarity by seed, coordinates, label, path, or timestamp is never a
join key. Missing, noncanonical, cross-envelope, cross-world, incomplete,
dropped, open, or identity-incompatible input fails before graph execution.
The V1 pattern and raw schema are read and hashed without rewriting their
identity-bearing bytes.

## Same-run capture and closure

The streaming capture adapter validates every raw V1 row against the retained
schema while bounding line size, record count, and total input. It requires
closed population roots, event/listener reachability, causal feature decisions,
logical writes, terminal write transitions, truthful drop/open accounting, and
an immutable terminal result.

The join replays the exact Java RNG sequence for the selected feature and
joins causal writes to Strata positions only inside the same execution and
world epoch. A missing optional write becomes
`known-absent-by-closed-decision` only when the causal decision and closed
selector establish that absence. Final air, a missing row, or block-name
similarity cannot establish causation or absence.

The stability receipt compares the two controls site by site. It reports only
local `stable`, `unstable`, or `inconclusive` classifications within the
declared selector and settle boundary; it makes no whole-world determinism
claim.

## Owner-separated graph members

The closed graph set contains these ten ordinary members:

| Owner | Category | Stored resolutions |
| --- | --- | --- |
| Crucible | `crucible.worldgen.capture-health.v1` | operational |
| Crucible | `crucible.worldgen.occurrence.v1` | exact, chunk, region |
| Crucible | `crucible.worldgen.stability.v1` | chunk, region |
| Atlas | `atlas.worldgen.generative.v1` | operational |
| Atlas | `atlas.worldgen.realized.v1` | position, chunk, region |

Every member has an exact authority owner, recipe, implementation binding,
dependency manifest, graph revision, category, and resolution. Owner/category
substitution fails closed. The generative graph describes possible behavior
and retains the stale-pattern conflict; it cannot assert occurrence. The
realized graph records both exact controls and retains each control's envelope
and world epoch; it cannot replace Crucible capture health, occurrence, or
stability.

Stored refinement recipes declare deterministic descent across the available
resolutions and preserve omission semantics and drill-down roots. Composition
adds no inferred join edge and does not copy or strengthen owner facts.

## Deterministic execution and incremental equivalence

Each source projection is canonical LF-framed NDJSON with explicit row IDs.
The existing closed materializer runs one registered capability per member,
validates the owner result, constructs deterministic partitions and shards,
and seals the dependency and graph revisions. The graph-set revision and
disposable stored-query index are content addressed.

Three independent clean builds must have identical canonical identities. The
retained A-only to A/B append proves that the five control-independent members
are reused and that Atlas realized plus Crucible stability advance. A separate
eligibility-bound variant rebuilds only Atlas generative and reuses the other
nine members. In both cases the cache-assisted result must be byte-identical to
an independent clean build for the same effective inputs.

This is the accepted incremental proof. It does not implement general
changed-input, impacted-only publication.

## Publication, reads, and recovery

`WorldgenGraphStore` writes immutable content-addressed objects and manifests,
then moves one canonical current marker last. Resolution reopens and validates
the complete graph set, member, descriptor, object, shard, and index closure.
Faults after objects, after the manifest, or before the marker leave the prior
complete current graph resolvable; an incomplete candidate is never exposed.

`WorldgenStoredQueryService` serves only the stored index for an exact pinned
graph-set revision. The retained queries cover capture health, exact site
occurrence, known absence, stale-pattern conflicts, and realized drill-down.
The benchmark counts raw-archive opens and requires zero, 20 warm repetitions
per query, less than five seconds per repetition, and less than 4 GiB process
RSS on the proving host.

## Experimental action policy

Graph visibility and action authorization remain separate. With the exact
draft profile policy, read/query/visualize, plan/review, rollback, and guarded
disposable apply are allowed. Protected-world mutation, stable-support
promotion, and authoritative semantic publication are blocked. If the policy
is absent, policy-dependent behavior is unavailable. A synthetic
tested-supported fixture proves the positive gate mechanics without assigning
tested support to Cleanroom `0.6.8-alpha` or Supersymmetry.

## Retained proof

Generated captures, graph stores, query results, and proof indexes remain under
ignored `.workbench/` storage. No game binary, downloaded runtime, world, raw
capture, or generated store is committed.

## Explicit limitations

This contract does not claim:

- the hosted/embedded World Studio query, diff, and visualization handoff;
- persistent service, transport parity, jobs, cancellation, or reader leases;
- general changed-input construction and atomic incremental publication;
- GTCEu ore/fluid worldgen inventory or realized deposit traces;
- stable profile support, Blueprint construction admission, protected-world
  mutation, or release approval; or
- product adoption, release qualification, or whole-project completion.
