# CleanMix injector-handler order fixture

This exact-profile fixture exercises a two-level Mixin handler-inheritance
shape without modifying CleanMix, Cleanroom, or the transformer chain. It is a
bounded regression oracle, not a general compatibility claim.

## Runtime oracle

`HarnessLoadingPlugin` installs a native FML `IClassTransformer` at the lowest
sorting index. `TransformOrderProbe` returns bytes unchanged and records entry
for `ParentTarget` and `ChildTarget`. `HarnessMain` records the requested
class-load order, observed native-transform entry, observer health, selected
service, classloaders, and behavioral counters.

Native transformer entry is deliberately separate from delegated Mixin
application order. A requested-order property, direct classloader call, or
CleanMix `APPLY` log line is not an adequate application-order oracle.

## Source boundary

- `bootstrap/` observes native transform-chain entry without changing bytes.
- `mixin/` contains the parent injector and child Java override.
- `target/` contains the corresponding target hierarchy.
- `HarnessMain` owns the order and behavioral assertions.
- `HarnessMod` invokes the oracle in a disposable dedicated server.
- `HarnessTweaker` is a standalone fixture-development launch shim; it does
  not install another transformer chain.

The fixture does not import, inspect, or adapt pack-specific worldgen mods.

## Validation

From the repository root:

```bash
python3 -m unittest -v \
  profiles.platforms.cleanroom.tests.test_cleanmix_handler_regression_fixture \
  profiles.platforms.cleanroom.tests.test_cleanmix_runtime_regression_matrix \
  profiles.platforms.cleanroom.tests.test_cleanmix_p0_regression_fixture
```

Every matrix row must run in a fresh JVM with its own launch identity. Keep
observer availability, direct application, final definitions, and behavior as
separate result fields; one cannot stand in for another.
