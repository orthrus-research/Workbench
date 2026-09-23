# Groovy material declaration normalizer v2

Status: active bounded material-declaration workflow

V2 preserves exact lexical operations, source spans, source authority, and
material/form separation while adding a composed
`workbench-atlas-material-semantics-policy-v2` binding. Its established V2
policy and record identities are stable. The private lexical base used to
construct those records is an implementation detail, not a supported
predecessor workflow.

## Import-aware flag resolution

Atomic flag constants and finite presets resolve only through exact qualified
symbols, explicit static imports, or static wildcard imports admitted by the
bound semantic policy. Literal string flags resolve through the profile's
case-insensitive runtime-name table. Ambiguous symbols and arbitrary
expressions remain unresolved.

Each argument retains a resolution trace including whether it was an atomic
constant, preset, or runtime-name string and the exact expanded flag names.
Preset expansion never evaluates a Java or Groovy collection.

## Value-dependent closure

V2 evaluates only the bounded literal and profile-constant expression subset
needed by declared conditional rules. Results retain exact operands, Java
coercions, predicate outcomes, and rule IDs. An unknown input yields a
lower-bound closure rather than selecting a likely branch.

The pinned GTCEu wire rule models the exact signed-`int` voltage conversion,
the IV threshold, the default non-superconductor argument, and its conditional
`generate_foil` effect. Transitive flag closure remains owned by the compiled
runtime material policy.

## Remaining boundary

V2 still treats helper-generated builders and post-registration material
mutations as separate source-analysis work. It does not execute Groovy or
claim runtime registration.
