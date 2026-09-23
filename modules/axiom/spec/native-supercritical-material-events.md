# Native Supercritical configuration and materials

The bounded pack context executes original Supercritical configuration,
registry/material callbacks and prefix declarations. It does not complete
construction, generated content, fluids, recipes or whole-pack initialization.

## Source contract

The selected Supersymmetry 0.1.16.15 descriptor binds Supercritical 0.2.7.
Its original classfiles, annotations, mixin configuration and refmap are inspected
from that exact artifact, not inferred from another upstream revision. The API
manifest records every selected class/resource hash. The source inventory also
pins the complete saved `config/supercritical.cfg`, Susy's construction source,
Cleanroom configuration/event code and GT's native material registry classes.

Original `SCConfigHolder` runs through Cleanroom's `ASMModParser`,
`ConfigManager.loadData` and `ConfigManager.sync`. Native code owns nested
categories, defaults, conversion and worker-copy writes. Axiom reports input
presence and input/worker hashes; it never writes settings back to the checkout.
Missing files receive original defaults in the disposable worker only.
Non-finite numeric observations are strings with raw IEEE bits so serialization
does not hide a native result or normalize a setting.

## The construction dependency

Susy's original `Supersymmetry.onModConstruction` initializes IR definitions and
entities, forces `SCConfigHolder.misc.enableMaterialModifications = false`, then
performs a cache check/cleanup. That complete callback is **not composed**.
Copying its assignment into an Axiom replacement would hide this dependency.

- If native configuration already yields `false`, Axiom registers the complete
  SC subscribers. The missing assignment would leave this particular value
  unchanged; this is condition-specific composition, not construction parity.
- If native configuration yields `true`, including the missing-setting/file
  default, SC subscribers are deferred with
  `material-context.supercritical-construction-override-incomplete`.
  The true value is retained, not forced false or executed as effective pack
  configuration. This is an incomplete dependency, not an invalid developer edit.

Both paths remain incomplete overall. Other bounded callbacks can still run;
their observations do not compensate for the deferred construction dependency.

## Original callbacks and ownership

| Native callback | Behavior retained |
| --- | --- |
| `CommonProxy.createMaterialRegistry` — NORMAL registry event | Creates the separate `supercritical` registry during PRE, before registration opens. |
| `SCEventHandlers.registerMaterials` — HIGH material event | Calls complete `SCMaterials.register()` and `SCOrePrefix.init()`. |
| `CommonProxy.postRegisterMaterials` — NORMAL post-material event | Adds all nine SC ore prefixes to native `MetaItems` declarations. |
| `SCEventHandlers.registerMaterialsPost` — NORMAL post-material event | Original configuration condition controls material modifications. In the composed false context, the original method returns without them. |

`SCMaterials.register()` always builds Corium (ID 0) before checking
`disableAllMaterials`. The selected true setting therefore leaves one SC
material, not zero. False continues through all four original producer groups;
the bounded witness contains 35 SC materials. Counts are observations, never
replacement catalogs or filtering rules.

The original `MixinOrePrefix` supplies SC's radiation-function extension,
alongside the previously required `MixinElement`. Original `SCOrePrefix.init()`
assigns six radiation functions and one hot-fuel heat function. The observer
reads their presence, native prefix identity and declaration counts without
invoking damage functions, generating items or registering queued fluids.
Corium's original 2500 K fluid builder remains queued.

Both complete original subscriber classes are registered with native EventBus
under SC owner metadata; the previous active owner is restored. This also
registers later block/item/recipe handlers, but those events are not posted
without their prerequisites. No callback bodies are extracted or rewritten.

The explicit bounded registration order is GCYM, SC proxy, SC events, Susy,
then script listeners. Native priorities and dispatch-cache ordering are
preserved. Same-priority full-addon discovery order remains unqualified.
Dispatch snapshots are taken before each registry/material/post-material event;
they are not per-listener completion traces.

`execution.materialRegistries` reports each actual registry, native network ID,
default-registry identity and material count. Existing `registeredMaterials`
continues to mean the default registry only. The unchanged pack currently has
3,441 default-registry materials plus Corium in the SC registry. Network IDs
describe this bounded composition, not a qualified installed-pack ordering.

## Verification and remaining work

`tools/axiom_pack_supercritical_conformance.py` runs complete saved programs in
fresh native workers: both catalog settings, numeric configuration changes,
invalid and non-finite values, missing inputs, construction deferral, Corium
mutation, a new namespaced material, native setter errors and correction.
The unchanged full-pack capture is a separate bounded witness. Native diagnostics
retain saved-source locations; Axiom does not repair source.

Still pending: complete Susy construction/IR prerequisites, GTFO item/material
initialization and mod-presence conditions, full discovery/order, generated
blocks/items, actual fluid registration, prefix processing and recipes.
Neither the material counts nor attached damage functions qualify gameplay,
the exact installed pack JRE, or the initialization-latency target.
