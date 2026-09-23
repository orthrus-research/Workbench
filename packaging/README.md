# Native wheel assemblies

Workbench ships native Python wheels, not an embedded source checkout or a
second bundled `workbench-core`. API, Core, modules, and profiles each have one
package manifest. A Suite is only an explicit selection of those packages.

The installation prerequisite is Python 3.12–3.14 with the standard `venv`
module. No bundled interpreter, system-wide installation, legacy launcher,
shell profile modification, automatic migration, or installer rollback is
provided. Platform support requires successful qualification on that target;
a configured CI runner is not evidence that its run passed.

From a development checkout with pip and the development dependencies:

```text
python tools/build_native_distribution.py --output .workbench/build/core
python tools/build_native_distribution.py --suite --output .workbench/build/suite
python tools/install_workbench.py .workbench/build/core --destination /absolute/new/workbench-env
```

On Windows use a new absolute destination such as `C:\Workbench\env`. Run the
returned `bin/workbench` or `Scripts/workbench.exe` directly. Build outputs and
installation destinations must not already exist; the tools do not overwrite
existing installations or delete environment data. A failed environment is
retained with a failed receipt for inspection. To replace an environment,
install another reviewed assembly into a different path and explicitly select
its executable after stopping any old active work.

The builder stages fresh repository inputs, builds the selected local package
closure, then resolves third-party wheels for the current Python/OS/architecture.
Dependencies are locked by name, version, size and SHA-256 in `wheelhouse.json`
and `requirements.lock`. A source SHA-256 binds the exact staged input paths
and file digests, including current edits when building locally. The reviewed assembly includes a hash-locked pip
bootstrap wheel, so installation does not require ensurepip or a package index.
Installer admission copies only declared wheels privately, re-verifies those
bytes, installs offline with required hashes, and checks dependency consistency.

Assemblies are target-specific. Rebuild and qualify separately for other
Python minor versions, operating systems and architectures. Resolving a new
assembly may select newer third-party dependencies permitted by the native
manifests; retain the original assembly and hashes to reproduce an installation.

Hashes detect corruption, not publisher authenticity. Only install a manifest
and installer from a trusted, reviewed distribution. A build's `qualified: false`
is intentional: qualification is a separate exact-artifact test receipt, not
an editable success flag. The first release does not claim the retired
portable/native bundled-interpreter artifact families are supported.
