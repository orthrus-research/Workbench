# Observe an exact Cleanroom worldgen run

Status: experimental working guide

Use this when a question needs a closed, reproducible evidence bundle rather
than ordinary development logs.

## Authority boundary

The launcher creates observations, Crucible validates and seals them, and
Atlas answers only supported questions over the sealed bundle. A successful
capture does not by itself prove compatibility or release readiness.

## Procedure

1. Verify the exact fixture source and its focused tests.
2. Build the remapped observer and stage a disposable target.
3. Assign a new capture identity; never append a restart to an old capture.
4. Launch one bounded route and stop the server normally.
5. Audit manifest, artifact, log, process, event-order, and terminal controls.
6. Evaluate hook health separately from capture closure.
7. Normalize only from a closed worker specification.
8. Ask one Atlas question and retain its exact source bundle reference.

Useful entry points:

```bash
python3 modules/crucible/tools/assemble_exact_runtime_manifests.py --help
python3 modules/crucible/tools/audit_exact_runtime_session.py --help
python3 modules/crucible/tools/evaluate_cleanroom_hook_health.py --help
python3 modules/crucible/tools/run_cleanroom_worldgen_case.py --help
python3 modules/atlas/tools/query_worldgen_observatory.py --help
```

Keep each output under a fresh `.workbench/evidence/<case>/` directory. A
missing completion marker, reused identity, corrupt artifact, unhealthy hook,
out-of-order record, or unclosed worker result stops the workflow. Retain the
incomplete evidence for diagnosis; do not relabel it as a sealed bundle.

Atlas answers such as `who-wrote-block`, `event-handler`, and first divergence
remain bounded to the records the bundle contains. Absence from a bounded
capture is not global absence.
