# Workbench runtime plan V1

Status: experimental executable contract

## Purpose

`runtime/plan` turns exact project and profile context into a concrete local
Cleanroom client or server plan. It is a working read-only feature: it selects
the supported launcher adapter, names the disposable target, lists intended
writes and operations, and explains every missing input.

Planning never creates `.workbench/` state, refreshes Packwiz, downloads an
artifact, edits launcher metadata, or launches Minecraft.

## Requests

A request selects:

- `client` with either `prism` or `multimc`; or
- `server` with `dedicated-server`.

The CLI supplies sensible defaults. Protocol clients send both fields
explicitly.

## Result

The V1 result includes:

- a deterministic plan identity;
- exact project revision, Packwiz manifest, index, source loader, pack profile,
  and target platform bindings;
- a deterministic disposable fixture URI;
- the selected Cleanroom bootstrap and Packwiz Installer identities;
- intended write roots;
- the actual provision, install, verify, and launch sequence;
- warnings that do not prevent experimentation; and
- blockers with a concrete resolution.

`ready` means the current planner has the exact inputs it requires.
`blocked` means the plan was successfully produced but cannot yet be executed
truthfully. A blocked plan is not an empty result or a protocol failure.

Dirty source is a warning rather than a toll gate. Local materialization copies
the current bytes of Git-tracked regular files and records the resolved source
tree; untracked files are excluded. A local Packwiz index mismatch is also a
warning: the materializer refreshes a disposable copy and verifies the
resulting index without changing the checkout.

This tolerance is specific to local authoring source. A published Packwiz
distribution must already match its declared index and fails on mismatch.

The plan ID is an operational planning identity, not a canonical runtime
snapshot or Crucible evidence identity.

## Artifacts

- [runtime-plan-v1.schema.json](../schemas/runtime-plan-v1.schema.json)
- [client protocol V2](client-protocol-v2.md)
