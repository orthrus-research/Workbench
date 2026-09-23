# Decision 0007: Pixi owns source development, not installed packages

Status: amended for the native-package public baseline

Original decision: 2026-08-23. Amendment: 2026-09-07.

## Decision

Pixi remains Workbench's pinned source-development environment. The checked
[`pixi.toml`](../../pixi.toml) and [`pixi.lock`](../../pixi.lock) are its exact
inputs. Source setup verifies the pinned Pixi binary and uses locked,
configuration-isolated materialization. A project-local Pixi configuration is
rejected before claiming exact setup.

Installed Workbench uses independently versioned native Python wheels and
a target-specific, hash-locked offline wheelhouse. The old portable Core,
bundled interpreter and V5 distribution formats are retired. Their package
manifests and installation receipts are not accepted by the new installer.
See [native packages](../architecture/NATIVE-PACKAGES.md).

The boundary is:

- Native project metadata owns runtime Python dependencies, versions, entry
  points and resources. A source workspace lock is not a second package authority.
- The native builder resolves declared dependencies, records exact artifacts
  and targets, and creates a new wheelhouse. Building can require network access.
- The installer verifies that reviewed wheelhouse and installs offline into a
  new environment using an existing supported Python interpreter. It does not
  invoke Pixi or mutate a source checkout.
- Core reports native installation metadata separately from verified source
  Pixi observations. Merely observing an environment variable is not evidence
  of an installation or artifact digest.
- Profiles own Cleanroom/pack-specific Java and runtime selection. Neither
  Pixi nor native Python packaging becomes a second domain authority.

The `.pixi/` directory is disposable source-development state.
Workbench-owned downloads, runtimes, evidence, sessions and build output belong
under ignored `.workbench/` storage or another explicit external store.
Cleanup remains a separate operation with ownership and retention checks.

## Consequences

There is one public Python packaging path, with no custom bundled-interpreter
launcher or alternate portable wheel. A user supplies a supported Python
interpreter, and qualification must exercise the exact target host and
installed package tuple. A source lock or successful cross-build does not
establish that qualification.

Upgrading source-development tools requires a reviewed Pixi manifest/lock
change. Upgrading runtime package dependencies requires native metadata and
artifact qualification. Neither path rewrites profile policy or historical
evidence identities.
