# Localize a CleanMix failure

Status: experimental working guide

Treat Mixin startup as a sequence of independently observable boundaries.
Find the last positive boundary and the first missing or negative one; do not
substitute a later log line for missing earlier evidence.

## Authority boundary

Static inspection, compiler/AP receipts, runtime discovery, configuration
lifecycle, application, and final class definition have different evidence
owners. No single `APPLY` message proves all of them.

## Diagnostic order

1. **Artifact topology** — validate manifests, configurations, refmaps,
   owners, dependencies, and collisions.
2. **Compiler and annotation processor** — bind the actual invocation,
   processor path, inputs, outputs, and exit status.
3. **Defining-loader discovery** — record ordered provider attempts,
   validity, failures, and selected service within one launch.
4. **Configuration lifecycle** — distinguish requested, admitted, prepared,
   promoted, rejected, duplicated, and thrown configurations.
5. **Application** — distinguish application entry, plugin post-apply,
   CleanMix postprocessing, and generation.
6. **Final definition** — hash the bytes returned by the defining loader for
   an explicitly named target.
7. **Behavior** — run the fixture's own oracle; transformed bytes alone do not
   establish behavior.

Start with these entry points:

```bash
python3 tools/inspect_mixin_artifacts.py --help
python3 profiles/platforms/cleanroom/tools/run_mixin_doctor.py --help
python3 tools/assemble_mixin_compiler_ap_build_receipt.py --help
python3 tools/inspect_mixin_ap_compatibility.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_defining_loader_discovery_trace.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_config_lifecycle.py --help
python3 profiles/platforms/cleanroom/tools/import_cleanmix_audit.py --help
python3 modules/crucible/tools/assemble_mixin_transformation_ledger.py --help
```

Use a fresh launch identity for every retry. Evidence from another launch
cannot fill a gap in the current one. If an observer is unavailable for a
candidate, record it as unavailable rather than treating silence as success.

For the handler-order fixture, use the exact validation command in
[`cleanmix-handler-regression/`](../../../../../profiles/platforms/cleanroom/fixtures/cleanmix-handler-regression/README.md).
