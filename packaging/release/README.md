# Native component releases

Each Python distribution owns its version in its native `pyproject.toml`:
API, Core, every module, and each profile. VS Code owns its version in
`clients/vscode/package.json`; IntelliJ owns its version in
`clients/intellij-community/build.gradle.kts`. Lockfiles and built plugin
descriptors are derived projections, not additional writable authorities.
There is no repository-wide or Suite package version.

```text
python tools/component_versions.py check
python tools/release.py show
python tools/release.py tag workbench-atlas --candidate 1
python tools/release.py artifact workbench-atlas.wheel
python tools/build_native_distribution.py --component workbench-atlas --output .workbench/build/atlas
```

`tools/release.py` generates the release view from native manifests in memory.
No synchronization command can overwrite package versions. Tags use each
distribution's namespace (`workbench-atlas/v0.1.0`); IDE clients use their
own corresponding names. Increment only the changed owners, changing required
dependency bounds explicitly when their contracts change.

The candidate workflow validates annotated tags, builds the selected component
and required dependency closure, and retains the selected artifact for review.
It does not publish GitHub releases, PyPI packages, or IDE marketplace entries.
Release uploads require review of the exact artifacts and qualification evidence.

See [native assemblies](../README.md) and [compatibility evidence](compatibility/README.md).
