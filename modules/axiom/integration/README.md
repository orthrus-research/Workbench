# Workbench integration boundary

Implemented in [the native integration package](../src/workbench_axiom/__init__.py).

The module ID is `axiom`; installed Python metadata registers
`axiom check`, `axiom query`, `axiom coverage`, `axiom target`, `axiom platform`
and `axiom material-program`.
The package validates the exact installed JAR inventory and compatibility,
then invokes Java through `workbench_api.processes`. Core binds that port to
its existing process supervisor. No recipe, matching or machine rules live
in Python.

`engine-contract.json` is an installed resource naming the compatible engine
range, protocol and main class. Release inventory includes separate native
Java-engine and Python-integration components.

Invocation uses explicit `--engine-home` and profile-pinned `--java` paths. Java
verifies its installed Cleanroom-owned runtime policy before evaluation. It does not
guess cache locations, download dependencies or use a Minecraft installation.
Core owns process cancellation and temporary request cleanup. Java owns
source isolation and domain limits.

The result is forwarded without reinterpreting recipe validity; integration
adds only installation/request custody. Target selection is explicit.
The Supersymmetry profile does not yet supply a complete qualified target package.
It does supply a packaged source-capture policy through `workbench.axiom_targets`.
`axiom target --profile supersymmetry` binds that installed policy to the
independently inspected archive; see [source targets](../spec/source-targets.md).
Optional composition side/options travel in that same request; Java inspects
the dependency declarations and reports gaps. The profile bridge does not
parse TOML, select an implicit platform, or infer installed mod activation.
`--artifacts` forwards one explicit offline bundle path through the same Core
process port. Java verifies all bytes and declarations; Python loads no mod code.

The Cleanroom profile exposes its native platform lock through the same extension
group. `axiom platform --profile cleanroom` verifies the selected archive against
that installed owner. Combined `axiom target --platform ... --platform-profile
cleanroom` binds both explicit owners; there is no implicit platform substitution.
Policy resources are rechecked after invocation, not only their Python adapters.
See [platform inputs](../spec/platform-inputs.md).

`axiom material-program` binds `material_admission(context_id)` from the selected
installed profile, plus an explicit native runtime directory and complete saved
source ZIP. The bridge injects and rechecks profile policy identities, while Java
owns source admission, isolated native execution and diagnostics. This currently
returns observations, never accepted material validity. It is not yet integrated
with retained saved checks or IDE navigation. See
[material-program preflight](../spec/material-program-preflight.md).

Material requests can include native observation identities and typed developer
expectations. Optional `--baseline-program` requests independent baseline/candidate
workers; Python forwards their domain results unchanged and uses Core's bounded
75-second paired invocation. A native-clean candidate can still miss an explicit
expectation; neither matched expectations nor unchanged replay grant qualification.

Removing the Python package must not remove developer sources, engine
installations selected by explicit path, retained results or Core functionality.

See [module conventions](../../../docs/guides/module-development.md),
[architecture](../spec/architecture.md) and [usage](../spec/first-slice.md).
