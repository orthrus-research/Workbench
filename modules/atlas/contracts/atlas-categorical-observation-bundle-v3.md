# Atlas categorical observation bundle V3

V3 adds an explicit source authority for versioned retained observations,
including native initialization snapshots. It does not change the meaning or
identity of retained Crucible V2 graphs.

The manifest format is `workbench-atlas-categorical-graph-bundle-v3`, schema
version 3, with graph identities beginning `workbench-atlas-graph-set-v3:sha256:`.
Its authority is exactly:

```json
{"owner":"Atlas","claim":"derived categorical projection over versioned retained observations"}
```

V3 reuses the existing V2 node, edge, partition and derived SQLite contracts.
It requires the declared-dependency-closure validation profile; the historical
V2 earlier-partition fallback is unavailable. Graph identity includes the new
format and authority. Index verification and explicit rebuilding retain the
original graph identity and source authority.

`CategoricalGraphBundleBuilder(..., evidence_authority="retained-observations-v1")`
selects V3 explicitly. The default remains the unchanged V2 Crucible authority.
Neither format accepts the other's authority under its existing identity.

The evidence binding identifies the original producer, retained snapshot, source
result, input/context/engine/runtime/JVM bindings, native outcome and capture
coverage. Scope names the actual selected side and initialization boundary.
Producer coverage, readable storage, interpreted relationships and executed
behavior remain separate facts. A completed projection can retain a native
failure or incomplete observations without promoting either to success.

Snapshot-local occurrence and native-reference identities include their snapshot
and selected side. They do not establish cross-run recipe correspondence.
Relationships retain exact source pointers and their interpretation meaning.
An observed stored ingredient, callback, registry member or fingerprint does not
establish executed matching, callback execution, source causation or usable supply.

V3 observation nodes do not become legacy finite GT recipe nodes. Recipe impact
and comparison retain their existing admission requirements. Observation queries
may inspect either graph format without extending those analysis claims.
