# Retained registration and loading findings

These are source-traced requirements, not claims that the native execution
pipeline is already implemented. Keep them when replacing the retired models
with actual upstream classes and narrow, audited extraction boundaries.

- **Phase and mutation order:** material registry events, material declarations,
  post-material processing and recipe registration are dependent phases. Resolve
  actual listener construction and registration order; declaration inventory
  alone does not supply a complete active event bus.
- **Class identity:** a binary name alone does not identify a loaded class.
  Preserve defining loader, source artifact and applied transformation order.
  Let the JVM perform linking and initialization; do not manually mark upstream
  classes initialized or bypass an initializer to continue registration.
- **Foundation transformations:** resource order, exclusions, ordinary versus
  explicit transformer paths and mutable transformer state affect definitions.
  Executing under Java 25 does not reproduce those policies automatically.
- **Events:** priority, parent listener tables, cancellation filters, listener
  snapshots, context restoration and exception handling can affect which recipe
  producers execute and which mutations survive a failure.
- **Resources and modules:** JAR manifests, provider order, module access and
  service discovery can affect the construction environment. Use actual selected
  runtime facilities; capture the relevant inputs rather than inventing a
  favorable resource or module result.
- **Failures and provenance:** retain effects preceding failure and source
  locations through helper/generated registrations. Missing dependencies and
  sandbox limits must not become a native rejection or a successful definition.

The retained [Cleanroom event lock](../sources/cleanroom-events.lock.json),
[Foundation source lock](../sources/foundation-classloading.lock.json) and
[launcher source lock](../sources/launch-paths.lock.json) identify the original
research inputs. They are reference data, not shipped execution dependencies or
assertions that installed source/artifact composition is qualified.

Machine-specific findings and recipe-source examples remain in
[construction requirements](recipe-construction.md), [behavior requirements](behavior-and-parity.md)
and the [bounded implementation](first-slice.md). JVM internals are no longer
Axiom's implementation roadmap; see [native runtime execution](native-runtime.md).
