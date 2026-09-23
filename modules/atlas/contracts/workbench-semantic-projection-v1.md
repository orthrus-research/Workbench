# Workbench Atlas Semantic Projection V1

The projection is a versioned, content-addressed Atlas view with one canonical
semantic identity across three non-interchangeable layers:

- `SOURCE` contains Pack Program Studio static declaration candidates.
- `RUNTIME` contains an immutable Crucible stage snapshot. Atlas links these
  records but never promotes them into Atlas-owned observations.
- `PLAYABLE` contains Atlas-derived conclusions bounded by the selected pack
  adapter, progression order, source feed, and runtime snapshot.

Every record retains its owning authority, evidence state, semantic descriptor,
and provenance. Diagnostics point to exact records in the source and runtime
layers. The V1 derivation surface covers required-node reachability, recipe
fluid-capacity feasibility, and duplicate observed registration effects.

Canonical semantic identity hashes only the normalized descriptor
(`domain`, `kind`, and pack-adapted key). It therefore remains stable across
file moves, runtime record IDs, and projection builds while distinct evidence
records remain independently content-addressed.

The first CLI surfaces are `workbench check`, `workbench why`, and
`workbench impact`. They require an explicit pack profile; Supersymmetry is an
adapter, never an implicit universal.

Schema: `../schemas/workbench-atlas-semantic-projection-v1.schema.json`.
