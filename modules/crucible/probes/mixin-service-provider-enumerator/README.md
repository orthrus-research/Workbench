# Mixin service-provider enumerator probe

This dependency-free Java 8 probe performs the V1
`java.util.ServiceLoader.iterator` enumeration defined by
[`mixin-service-provider-enumeration-evidence-v1.md`](../../contracts/mixin-service-provider-enumeration-evidence-v1.md).

The probe must run on the launched process thread whose context classloader
owns the Cleanroom/Mixin service view.  A launch hook should call
`MixinServiceProviderEnumerationProbe.capture(args)`; that method publishes a
typed evidence document and returns `false` on enumeration failure without
terminating the game.  The `main` entry point is useful for isolated classpath
checks and exits with status 2 after publishing failed evidence.

V1 requires these exact argument pairs:

```text
--output <ignored-.workbench-path>
--session-id <crucible-session-id>
--launch-id <launch-id>
--profile-id <platform-profile-id>
--side <client|dedicated_server|integrated_server>
--candidate-toolchain-lock-sha256 <64 lowercase hex>
--component-topology-receipt-id <workbench-mixin-topology-receipt ID>
--component-topology-receipt-sha256 <64 lowercase hex>
```

The probe uses reflection to load `IMixinService`, so it compiles without a
Mixin dependency. Compile it with the current proven modern compiler and
`--release 8`, verify that every shipped class is major 52, and place its
classes or JAR on the launch hook classpath. Use a Java 8 compiler only when a
specific toolchain cannot accept modern `javac`; Java 8 bytecode is the
consumer constraint, not the preferred development runtime. Do not put
generated classes or probe evidence in the repository; retain them under
`.workbench` custody.

The probe instantiates ServiceLoader providers only to read their public
`getName()` result.  It records each implementation class protection-domain
source and never uses the Mixin subsystem code source for provider
attribution.  It does not prove which provider Mixin selected.
