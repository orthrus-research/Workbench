# Supersymmetry pack profile

Supersymmetry is Workbench's first pack profile and primary developer workflow.
The integration should help people maintain the pack they already work on:
understand its behavior, make focused changes, test them safely and explain
unexpected results. The generic framework serves those tasks, rather than
requiring developers to learn its internal ownership and evidence machinery.
Supersymmetry is never an implicit universal: every pack-specific operation
selects the `supersymmetry` profile explicitly.

The current profile and its source locks live under
[`profiles/packs/supersymmetry/`](../../profiles/packs/supersymmetry/README.md).
Run `workbench capabilities supersymmetry` for the installed core's current
public capability inventory rather than relying on counts copied into prose.

## Developer outcomes

The following is the acceptance direction for the product, not a claim that
every journey is already available end to end. Availability remains explicit
in the installed catalog; source tests, installed checks and real pack-runtime
qualification establish different things.

| Developer task | Useful outcome | Important boundary |
| --- | --- | --- |
| Open an existing checkout | Identify the pack, source repositories, dirty files, toolchain and next usable action without rewriting the project | Read-only inspection first; no implicit upgrade or profile selection |
| Find a recipe, material or machine | Navigate from an exact identity to declarations, related resources and supplied runtime observations | Source presence is not proof of registration, lookup success or client display |
| Change a script, config, asset or Java implementation | Get an exact diff, relevant diagnostics and the least expensive meaningful validation | Preserve unrelated edits; Java-owned behavior must use its real build path |
| Test the change | Choose a justified reload, restart or build in a disposable target and see progress and failure clearly | The selected lifecycle, side, pack and platform must match the change |
| Explain an unexpected result | Compare the intended change with the observed result and identify the next missing observation | Missing or unstable evidence remains unknown, not a confident causal explanation |
| Hand off or clean up | Retain a small reproducible result and safely reclaim owned generated state | Never erase the developer's source, everyday instance, worlds or retained evidence implicitly |

For example, "why did this recipe not appear?" should start with the selected
recipe and current workspace, not a requirement to assemble a large general
graph. A useful explanation separates declaration, ingredient resolution,
execution, validation, registration, final lookup and client presentation when
observations are available. It must say which stages were not observed. Static
analysis cannot invent an exact runtime insertion failure.

Fast source feedback should not require a game launch or release qualification.
Conversely, a source check or successful package installation cannot stand in
for observing the change in the actual pack. Reload suitability comes from the
selected profile and operation; a pre-initialization change must not be treated
as reloadable merely because a console command exists.

## Implementation direction

Core owns provisioning, installation, process coordination and storage cleanup.
It should not acquire Supersymmetry recipe semantics, pack defaults or business
rules as these workflows improve. The Supersymmetry profile owns its layout,
target policy and domain-specific adapters. Product modules own reusable
analysis, construction, observation and explanation. Shell and the IDE clients
compose those owners into a comprehensible workflow.

Prefer completing one narrow vertical workflow before introducing another
general-purpose studio, catalog or result store. Keep implementations that
already meet their contracts; replace duplicated dispatch, hard-coded pack
assumptions and unverified shortcuts when they obstruct the developer outcome.
Do not add legacy adapters solely to preserve an obsolete internal layout.

A workflow is ready for broader use only when it is exercised from installed
packages outside the Workbench checkout, preserves developer changes, retains
bounded failure evidence and presents the same meaning in the CLI and native
clients. Runtime claims additionally need the exact selected pack and platform.
Optional agent automation may assist, but cannot be necessary to perform any
authoritative public operation.

## What the profile owns

A pack profile binds the pack-specific authority needed for accurate work:

- pack identity, source revision, dependency topology, and owned repositories;
- source, script, resource, configuration, quest, and world layouts;
- the selected platform and runtime profiles;
- Atlas evidence, policies, supported questions, and explicit unknowns;
- Blueprint standards and allocation authorities;
- practical Manuals guidance;
- compatibility and migration policy; and
- exact fixtures and retained runtime expectations where they exist.

The profile points to these authorities and records when they apply. It does
not copy their truth into a second catalog.

## Current evidence boundary

Supersymmetry contains substantial exact source and runtime knowledge,
including recipe, machine, route, infrastructure, registration, and
presentation relationships. The capability catalog exposes only the slices
that have executable contracts and tests.

Some retained production evidence was observed under an exact historical
Forge environment. It remains authoritative for that environment and useful
for comparison, but it is not a Cleanroom baseline. A Cleanroom claim requires
evidence from the exact Cleanroom profile named by the claim.

Source written with a modern Java toolchain or transformed to older bytecode
is also only evidence about that build pipeline. It does not independently
prove Cleanroom runtime behavior.

## Construction boundary

Atlas describes what is observed and what remains uncertain. Blueprints may
construct only from a named standard whose status, profile, inputs, mutation
scope, and validation requirements are explicit.

Experimental Supersymmetry construction records may be retained as exact
evidence without becoming stable standards. A successful run for one source
revision and one feature family proves that bounded case only. Java-owned
families still require their real source and build path; Workbench does not
emulate them through an unrelated scripting hook.

A stable Supersymmetry construction capability must target a tested Cleanroom
profile. Until that standard is admitted, the product reports the capability
as experimental or unavailable instead of inventing readiness.

## Authority order

| Level | Meaning |
| --- | --- |
| Cleanroom invariant | Required by the exact selected platform |
| Dependency requirement | Required by an exact API, mod, or framework |
| Pack standard | Approved Supersymmetry construction or policy |
| Observed pattern | Present in evidence, but not automatically normative |
| Manual guidance | Evidence-backed teaching that cannot authorize code |
| Developer choice | Variation permitted by all higher authorities |

Frequency in source can motivate investigation. It cannot admit a Blueprint
standard or turn a diagnostic into policy by itself.

## Core isolation

Generic Workbench code must not assume that another pack:

- uses Supersymmetry paths or identifiers;
- shares its Groovy lifecycle or dependency set;
- uses GTCEu, its allocation ranges, or its recipe and machine semantics;
- selects the same Cleanroom profile; or
- accepts Supersymmetry implementation standards.

Reusable behavior belongs under `modules/`. Pack and platform authority
belongs under `profiles/`. A later pack earns support by supplying its own
exact authorities without weakening identity, uncertainty, or validation.

## Profile acceptance

A capability is ready for this profile only when a developer can identify the
exact workspace and platform, understand the relevant evidence frontier,
perform the operation through a tested public surface, review every intended
mutation, and retain enough construction or runtime identity to diagnose a
failure.

The profile remains incomplete wherever those outcomes depend on an unstated
platform assumption, missing authority, fabricated standard, or untested
workflow.
