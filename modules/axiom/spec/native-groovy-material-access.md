# Native Groovy material access

This increment lets saved material code use ordinary Groovy properties and the
selected pack's native helper operations. Axiom does not translate pack source
into different method calls or implement material rules. Original Groovy dispatch
and original GT methods still choose overloads, coerce values and produce errors.

## Property dispatch

For a native Java receiver, admission consults the selected Groovy runtime's own
metaclass property metadata. A `MetaBeanProperty` must expose an original
`CachedMethod` getter or setter whose exact public, nonstatic descriptor family
is already selected for that receiver. Admission never invokes that accessor.
The author's original invokedynamic or ScriptBytecodeAdapter call still executes.

This supports `material.materialRGB`, boolean `material.solid`, and property
assignment such as `dust.harvestLevel = 0`. The last expression retains GT's
original `IllegalArgumentException` and saved Groovy location. Getter names are
not guessed: Groovy's acronym and boolean property rules remain authoritative.
Class receivers, source-owned Groovy classes, dynamic properties and multiple
setters do not borrow this bounded native-bean admission.

The pack also reads `FluidBuilder.temperature`, which is an original private
field, not a bean getter. The pack-only policy separately selects the exact
read descriptor `temperature:I`. Groovy must expose the original `CachedField`
on the exact declaring receiver. Axiom does not add a getter, invoke reflective
field reads itself, or admit writes/direct-field syntax through this selection.

## Actual pack temperature helper

The selected unchanged `groovy/globals/Globals.groovy` owns these decisions:

- Gas/liquid helpers first inspect the corresponding queued native builder. An
  explicit temperature other than the native `-1` sentinel takes precedence.
- The gas fallback uses room temperature without a blast property, otherwise
  blast temperature plus the native gas offset.
- The liquid fallback uses the native solid/liquid temperature for dust without
  blast, room temperature without dust/blast, or blast temperature plus its offset.

These are source declarations, not host algorithms. The pack helper executes
unchanged. GT `FluidProperty.getStorage()` returns the FluidProperty itself;
its `getQueuedBuilder` delegates to the internal original `FluidStorageImpl`.
Do not infer the runtime receiver from the method's interface return type.
Queue reads neither build nor register a fluid. The original fluent
`FluidBuilder.temperature(int)` still rejects nonpositive values.

## Concatenation and material expansion

String concatenation admits both original Groovy `StringGroovyMethods.plus`
overloads applicable to String receivers: `(CharSequence,Object)` and
`(String,CharSequence)`. Other receiver families are not selected. Original
Groovy handles the scalar conversion and result, including null; Axiom's bounded
operand surface covers strings, native numeric scalars, booleans and characters,
not arbitrary object-formatting callbacks.

The existing original GT compatibility callback installs
`MaterialPropertyExpansion.addIngot(Material)`. The pack-only policy now admits
that exact extension. Its original body checks the frozen phase, adds an ingot
property only if absent, and uses normal native property verification. That can
also add dust. Repeated calls retain the existing ingot instance.

**The original return type is void.** In Groovy an assignment from `.addIngot()`
receives null, even though the registered material was modified. In particular,
the selected pack assigns `HighPurityPlatinum` from such an expression. Axiom
does not change this into a fluent return or repair the pack. This observation
alone does not establish whether a later pack consumer fails.

## Primitive-array reads

The shared profile vocabulary now admits `int[]` `getAt` with an Integer index,
using the existing native-dispatch path. It does not admit array writes, ranges,
collection indices or other primitive-array families. Groovy 4.0.30's original
`IntegerArrayGetAtMetaMethod` normalizes negative indices through `ArrayMetaMethod`,
performs the JVM array read and boxes the result. Axiom does none of those steps
itself. Regression programs use GT's original `VA` table, including positive and
negative reads and both native out-of-bounds errors with saved-source locations.

The pack's `GTValues.VA[GTValues.MV]` now runs unchanged. Its returned voltage is
still passed to the original material builder; this is not recipe execution.

## Evidence and next boundary

The [source lock](../sources/material-program.lock.json) includes the selected
`Globals`, `FirstDegreeMaterialsA`, GT property expansion, FluidBuilder,
FluidProperty, FluidStorageImpl and BlastProperty sources. They are pinned to
Supersymmetry 0.1.16.15 and GTCEu 2.8.10-beta, not inferred from current upstream.
Original extension descriptors and Groovy 4.0.30 property machinery were also
inspected in the bound artifacts. No native API/language body changed here.

`tools/axiom_pack_property_conformance.py` exercises successful property reads
and writes, native setter errors, scalar concatenation, queued default/explicit
temperatures, invalid temperatures, blast getters and addIngot's native effects,
return and wrong-argument error. It also retains the earlier addon-property
witnesses and fresh-worker isolation checks. This shares dependencies with
production and is not an independent whole-pack parity oracle.

The next increment also supports Susy's native `setBaseProof` expansion and
Supercritical's original FissionFuel builder, described in the
[property coverage](native-material-properties.md). The unchanged selected pack
now passes `SusyMaterials.MolybdenumDisilicide` after native Susy subscriber
composition. [Native mapper binding and dispatch](native-material-mappers.md)
now pass all four producer call sites, reaching 3,441 partial default-registry
materials. [Native mutations](native-material-mutations.md) subsequently complete
ChangeFlags; [native custom items](native-custom-items.md) now also complete
PostMaterialEvent with 409 declared variants and phase FROZEN.
All 2,364 saved Groovy/configuration files remain retained. This is an
Axiom composition gap, not a native source invalidity or passing pack baseline.

Remaining work includes pack-wide item registry insertion and other addon callbacks. The
[addon hook/configuration dependencies](initialization-hooks.md), generated
fluids, later recipes and exact installed JRE qualification remain open. An
early-stop timing must not be described as complete initialization performance.
