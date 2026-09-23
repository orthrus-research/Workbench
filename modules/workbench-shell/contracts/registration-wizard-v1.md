# Active-instance registration wizard V1

Status: experimental executable contract

The registration wizard is the human CLI over profile-owned Blueprints. It
does not decide what Supersymmetry owns: it loads the pack registration
catalog, asks its reusable form questions, obtains live Atlas facts when a
pattern requires them, previews exact edits, and applies only after explicit
confirmation.

## Active instance

`workbench initialize WORKSPACE --instance INSTANCE` selects an installed
Prism/MultiMC instance, or its `.minecraft`/`minecraft` payload. Selection is
accepted only when all of these are exact:

- the workspace resolves to the constructible Supersymmetry profile;
- Minecraft and the profile-selected Cleanroom component versions match;
- one regular GregTech, GroovyScript, and Supersymmetry/SuSyCore runtime JAR
  is present; and
- the launcher component, stable instance configuration, payload layout, and
  required JAR identities can be retained.

Workbench rechecks that identity before every catalog, plan, and apply call.
The mutable Groovy and resource bytes are intentionally outside the selection
identity because they are the wizard's direct outputs.

## Authority and forms

The reusable form and family schema is
[`registration-catalog-v1.schema.json`](../../blueprints/schemas/registration-catalog-v1.schema.json).
Supersymmetry's instance is
[`catalog-v1.json`](../../../profiles/packs/supersymmetry/registration/catalog-v1.json).
It enforces this lookup order per family:

1. Pack Groovy constructs the registration when an observed constructor
   exists.
2. SuSyCore constructs it when Groovy is only a binding or has no constructor.
3. Pack configuration/resource data constructs it only when neither source
   authority does.

The catalog exposes all investigated families and their support states. Only
patterns marked executable are selectable; planned or source-build-required
families are never rendered through a guessed fallback.

## Plan and apply

`workbench register WORKSPACE` is an interactive wizard. `--answers FILE`
uses the same questions for machine-driven clients. Planning returns unified
diffs and does not mutate the payload. Applying:

- re-renders and rechecks every baseline SHA-256;
- accepts only regular existing UTF-8 files under the selected payload;
- retains each original file under ignored `.workbench/registrations/` state;
- replaces all targets atomically one at a time;
- verifies every resulting SHA-256; and
- restores already changed files if a later operation fails.

There is no event-listener hook, generated shadow pack, source-checkout
rewrite, or launcher projection in this flow. V1 application modifies the
selected installed payload itself.

## Shipped patterns

- `material-backed-fluid` updates `SuSyMaterials.groovy`, the
  `PetrochemistryMaterials.register()` aggregate, and the central English
  fluids localization after a live material census and ID allocation.
- `machine-recipe` appends the observed repeated GregTech builder calls to an
  existing domain-owned `postInit` script using an alias resolved from
  `prePostInit/Recipemaps.groovy`.
- `ore-dictionary-entry` appends the observed `ore(name).add(stack)` form to
  the central `prePostInit/oreDict.groovy` owner.

These patterns are still experimental. A successful file transaction does
not prove Groovy compilation, recipe-map slot compatibility, or runtime
registration; the returned plan names the required launch checks.
