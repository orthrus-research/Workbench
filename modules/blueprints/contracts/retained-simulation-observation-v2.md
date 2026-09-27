# Blueprints retained simulation observation v2

Status: opt-in diagnostic interface; no release admission

`Simulator.execute(..., custody_mode="retained-v2")` runs the existing domain
validation stages against a Core-issued private scratch lease. Core records
the lease before creating the scratch directory, holds it during execution,
and retains it on completion or interruption. The returned
`observation_v2.status` is always `observed-unqualified`, even if every domain
gate passed. This observation cannot be supplied to the V1 Blueprints release
path. The default `Simulator.execute` and historical V1 evidence remain
unchanged.

`BlueprintsCore.observe_simulation_v2(environment_lock)` and the
`observe-simulation-v2 --environment-lock <file>` CLI command expose this
diagnostic route from a planned session. The optional `--dependency-source`
argument has the same local, digest-checked meaning as V1 simulation. The
direct CLI derives the target from the validated session and binds Core's
scratch host to that target and the selected configuration home. Installed
dispatch binds the same Core host to its selected workspace and configuration
home; an observation whose session or reviewed target differs from that
selected workspace is refused before scratch allocation.

The separate `susy-blueprints-observation-result-v2` envelope reports
`observed-unqualified` and exits with code 5 on stdout when an observation was
captured. This exit is deliberately nonzero even when `gate_status` is
`passed`, so a caller checking process success cannot mistake a diagnostic for
a releasable simulation. A pre-observation refusal reports `rejected` with a
diagnostic on stderr. The V1 session pointer, run state, events, simulation
result, and release admission are unchanged: `generate` still requires a V1
`simulated` run. The private V2 evidence locator is not included in the CLI
result.

The V2 observation and its separate private evidence record identify the
scratch lease and report `retained-process-absence-unproven`. They report the
current process handling precisely: Core runs V2 Git calls with a five minute
timeout, a 1 MiB input limit, and a 4 MiB limit for each output stream. Core
retains private process captures for launched V2 Git calls, including
incomplete captures when supervision fails after capture startup. The private
evidence lists completed Git capture IDs and exact attempt names under the
retained lease; its reader refuses duplicate attempt names.
Core also runs opted-in V2 Bubblewrap commands under the full approved lock:
1 to 3,600 seconds and 0 to 16 MiB per output stream. It never clamps a legal
lock to the general native-tool port's smaller bounds. Each launched command
gets a private Core capture under the same retained lease. Core retains the
first approved bytes plus one overflow byte per stream, so zero is a valid
output limit and gate digests cover exactly the approved prefix. Timed-out and
overflowed attempts retain an incomplete capture with an exact ID. The private
evidence lists each observed Bubblewrap attempt, stage, command digest, capture
ID and outcome; its reader checks unique attempt/stage identities and gate
links. Historical V2 records without these lists keep their original meaning.
Git and Bubblewrap supervision proves closure of the original process group
only. Neither observation proves that detached descendants are absent. No V2
result says that scratch was deleted. Candidate bytes can remain in the
owner-private Core lease; the public result includes only identifiers, gate
status/digests, and the retention state.

V2 private evidence uses `blueprints-simulation-evidence-v2.schema.json`, a
separate content-addressed `v2/objects` namespace, and a
`local-simulation-evidence-v2:sha256:` locator. `read_v2` checks its exact
canonical bytes, schema, and gate digests. The historical V1 reader and
`local-simulation-evidence:sha256:` locator continue to mean the original V1
cleanup claim. Core's temporary-lease inventory is the current authority for
whether retained scratch still exists. `read_v2` validates the typed Git and
Bubblewrap capture references and evidence bytes but does not reopen the
physical lease or captured streams; the recorded disposition is a
publication-time claim.
Current lease and process-capture state requires separate Core readback.

Before a V2 observation can become a releasable simulation, Core must prove
full process absence for Git and Bubblewrap across interruptions and restarts,
and provide a safe retained lease disposition.
That work needs separate native checks on Linux, WSL Linux filesystems, WSL
Windows mounts, and Windows. This V2 route makes none of those claims.
