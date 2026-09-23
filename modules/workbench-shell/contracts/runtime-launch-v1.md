# Workbench Prism/MultiMC client launch V1

Status: experimental executable contract

## Purpose

`runtime-launch` turns an exact populated Cleanroom client materialization into
a disposable Prism Launcher or MultiMC instance and observes its first useful
runtime checkpoint or concrete failure. It is the first slice that crosses the
launcher boundary; it does not make the mutable launcher instance canonical.

The CLI remains the complete path. VS Code and IntelliJ may eventually present
the same operation, but neither editor selects Java, edits launcher metadata,
or launches Minecraft itself.

## Inputs and launcher boundary

The command composes `runtime-materialize`, then requires:

- a Prism Launcher or MultiMC executable whose reported family matches the
  requested adapter;
- an already initialized launcher data root;
- an optional configured launcher profile name, or a valid offline player name;
- a client heap limit; and
- a bounded checkpoint timeout.

Prism and MultiMC expose compatible `--dir`, `--launch`, `--offline`, and
`--profile` command-line surfaces. Their first-run and ownership UI remains
launcher authority. In particular, Prism 11 can open Quick Setup or an account
refresh dialog before honoring an offline launch.

The account modes are mutually exclusive. With `--launcher-profile`, Workbench
passes only the launcher's authenticated `--profile` selection. Without it,
Workbench passes only `--offline <name>`. It never combines the two Prism
options.

Workbench checks only that the launcher account store exists. It does not open,
copy, parse, hash, or retain `accounts.json`. When `--launcher-profile` is
used, the value is passed to the launcher but replaced with
`<redacted-profile>` in the receipt and Workbench-authored command record.
Launcher stdout and stderr are discarded before retention because Prism may
emit unrelated account names, internal account identifiers, and authentication
details while refreshing its global account store. The launcher may perform
that refresh independently of Workbench. Game-log and crash-report snapshots
receive an exact-value replacement for the supplied launcher profile before
capture, and their evidence records state the treatment and match count. The
source files in the disposable launcher projection are not rewritten.

## Exact launcher-host Java

The launcher and Workbench process may run on different operating systems, as
with Windows Prism invoked from WSL. Workbench resolves the launcher's
executable host and requires the exact profile-selected Temurin release for
that host.

An explicit compatible Java may be reused. Otherwise the managed Temurin V2
provisioner downloads, verifies, extracts, executes, and records the exact host
archive in a Workbench-owned state root. Windows paths are converted only at
the launcher command/configuration boundary. System Java, `PATH`, and global
launcher Java settings remain unchanged.

## Disposable projection

Each invocation creates a unique instance ID and copies the canonical
materialization into `<launcher-root>/instances/<instance-id>` through staging.
Publication is atomic.

The source payload keeps its mode-aware canonical digest. A Windows filesystem
cannot preserve Unix mode bits, so Workbench separately computes a portable
projection digest over every relative path, byte hash, and size and requires
the copied tree to match. The launcher base is compared in the same portable
form.

Only the projected `instance.cfg` is changed. V1 records and applies:

- exact launcher-host Java path and identity;
- Java compatibility override required by the Cleanroom Java 25 instance;
- explicit minimum and maximum heap;
- unmanaged-pack status;
- a Workbench display name; and
- launcher lifecycle/console behavior.

The source fixture and its materialization receipt remain unchanged. The
projection is intentionally retained for inspection and is mutable after
launch.

## Observation and outcomes

Workbench starts the launcher in the selected authenticated-profile or offline
mode and monitors only the projected instance, its process lifecycle, and
non-sensitive window state. It does not retain the launcher's global log or
account data.

The V1 named checkpoint is `fml-client-loaded`, observed from:

```text
Forge Mod Loader has successfully loaded <count> mods
```

This proves that client mod loading completed. It is not a screenshot-based
assertion that a custom main menu rendered correctly.

The result outcome is:

- `checkpoint-reached` when the named marker is observed;
- `failed` for a crash report, nonzero launcher exit, stop before checkpoint,
  Quick Setup, or account-refresh window; or
- `timed-out` when the bounded wait expires while the launcher remains active
  or an already-running launcher accepted the command.

Crash, latest-game-log, and Workbench-authored launcher-command snapshots are
copied into the ignored run evidence directory when present. Captured game
files record any exact launcher-identity redaction alongside the hash of the
retained bytes. A failed result still receives a receipt and the CLI exits
nonzero.

## Retained record

`workbench-runtime-launch-receipt-v1` binds:

- exact plan and materialization IDs;
- launcher executable, version, host, data root, and disposable instance;
- exact Temurin runtime and launch executable;
- redacted command and mutually exclusive account-mode policy;
- canonical source and portable projection identities;
- process/window/checkpoint or failure observation;
- captured evidence hashes and paths; and
- explicit limitations.

The receipt is a runtime observation, not a Crucible experiment or an Atlas
interpretation.

V1 does not contain compatibility overlays. The identity-bearing V2 receipt
and result are used when an explicit disposable compatibility patch is
requested; see [runtime launch V2](runtime-launch-v2.md).

## Primary launcher references

- [Prism Launcher command-line interface](https://prismlauncher.org/wiki/getting-started/command-line-interface/)
- [Prism Launcher data locations](https://prismlauncher.org/wiki/getting-started/data-location/)
- [Prism Launcher Java settings](https://prismlauncher.org/wiki/help-pages/java-settings/)
