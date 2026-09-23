# Workbench host repair V1

Status: current host-requirements and repair contract

## Purpose and ownership

`workbench repair` checks and repairs the baseline system tools needed before
Workbench user setup. It owns host discovery, one reviewed native installation
command, and rebinding an already validated executable. It does not own pack or
platform policy, Java versions, Pixi locks, launcher accounts, project source,
or user shell configuration.

User Setup V1 remains the only owner of the selected physical Git binding.
Repair updates only its existing `git_executable` field and preserves every
other selection field verbatim. The update requires the exact setup record ID
seen during planning and is serialized with the setup writer's sibling lock,
so a concurrent setup change fails before overwrite. The binding writer also
repeats the Git probe under that lock and requires the executable identity
observed immediately before the update.

## Pre-setup discovery

Before the first setup wizard, Workbench first probes these caller-owned
selections in order:

1. an explicit Git executable;
2. a saved setup binding; and
3. a `WORKBENCH_GIT_EXECUTABLE` binding.

It then uses a host-specific fallback order. Linux checks `git` on the current
`PATH`, bounded conventional Linux directories, and finally Git beside the
active Workbench Python runtime. Windows checks bounded conventional Windows
directories first, then `git` on the current `PATH`, and finally Git beside the
active Workbench Python runtime. Checking conventional Windows locations before
`PATH` prevents a Git bundled into the packaged runtime from masking an
installed host Git. Other detected families check `PATH` and then the active
Workbench runtime.

The Linux directory set is the user's `.local/bin`, `/usr/local/bin`,
`/usr/bin`, and `/bin`. The Windows set covers Git's `cmd` and `bin`
executables below native and 32-bit Program Files, the local
`Programs\Git` installation, Chocolatey, and Scoop. Workbench does not walk a
drive, search the registry, execute a shell, or accept a candidate merely
because its filename is `git`; each candidate must execute `--version` and
report one bounded, single-line UTF-8 Git version within five seconds. The
probe content-measures the executable before and after execution and rejects
output overflow or identity drift.

The selected absolute executable is passed into Setup V1. This makes later
repository flows independent of incidental `PATH` changes.

## Host and package-manager policy

The repair router recognizes Linux and Windows. This family signal is not a
support claim and does not promise that a usable package manager exists. Linux
reads the bounded resolved `/etc/os-release` file to choose a conventional
manager order, then detects Apt, DNF, Yum, Zypper, or Pacman. A non-root command
is wrapped in the exact detected `sudo` executable; without it, repair remains
manual.

Windows x86-64 prefers Winget and falls back to Chocolatey. Workbench supports
Winget's fixed zero-byte WindowsApps execution alias by binding its filesystem
identity before apply. Winget is bound to the exact `Git.Git` package and
includes package/source agreement flags. Chocolatey is offered only when the
current process is already elevated; repair does not synthesize an elevation
prompt. Windows ARM64 can discover and bind an existing Git but keeps automatic
installation manual until a native route is qualified. Other operating systems
receive read-only discovery and explicit manual guidance; V1 makes no
automatic repair claim for them.

The manager's presence is an implementation capability, not release or support
qualification. Native cold-host qualification remains a separate release gate.

## Check, plan, consent, and result

The read-only check format is `workbench-repair-check-v1`. It reports:

- the detected system, machine, OS family, and Linux distribution identity;
- the setup record path and exact record ID when configured;
- each bounded Git candidate checked through the first usable match, with its
  provenance and probe state; and
- either the validated Git executable or the available/manual repair route.

The human check, plan, and result use the shared compact bracketed status
labels. Green, yellow, and red are additive scan aids only when the destination
is an interactive terminal. Redirected output, `TERM=dumb`, and `NO_COLOR`
retain the same words without ANSI control sequences; structured JSON is
unchanged.

The plan format is `workbench-repair-plan-v1`. Its content-addressed `plan_id`
binds the host observation, setup identity, requirement observation, exact
actions, exact package-manager argv, and blockers. Planning never runs the
command.

Interactive apply requires `apply <short-plan-token>`. Automation passes the
complete plan ID with `--apply`. The package manager is invoked as an argv
sequence without a command shell. Before execution, Workbench content-measures
every package-manager and elevation executable against the reviewed
identities. After the command exits, Workbench rediscovers and probes Git; a
failed verification prevents setup rebinding.

The result format is `workbench-repair-result-v1`. It reports the applied plan,
installation outcome, exact validated Git path/version, resulting setup record
ID, and the next setup or Home command. If a host command was attempted but a
later command, Git verification, setup rebind, or setup recovery fails, the
same format reports `outcome: partial`, the observed side effects, a bounded
failure description, and the next read-only check. Structured mode drains child
output continuously, retains at most the final 64 KiB, and keeps that bounded
package-manager chatter on stderr so stdout remains one JSON document.

An invalid setup entry is never overwritten. A bounded regular file or
symlink is content/identity-bound in the plan and, after consent, moved to a
deterministic `.invalid-<token>.bak` sibling. Directories, unreadable entries,
and oversized files remain manual blockers.

## Commands and exit status

```text
workbench repair
workbench repair --check [--json]
workbench repair --plan [--json]
workbench repair --apply PLAN_ID [--json]
workbench repair --git-executable PATH
```

Exit `0` means the check is ready, a plan was rendered, repair was cancelled
cleanly, or an exact plan succeeded. Exit `1` means a read-only check or
interactive plan still needs attention. Exit `2` means invalid input, unsafe or
changed setup state, stale consent, package-manager failure, or failed Git
verification. Ctrl+C at the public `workbench repair` boundary exits `130`
without a Python traceback.

Repair never changes a shell startup file, global `PATH`, Workbench profile,
project checkout, Pixi manifest/lock, or launcher/account state.
