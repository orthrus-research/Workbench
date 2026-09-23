# Developing a module

Workbench is one source repository with independently installable packages.
Core owns the environment; modules own domain behavior; profiles own exact
target policy. New capabilities do not require editing a central router.

## Package layout

Create only the directories your module needs:

```text
modules/example/
  pyproject.toml
  LICENSE
  NOTICE.md
  src/workbench_example/
    __init__.py
    registration.py
    commands.py
  tests/
  schemas/                  when there is a persisted or exchanged contract
```

Declare a native package name and version, the supported Python range,
`LGPL-3.0-only` license metadata, license/notice files, and all dependencies in
`pyproject.toml`. Use a build backend supporting that metadata. Existing module
manifests demonstrate the resource packaging convention.

Register the module through Python entry points:

```toml
[project.entry-points."workbench.modules"]
example = "workbench_example.registration:module"

[tool.workbench]
module-id = "example"
requires = []
```

The entry point returns the API contract:

```python
from importlib.metadata import version
from workbench_api import Capability, Module

def module():
    return Module(
        "example",
        version("workbench-example"),
        capabilities=(Capability(
            "example.inspect", ("example", "inspect"),
            "workbench_example.commands:inspect", "Inspect without mutation",
        ),),
    )
```

Handlers accept `argv` and a keyword-only `context`. Call
`context.check_cancelled()` before work and at meaningful safe checkpoints.
Keep registration cheap: do not import the domain engine until its handler is
used. Dependencies on other modules must appear in native dependencies,
`tool.workbench.requires` and the returned `Module.requires`.

## Ownership and resources

- Do not import Core implementation from a domain module to bypass host policy.
  Use explicit API ports; the host supplies their implementations.
- Declare required profiles on capabilities with `requires_profiles`. A missing,
  broken, disabled or incompatible owner makes that route unavailable, not a
  fallback to another target.
- Package read-only schemas, templates and policy with the package that owns
  them. Use the resource helpers rather than guessing a checkout from cwd.
- Keep workspace data, jobs, logs and evidence outside installed files. Removal
  of a package must not delete those records.
- Source names stay stable. Change package versions for implementation releases;
  change schema/protocol identities only when their contracts actually change.
- Services supply an explicit typed store factory. Core provides scheduling,
  transport and activity exclusion; the domain store owns its record semantics.

Module/profile installation checks file and launcher ownership before invoking
pip. Shared namespace directories may merge, but files owned by another
distribution and existing unowned files may not be overwritten. Updates may
replace the same distribution's files. The `workbench` launcher is reserved.
Package resources as package data: ordinary wheel members and `.data/purelib`
or `.data/platlib` layouts are supported; raw `.data/scripts`, headers and
arbitrary data destinations require a separate installation contract.
This protects against mispackaging; installed Python modules are trusted code,
not sandboxed plugins.

## Develop and verify

Use a dedicated virtual environment. Install API and the selected module's
declared dependencies, then install that module editable. Install Core only
when testing host integration. The repository root is not an installable
distribution.

```bash
python tools/module_packages.py --check
python validation/validate.py --suite MODULE_SUITE
python tools/validate_native_packages.py
```

Replace `MODULE_SUITE` with a registered suite shown by the validator's help;
register a focused suite when adding a module. The native check rebuilds from
fresh source inputs, installs packages outside the checkout, checks artifacts,
and exercises installed workflows. Source-path tests alone do not prove that
required resources or dependencies ship.

Version changes belong in native metadata. Release tooling reads that metadata;
an optional assembly records exact selected versions without imposing a shared
version. Never infer compatibility merely because version numbers match.
