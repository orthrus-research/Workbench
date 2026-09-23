# Workbench runtime diagnosis V2

Status: experimental executable contract

## Purpose

`runtime-diagnose` explains one retained Workbench launch result without
changing the pack checkout, materialization, launcher instance, archives, or
evidence. The CLI, JSON-RPC host, and future IDE clients receive the same
structured result.

Diagnosis remains a non-normative Workbench integration observation. It does
not create Atlas knowledge, make a Sentinel policy finding, select a
Blueprint, authorize a source correction, or turn pack-specific guidance into
a universal rule.

V2 replaces V1 result semantics. V1 remains an immutable historical format;
it is not reinterpreted as V2.

## Inputs and trust boundary

The command binds:

- the initialized or CLI-selected native project workspace;
- one local `workbench-runtime-launch-receipt-v1`, V2, or close-observed V3
  receipt; and
- optional local instance, Minecraft, or `mods` roots.

Every captured evidence file is size- and SHA-256-checked against the launch
receipt before analysis. A changed capture fails diagnosis; a missing capture
is reported as unavailable. Reads are bounded, symbolic links are rejected,
and JAR entries are inspected without extraction.

A V3 receipt is accepted only with its parent receipt identity, exact
session-lifecycle shape, retained latest-log binding after a loader checkpoint,
and an observation boundary consistent with the lifecycle state. Relabeling a
V1/V2 receipt as V3 or labeling a timeout as process-exit evidence fails closed.
Diagnosis verifies the retained parent bytes and recomputes the V3 launch ID
over the parent launch ID, lifecycle observation, and evidence identities.

Artifact bytes require a separate provenance decision. When Workbench falls
back to the retained materialized source after a launcher projection has been
removed, it checks all of the following:

1. the artifact root is the source instance named by the launch receipt;
2. the materialization ID and payload identity agree between the launch and
   materialization receipts;
3. both receipts name the same instance and Minecraft payload root;
4. a fresh mode-aware hash of the complete payload tree equals the recorded
   tree identity; and
5. the launch did not supersede that source with a compatibility patch.

Only artifacts satisfying all five checks are `verified` and launch-bound.
Artifacts from an explicit or mutable local root remain useful observations,
but are `unverified` or `drifted` and cannot support `confirmed` confidence or
exact profile guidance.

A compatibility-patched launch therefore cannot use its pre-patch source
materialization as final launched artifact evidence. V2 reports that source as
unverified even when its own tree is intact. A later result version may bind
and reconstruct selected post-patch entries from their exact patch records;
V2 does not claim that capability.

## Outcome-aware analysis

V2 distinguishes causal evidence from incidental log content:

- for a failed launch, the retained crash report is the causal source when
  present; otherwise a log is selected only when it contains a recognized
  blocking injection failure;
- `exception_chain` contains exceptions only from that selected failure
  source;
- for a non-failed launch, `exception_chain` is empty and
  `primary_failure` is null; and
- exception-shaped lines in a successful latest log are grouped by type in
  `log_observations` as bounded, non-terminal observations.

This prevents warnings and handled exceptions from being presented as a
causal chain merely because they occur in the same log.

Failure analysis remains generic: it parses Mixin critical-injection details,
locates implicated class and configuration entries, reads class metadata, and
detects a required `@ModifyVariable` named discriminator that is absent from
the inspected target arguments. A present target class plus a deeper
transformation error classifies matching `NoClassDefFoundError` and
`ClassNotFoundException` entries as transformation wrappers.

Pack profiles may provide exact diagnostic guidance. Guidance is returned
only when its fingerprint matches launch-bound artifact entries.

## Result states

`workbench-runtime-diagnosis-v2` has three states:

- `blocked`: a recognized blocking finding was derived from the retained
  failure evidence;
- `checkpoint-reached`: the launch receipt records an exact checkpoint and no
  blocking finding; or
- `inconclusive`: neither condition can be established.

V2 does not report broad `compatible` state. `checkpoint-reached` carries the
receipt's exact checkpoint ID, marker, and evidence source. It proves only that
checkpoint. The diagnosis also carries the launch receipt's limitations, such
as the distinction between loader completion and a visual main-menu
assertion, mutable launcher state, or account boundaries.

The result additionally contains exact source and receipt identities,
verified evidence records, grouped log observations, artifact provenance,
archive-entry identities, findings, and a deterministic diagnosis ID over all
other fields.

A `blocked` report describes the runtime and is still a successful diagnostic
command. Runtime diagnosis performs no repairs.

## Interfaces

CLI:

```text
workbench runtime-diagnose <workspace> --receipt <receipt.json>
  [--artifact-root <instance-or-mods-root>] [--json]
```

JSON-RPC V2.2:

```json
{
  "method": "runtime/diagnose",
  "params": {
    "receipt_uri": "file:///.../runtime-launch-v2.json",
    "artifact_root_uris": ["file:///.../.minecraft"]
  }
}
```

The initialized workspace is implicit in JSON-RPC. Every supplied URI must be
a local `file:` URI.

## Contract artifact

- [runtime-diagnosis-v2.schema.json](../schemas/runtime-diagnosis-v2.schema.json)
