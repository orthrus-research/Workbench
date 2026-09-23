# Workbench launcher setup V1

Status: experimental executable contract

## Purpose

`workbench launcher setup` connects the existing guarded Runtime Launch owner
to one user-selected Prism Launcher or MultiMC installation. It is additive to
Workbench User Setup V1: neither that record nor any Runtime Launch V1 identity
format changes.

Launcher setup validates and records only:

- `prism` or `multimc`;
- the exact launcher executable path; and
- the initialized launcher data-root path.

The record is `workbench-launcher-setup-record-v1` at `launcher-v1.json` beside
`setup-v1.json`. Its content-addressed identity covers all three values. It is
bounded, regular, non-symlink UTF-8 JSON and is published atomically after an
exact reviewed plan.

## Credential boundary

Workbench executes the launcher's version probe and verifies the expected
family. It checks the family-specific configuration file, the shape of the
optional `instances/` directory, and whether `accounts.json` is a regular file.

Workbench never opens, parses, copies, hashes, logs, or retains
`accounts.json`. Its absence is reported as `missing-manual` account state,
but does not block saving a valid launcher binding or reporting tooling
initialized. The user completes Quick Setup, login, or offline setup in the
launcher itself before a launch that requires it. No account or profile name
is stored in the launcher binding.

## Plan and apply

```text
workbench launcher setup
workbench launcher setup --check [--json]
workbench launcher setup --plan --family prism \
  --executable /path/to/prismlauncher \
  --launcher-root /path/to/PrismLauncher [--json]
workbench launcher setup --apply PLAN_ID ... [--json]
```

When Core has a verified managed Prism installation, `--executable` may be
omitted for a Prism selection. Launcher setup reads the current executable
through Core's tooling API rather than deriving it from a checkout. A launcher
data root is still selected explicitly.

Planning is read-only. Interactive use requires a TTY and exact consent.
Non-interactive apply must repeat the current plan ID. An uninitialized root,
wrong executable family, unavailable required dependency, or substituted
record fails closed. Account presence is advisory at this stage.

## Runtime handoff and readiness states

The top-level `workbench runtime-launch` command fills omitted launcher family,
executable, and root options from this record. Explicit complete command
options remain authoritative.

The three user-visible stages are distinct:

1. Tooling initialized: the executable and initialized root are bound. Opaque
   account-store presence is reported separately. No instance exists and no
   launch occurred.
2. Instance materialized: Runtime Launch has composed the exact Packwiz
   materializer, verified its complete payload receipt, and only then projected
   a fresh instance below the launcher's `instances/` directory.
3. Launch observed: the launcher process reached the named FML checkpoint,
   failed concretely, or timed out. Setup readiness never implies this state.

Runtime Launch retains its existing account rules: callers either select a
launcher-owned profile explicitly or use the declared offline mode. The
current Prism/MultiMC launch guard also requires `accounts.json` to exist,
including for offline CLI launch, because an uninitialized launcher opens its
setup wizard instead of the requested instance. A launch failure reports this
specific recovery action without revoking tooling initialization. Workbench
does not own authentication and never describes account-store presence as a
successful game launch.
