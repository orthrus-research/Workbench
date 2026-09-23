# Atlas frozen runtime material evidence binding v1

Status: active bounded evidence envelope

`workbench-atlas-runtime-material-evidence-binding-v1` binds one canonical
`workbench-atlas-runtime-material-classification-v1` result to the exact graph
database and Crucible-owned frozen runtime capture from which Atlas derived it.
It adds no fields to the existing material-classification V1 format.

## Required binding

The envelope contains:

- the pack, platform, runtime-graph profile, and physical-side scope;
- the complete material-classification SHA-256 and policy ID/SHA-256;
- the normalized runtime-graph database SHA-256 and normalization identity;
- the Crucible snapshot and capture identities; and
- the observed frozen stage and material-manager phase.

All fields contribute to `evidence_binding_id`. A consumer that also has the
classification object must recompute its canonical SHA-256 and require exact
agreement with the envelope. Profile, side, or policy drift fails closed.

## Authority

Atlas owns only the binding and derived classification. Crucible retains
authority for the referenced runtime capture. Pack Program Studio retains
authority for source declarations introduced by a later comparison.

The envelope proves that these exact classification bytes were associated
with one declared frozen capture. It does not prove that a Groovy source file
ran, that a particular call site created a material, or that no other source
could have produced equivalent final state.

## Causal boundary

Final-state comparison may cite this envelope as runtime identity evidence.
Causal reconciliation additionally requires an independently validated
operation or transition ledger whose capture identity agrees with this
envelope. Similar source and runtime values cannot substitute for that ledger.
