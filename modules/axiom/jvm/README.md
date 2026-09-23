# Axiom Java library and application

Current delivery priority is the [native initialization-check MVP](../spec/initialization-mvp.md):
execute saved edits without Minecraft and report native errors, not automatic
repairs. The installed `material-program` operation is this path; the bounded
recipe AST endpoint described below is separate.

Axiom builds and executes on the profile-selected Temurin HotSpot **25.0.4+7**
Linux x86_64 JDK. There is no custom JVM backend and no Java 17 fallback.
The exact runtime-file inventory is owned by
[the Cleanroom profile](../../../profiles/platforms/cleanroom/jvm-runtime.json)
and included as an installed engine resource.

```sh
python3 tools/build_axiom.py --provision
```

Alternatively supply explicit `--gradle`, `--java-home` and `--registry-root`
paths for an offline build. The registry root contains the separately obtained
original utility JARs pinned by the Cleanroom profile; no Minecraft JAR is
bundled. `--provision` acquires and retains these exact inputs through Core in
ignored local state. The build verifies the runtime and compiler files, cleans
stale output, then runs `test installDist distZip sourcesJar` and the installed
smoke.
Gradle dependency locks and verification metadata still bind Groovy 4.0.30,
TomlJ, Fastutil and their transitive parser libraries. The build and runtime use Java 25;
version metadata remains in `build.gradle.kts`.

The public Java entry point is `research.orthrus.axiom.Engine.run`.
It admits the pinned runtime before evaluation. The current recipe endpoint
still interprets a bounded Groovy AST; a trusted native-Groovy test is not a
new arbitrary-source endpoint. `Main` supervises isolated Linux bubblewrap
workers with input/output and time bounds. Each worker additionally installs
thread-synchronized Linux syscall restrictions and hard resource limits before
compilation. Native access is enabled only for that child to install the kernel
policy; the host/library JVM is never subjected to those process-global limits.

The engine's installed sources and library inventory contain no retired
bytecode/heap/Class bootstrap implementation and no upstream game/mod classes.
The JDK is separately provisioned, never bundled or implicitly downloaded.
See [native runtime contract](../spec/native-runtime.md) for identity limits
and the next actual upstream-execution gate.

Native registry tests require `AXIOM_REGISTRY_TEST_ROOT` when invoking Gradle
directly; the official build sets this after verifying/provisioning the selected
inputs. Direct Gradle without that input skips registry-runtime tests and must
not be reported as registry qualification. Fluid-queue tests always run.
