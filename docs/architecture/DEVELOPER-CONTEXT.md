# Shared developer context and profile interfaces

The primary developer flow is now [IDE-first local source review](LOCAL-SOURCE-REVIEW.md).
Edit normally in the IDE, then review saved changes against an explicit baseline.
Guided construction is an optional tertiary feature, not a prerequisite for review.
Explicit [saved-candidate checks](SAVED-CANDIDATE-CHECKS.md) use the same selection
without construction plans. Only confirmation of an exact prepared check may
launch a runtime; selecting a context or reviewing source cannot do so.

The first Supersymmetry developer vertical shares an explicit selection between
recipe review, recipe/quest/material-fluid planning and runtime preparation.
It is source-first: selecting a pack or preparing reviewed bytes does not
install Java, launch Minecraft, modify the pack or grant profile qualification.

## Selection and observations

A selection identifies a pack checkout, optional constituent-mod checkout,
native pack/platform profile IDs and a profile variant. It is immutable and
retained in an existing Work Session header. There is no global last-project
pointer, agent-control file, new database or tracked marker in the pack.
Select another session to switch projects. Exact session IDs are required;
`latest` is deliberately not a developer-context selector.

Each operation takes a fresh Project Intelligence observation. It binds the
Git revision/index and current tracked/non-ignored source bytes, selected
profile documents/resources and native owner code digests. Successive dirty
edits therefore have different observations even when Git status looks the
same. Relevant source drift rejects an old reviewed plan; it does not revoke
profile qualification. Observations describe inputs, not an execution lease.

Shell's immutable `DeveloperOperation` is a composition value, not a parameter
that profiles import. CLI and service compositions use the same selection and
observation functions. Existing domain records retain their own exact-input,
transaction and evidence validation. Runtime availability remains separate
from source applicability and release/support qualification.

## Use the first vertical

With Shell and the Supersymmetry/Cleanroom profiles installed:

```bash
workbench context select /path/to/Supersymmetry \
  --pack-profile supersymmetry --platform-profile cleanroom \
  --variant cleanroom-provisional
workbench context show SESSION_ID
workbench context run SESSION_ID -- review recipes --baseline-ref HEAD
workbench context run SESSION_ID -- feature options recipe-change
workbench context run SESSION_ID -- feature plan recipe-change \
  --recipe-script groovy/postInit/chemistry/ExistingOwner.groovy \
  --recipe-map mixer --fluid-input '{"name":"water","amount":1000}' \
  --fluid-output '{"name":"steam","amount":1000}' \
  --duration 100 --voltage-tier LV
workbench context prepare SESSION_ID /path/from/owner_record_ref/to/plan.json
workbench context show SESSION_ID
```

Use real existing owners and supported ingredients from your pack; the example
is not a generally applicable recipe. Recipe construction remains **add-only**.
`context run` also accepts `feature options/plan quest-for-process`,
`feature options/plan material-fluid-recipe`, `runtime-plan`, and
[source search/inspect/related/location](SOURCE-NAVIGATION.md).

`context --state-root /private/state ...` selects private retained storage.
It must be outside the selected source checkout. Plan and preparation references
are appended to the selected Work Session with its existing optimistic
concurrency checks. A concurrent append conflict retains the owner record and
returns its reference; it does not overwrite journal state or claim completion.
An explicit action workspace overrides the selection for that operation and
is not attached to a session for a different project.

Preparation stages an exact baseline and reviewed candidate in private state,
binds the observer protocol, and records `prepared-not-run`. It does not start
a runtime. Source application and actual comparison still require the existing
owner commands and explicit consent. The current close-observed recipe runtime
comparison is client-only; preparation proves neither dedicated-server parity
nor gameplay, stable support or publication readiness.

## Ownership and native packaging

| Responsibility | Owner |
| --- | --- |
| Host installation, processes, cancellation, storage and cleanup | Core |
| Selection, navigation, owner references and orchestration | Shell |
| Workspace inspection and exact tracked-source projection | Project Intelligence |
| Construction contracts, reviewed-delta staging and transactions | Blueprints |
| Recipe observation consumer contract | Atlas |
| Saved-check, runtime-pair and explicit execution-service contracts | Crucible |
| Supersymmetry renderers, interpreters and pack/runtime policy | Supersymmetry profile |

Recipe/quest constructors, material/fluid interpreters and runtime-pair owners
are ordinary modules in `workbench_profile_supersymmetry`, registered through
native entry points. These migrated owners are not duplicated as executable
package data and have no loose-file fallback. Profiles do not import Shell or
Core implementation; Shell supplies explicit per-invocation collaborators.

API performs profile/distribution admission; Blueprints, Atlas and Crucible
validate their consumer contracts. Missing, duplicate, disabled, incompatible
or dependency-broken profiles reject the affected operation. Admission is
checked again for cached providers. Native owner identity includes package
source bytes, not just a version number; changed loaded code requires a host
restart. Generic Core/control capabilities remain independent.

Future modules should consume these boundaries instead of introducing another
context store, path loader or reverse profile-to-Shell import. The
[shared source-intelligence interface](SOURCE-NAVIGATION.md) joins recipe,
material and quest navigation while keeping static analysis and runtime
evidence distinct. Local source review and saved-candidate checks consume that
foundation without Blueprints.
