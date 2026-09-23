# Atlas material/fluid coverage contract v1

Status: active bounded runtime-graph projection

This contract adds the material-centric inverse of Atlas's complete runtime
fluid classifier. It does not alter
`workbench-atlas-runtime-fluid-classification-v1`; instead, it binds that
classification by canonical SHA-256 and emits a new
`workbench-atlas-material-fluid-coverage-v1` document.

## Question

For one exact runtime-graph profile and physical side:

1. which registered fluids have confirmed, one-sided, conflicting, or absent
   material identity evidence;
2. which exact fluids are observed for each exact material node;
3. which materials have no observed fluid association; and
4. which identity or resource-location gaps remain before a fluid can be
   treated as an exact material-backed fluid.

## Authority and scope

Atlas reads one already-admitted immutable runtime graph. The scope is exactly
one `ProfileScope`; rows never merge sides, profiles, snapshots, or platform
families. The source fluid classifier remains the authority for fluid
incidence and material-identity status. The inverse projection enumerates all
in-scope `material` nodes, including materials with no fluid relationship.

Only these retained `has_material` roles participate:

- `effective_fluid_unifier_lookup` is a reverse observation; and
- `material_fluid_storage_form` is a structural observation.

A fluid association is `confirmed` only when the source classifier status is
`consistent`. One-sided and conflicting links remain `unconfirmed`; absence
remains `unlinked`. The projection never repairs, guesses, or votes between
those states.

Material association status is derived without a closed-world game claim:

- `confirmed_only`: every linked fluid is confirmed;
- `unconfirmed_only`: links exist, but none is confirmed;
- `mixed`: both confirmed and unconfirmed links exist; and
- `no_observed_fluid`: no admitted relationship links a classified fluid to
  the material. This last state does not assert that the material can never
  have a fluid form.

## Coverage frontier

Every fluid row retains its runtime node ID, all observed
`fluid-resource-location` values, source material-identity status, reverse and
structural material IDs, and zero or more frontier issue codes. Any non-exact
resource-location status and any material-identity status other than
`consistent` is an explicit frontier issue. Summary counts must reconcile to
all emitted fluid and material rows.

The result supports exact lookups by runtime node ID,
`fluid-resource-location`, `material-resource-location`, and
`material-numeric-id`; ambiguous identity values return every matching row and
are never resolved by selection order. Callers may also query the complete
fluid frontier by issue code and materials by association status.

## Identity and limits

Canonical JSON uses UTF-8, sorted object keys, no insignificant whitespace,
and no timing fields. The result records the complete source classifier
SHA-256 and structural bounds. Material enumeration, material identity keys,
fluid variants, material relationships, and incidence relationships all have
explicit fail-closed caps. Operational SQL batch size does not change semantic
bytes.

## Non-claims

Passing this projection does not establish current Cleanroom behavior, source
declaration parity, localization, thermodynamic properties, state/form
completeness, realized worldgen, route completeness, or C01/V2 graph
publication. Historical Forge evidence remains valid only for its exact
legacy profile. A new platform/profile claim requires a separately admitted
runtime graph and a fresh result.
