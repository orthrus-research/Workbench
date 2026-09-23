# Iterate the first playable worldgen replacement

Status: experimental working guide

Use this direct fixture loop when changing the generator or diagnosing its
build and launch plumbing. For normal daily work, prefer
[the one-command iteration runner](run-worldgen-iteration.md).

The source fixture is
[`worldgen-prototype-fixture/`](../../../../../profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture/README.md).

## Authority boundary

A clean local launch is development feedback, not profile support or release
evidence. Use a disposable world for every terrain-affecting change.

## Choose the owning code

- Plan defaults and validation: `world/plan/WorldStudioPlan.java`.
- Region, climate, biome, lithology, and channel behavior:
  `world/WorldStudioTerrain.java`.
- Drainage and tile caching: `world/hydrology/WatershedEngine.java`.
- Generation, carving, and population order:
  `world/PrototypeChunkGenerator.java`.
- Pack controls: the example Groovy plan under the fixture's
  `examples/groovy/postInit/` directory.

Groovy must publish an immutable plan before world construction. Do not run a
Groovy closure from a terrain-sampling hot path.

## Build and run

```bash
gradle \
  --no-daemon \
  -p profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture \
  --project-cache-dir .workbench/gradle-project-cache/worldgen-prototype-fixture \
  clean remapJar
```

Create a server directory under `.workbench/` with a fixed numeric seed, a new
`level-name`, and `level-type=wb_proto`. Then run the fixture with an absolute
ignored server directory:

```bash
gradle \
  --no-daemon \
  -p profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture \
  --project-cache-dir .workbench/gradle-project-cache/worldgen-prototype-fixture \
  -PworkbenchServerRunDir="$PWD/.workbench/runs/worldgen-prototype-dev" \
  -PworkbenchWorldgenDiagnostics=true \
  -PworkbenchWorldgenSampleModulo=16 \
  runServer
```

Wait for server readiness, generate the intended chunks, and enter `stop` so
the world is saved and the process exits normally.

## Inspect

```bash
rg 'WORLDGEN_PROTOTYPE' .workbench/runs/worldgen-prototype-dev/logs/latest.log
rg 'chunk.failure|Exception|ERROR|FATAL|Done \(' \
  .workbench/runs/worldgen-prototype-dev/logs/latest.log
```

Compare the same seed, plan hash, mod set, and chunks. A changed height hash
localizes a difference but does not explain it; use the region, field, biome,
lithology, channel, and carver records for that.

Fresh-world and reload tests answer different questions. Generate a fresh
world after terrain changes; reopen the same world only to inspect persistence
and lookup behavior.
