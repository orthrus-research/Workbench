# Workbench Cleanroom client bootstrap V1

Status: experimental executable contract

## Purpose

`runtime-bootstrap` performs the first recoverable mutation in the local
Cleanroom flow. It turns the exact Cleanroom client artifact selected by a V1
runtime plan into a verified Prism/MultiMC base instance under ignored
`.workbench/` storage.

This is narrower than `runtime-materialize`. By itself it does not install a
Packwiz payload, select or download Java, edit a launcher, launch Minecraft,
or claim that the full runtime plan is ready.

## Operation

The operation:

1. composes the current project runtime plan;
2. requires an exact Cleanroom version plus client source revision, URL, byte
   size, and SHA-256 from the platform profile;
3. downloads into the content-addressed artifact cache or verifies the
   existing cache entry;
4. rejects encrypted, escaping, duplicate, symbolic-link, special, oversized,
   or malformed ZIP entries;
5. extracts into a staging directory;
6. verifies Minecraft and Cleanroom component identities in
   `mmc-pack.json`;
7. computes a deterministic manifest and tree digest over the launcher base;
8. writes a receipt in the staged fixture; and
9. atomically publishes the complete fixture.

A hash, size, archive, identity, or extraction failure may leave a verified
artifact cache entry, but it never publishes a partial fixture.

## Target behavior

The fixture target comes from the runtime plan and must remain beneath the
selected Workbench state root. Existing targets are never silently replaced.
An exact target with an exact receipt and unchanged tree is reused. A missing
receipt, different plan or artifact, symbolic link, special file, or tree
drift is a hard failure.

The bootstrap fixture is a pristine base. Any downstream payload or receipt is
drift and prevents bootstrap reuse; materialization derives a separate target
from this verified base instead of mutating it in place.

The fixture contains:

```text
<fixture>/
  instance/   Extracted Prism/MultiMC base instance
  receipts/
    cleanroom-client-bootstrap-v1.json
```

The upstream archive remains in
`.workbench/artifacts/sha256/<artifact-sha256>`.

## Result and receipt

The command returns
`workbench-runtime-bootstrap-result-v1` with an `outcome` of `created` or
`reused`. The nested deterministic
`workbench-runtime-bootstrap-receipt-v1` binds:

- the V1 plan ID;
- source workspace, project, platform, side, and launcher;
- exact artifact source revision, URL, size, SHA-256, and cache URI;
- fixture, instance, and receipt URIs;
- every instance file and directory plus the resolved tree digest;
- remaining blockers for the complete runtime plan;
- planning warnings; and
- the bootstrap operation's deliberately unimplemented payload, Java, and
  launcher mutations.

The bootstrap ID is SHA-256 over the plan ID, exact artifact identity, and
resolved instance tree digest. It is a bootstrap identity, not a canonical
runtime snapshot or Crucible evidence identity.

The CLI is currently the only mutation surface. A later
mutation-capable protocol method must preserve this same operation and receipt
instead of reimplementing bootstrap logic in VS Code or IntelliJ.
