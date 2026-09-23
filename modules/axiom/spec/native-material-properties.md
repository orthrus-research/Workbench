# Native addon material properties

The pack context admits exact original constructors for Susy-Core's
`FiberProperty`, `MillBallProperty` and `DummyABSProperty`, and Supercritical's
`CoolantProperty`. It also admits selected original property methods used by
the regression programs and pack's coolant registrations. Those addon constructor
selections remain pack-only. This is bounded authoring coverage, not complete addon loading.

These classes come unchanged from the selected pack artifacts. Axiom does not
implement their verification rules, replace their constructors, or rewrite
developer Groovy. The original Groovy dispatch selects overloads and invokes
the original GT `Material.setProperty`, which inserts the property and then
runs `MaterialProperties.verify`. Verification can add dependencies and flags,
or throw after insertion. A stopped material is not a rolled-back material.

## Source-backed behavior

| Native property | Behavior relevant to saved developer edits |
| --- | --- |
| Fiber | The default is non-solution-spun, melt-spun, non-weaving. A non-melt-spun fiber cannot coexist with a fluid property. Solution spinning adds wet-fiber generation; weaving adds plate generation; successful verification adds fiber/thread and disables decomposition. Fiber-only materials also require the native base-type registration performed by the pack's `MaterialChanges.groovy`. |
| Mill ball | The original record stores durability and requires dust during verification. Its constructor does **not** reject negative durability. It has no no-argument constructor: native `PropertyKey.constructDefault` returns null on failed construction, and subsequent verification fails natively. Axiom does not supply a default. |
| Dummy alloy-blast | The original Susy subclass installs its own no-op recipe producer. Inherited GCYM verification requires blast/fluid properties and replaces the stored temperature with the material's blast temperature. Constructor temperature alone does not establish the verified temperature. The inherited explicit temperature setter rejects nonpositive input. |
| Coolant | The original constructor stores the hot material, storage key, thermal coefficients and material mass. Native setters preserve hydrogen/absorption edits. Verification requires a fluid property; creating an empty default fluid property is insufficient because GT requires a primary storage key. |
| Fluid pipe containment | Susy's original `setBaseProof` expansion modifies GT's containment predicate for `susy:base`. A missing pipe logs an error and returns; disabled integration leaves the method unavailable. Explicit false is a stored predicate entry, not deletion of its key. |
| Fission fuel | Supercritical's original factory and scalar builder store the supplied fields. Native verification ensures dust. `requiredNeutrons` defaults to 1 only when not explicitly set; setting 0 preserves 0. Construction does not reject negative scalar values or execute later depleted-fuel callbacks. |

The fixture's valid fluid cases use original `enqueueRegistration` before
property verification. That is a developer-authored native queue operation,
not an Axiom-created fluid or proof that later fluid registration succeeded.
The missing-fluid case retains the original native error instead.

Susy's `SuSyMaterialProperties` private base-type set is not GT's active
`MaterialProperties` base-type set. It must not be mistaken for the pack's
explicit `MaterialProperties.addBaseType(SuSyPropertyKey.FIBER)` call.
Native mutable state is always initialized in a fresh worker; the repeat
missing-base-type case checks that earlier registrations do not leak.

### Conditional containment and original builder behavior

`setBaseProof(Material, boolean)` is installed by the same original Susy
`GrSModule.onCompatLoaded` callback controlled by saved SussyPatches `recipeInfo`.
Admission does not bypass that condition. Its missing-pipe case retains the
original Groovy log message and saved source location while native initialization
continues; a logged error must not be changed into a thrown error or omitted.
When integration is disabled, Groovy's original MissingMethodException remains.

The attribute namespace is **`susy:base`**, as produced by the original
`SuSyUtility.susyId` and artifact's compiled mod ID. A Java package beginning with
`supersymmetry` is not evidence for a resource namespace. The observer retains
the actual containment map, including explicit false values, without calling
fluid filters or tooltip consumers.

FissionFuel construction uses the original static factory and original builder,
not an Axiom data model or a newly public constructor. Selected scalar setters
and `build()` are admitted; supplier setters and later item callbacks are not.
The observer reads scalar fields and callback presence only. Unset callbacks can
remain absent after successful property verification: that is not proof that
later reactor/item behavior is usable. No balancing or physics validity is claimed.

The wrong-argument witness `builder.duration(1, 2)` produces an original
`groovy.lang.MissingMethodException` naming `java.lang.Integer.call()` in the
selected runtime. Reporting preserves that native message and saved location;
it does not replace it with an invented duration validation error. Duplicate
property assignment also retains its original error and the first property's state.

## Passive reporting

Requested materials now include `nativePropertyState`, schema
`axiom.native-material-property-state.v1`:

- Actual attached property identities and implementation classes, including
  null values left by native failed default construction.
- Actual material flags, including addon flags absent from the GT-only vocabulary.
- Selected native fields for the property families above. `valuesObserved`
  is false for other families; absent observations are not inferred defaults.
- Exact float64 bits for coolant and FissionFuel fields, with explicit handling of
  non-finite values. Observing a value is not declaring it technically valid.
- The native DummyABS recipe-producer class, without invoking it.
- GT fluid-pipe scalar fields and actual containment predicates; FissionFuel
  scalar fields and presence of its two deferred callbacks, without invoking them.
- Attached GT fluid properties' original storage state: registration completion,
  primary key, pending builder queue and registered key/fluid bindings. Stored
  bindings retain original material-property lookup and Forge registry identity.
  Queued builders retain their selected fields without being executed. These
  storage observations do not claim fluid block, bucket or recipe behavior.

The observer does not create keys, initialize addon catalogs, invoke property
verification, drain fluid queues or run recipe producers. These observations
are separate from the existing GT expectation vocabulary and do not expand
its qualification. Native errors and saved Groovy source locations remain
available without requesting material observations or providing expectations.

## Evidence and remaining work

The selected inputs are Supersymmetry 0.1.16.15, Susy-Core 0.1.118,
GTCEu 2.8.10-beta, GCYM 1.2.11 and Supercritical 0.2.7. The
[source lock](../sources/material-program.lock.json) pins Susy's property,
key and flag sources alongside the existing GT lifecycle sources. Original
GCYM/Supercritical constructor and verification bytecode was inspected in the
pack-hash-verified artifacts; this is not a claim about newer upstream code.
Susy's published MillBall record is JVM-downgraded bytecode with a bundled
record base class. Axiom uses that original class, not a recompiled replacement.

Primary anchors are GT `Material#setProperty`, `MaterialProperties#verify`,
`PropertyKey#constructDefault` and `FluidProperty#verifyProperty`; Susy's
`api/unification/material/properties/` and `info/SuSyMaterialFlags`; GCYM's
`AlloyBlastProperty`; Supercritical's `CoolantProperty`; and the pack's
`MaterialChanges.groovy`, `ChangeFlags.groovy`, `UnknownCompositionMaterials.groovy`,
`OrganicChemistryMaterials.groovy` and `ThermodynamicsMaterials.groovy`.

Run `tools/axiom_pack_property_conformance.py` with explicit `--java-home`,
`--engine-home`, `--runtime-home`, `--pack`, and a new `--report`;
`--whole-pack` additionally checks the unchanged complete selected checkout.
It exercises native flags/dependencies, duplicate assignment, original errors
and saved source lines, constructor defaults, setter effects and worker isolation.
This shares native dependencies with production; it is a bounded regression lane,
not an independent whole-pack parity oracle.

The subsequent [native Groovy access increment](native-groovy-material-access.md)
passes ordinary property syntax, scalar concatenation, queued temperature reads,
native addIngot and primitive-array reads. Along with setBaseProof and FissionFuel
construction, original Susy callback composition and [native mapper dispatch](native-material-mappers.md),
the unchanged pack reaches 3,441 partial default-registry materials and returns
from its complete material-producer chain. The subsequent
[native mutation increment](native-material-mutations.md) also returns from
`ChangeFlags.init()`. The [custom-item increment](native-custom-items.md) now also
completes PostMaterialEvent with 409 variants and phase FROZEN; pack-wide generated
content remains deferred, not an invalid pack edit. Focused property and
lifecycle witnesses run separately.

Remaining producer closure includes native addon material producers and further expansions,
original addon configuration, registry ownership, selected mixins and native event
registration order. The [hook map](initialization-hooks.md) keeps those dependencies
explicit. Susy's native post-material callback now adds tankless fluid-pipe
properties, but their direct authoring/value qualification remains pending.
Full material initialization, generated-fluid completion,
recipe initialization and exact installed JRE identity remain unqualified.
