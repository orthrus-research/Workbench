# Source development environment

Root `pixi.toml` and `pixi.lock` are development toolchain authorities, not
Workbench version or distribution authorities. The
[platform matrix](workbench-platform-matrix-v1.json) pins Pixi bootstrap assets
and implemented source environment rows. It does not claim release qualification.

```text
bash tools/bootstrap_pixi.sh
pixi run --locked --no-config workbench --help
pixi run --locked --no-config -e release build-wheelhouse --output .workbench/build/native
```

Windows uses `powershell -NoProfile -ExecutionPolicy Bypass -File tools\bootstrap_pixi.ps1`.
Both scripts retain exact digest-checked Pixi privately, then run source
`setup-core`. They do not install a public package or modify global PATH.
`--print-plan`/`-PrintPlan` is read-only; `--pixi-only`/`-PixiOnly` stops after
bootstrap provisioning. Windows ARM64 currently requires that bootstrap-only
option; it does not have an admitted full workspace row.

- `default`: pinned Python and development dependencies.
- `workspace`: default plus locked Git on implemented workspace rows.
- `release`: native wheel/IDE build tools.
- `compatibility`: alternate pinned Python closure for environment tests.

The obsolete Pixi Pack/unpack distribution pipeline has been removed. Native
wheel assemblies use [the common build and install route](../README.md).
