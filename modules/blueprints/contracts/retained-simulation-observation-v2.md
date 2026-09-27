# Blueprints retained simulation observation v2

Status: internal API milestone; no supported CLI binding or release admission

`Simulator.execute(..., custody_mode="retained-v2")` runs the existing domain
validation stages against a Core-issued private scratch lease. Core records
the lease before creating the scratch directory, holds it during execution,
and retains it on completion or interruption. The returned
`observation_v2.status` is always `observed-unqualified`, even if every domain
gate passed. This observation cannot be supplied to the V1 Blueprints release
path. The default `Simulator.execute` and historical V1 evidence remain
unchanged.

A caller must explicitly bind `CoreSimulationScratch` through the API scope.
The current Blueprints CLI and installed interface do not bind that scope or
expose the V2 mode. Wiring a user route is separate work.

The V2 observation and its separate private evidence record identify the
scratch lease and report `retained-process-absence-unproven`. They report the
current process handling precisely: Git calls wait for their direct child;
Bubblewrap commands create an initial process group and the timeout path
signals that group. Neither observation proves that detached descendants are
absent. No V2 result says that scratch was deleted. Candidate bytes can remain
in the owner-private Core lease; the public result includes only identifiers,
gate status/digests, and the retention state.

V2 private evidence uses `blueprints-simulation-evidence-v2.schema.json`, a
separate content-addressed `v2/objects` namespace, and a
`local-simulation-evidence-v2:sha256:` locator. `read_v2` checks its exact
canonical bytes, schema, and gate digests. The historical V1 reader and
`local-simulation-evidence:sha256:` locator continue to mean the original V1
cleanup claim. Core's temporary-lease inventory is the current authority for
whether retained scratch still exists. `read_v2` validates evidence bytes but
does not reopen the physical lease; the recorded disposition is a
publication-time claim. Current lease state requires a separate Core catalog
readback.

Before a V2 observation can become a releasable simulation, Core must own
the Git and Bubblewrap process lifecycle, prove full process absence across
interruptions and restarts, and provide a safe retained-lease disposition.
That work needs separate native checks on Linux, WSL Linux filesystems, WSL
Windows mounts, and Windows. This V2 route makes none of those claims.
