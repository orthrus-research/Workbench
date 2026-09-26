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

## Interrupted completion

If the wizard exits after every ordered source replacement but before the
applied receipt reaches its final path, `workbench register WORKSPACE
--finalize-attempt PLAN_ID` asks Core to finish publication. Core reopens the
retained attempt under its private lease and verifies the selected instance,
workspace, state root, original receipt, ordered journal, retained images and
exact current source bytes. This action does not rewrite the installed source.
It is repeatable after successful promotion.

The completion action leaves partial attempts, changed source, remaining staged
files and changed retained evidence protected for review. It does not assert
Groovy compilation or runtime registration; launch checks are still required.

`workbench register WORKSPACE --resume-attempt PLAN_ID` has a narrower restart
action for a partial attempt. Core requires every source stage to have an
ordered journal marker, a prepared receipt, intact retained images, unchanged
selected roots and source parents, and no extra stage files. Completed source
edits must form a prefix with their stages consumed. Only the last attempted
edit may still show its original bytes, and then its exact staged replacement
must remain. Later unattempted edits must also retain their original bytes and
exact stages. Core records the next attempt marker before each replacement,
rechecks the source bytes, and finishes through the same applied receipt and
no-replace promotion as uninterrupted application.

The action can be repeated after an exit around a marker, replacement, receipt
or promotion. Forward resumption refuses missing stage markers, interrupted
rollback, unknown source bytes or changed retained evidence. An applied V1
receipt still means that every planned source edit was verified; it does not
claim Groovy compilation or runtime registration.

`workbench register WORKSPACE --rollback-attempt PLAN_ID` restores source from
a staged, partial attempt when Core can prove the exact retained before and after
images, selected instance, source parents, complete stage journal and ordered
attempt prefix. It restores only files still at their recorded after image,
working in reverse order. Files already at their recorded before image are
left alone. Core records rollback intent before the first restoration; after
that, `--resume-attempt` and `--finalize-attempt` refuse to move the same
attempt forward. Repeating rollback after an exit is safe while the journal
and source evidence remain intact.

The rollback result reports `source-restored`. The prepared receipt, retained
attempt, backups and incomplete stages remain in place for explicit review;
this command does not remove them. Unknown or externally changed source bytes,
missing stage marks, extra token-named stages and changed retained evidence
stop rollback without overwriting those bytes. A hard exit during creation
of a restoration stage can leave an extra stage and require manual review.
Source restoration does not establish Groovy or runtime behavior.
