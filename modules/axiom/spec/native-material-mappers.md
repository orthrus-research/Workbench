# Native material mapper execution

The pack context admits the original `material`, `metaitem`, `item` and `ore`
mappers through GroovyScript's original global bindings. It retains each selected
binding identity and leaves lookup, argument conversion and dispatch native.
Changed identities and unselected mappers refuse. GT-base does not admit these
mapper calls. Recipe admission is still incomplete; see the current
[original continuation boundary](native-root-class-space.md#original-recipe-continuation).

## Earlier bounded material composition

GT's complete `GroovyScriptModule.onCompatLoaded` registers five mappers. After
compatibility loading, the host performs the binding loop from GroovyScript's
`initializeGroovyPreInit`: each currently registered original mapper is supplied
to original `GroovyScriptSandbox.registerBinding(INamed)`, before PRE_INIT source
loading. In this earlier composition, full `ObjectMapperManager.init` and plugin
discovery were uncomposed; the current original startup route executes them.
Registering these five bindings does not admit every mapper to developer code.

The material binding must be the original `ObjectMapper` owned by the native GT
container, returning the original Material class, with its original String
signature. That bounded admission retained the actual binding identity and allowed
only the selected material mapper. Guest class/script lookup and a saved closure
reference still use original Groovy dispatch; the gate does not invoke a mapper
or choose an overload. Other missing guest methods and other native mappers are
not opened. Changing a bound identity cannot reuse its admission.

Java constructor/method descriptor verification is cached per immutable admission
policy and Class identity. Groovy metaclass lookup, overload dispatch, native
values and effects are not cached. A different policy cannot reuse an earlier
policy's checks, and descriptor drift still fails. This avoids repeated host
reflection during material construction without resetting native registries or
changing the worker's resource limits.

`execution.objectMapperBindings` records actual registration order, selected
admission and native identity. It explicitly disclaims complete mapper setup.
`native-object-mapper-bindings` in the initialization trace distinguishes a
completed binding step from a step never reached. Native mapper class definition
hashes are passively observed; no mapper bytecode is rewritten by Axiom.

## Source-backed behavior

| Input | Original behavior observed |
| --- | --- |
| `material('water')` | Resolves native GT Water, ID 269. |
| `material('susy:molybdenum_disilicide')` | Resolves the native Susy producer's object, ID 8792. |
| Unregistered namespace plus `:water` | Native manager falls back to its default registry and finds Water; namespace alone does not create a registry. |
| Missing name or empty string | Native mapper logs an error and returns null; source can continue. |
| `material()` | Returns the original null default without a missing-name log. |
| Extra argument after the name | Native parser logs that extra arguments are not allowed, then returns null. |
| `material(null)` | Native manager throws NullPointerException when reading the name. |
| `material(42)` | Native Groovy throws MissingMethodException for `doCall`. |

The complete saved fixture corpus also tests editing a looked-up material, native
property rejection, closure-reference invocation, source locations and fresh
worker isolation. Logged errors are not rewritten into exceptions, and native
continuation is not a clean validity pass. These are bounded native observations,
not proof of every dynamic dispatch branch or complete pack initialization.

## Unchanged pack evidence and next boundary

At selected Supersymmetry 0.1.16.15, there are four `material(...)` call sites in
the material producer directory: SecondDegreeMaterials lines 80/110 and
ThirdDegreeMaterials lines 24/30. Native execution now observes both calls in
each class and returns from `SuSyMaterials.init`, including all eleven producer
groups and `changeFormulas`. No individual material definitions were copied or
patched. All 2,364 saved Groovy/configuration inputs remain present.

The subsequent [native mutation increment](native-material-mutations.md) retains
original recipe-map catalogs and completes `ChangeFlags.init()`, with 3,441
partial default-registry materials. The subsequent [custom-item increment](native-custom-items.md)
also returns from PostMaterialEvent with 409 declared variants and phase FROZEN. This is not proof
that POST_INIT recipe bodies have executed. Remaining addon callbacks/order are
unqualified and pack-generated block/item content remains explicitly deferred.

Earlier whole-pack attempts exhausted the former 25 CPU-second worker limit
without a native result. Descriptor caching alone did not resolve that failure.
Worker resource targets are now suspended until post-MVP optimization. This
policy change does not itself establish reliable complete initialization. Resource-
terminated attempts remain incomplete checks, not invalid developer edits;
successful partial runs do not erase earlier failures.

The earlier bounded mapper lane used `tools/axiom_pack_mapper_conformance.py`.
Current raw-context regression uses the native root-stage tool described above.
Primary source paths and immutable identities are retained in
[the material-program source inventory](../sources/material-program.lock.json):
GT GroovyScriptModule/MaterialRegistryManager; GroovyScript, GroovyScriptSandbox,
ClassMetaClass, ObjectMapperManager, ObjectMapper, AbstractObjectMapper and
IObjectParser; the original pack consumers and MaterialChanges/ChangeFlags.
