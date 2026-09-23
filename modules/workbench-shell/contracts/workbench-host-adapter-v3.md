# Workbench Host Adapter V3

Status: flat local conformance slice implemented; packaged and release
qualification remain unavailable.

## Purpose

Host Adapter V3 is the sole current local host-conformance receipt. It directly
contains and validates the complete capability vocabulary, including
`executable-identity-discovery`; it does not embed, translate, or accept a
predecessor receipt.

Workbench remains host-neutral: no operating-system or machine label selects
eligibility. Operations declare required capability IDs, and the adapter
reports only what it demonstrated on the selected host and storage.

## Executable identity discovery

V3 resolves either one absolute candidate or one filename against an explicit,
bounded tuple of search roots. It never consults ambient `PATH`. Zero matches
and distinct multiple matches fail with developer-readable diagnostics.

The selected target must be a regular executable candidate. V3 opens it in
binary mode where the host exposes that flag, so Windows bytes and `0x1A`
cannot be translated or truncated. V3 measures the
running Python executable before and after its complete V3 probes; either
measurement failure or identity drift keeps discovery unavailable. Each
measurement opens the resolved path with binary, close-on-exec, no-follow, and
nonblocking flags where the host exposes them. Nonblocking open prevents a
post-resolution FIFO replacement from hanging the diagnostic; descriptor type
validation then rejects it. The measurement bounds the file at 512 MiB and hashes the complete
descriptor stream, compares descriptor identity before and after reading, and
verifies through the host access API that the descriptor and final pathname
retain executable permission and that the pathname still names the measured
identity. It then rechecks the complete descriptor identity once more after
the pathname and permission checks and rechecks the pathname after that final
descriptor read. The paired closure catches same-size/restored-mtime rewrites
and parent-namespace swaps during final verification. Descriptor-to-path identity uses device, file identity, size, and
modification time; it does not compare mode or change time because native
Windows reports those differently between `fstat` and pathname `stat` for the
same file. Permission is checked separately. POSIX measurements additionally
require descriptor execute bits;
Windows measurements do not invent POSIX bits that native `stat` does not
carry. A native Windows identity-discovery candidate must have `.exe` or `.com`
suffix and its measured binary stream must begin with the `MZ` candidate header;
Windows `os.access(..., X_OK)` alone is not accepted because it is true for
ordinary unrelated files. This is a bounded candidate fingerprint: it proves
the selected path, descriptor identity, bounded byte length, and SHA-256 seen by
this probe, plus only the stated Windows candidate checks. It does not prove
that the Windows loader accepts the file, that the PE machine architecture
matches the host, or that dependencies, subsystem, signature, or launch policy
are compatible.

Process proof stays separate. The `exact-argv-spawn` row exercises
shell-free exact-argument spawning of the running interpreter; it does not
spawn, load, or qualify the candidate fingerprinted by
`executable-identity-discovery`. Each measurement also carries explicit
`path_flavour`; the V3 adapter binds its own measured local path flavour and
rejects a measurement that selects different semantics. The OS label remains
diagnostic and never selects eligibility. The validator accepts
only one requested filename, one resolved filename, one canonical absolute
`file:` URI whose final component is the resolved filename, and a bounded
descriptor mode, with execute bits required for a POSIX URI. Keeping both names makes an
explicit symlink request visible without confusing it with the opened target;
resealing an arbitrary URI or a non-executable POSIX mode cannot create
discovery evidence. The
content-addressed measurement retains
the requested filename, resolved file URI, size, SHA-256, and host descriptor
identity. Packaged execution must remeasure immediately before process
creation; the disposable local receipt is not a package or release decision.
The measurement descriptor is closed afterward, so it is not spawn custody.
The packaged operation must repeat the same pinned measurement immediately
before a separately qualified exact-argv spawn, reject any identity or
permission drift, and supply its own loader/architecture evidence where the
target host requires it.

The standard `--version --json` interface follows the current
[component-version contract](core-version.md). It reports only native Core
package identity and version; it does not turn this local Host Adapter
observation into loader proof, package conformance, release qualification,
or support.

## Operation requirements

V3 carries one fixed capability set for each diagnostic operation:

- `core-host`: portable path/Unicode fidelity, filesystem identity, atomic
  replace, file sync, `executable-identity-discovery`, `exact-argv-spawn`, direct
  termination, and monotonic time;
- `durable-publication`: core hosting plus directory sync and exclusive lock;
- `hosted-local-service`: core hosting plus locking, local IPC, and descendant
  process termination;
- `credential-backed-operation`: core hosting plus credential storage;
- `desktop-notification`: core hosting plus desktop notifications; and
- `delegated-runtime`: core hosting plus identity-bound runtime delegation.

The derived state disables only the affected operation and lists its exact
missing capabilities. Unknown OS labels remain eligible when the declared
capabilities pass. The developer-facing receipt retains the selected candidate
identity or exact discovery limitation, missing capability IDs, and a next safe
action. These local operation states are diagnostics, not support claims.

## Receipt and failure boundary

`workbench-host-adapter-conformance-v3` has schema version `3`, adapter ID
`workbench.local-python-host-adapter:v3`, the active executable measurement or
explicit discovery failure, canonical V3
capabilities, derived operation states, and a SHA-256 receipt ID. Validation
rejects unknown fields, reordered or altered capability rows, contradictory
measurement state, forged derived readiness, unsupported release claims, and
identity drift. The nested `workbench-executable-measurement-v1` format remains
unchanged because this successor does not alter its shape or byte-custody
semantics.

The receipt always has `release_qualified: false`. It does not prove packaged
discovery, Windows loader or architecture compatibility, a spawn of the
fingerprinted candidate, crash durability, descendant containment, remote
custody, credentials, notifications, performance, profile behavior, recovery,
or any cross-host release matrix. Those remain later, independently owned
gates.
