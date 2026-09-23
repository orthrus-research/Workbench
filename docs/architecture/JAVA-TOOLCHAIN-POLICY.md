# Java toolchain policy

Status: active experimental policy for Cleanroom construction

Workbench uses the newest proven Java for each role. “Java 8 compatible” is an
artifact constraint, not permission to run the whole development workflow on a
legacy JDK.

## Separate the roles

Every build or receipt that crosses the Minecraft 1.12.2 boundary should name
these roles independently:

| Role | Current direction |
| --- | --- |
| Cleanroom runtime | Use the exact modern JDK admitted by the selected Cleanroom profile; the current World Studio runtime is proven on Java 25.0.4. |
| Workbench and mod source compiler | Use the newest compiler proven by the project. Cleanroom-only code may target the modern runtime directly. |
| Gradle control process | Use the newest JDK the exact Gradle/Groovy/plugin stack can execute on. Upgrade that stack when practical instead of retaining an old host by habit. |
| Minecraft interoperability artifact | Emit the classfile level required by the consumer, preferably from a modern compiler with `--release`; inspect the produced classfiles. |
| Legacy patched-Minecraft toolchain | Provision Java 8 only for tasks that demonstrably require it, such as the current RetroFuturaGradle decompile/patch pipeline. |

This separation prevents an old game binary from forcing IDEs, analysis tools,
test runners, or Cleanroom-native mods onto Java 8.

## Current Strata decision

Strata's in-game observer must load beside Minecraft 1.12.2 classes, so its
shipped artifact remains classfile major 52. Version `0.2.0` now compiles that
source with Java 25 and `--release 8`, inspects every output class, and records
the compiler and target in the jar manifest. Java 8 is restricted to the
RetroFuturaGradle patched-Minecraft toolchain.

The exact Gradle/Groovy build stack does not currently run on Java 25; it fails
before observer compilation with `Unsupported class file major version 69`.
The proven transitional split is therefore:

```text
Cleanroom runtime        Java 25.0.4
observer source compiler Java 25.0.4, --release 8
Gradle build host        Java 21.0.11
patched-Minecraft tools  Java 8
observer output          classfile major 52
```

This is a measured compatibility boundary, not a recommendation to freeze the
build host on Java 21. A future build-stack update should rerun the same checks
and advance the host when it passes.

## Admission rules

- Bind the exact JDK release identities when toolchain behavior is part of an
  experiment receipt.
- Fail when a compatibility artifact contains an unexpected classfile major;
  source declarations alone are insufficient.
- Do not compile new Cleanroom-only modules to Java 8 without an identified
  Java 8 consumer.
- Do not replace a working modern runtime with Java 8 to accommodate one build
  plugin. Isolate that plugin's role or update it.
- Treat preview, incubator, native-access, and concurrency features as explicit
  design choices with focused compatibility tests; a modern JDK does not make
  Minecraft chunk generation thread-safe.
