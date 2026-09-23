# Workbench close-triggered runtime observation V1

Status: experimental executable contract

## Purpose

`runtime-observe` composes the guarded client launch with a bounded full-session
observer. It remains active after `fml-client-loaded`, waits for the exact
projected Minecraft process to disappear, captures instance-local logs, and
immediately invokes retained-runtime analysis. Evidence is called quiescent
and final only when exact process disappearance was observed.

This is a Shell orchestration boundary. It does not give the Shell authority to
reinterpret Atlas observations, mutate the source pack, or treat gameplay as a
stable compatibility admission.

## Inputs and launch behavior

The command accepts the same project, launcher, Java, Packwiz seed, memory,
account-mode, and optional compatibility-overlay inputs as `runtime-launch`.
It also requires positive, bounded attach and session timeouts.
All launch, attach, session, and polling bounds must be finite. Unsupported
launcher hosts and invalid bounds fail before materialization or launch.

The launch phase emits its ordinary V1 or V2 receipt at the existing
`fml-client-loaded` boundary. Observation then binds only a Java process whose
host-side command line contains the unique Workbench projection instance ID.
For a Windows launcher host, filtering runs inside PowerShell/CIM and returns
process IDs only. Workbench never receives or retains the Java command line,
launcher-global logs, account records, or access tokens.

V1 requires the instance process to be observed before its later disappearance
can close a session. An empty first probe is not interpreted as exit. Probe
failure, inability to attach, or timeout is explicit and cannot become a clean
session result.

## Final evidence and analysis

After process disappearance, Workbench captures bounded regular files from the
disposable projection only:

- `logs/latest.log`;
- `logs/debug.log`;
- `logs/groovy.log`;
- `logs/cleanmix.log`; and
- the newest regular crash report, when present.

Symbolic links in either the leaf or an ancestor, escaped paths, and oversized
captures fail closed or remain explicitly unavailable according to the
underlying capture contract. A new
`workbench-runtime-launch-receipt-v3` binds the parent launch ID, process-exit
observation, retained evidence identities, and final receipt location. Its
observation boundary is `projected-client-process-exit` only for an observed
exit; incomplete sessions use `incomplete-process-observation` and retain a
clearly labeled, potentially live snapshot. V1 and V2 receipts remain
byte-for-byte unchanged.

After an observed exit, the observer runs:

1. `runtime-diagnose` against the V3 final-evidence receipt; and
2. `runtime-worldgen-audit` against the same quiescent projection root; and
3. the experimental Atlas Anvil observer against each direct projected save.

Each Atlas Anvil result is retained separately and identity-bound from the
session receipt. V1 validates region allocation, compression, bounded NBT, and
`Level.xPos/zPos` placement. It does not validate terrain continuity, biome
correctness, lighting semantics, or a reported visual symptom.

The exact instance process is probed again before and after each live-root
analysis boundary. A reappearing process or failed lifecycle probe prevents or
discards that live-root result. Anvil worlds are processed and retained one at
a time under aggregate byte/chunk limits.

The durable `workbench-runtime-observation-session-v1` receipt binds the launch
and analysis identities. It is an integration observation, not an Atlas
publication or a supported-profile decision.

For an incomplete lifecycle, retained-runtime diagnosis may inspect the stable
snapshot copy, but neither live-root world-generation analysis is run and
their absence is explicit in the session receipt.

Analyzer failures are independent: every successful result is retained before
the next analyzer runs, and the session records bounded failure details. An
optional Anvil failure cannot erase retained log diagnosis or the profile audit.

## Outcomes

- `completed`: the projected Java process was seen and later disappeared, final
  evidence was retained, and the log, profile-worldgen, and available Anvil
  analyses returned structured results.
- `launch-failed`: the guarded launch did not reach its checkpoint; available
  evidence is retained as an incomplete snapshot and diagnosed.
- `timed-out`: the process did not attach or close within its declared bound.
- `probe-failed`: exact process lifecycle could not be observed safely.
- `analysis-incomplete`: process exit was observed, but at least one analyzer
  or live-root lifecycle recheck did not produce a trusted retained result.

The command performs no source-pack write. Its mutations are limited to the
ordinary ignored materialization/projection state and new ignored evidence
records.
