# Workbench

Workbench is a modular development environment. Core provisions, installs,
coordinates and cleans up the environment; optional modules provide project
inspection, construction, runtime tooling and diagnostics. The first platform
and pack integrations target CleanroomMC and Minecraft 1.12.2.

Supersymmetry is the first pack profile and proving ground. It is not an
implicit universal.

## Linux x64 MVP: Core, Atlas and Axiom

Start with [Getting started](docs/guides/getting-started.md) for the Linux x64
suite installation. Windows users can run the Linux build inside WSL2; native
Windows builds are outside this release. The [Atlas MVP usage guide](docs/guides/atlas-mvp.md)
covers importing a shared scan, finding missing producers or unused outputs,
following recipe relationships, capturing your own saved branch and sharing it.

Completed scans can be read without Minecraft or Java. Findings describe the
captured recipe families and retain unknowns; they do not certify whole-pack
reachability. Native capture currently requires the explicitly supported
profile/runtime/JDK combination. The guide lists the current limits.

Axiom checks saved Supersymmetry material, item, fluid and recipe edits in its
selected Linux SERVER/common native context. It requires a separate Axiom engine
ZIP and the profile-selected Java runtime. See [Axiom](modules/axiom/README.md)
for its exact supported scope and setup.

Developer-authored edits in the IDE are the primary workflow. Workbench reviews
saved changes, explains source findings, and supports explicit checks; guided
generation is an optional tertiary feature. Start with
[local source review](docs/architecture/LOCAL-SOURCE-REVIEW.md).

## Install

Use Python 3.12–3.14 on Linux x64 and a reviewed native wheelhouse built for
that Python minor version. The installer creates a new
isolated environment; it never overwrites an existing installation or edits
shell startup files. Python itself is not bundled.

```bash
python /path/to/wheelhouse/install_workbench.py /path/to/wheelhouse --destination /new/path/to/workbench-env
```

Run the installed executable from `workbench-env/bin`. Add that directory to
`PATH` yourself if desired. A Core-only
wheelhouse does not include Shell, a game profile or either IDE client.

To build from a source checkout, use Pixi 0.75.0 and the repository's locked
`release` environment, which includes Python and pip:

```bash
# Core and its declared dependencies
pixi run --locked --no-config -e release python tools/build_native_distribution.py --output .workbench/build/core-wheelhouse

# Optional assembly containing all native modules and profiles
pixi run --locked --no-config -e release python tools/build_native_distribution.py --suite --output .workbench/build/suite-wheelhouse
```

Build output must be a new directory. Building may download build tools and
dependencies; installation from the completed wheelhouse is offline. Hashes
detect changed artifacts but do not authenticate an unknown publisher.

## First use

```bash
workbench setup
workbench modules list --json
workbench profiles list --json
workbench --help
workbench --version
```

`workbench setup` reviews an exact plan before making local changes.
With the optional Shell module installed, `workbench open /path/to/project`
is read-only and `workbench capabilities` exposes its product catalog.
Core help lists available module commands; profile-dependent routes are only
available when their required profiles and dependencies are enabled.

Structured output is available for clients and automation:

```bash
workbench setup --check --json
workbench environment status --json
workbench --version --json
```

Every command owns its current usage details:

```bash
workbench --help
workbench COMMAND --help
```

See [Getting started](docs/guides/getting-started.md) for project profile and
setup examples.

## IDE clients

VS Code and IntelliJ Community are optional native clients over the same
independently runnable Workbench core. They do not duplicate profile policy,
construction authority, or release decisions.

- [VS Code client](clients/vscode/README.md)
- [IntelliJ Community client](clients/intellij-community/README.md)

## Repository layout

```text
api/                  lightweight module and provider contracts
core/                 native environment-management host
clients/              VS Code and IntelliJ Community clients
modules/              product-generic implementation
profiles/             platform- and pack-specific authority
packaging/            component packaging and release metadata
tests/conformance/    cross-component protocol checks
tools/                contributor and packaging entry points
validation/           repository validation
docs/                 public user and architecture documentation
```

Generated state, downloaded dependencies, managed runtimes, worlds, captures,
sessions, credentials, and build output belong under ignored `.workbench/`
storage or another declared external root. They are never public source or
package inputs.

## Development

Run the repository validator and whitespace check before handing off a change:

```bash
python3 validation/validate.py
git diff --check
```

Focused module and client checks are described in
[Validation and testing](docs/architecture/VALIDATION-AND-TESTING.md).

## Releases

Every installable API, Core, module, profile and IDE client owns its native
version metadata. Release assembly reads those versions; it does not assign
them. Exact tested component tuples are recorded separately; optional suite
assemblies have no independent package version. Maintainer commands are in
[Component releases](packaging/release/README.md).

No release or repository publication action is authorized by these files.

## Documentation and project policies

- [Documentation index](docs/README.md)
- [Atlas MVP usage guide](docs/guides/atlas-mvp.md)
- [Product boundaries](docs/product/README.md)
- [Architecture topology](docs/architecture/TOPOLOGY.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Security](SECURITY.md)
- [Governance](GOVERNANCE.md)
- [License](LICENSE)
