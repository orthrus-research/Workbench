# Pack Program Studio

Pack Program Studio is Workbench's GroovyScript-first view of an executable
modpack scripting layer. It keeps static source analysis, compiler diagnostics,
managed-client observation, and runtime registration signals distinct.

The generic analyzer lives here. GroovyScript lifecycle and cache behavior
belong to the Cleanroom platform profile; Supersymmetry paths, identity rules,
and diagnostic policy belong to the Supersymmetry pack profile.

## Current capabilities

The product catalog exposes four owner commands and two reviewer-facing front
doors that compose them with Project Intelligence:

| Capability | Command | What it establishes |
| --- | --- | --- |
| `pack-program.groovy-dev` | `workbench groovy dev` | A bounded, comment-aware static program model and optional baseline comparison. |
| `pack-program.groovy-check` | `workbench groovy check` | Diagnostics from GroovyScript's embedded compiler over exact supplied bytes. |
| `pack-program.groovy-session` | `workbench groovy session` | One managed disposable client and shared language-service endpoint. |
| `pack-program.recipe-invalidations` | `workbench diagnose recipe-invalidations` | Bounded Supersymmetry runtime registration-signal groups. |
| `review.pull-request` | `workbench review pr` | Consented provider-bound PR preparation followed by an immutable historical recipe-delta review. |
| `review.recipes` | `workbench review recipes` | Non-mutating recipe analysis over one explicit baseline, with an optional fresh report. |

Use `workbench capabilities "recipe review" --json` for the reviewer routes or
`workbench capabilities pack-program --json` for the full suite, including
exact current risks and limitations.

## Inspect a Groovy program

```bash
workbench groovy dev \
  --profile supersymmetry \
  --source /path/to/Supersymmetry

workbench groovy dev \
  --profile supersymmetry \
  --baseline /path/to/baseline \
  --source /path/to/candidate \
  --changed postInit/chemistry/Catalysts.groovy \
  --json \
  --output .workbench/evidence/groovy-program/catalysts.json
```

The report binds selected source and `runConfig.json` bytes, profile identity,
side, pack mode, Git context when available, and optional runtime evidence. It
provides loader order, dependency and cycle information, conservative call and
effect candidates, bounded material/recipe projections, baseline differences,
and reload/restart/save-risk guidance.

Static findings remain `static-candidate` or `static-possible`. The analyzer
does not execute Groovy, observe registries, or expand arbitrary closures,
reflection, metaclass behavior, dynamic dispatch, or unbounded loops.

## Check with the exact runtime compiler

```bash
workbench groovy check \
  --profile supersymmetry \
  --source /path/to/Supersymmetry \
  --runtime-root /path/to/client/.minecraft \
  --file postInit/chemistry/Catalysts.groovy \
  --strict
```

Workbench inventories the selected runtime, reads the exact in-memory source,
and sends it to GroovyScript's embedded language server. A deliberate syntax
canary distinguishes an explicit empty diagnostic publication from endpoint
silence. This establishes compiler canonicalization only; it does not prove
script execution or authenticate an independently supplied TCP endpoint.

## Start a managed language session

```bash
workbench groovy session \
  --profile supersymmetry \
  --source /path/to/Supersymmetry \
  --runtime-root /path/to/disposable-instance/.minecraft \
  --launch-receipt /path/to/runtime-launch-v3.json \
  --json-events
```

The manager accepts a completed Workbench V3 launch receipt for a disposable
Prism projection, reserves a loopback port, applies checked temporary overlays,
proves compiler readiness, publishes terminal/IntelliJ/VS Code endpoint data,
supervises the exact process, and restores owned configuration on stop.

## Recipe review

The separate reviewer-facing route reuses the same bounded analyzer:

```bash
workbench review recipes \
  --profile supersymmetry \
  --source /path/to/candidate \
  --pr-base origin/master-ceu \
  --side dedicated-server
```

Select exactly one directory baseline, exact local Git baseline, prepared PR
receipt, or unique local merge base. Git modes never fetch unless the separate
provider-bound preparation flow has received explicit consent. The review may
pair uniquely corresponding property-only changes for presentation, but
ambiguous rows remain separate. It does not compile or execute Groovy, mutate
the candidate checkout, or claim that a source statement ran.

For a remote GitHub pull request, `workbench review pr` first emits a
provider-bound plan. Only `--yes` or the exact current `--apply PLAN_ID`
authorizes provider re-observation, fetching, immutable Workbench refs, and a
retained receipt; analysis then runs against those exact base and head objects
without switching the checkout.

## Authority and storage

Atlas remains the observed and derived knowledge authority; Blueprints remains
the construction authority. Runtime logs can be correlated only within their
declared scope and never upgrade static candidates into executed effects.

Generated reports, temporary Git projections, runtime inventories, language
sessions, logs, and receipts belong under ignored `.workbench/` storage or
another declared external store.

Relevant contracts:

- [static Groovy program](contracts/groovy-pack-program-v1.md)
- [source-declaration feed](contracts/source-declaration-feed-v1.md)
- [material program](contracts/material-program-v2.md)
- [language service](contracts/groovy-language-service-v1.md)
- [managed session](contracts/groovy-managed-language-session-v1.md)

Run the focused tests with:

```bash
python3 -m unittest discover \
  -s modules/pack-program-studio/tests \
  -p 'test_*.py'
```
