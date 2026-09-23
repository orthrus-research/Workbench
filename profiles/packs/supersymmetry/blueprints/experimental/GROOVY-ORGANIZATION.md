# Supersymmetry Groovy organization

Observed authority: Supersymmetry commit
`9c056ec6481516cd064865429b8ea26fe35d0d4c`.

Supersymmetry organizes Groovy by lifecycle and domain rather than by generated
feature. The current top-level tree has reusable code in `classes/`, global
definitions in `globals/`, material registration in `material/`, lifecycle
entry points in `preInit/` and `prePostInit/`, and recipes and late mutations
in the categorized `postInit/` tree.

Material construction follows one central registration chain:

1. `groovy/preInit/MaterialChanges.groovy` listens for `MaterialEvent` at
   `EventPriority.LOWEST` and calls `SuSyMaterials.init()`.
2. `groovy/material/SuSyMaterials.groovy` owns the public static material
   fields and calls each category's `register()` method in a fixed order.
3. `groovy/material/PetrochemistryMaterials.groovy` imports those fields and
   assigns petrochemistry builders inside its single `register()` method.
4. `resources/langfiles/lang/en_us.lang` owns the shared English material
   localization; material-backed fluids are localized under `# Fluids`.

Therefore a new petrochemistry material-backed fluid is exactly three source
updates: a static field in the `// Petrochem Materials` block, a builder
assignment in `PetrochemistryMaterials.register()`, and a `susy.material.*`
entry in the central fluids language section. It does not add another
`MaterialEvent` listener, another pre-init script, or another Resource Loader
domain.

The current petrochemistry builders occupy the `20xxx` family and use IDs
`20000` through `20162`, with gaps including `20008`. The experimental pattern
uses the provisional `20000..20999` category envelope and always checks the
complete tracked Groovy census before selecting the first free ID. The
envelope is profile authority, not a universal GregTech allocation rule.

The executable anchored pattern is
[`patterns/material-backed-fluid-v1.json`](patterns/material-backed-fluid-v1.json).
It requires the three current structural anchors to be unique and rejects
duplicate Groovy symbols, registry names, localization keys, uncertain builder
parses, and occupied numeric IDs.
