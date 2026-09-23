# Crucible Pyrotech recipe capture V1

Status: qualified experimental profile adapter in cohort `m5-pyrotech-003`

The `pyrotech-custom-recipes` adapter captures the exact final contents of all
24 Pyrotech 1.6.19 custom Forge registries in the Supersymmetry provisional
Cleanroom profile. It is a profile-specific semantic capture, not generic Forge
or universal Pyrotech policy.

Recipe identity, registry identity, process classification, machine
classification, input occurrences, output occurrences, failure occurrences,
and execution boundaries are separate facts. Ingredient alternatives remain a
choice set. Item and fluid roles remain distinct. Numeric registry IDs are
launch-scoped observations and do not become semantic recipe identity.

The adapter may call stable public projection getters and validate each finite
ingredient alternative against its ingredient predicate. It does not call
recipe lookup, recipe matching, random-output selection, inventory mutation,
machine execution, fuel behavior, or world-dependent behavior. Public method
surfaces and dynamic fallback classes remain explicit executable boundaries.
In particular, an empty Campfire, Worktable, Stone Oven, or Brick Oven custom
registry is not proof that the corresponding runtime process has no recipes.

The category is complete only when all 24 declared registries are present, 19
are nonempty, exactly 700 custom entries survive, repeated canonical bytes are
stable, and the adapter reports zero diagnostics and unsupported values. Those
counts bind this exact profile and must fail closed on drift.

The independent Pyrotech domain graph requires only mod, Forge registry, Forge
fluid, and Pyrotech category evidence. A separate program-reconciliation graph
may join final recipe identities to Groovy mutation occurrences, but only by
observed runtime entry identity. Final registry membership or equal recipe
state never establishes source causality.

Producer `0.7.1` passed the 33-adapter gate with two byte-identical samples,
zero unsupported values, zero diagnostics, a clean process exit, and complete
frozen artifact reconciliation. The qualified category contains one authority,
24 registry records, and 700 recipe records. Qualification is exact to the
captured Supersymmetry revision, Pyrotech 1.6.19 artifact, provisional
Cleanroom profile, producer artifact, and dedicated-server cohort; it is not a
universal Pyrotech claim.
