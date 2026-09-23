# Workspace Doctor V1

Status: implemented experimental read-only vertical slice for the exact
Supersymmetry World Studio development target

## Developer outcome

`workbench doctor` answers “what am I about to build and run?” before Gradle,
runtime provisioning, or Minecraft changes any state. Project Intelligence
discovers the bounded workspace surface; the selected Cleanroom and pack
profiles supply exact authority; Crucible audits the existing disposable
runtime input using the same code path enforced by `worldgen dev`.

The first composed target is explicit:

```bash
python3 tools/workbench.py doctor --profile supersymmetry
```

The concise view reports readiness and copyable next commands. `--json` emits
the complete
[Workspace Doctor report V1](../../modules/project-intelligence/contracts/workspace-doctor-report-v1.md),
and `--output` may retain that report at an explicit path. Report emission is
the only permitted write; the inspection itself does not build, download,
provision, launch, repair, or mutate a world. V1 refuses a symlink or existing
output path instead of overwriting it.

Running without a profile performs generic workspace-context discovery:

```bash
python3 tools/workbench.py doctor path/to/mod-project
```

Supersymmetry is never inferred as a universal default. V1 requires an
explicit profile for its pack-specific `worldgen-dev` readiness checks.

## Composition and authority

```text
Workbench command router
  -> Project Intelligence workspace discovery
       -> Gradle declarations, source/resource roots, mod descriptors
       -> Git branch, HEAD, and bounded dirty state
  -> explicit pack worldgen iteration profile
       -> exact Cleanroom candidate lock and Java/Gradle requirements
  -> Crucible read-only preflight
       -> selected runtime template
       -> copy/provision safety audit
       -> exact server jar and mod archive inventory
       -> Strata and current build-artifact availability
  -> one V1 report with proposed repairs and next commands
```

The report does not reinterpret these inputs. Project Intelligence owns the
discovered project context. Cleanroom and Supersymmetry profiles own their
version-specific declarations. Crucible owns controlled-runtime safety and
later execution. Atlas remains the authority for interpreted assembled-game
knowledge, and the separate Cleanroom Mixin Doctor owns its profile policy.

## Implemented checks

The generic layer currently records:

- nearest supported Gradle root, build scripts, wrapper declaration, plugins,
  dependencies, and declared source/target/toolchain Java;
- Git repository root, branch, HEAD, selected pathspec, and staged, unstaged,
  conflict, and untracked counts;
- conventional Java, Groovy, Kotlin, resource, generated, configuration, and
  Mixin-config surfaces; and
- Forge `mcmod.info` identities found in declared resource roots.

The `worldgen-dev` adapter additionally records:

- exact pack profile, Groovy plan, Minecraft, Cleanroom, Forge, mappings,
  maturity, candidate-lock identity, and digest;
- Cleanroom runtime, mod compiler, Gradle-control, and emitted-bytecode roles
  separately;
- selected Java and Gradle executable bytes and version output, including the
  profile minimum and proven versions;
- exactly one profile-matching runtime template, every copyable JAR digest,
  loaded `mods/**` IDs, V2 profile-required mod IDs, duplicate mod IDs,
  excluded world/run residue, unsafe links or special entries, ZIP integrity,
  and the exact server JAR;
- compatible local Strata entrypoint and current remapped artifact state; and
- the exact `worldgen dev` reproduction command plus a bounded static Mixin
  Doctor command.

The runtime audit runs before provisioning and is called again by the runner.
An unsafe link, corrupt archive, missing required mod ID, ambiguous server JAR,
or duplicate loaded mod ID therefore cannot pass Doctor and fail later because
the two paths used different rules. Provisioning uses independent file copies,
not hard links into the template.

## Findings and exits

Findings are `blocker`, `warning`, or `info`. Every finding cites bounded
evidence and contains a repair proposal that was not applied. Status is
derived: blockers produce `blocked`, warnings without blockers produce
`attention`, and otherwise the report is `ready` within its limitations.

The command returns `1` for `blocked`, `0` for `ready` or `attention`, and `2`
for invalid invocation/report construction. `--strict` also returns `1` for
`attention`. A dirty worktree and an experimental candidate are informational
for a reversible local run; neither is disguised nor promoted to a stable
claim.

## V1 boundary

V1 is not the complete wishlist doctor. It does not acquire runtimes or JDKs,
run Gradle, inspect emitted classfile bytes, launch client or server, evaluate
all Mixin rules automatically, resolve registry/runtime state, analyze logs,
prove dependency closure, or assess save compatibility. Its exact composed
readiness is limited to the existing dedicated-server Supersymmetry worldgen
profile. Integrated-client coverage and stable Cleanroom promotion remain
open.

These absences are emitted as limitations or unavailable/unresolved states,
not empty success. Later adapters can extend the same report without moving
pack facts into generic code or creating another truth store.
