# Native material-property kernel

Status: implemented **internal prerequisite**, not an executable pack bootstrap
or new `check` capability. The recipe endpoint still uses its explicit-context
registry. No generated item, fluid, ore membership or registered material is
produced by this kernel.

## Source and admitted scope

GTCEu commit `9fe140febe8747bbe2f06dfd570421331ec06f4b` owns the extracted
`MaterialProperties`, `PropertyKey`, `IMaterialProperty`, `DustProperty`,
`GemProperty` and `IngotProperty` class bodies. Their original file/blob/SHA-256
identities are in `sources/supersymmetry.lock.json`. The extraction recipe is
`tools/axiom_material_sources.py`; the source comparison rejects drift between
the retained Java and that explicit recipe.

The original comparison domain is **dust, gem, ingot and empty**. The production
key catalog has now expanded with the [native material construction domain](material-construction.md),
including fluid, polymer, ore, tool and pipe properties. Addon properties and
missing external effects are not replaced by dummy implementations.

The following substitutions are deliberate and bounded:

- Relocate the package, make classes package-private, remove Nullable annotations.
- Substitute `MaterialState` for `Material`: it carries a name, a property
  collection and the material-manager mutation predicate, not registration,
  flags, composition, resource locations or generated items.
  The registry prerequisite additionally accepts explicit numeric ID/namespace;
  property-only carriers do not invent those identities.
- The standalone property comparison projects four built-in keys. Production
  retains all GT keys; FLUID is activated as a base type by the explicit fluid
  environment. Property-only custom-key probes do not qualify addons.
- Omit the optional debug log when an empty placeholder property is created.
- Extract `Material.setProperty` with only its manager access substituted, and
  the manager's PRE/OPEN/CLOSED/FROZEN mutation predicate. Neither the phase
  transitions nor the Forge event dispatcher is implemented here.
  Phase transitions are now implemented separately by the
  [native registry lifecycle](native-registries.md); [event dispatch](native-events.md)
  is also retained separately, without an executed pack listener universe.

There is no compatibility layer in the `gregtech` namespace, no world stub and
no new custom JVM. The selected native JVM executes these Java classes. The
public engine distribution contains their source and LGPL notices.

## Actual behavior that must survive integration

`MaterialProperties.verify()` takes a snapshot of property values, invokes their
verification methods, and repeats **only while the property count changes**.
It is not a general graph fixed-point solver. The native HashMap/HashSet behavior
and recursive calls are retained, including their failure behavior.

An ingot ensures dust and rejects a simultaneous gem. Missing smelting,
arc-smelting and maceration references become the owning material. Explicit
references ensure the ingot property on the referenced material; magnetic
references do so too. `ensureSet(key, true)` verifies only when it inserts a new
property. It does not revisit an existing one merely because `true` was passed.

Verification is not transactional. If a referenced gem acquires an ingot and
verification fails, that ingot and earlier dust additions remain. Axiom must
retain those effects rather than undoing them or labelling the graph clean.

Property identity is the key string, not the property class. `setProperty`
rejects null and duplicates but does not perform the typed cast; `getProperty`
does. Default construction uses the original `Class.newInstance()` behavior:
caught exceptions yield null, which `ensureSet` can retain in the map and which
can cause a later verification failure. Replacing this with modern reflection
without accounting for its different exceptions would change behavior.

Dust constructor arguments are not validated like their setters. For example,
a negative burn time supplied to the constructor is retained, whereas the
setter rejects it. This is upstream behavior, not an added acceptance rule.

`Material.setProperty` allows OPEN and CLOSED and rejects PRE and FROZEN.
Direct `getProperties()` mutation bypasses that guard in the upstream code.
The kernel preserves that distinction; it is not a promise that arbitrary
post-freeze mutation is safe or supported by a future profile API.

The extensible base-type set is upstream static state. The future native
bootstrap must run in the existing disposable worker so one candidate cannot
change the next candidate's base types. A controlled two-worker probe verifies
that an added base type is absent in the next worker. Test-only custom keys are not Susy FIBER
support, and in-process calls are not isolated evaluation sessions.

## Evidence and limits

Directed tests cover default construction, duplicate/null/type behavior,
dependency propagation, cross-material cycles, failure side effects, phase
access and base-type extension.

`tools/axiom_source_conformance.py` independently reads the locked source and
compiles its extracted property classes into a separate namespace on the
selected JVM. Both implementations receive the same 48,000 operations over
2,000 three-material graphs. Outcomes, diagnostics, property membership, dust
fields and ingot references are compared after each operation. Name/property
carriers and the restricted catalog are shared assumptions, explicitly recorded
in the receipt; this does not independently qualify those substitutions.

The existing 80,320 recipe/overclock comparisons remain separate evidence.
Neither count is a percentage of full-game parity.

## Remaining dependency boundary

The registry, fluid and expanded construction dependencies now retain the
bounded native behavior described in their specifications. Complete Susy/pack
property additions, including FIBER and Supercritical dependencies, actual
prefix/enchantment identities and applied listener/configuration transforms
before qualifying complete producers. Material verification alone never
establishes ore/fluid identity, recipe construction or machine acceptance.
