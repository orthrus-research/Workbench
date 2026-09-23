# Relay

Relay moves an exact identity between retained runtime evidence and its
owner-backed source or evidence location. It transports context; it does not
decide what the identity means and it does not turn an uncontrolled live-game
observation into accepted evidence.

The first bounded flow is:

```text
workbench relay locate machine:example:press \
  --explorer-result retained-explorer-result.json

workbench relay locate launch:retained-run \
  --identity-kind launch-id \
  --receipt retained-crucible-snapshot.json
```

An immutable Atlas runtime database can be supplied with `--runtime-db`.
Relay uses the Exact Runtime Explorer's providers and result validator rather
than maintaining another identity database.

Resolution succeeds only when the exact identity is unique and unambiguous,
the relevant evidence is not truncated, an observed Atlas or Crucible runtime
facet is retained, an owner is bound, and at least one source or owner-evidence
locator exists. Otherwise the command prints `unresolved` and exits 1.

The JSON result retains the selected Explorer entity, its complete facets,
and its relevant source rows. Profile, side, session, snapshot, owner,
evidence-state, and limitation fields therefore remain attached to the
handoff. See [Relay location V1](contracts/relay-location-v1.md).

Some retained identities intentionally occur on more than one entity. For
example, a runtime snapshot ID is also carried by each capability inside that
snapshot. Relay reports that request as unresolved; selecting the exact launch
ID locates the snapshot itself without guessing.
