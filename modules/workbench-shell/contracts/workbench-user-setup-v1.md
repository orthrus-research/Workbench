# Workbench user setup V1

Status: first user-facing setup contract

## Purpose

`workbench setup` connects an installed Workbench command to one user's local
workspace and tools. It does not replace `workbench.toml`, select a pack on the
user's behalf, or copy profile-owned versions and policy into another source of
truth.

Plain setup offers three explicit journeys:

- **Full developer** is the recommended default. It asks for the project
  workspace and an existing Workbench Configuration V1 manifest, then uses
  that profile authority to validate or provision the exact required Java.
- **Review-only** defaults the workspace to the current directory and
  deliberately selects no runtime or pack profile. Source and recipe review
  can therefore be configured without treating Supersymmetry as universal,
  but the human result remains visibly
  `REVIEW-ONLY · DEVELOPER SETUP INCOMPLETE`.
- **Repair** reuses the last saved selection and rechecks it before offering a
  new plan.

The preflight checks `PATH`, the active Workbench runtime, and bounded
conventional host directories before the first wizard question. It passes the
exact validated Git executable into either setup journey. Full developer setup
can explicitly select:

- a workspace;
- an existing Workbench Configuration V1 manifest;
- the Workbench-owned runtime state root;
- an existing Java home; and
- an existing Git executable.

The directory in which setup starts is a default, not an authority. A later
workspace answer is retained through check, plan, apply, the saved record, and
the displayed next command.

## Locations

Current fresh setup stores this V1-shaped record in the stable user
configuration home, `~/.workbench/setup-v1.json` (or
`%USERPROFILE%\.workbench\setup-v1.json` on Windows). This is a compatibility
record beside the stable-named [user settings](../../../docs/architecture/USER-PREFERENCES.md).
The paths below remain legacy read locations for existing installations.
`workbench settings migrate` copies their validated records into the stable
home without deleting the originals.

The older setup record is outside the selected project and installed package
under the platform user configuration directory:

- Linux: `$XDG_CONFIG_HOME/workbench/setup-v1.json`, defaulting to
  `~/.config/workbench/setup-v1.json`;
- macOS: `~/Library/Application Support/Workbench/setup-v1.json`; and
- Windows: `%LOCALAPPDATA%\Workbench\setup-v1.json`, with `%APPDATA%` as the
  fallback when local application data is unavailable.

`WORKBENCH_CONFIG_HOME` explicitly replaces the parent directory. This is a
physical location override only; its contents remain subject to this contract.
The record is a bounded regular non-symlink UTF-8 JSON file and is published
atomically with user-only creation permissions where the host supports them.

## Record

The record format is `workbench-user-setup-v1`, schema version `1`. It contains
only `format`, `schema_version`, `record_id`, and `selection`. `record_id` is
`workbench-user-setup:sha256:` plus the SHA-256 of canonical JSON containing the
other three fields.

`selection` has exactly:

| Field | Meaning |
| --- | --- |
| `workspace` | Absolute selected workspace directory. |
| `state_root` | Absolute Workbench-owned runtime state directory. |
| `profile_config` | Absolute explicit Configuration V1 manifest, or `null`. |
| `profile_selection_digest` | The configuration owner's exact selection digest at consent, or `null`. |
| `java_home` | Explicit user-selected Java home, or `null`. |
| `managed_java_home` | Verified Workbench-managed Java home, or `null`. |
| `git_executable` | Explicit user-selected or consented preflight-selected Git executable, or `null` when no validated Git binding is available. |

The selection shape is shared by setup checks, plans, and the V1 record. On a
current fresh host where preflight cannot find Git, setup carries
`git_executable: null` into the check and plan so the missing dependency and
its OS-aware repair guidance remain explicit. Git is required before first
setup, so that plan is blocked and a successful fresh apply cannot publish the
null binding; rerunning setup after Git is available records the exact
validated executable. A retained V1 record with no Git binding remains
readable, and its Git requirement stays flow-conditional as described below.

The record does not contain credentials, account information, Java or
Cleanroom version declarations, pack policy, artifact locks, or copied profile
documents. A changed explicit profile selection fails closed until the user
reviews it through Customize setup.

## Subsequent command defaults

The top-level `workbench` launcher validates the saved record before normal
actions and applies only physical defaults. Workspace precedence is:

1. an explicit workspace or target supplied to the current command;
2. the saved, reviewed User Setup V1 workspace; and
3. an inherited bootstrap `WORKBENCH_WORKSPACE` value or the command's normal
   current-directory fallback when no saved record exists.

The saved workspace intentionally replaces an inherited
`WORKBENCH_WORKSPACE`: the environment value can describe the earlier Pixi or
launcher start directory, while the saved record contains the user's later
setup choice. This exception is confined to workspace selection. For the
other physical bindings, an existing caller environment continues to win over
the saved value.

After that precedence is resolved:

- the saved workspace is used by no-argument Home/Open and Doctor when the
  caller does not provide a workspace;
- the saved state root becomes the process-local Workbench state default;
- the saved Git executable is used by Workspace Doctor and Git ref/PR-base
  Recipe Review instead of ambient `PATH` discovery; and
- the saved existing or managed Java home becomes the process-local
  `WORKBENCH_JAVA_HOME` candidate used by Configuration V1.

The launcher does not change the process working directory or the user's shell
environment. Most importantly, it never turns `profile_config` into an
implicit action argument, pack selection, or runtime authority. Actions that
require a profile continue to require their exact profile/configuration input.

Setup, version, and help remain callable when a retained setup record is
invalid so the user can inspect and repair it. Other actions fail closed rather
than silently ignoring a corrupt or substituted record.

## Read-only check and dependency states

`workbench setup --check` reads the record and current host. It never creates a
directory, downloads an artifact, installs a dependency, or rewrites the
record. When the selected workspace already exists, `--json` continues to emit
`workbench-setup-check-v1` with schema version `1` verbatim. When that exact
workspace is absent, the additive `workbench-setup-check-v2` with schema
version `2` reports it as `missing-installable` and says that creation is
available only through a reviewed apply. No V1 meaning is widened to admit an
absent directory.

Every dependency is reported as one of:

- `ready`;
- `missing-installable`;
- `missing-manual`;
- `incompatible`; or
- `not-needed`.

The additive host-requirements preflight treats Git as a baseline before first
setup because acquisition, repository work, and commit/ref/PR review depend on
it. The CLI uses V1's existing required-Git dependency mode for that first
check, so missing Git remains represented as the normal dependency row instead
of replacing the setup protocol. The envelope is V1 for an existing workspace
or the absent-workspace V2 described above. It points a missing executable to
`workbench repair`. For an already configured record, Git remains
flow-conditional unless `--require-git` is passed; the exact-directory review
engine therefore retains its Git-free compatibility path. Setup itself never
invokes a host package manager. Pixi is required for source-checkout
environment repair and is not a packaged runtime dependency.

Java is required for the full developer journey. Workbench does not guess its
version: the user must explicitly select a Configuration V1 manifest, whose
platform profile supplies the Java policy. Review-only setup intentionally has
no such authority. Its unchanged V1 structured dependency remains
`not-needed`/not required for that bounded review-only selection, while the
human view labels Java `REQUIRED FOR DEVELOPMENT` and labels the whole journey
`REVIEW-ONLY · DEVELOPER SETUP INCOMPLETE`. It must not present that state as a
complete developer environment.

Project Intelligence Doctor supplies the bounded workspace observation.
Environment Status supplies source-versus-packaged execution and Pixi
observation. The selected platform profile and Runtime Java authority retain
Java policy and installation ownership.

## Human presentation

Human setup, environment-status, and repair output uses a shared compact status
vocabulary. Bracketed words and, where applicable, glyphs carry the meaning;
color is only an additive scan aid. Interactive terminals may show good states
in green, attention/installable states in yellow, and missing/blocked states in
red. Redirected output, `TERM=dumb`, and any environment containing `NO_COLOR`
emit no ANSI control sequences and retain the same state labels. Structured
JSON is unchanged by presentation policy.

## Plan and consent

When the selected workspace already exists and the saved setup already matches,
`workbench setup --plan` continues to return `workbench-setup-plan-v1`. Its
`plan_id` binds the selected paths, dependency observations, blockers, and exact
proposed actions. Planning is read-only.

For an absent selected workspace whose saved setup already matches, the
additive `workbench-setup-plan-v2` binds the same information plus one exact
`create-workspace` action. That action may create the selected directory and
its missing parents, but only after consent. The nearest existing ancestor
must already be a regular non-symlink directory. Apply revalidates the path and
removes only the exact empty directories that this operation created if a later
step fails.

Any plan that would create or replace the user setup record instead uses
`workbench-setup-plan-v3`. In addition to the reviewed selection and actions,
V3 binds either the exact prior setup `record_id` or the fact that no record
existed. Apply checks that expectation again while holding the setup-record
writer lock and fails without replacing a concurrent setup change. A successful
workspace-creation action still produces `workbench-setup-result-v2`, which
adds the exact created-workspace list. Existing-workspace operations continue
to produce `workbench-setup-result-v1`.

The V2 check/plan/result envelopes exist only for absent-workspace creation;
V3 is the compare-and-swap plan for setup-record mutation. Neither revises the
`workbench-user-setup-v1` record, Configuration V1, or any other
identity-bearing V1 format.

The selected state root has one canonical physical identity in inventory,
plan, and apply. Every existing path component must be a regular directory;
symbolic links, Windows junctions, and other reparse points are rejected.
Apply repeats this custody check immediately before a managed dependency may
write beneath the state root, so an alias introduced after review fails closed.

Interactive setup prints the inventory and plan, then asks
`Apply this setup? [Y/n]`. Empty input, `y`, or `yes` accepts the displayed
plan. `n` or `no` opens Customize using the immediately preceding values as
defaults; Workbench then checks and renders a new exact plan before asking
again. EOF cancels without mutation. A plan with a manual or incompatible
required dependency cannot be applied.

For non-interactive use, the caller first reviews `--plan --json` and passes
the complete, exact `plan_id` to `--apply`. A stale or substituted plan fails
before mutation. Plain setup without a TTY fails with guidance to the
read-only plan/apply route; it never assumes consent.

## Managed Java

The first managed dependency is the profile-selected Eclipse Temurin runtime.
It is offered only after the user explicitly selects a Configuration V1
manifest whose platform profile carries a supported provision policy. Apply
delegates download, checksum validation, extraction, runtime probing, receipt,
and reuse to Runtime Java.

Workbench installs the JDK beneath the selected state root. It does not change
system `PATH`, `JAVA_HOME`, an IDE's Java setting, or a launcher account. A
compatible explicitly selected Java home prevents the managed install.

If a Unicode Windows state path requires the verified ASCII short-path
execution route, the managed-install action also binds
`workbench-java-portability:windows-unicode-default-cds-removal-v1`. Its human
effect explains that Workbench will remove only Temurin's optional startup
cache archives in staging because that host cannot use them reliably, while
the Runtime Java V2 receipt records each removed file. Apply rejects a receipt
whose transform ID differs from the reviewed action. Canonical execution plans
bind no portability transform and retain the vendor archives.

After any managed operation, setup repeats dependency checks. It atomically
publishes the new setup record only when every required dependency is ready.
An install or verification failure preserves the previous valid record.

## Commands and exit status

```text
workbench setup
workbench setup --check [--json]
workbench setup --plan [--json]
workbench setup --apply PLAN_ID [--json]
workbench setup --repair
```

Workspace, profile configuration, state root, Java home, and Git executable
can also be supplied as explicit options. Relative profile configuration paths
are resolved against the Workbench suite root, matching Configuration V1.

Exit `0` means a plan was rendered, setup was cancelled cleanly, an exact plan
was applied, or a saved setup check is ready. Exit `1` means a check is not yet
configured/ready or an interactive plan has unresolved blockers. Exit `2`
means invalid input, unsafe retained state, a stale plan, or a failed owned
operation. Ctrl+C exits `130`, prints one concise cancellation message, and
does not publish a partial setup record. If setup created empty workspace
directories for the accepted operation before the interrupt, it removes only
those exact operation-owned directories.

## Current limits

- Setup never invokes an ambient package manager. The additive
  [Host Repair V1](workbench-host-repair-v1.md) may run one exact detected
  Linux or Windows Git installation command only after a separately reviewed
  plan and explicit consent. Pixi installation remains outside user setup.
- Launcher discovery and account setup remain outside this unchanged V1
  record. The additive [Launcher Setup V1](launcher-setup-v1.md) records a
  credential-free physical binding; login remains launcher-owned.
- The workspace Doctor observation can contain warnings without blocking
  review-only setup. Dependency blockers remain explicit in the setup check.
- Configuration V1 continues to select only suite-owned profile documents. A
  future configuration version, not this setup record, must define additional
  profile authority.
