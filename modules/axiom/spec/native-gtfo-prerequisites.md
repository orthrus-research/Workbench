# Native GTFO configuration and lifecycle prerequisites

Axiom applies the original GTFO configuration to the disposable native worker.
The GTFO construction and material callbacks are still deferred: configuration
loading alone does not establish addon presence or native lifecycle equivalence.

## Implemented boundary

The selected pack binds GregTech Food Option 1.12.10. Its original `GTFOConfig`
and nested classes execute through Cleanroom's `ASMModParser`,
`ConfigManager.loadData` and `ConfigManager.sync`. The complete saved
`config/gregtechfoodoption.cfg` is pinned in the source inventory. Original
classfiles are bound individually in the native API artifact inventory.

Native code owns parsing, defaults, arrays and worker-copy writes. The result
records input presence/hash, worker-file hash and selected native field values
at `native-config-sync-before-uncomposed-construction-overrides`. It does not
call these values the effective post-construction pack settings or repair the
developer's configuration.

The selected saved file has both an old `gtfoapplecoreconfig` category and the
current `gtfootherfoodmodconfig` category. Original GTFO binds the latter. Removing
its setting allows the native default; it does not import the old category's
value. Axiom preserves both input sections and introduces no legacy adapter.

## Source-backed dependency correction

`GTFOMetaItems.SHAPED_ITEM` is created by its original static initializer using
`new GTFOOredictItem((short) 0)`. It is **not** created by `GTFOMetaItems.init()`.
That later method constructs the food MetaItem, assigns registry names and calls
tool initialization. `CommonProxy.preLoad` invokes it during GTFO preInit, after
GT's material event in the selected dependency order. Moving it ahead of material
initialization would invent a different lifecycle.

Original `GTFOEventHandler.onMaterialsInit` first initializes the complete
`GTFOMaterialHandler`, applies its material modifications, then queries
`Loader.isModLoaded("gcys")` before invoking the additional GA material handler.
GTFO's separate mod-order declaration says `after:gcy_science`; do not treat
that spelling as evidence that its `gcys` query is true or false.
The material-handler static initializer contains both material declarations and
shaped-item declarations. Its helper deliberately uses the `gregtech` namespace;
storage ownership must be observed, not renamed to match the addon.

Separately, original `GregTechFoodOption.onStartup` calls `GTFOConfigOverrider.init`:

- NuclearCraft presence can disable its compatibility settings.
- Actually Additions presence can disable compatibility or mutate AA's coffee
  generation setting.
- AppleCore presence can disable its compatibility settings and log a warning.

The standalone material host's small `Loader.namedMods` map is not evidence of
the installed pack's absent mods. Until profile-bound native discovery supplies
these conditions, Axiom must not run the callbacks against fabricated false
answers, copy their assignments, extract a favorable branch, or create no-op APIs.

## Reporting and next dependency

`execution.gtfoConfiguration.prerequisites` lists the construction/material mod
queries, marks discovery unqualified, and records that the host has not registered
the material subscriber or invoked later item initialization. The trace retains
separate deferred construction, subscriber and item stages without visit timings.
`material-context.gtfo-mod-discovery-incomplete` remains a coverage gap.

`tools/axiom_pack_gtfo_configuration_conformance.py` exercises complete programs
with current/old categories, saved scalar/list changes, invalid numeric input,
missing configuration and fresh restoration, plus the unchanged whole pack.
This verifies native configuration and continued bounded material feedback—not
GTFO material execution, recipe effects, gameplay or full-pack initialization.

The [native addon candidate inventory](native-addon-discovery.md) now binds a
complete profile-selected artifact set and original configured-state observations.
Loaded states remain unresolved: native side eligibility, discovery filtering,
activation and ordering still need their actual initialization prerequisites.
Then register the complete original GTFO
subscriber in native priority/order, letting its static material/item declarations
run at their actual dependency point. Keep later item/tool initialization separate.
