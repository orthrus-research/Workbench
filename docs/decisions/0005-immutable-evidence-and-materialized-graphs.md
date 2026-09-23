# Decision 0005: immutable evidence and materialized graph revisions

Status: accepted
Date: 2026-08-08

## Decision

Crucible V2 represents evidence, admissions, and materialized graphs as
immutable, content-addressed records and revisions. The retained core validates
and constructs those values without owning a repository or publication path.

Every graph revision is the deterministic result of an authority-owned,
versioned materialization recipe over exact evidence-set and graph-revision
inputs. Categorical and lower-resolution graphs are persistent authoritative
materializations for their declared contract, scope, and owner. They are not
unqualified truth and do not transfer semantic authority to Crucible.

If an external integration exposes a mutable name such as `current`, that name
is only a convenience reference to an immutable revision. It is not evidence
and is not part of graph identity.

An applicable authority may admit validated runtime observations to an
evidence set and request dependency-aware rematerialization. Existing evidence
and graph records are never edited in place.

## Reasons

- Historical questions and comparisons require reproducible graph state.
- Different applications need different categories and resolutions without
  losing provenance or silently changing meaning.
- Incremental updates are practical only when dependencies and parent
  revisions are explicit.
- Content-addressed revisions permit safe shard reuse, caching, comparison,
  corruption detection, and crash recovery.
- Authority ownership must remain visible when the common Crucible engine
  performs deterministic construction or validation work.

## Consequences

- A recipe must declare its owner, version, inputs, scope, resolution contract,
  omissions, partition policy, and deterministic output rules.
- A claimed incremental assembly over identical inputs must match the clean
  assembly's canonical records, partitions, shard bytes, refinement maps, and
  revision identities.
- Runtime and world nondeterminism is represented in evidence and graph
  semantics; it cannot make materialization itself nondeterministic.
- Malformed and failed proposals never enter an accepted evidence-set
  revision. An incomplete capture may enter only a domain-contract-authorized
  diagnostic evidence set that retains its incomplete state and cannot provide
  Atlas evidence closure or masquerade as complete evidence.
- Existing identity-bearing V1 formats remain verbatim and enter V2 only
  through explicit adapters.
- External indexes and caches remain disposable accelerators. Portable graph
  manifests and canonical graph records carry materialized authority.
- Persistence, atomic publication, moving references, and crash recovery are
  responsibilities of an explicit integration layer, not the retained core.

The target architecture is defined in the
[Crucible graph model](../architecture/CRUCIBLE-GRAPH-MODEL.md).
