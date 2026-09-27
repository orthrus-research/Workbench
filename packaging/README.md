# Native wheel assemblies

Workbench ships native Python wheels, not an embedded source checkout or a
second bundled `workbench-core`. API, Core, modules, and profiles each have one
package manifest. A Suite is only an explicit selection of those packages.

Direct wheelhouse installation requires Python 3.12–3.14 with the standard
`venv` module. The planned Linux x64 release hook obtains a pinned, separately
downloaded Python 3.14 runtime before using this same wheelhouse installer;
the wheel assembly does not embed an interpreter. Neither path changes system
packages or shell profiles, migrates an existing installation, or rolls back a
failed one. Platform support requires successful qualification on that target;
a configured CI runner is not evidence that its run passed.

From a development checkout with pip and the development dependencies:

```text
python tools/build_native_distribution.py --output .workbench/build/core
python tools/build_native_distribution.py --suite --output .workbench/build/suite
python tools/build_native_distribution.py --suite --with-tui --output .workbench/build/linux-install-suite
python tools/build_native_distribution.py --component workbench-atlas --component workbench-axiom --component workbench-core --component workbench-shell --component workbench-profile-supersymmetry --component workbench-tui --output .workbench/build/linux-supersymmetry-client
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

The release-specific hook binds one assembled archive digest. The full Suite
edition includes its wheelhouse, Axiom engine ZIP, both IDE clients, guide and
an exhaustive file manifest. The Supersymmetry client edition has a smaller
exact native closure and the verified Axiom engine ZIP, without IDE artifacts.
Both editions retain the same byte, target, source and no-clobber checks; each has
its own bundle format. The composer publishes its verified archive and
descriptor through Core as one exact tree. See [the release procedure](../RELEASING.md#prepare-the-one-command-linux-bundle).
