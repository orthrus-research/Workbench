# Workbench build paths

Native package manifests are the version and resource authorities. The root
project is a source workspace, not another Core distribution. Inspect the
checked routes with:

```bash
python tools/build_paths.py list
python tools/build_paths.py show native-suite
python tools/component_versions.py check
```

| Route | Scope | Entry point |
| --- | --- | --- |
| `source` | Frozen repository validation | `validation/validate.py --full` |
| `native-component` | Selected Python package, including the opt-in Textual client, and declared dependency closure | `tools/build_native_distribution.py --component COMPONENT --output NEW_DIRECTORY` |
| `native-suite` | All current native modules and profiles | `tools/build_native_distribution.py --suite --output NEW_DIRECTORY` |
| `client` | One independently versioned IDE client | `tools/build_release_clients.py --component COMPONENT` |

Without a component selector the native builder assembles Core and its
dependencies only. `--suite` excludes the optional Textual client; select
`workbench-tui` explicitly to include its Python wheel and Textual closure.
Building can download dependencies; installing a completed
wheelhouse is offline and hash-locked. The assembly records its build-input
digest and exact package versions, Python minor version, OS and architecture.
Install it only on that target. It does not include a Python interpreter.

The component candidate workflow accepts an annotated namespaced tag such as
`workbench-atlas/v0.1.0-rc.1`. It builds the selected package and its dependency
closure, or the selected IDE client, and uploads temporary review artifacts.
It does not publish a release or marketplace entry. All components use their
own namespace; no repository-wide or Suite version is introduced.

Native package metadata owns versions, dependencies, entry points and resources.
Pixi owns the pinned source-development environment. Platform and pack profiles
own their specific runtime inputs. Exact tested tuples are evidence, not an
additional authority capable of overriding any of those owners.

No construction route grants qualification, publication or support claims.
Run native conformance and the installer on each intended target host before
claiming that target. Generated artifacts, toolchains, test evidence and
credentials stay under ignored `.workbench/` storage or an external store.
