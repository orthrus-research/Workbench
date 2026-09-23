# Developer experience

Workbench is one local product with three first-class surfaces: the CLI, the
VS Code extension, and the IntelliJ Community plugin. The IDE clients present
the independently runnable core; they do not reimplement its authority,
planning, mutation, or validation logic.

The installed capability catalog is the current inventory:

```text
workbench capabilities
workbench capabilities QUERY
```

It reports availability, authority, profile scope, interface, and supporting
documentation without requiring an IDE or an agentic workflow.

## Experience principles

- Begin with developer intent and exact workspace context.
- Show which platform, pack, source, and evidence identities apply.
- Distinguish observation, guidance, experimental construction, stable
  construction, and policy.
- Preserve unavailable, unresolved, bounded, conflicted, and truncated states.
- Make planned writes and validation visible before consent.
- Keep the CLI capable of every authoritative operation.
- Produce equivalent meaning through both IDE clients.
- Store generated state, captures, worlds, and sessions outside source.

## Core journeys

### Identify a project

Project Intelligence inspects or acquires a workspace, resolves an explicit
profile, and reports its build, repositories, source layout, dependencies,
dirty state, and applicable capabilities. Qualification is read-only until a
developer applies a named reviewed setup plan.

The result must expose missing authority rather than guessing from folder
names or treating Supersymmetry as an implicit default.

### Understand an existing system

A developer may begin with a symbol, registry name, material, recipe, machine,
resource, configuration value, runtime record, or presentation entry.
Workbench links exact identities and relevant Atlas answers while preserving
profile, side, snapshot, evidence state, limits, and source navigation.

Manuals may explain the result. A Manual cannot turn an observation into a
construction standard.

### Plan and review a change

A construction flow states whether it uses an admitted standard or a labeled
experimental pattern. It resolves required and derived parameters, checks the
target identity, lists affected files and relationships, and produces a
complete reviewable diff before mutation.

Equivalent inputs reach the same core plan through the CLI or either IDE. An
editor quick action is not consent.

### Apply and verify

Application requires an exact reviewed plan, authorized target, and drift
check. Workbench records what it intended to change and runs validation in
proportion to risk. A compiler error, failed required check, corrupt output,
or runtime crash remains a failure.

Reversible local experiments do not require heavyweight release proof, but
they remain clearly experimental and scoped to their observed result.

### Diagnose runtime behavior

Runtime planning, materialization, observation, and diagnosis are separate
steps. Workbench preserves the exact Cleanroom, Java, pack, side, launcher,
payload, target, and receipt identities involved.

Diagnostics distinguish causal evidence, correlation, static candidates, and
unresolved frontiers. Controlled validation uses disposable or explicitly
authorized environments, never an irreplaceable developer world by default.

### Review pack changes

Recipe review and pull-request preparation are explicit review flows over
exact source and profile context. They may summarize impact and produce
review artifacts, but do not publish, merge, or grant remote authority.

## Surfaces

### CLI and automation

The CLI is the reference headless surface for local use, testing, and CI. It
is not a reduced-trust backdoor: profile selection, review, consent, evidence,
and validation boundaries remain intact.

### VS Code

The extension starts or locates the local core, uses its published protocol,
and presents capability discovery, review, diagnostics, retained records, and
navigation in the workspace. Its package and version are released
independently from the core.

### IntelliJ Community

The plugin uses Community-compatible APIs and the same local core contracts.
Authoritative functionality does not require a commercial IDE feature. Its
package and version are released independently from the core and VS Code
extension.

The shared client boundary is described in the [IDE plugin
architecture](../architecture/IDE-PLUGIN-ARCHITECTURE.md).

## Visible context

Every surface should keep these facts inspectable when relevant:

- selected Cleanroom and pack profiles;
- workspace, revision, dirty state, and authorized target;
- capability availability and maturity;
- evidence scope and freshness;
- limitation or conflict state;
- planned writes and consent boundary;
- validation and runtime status; and
- navigation to the owning record or documentation.

Graphs, summaries, and status colors may clarify this context. They must not
replace it.

## Developer control

The developer supplies intent and explicit choices, reviews material plans,
authorizes mutations, sees blocking conflicts and failed checks, and can
inspect the evidence supporting a result.

Optional automation may reduce typing or help explain output, but the public
workflow never depends on an agent, prompt file, or private coordination
record. Deterministic commands, contracts, schemas, and tests remain the
authoritative path.

Workbench succeeds when convenience does not change meaning between surfaces,
hide a selected profile, collapse unknown into absent, treat a suggestion as
a standard, mutate without consent, or separate a claim from its evidence and
limitations.
