# Managed run profiles

Status: experimental dedicated-server vertical slice

Managed run profiles turn the existing disposable worldgen loop into named,
pack-owned developer actions without creating a second runtime or proof
authority. The first catalog belongs to Supersymmetry and exposes `fast`,
`debug`, `worldgen`, `performance`, and an explicitly unavailable `proof`
recipe.

## Use

Preview the exact target, stages, intended mutations, JVM settings, and
copyable command without creating an iteration:

```bash
python3 tools/workbench.py run fast --profile supersymmetry --show
python3 tools/workbench.py run performance --profile supersymmetry --json
```

Execute a ready or attention-level plan by omitting the preview flag:

```bash
python3 tools/workbench.py run fast --profile supersymmetry
python3 tools/workbench.py run worldgen --profile supersymmetry
```

Every invocation requires an explicit pack profile. `--show` and `--json` are
read-only; the default form delegates the resolved arguments to the existing
`workbench worldgen dev` runner.

Managed V1 always builds current source and uses the Groovy plan that Doctor
inspected from the selected pack profile. The lower runner's `--skip-build`
and arbitrary `--plan` overrides are intentionally not exposed until a later
plan contract can bind their alternate artifact or script truthfully. The
one-time command carries an exact iteration label; the reproduction command
omits that label so a later run can allocate a fresh destination.

## Composition and authority

The Workbench Shell first obtains a complete
[Workspace Doctor](WORKSPACE-DOCTOR.md) report. Crucible consumes that report,
the pack-owned catalog, and the exact worldgen profile to produce a closed
[`managed run plan V1`](../../modules/crucible/contracts/managed-run-plan-v1.md).
The plan binds the catalog, recipe, platform candidate, runtime template,
toolchains, Groovy plan, runner bytes, Doctor status, and effective operation.

Responsibilities remain separate:

- the pack profile owns recipe defaults and availability;
- Project Intelligence and the Doctor envelope own workspace context;
- the existing Crucible iteration runner owns provisioning and execution;
- Atlas and the Worldgen Observatory retain causal proof authority; and
- the Shell composes these records but creates no alternate truth or approval.

The generic planner never imports Project Intelligence or assumes
Supersymmetry. Preview performs bounded read-only discovery and hashing only.
Execution refuses an unavailable or Doctor-blocked plan. Immediately before
delegation, the Shell recomposes Doctor and the complete plan and requires the
plan identity to remain unchanged; the executor then rechecks bound file and
runtime JAR-inventory hashes.

## Current recipe projection

| Recipe | Existing runner projection | Current status |
| --- | --- | --- |
| `fast` | `fast` mode and the pack's smallest region | Available |
| `debug` | `debug` mode and retained bounded diagnostics | Available |
| `worldgen` | `debug` capture plus the checked Strata viewer handoff | Available |
| `performance` | `performance` mode with JFR and bounded summary | Available |
| `proof` | Separate Worldgen Observatory workflow | Unavailable in managed run V1 |

V1 supports only the exact dedicated-server worldgen target. Client and
integrated-server profiles, debugger attach, automatic acquisition, storage
reclamation, and a dedicated Observatory proof launcher remain future work.
An available read-only plan predicts neither build success nor game behavior;
the delegated runner still fails the iteration on any required-stage error.
