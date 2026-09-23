# Atlas material source-to-frozen-runtime comparison v1

Status: implementation contract

This contract defines the intended
`workbench-atlas-material-source-runtime-comparison-v1` projection. It compares
Pack Program Studio material intent with a frozen Atlas material
classification while retaining their different authorities.

## Inputs and binding

The comparison requires:

1. one validated `workbench-pack-source-declarations-v1` feed containing
   material registration and/or mutation candidates;
2. one canonical `workbench-atlas-runtime-material-classification-v1` result;
3. one validated
   `workbench-atlas-runtime-material-evidence-binding-v1` envelope for that
   result; and
4. one immutable profile comparison policy.

Pack and platform profile, physical side, expected-closure policy, runtime
classification policy, and all content identities must agree. A declared
profile-equivalence mapping must be an explicit policy input; name similarity
is not equivalence.

## Comparison lenses

The projection keeps four independent lenses:

- **registration parity** compares the complete statically known material core
  of a source-created material with its frozen runtime core;
- **mutation compliance** checks only source-declared additions, replacements,
  or value constraints against an independently owned material;
- **execution lineage** cites Crucible operation evidence when available and
  otherwise remains `not-established`; and
- **form realization** compares requested item/fluid relationships separately
  from material-core parity.

A source mutation never claims ownership of the target's complete material
core. Runtime materials outside the declared source registration scope remain
`unattributed-runtime`, not automatic errors.

## Identity and facet comparison

Exact material resource location is the primary join. Numeric ID is a separate
corroborating comparison and cannot be the sole global identity. Duplicate or
unresolved source/runtime identities produce an ambiguity frontier; the
projection does not select a likely candidate.

Properties and flags compare as sets after profile-bound expected verification
closure. Composition and presentation compare only when their source values
are exact. Requested forms are reported as realized, not observed, or
additional observed forms without altering the material core.

Each facet reports one of `equal`, `missing-at-runtime`,
`additional-at-runtime`, `different`, or `not-comparable`, with exact source
and runtime evidence pointers. Aggregate states distinguish `parity`,
`difference`, `frontier`, `source-only`, `runtime-only`, and `ambiguous`.

## Non-claims

State parity does not establish that source executed or caused the runtime
state. Runtime additions may come from verification, later mutation, another
mod, or unobserved metaprogramming. Causal wording is permitted only when a
capture-bound Crucible operation ledger establishes the transition.

The operation ledger must agree on pack, platform, physical side, capture,
snapshot, frozen stage, manager phase, declaration-set identity, and source
bytes. Atlas upgrades a declaration to `transition-established` only when an
exact source link observed an applied operation, its bounded delta matches the
declared constraint, and the compared frozen result still satisfies it. A
captured no-op establishes execution only. Failed, unbound, cross-capture, or
non-surviving operations cannot close lineage.
