# Releasing Workbench

This procedure governs candidates for `orthrus-research/workbench`. It does
not itself authorize pushes, repository settings changes, tagging, or publication.

The reviewed clean-root baseline was published as the initial public `master`
commit. The [Linux x64 MVP prerelease](https://github.com/orthrus-research/Workbench/releases/tag/linux-x64-mvp-2026-09-23)
followed on 2026-09-23. Its hosted validation had disclosed failures, so the
prerelease does not establish stable release qualification. The private development
`main` and public `master` have different histories; carry future reviewed source
deltas across as new public commits, never by merging or mirror-pushing private
history. Protected settings and support routes require separate observation.

The public `validate` workflow runs quick checks, Linux x64 installed-package
checks, Axiom engine checks and a `source-ci` tier. The latter excludes the
`validation-native-fixtures` and `blueprints-native-fixtures` suites; its artifact
inventories their cases as **not run**. Full `--tier canonical` and `--full` retain
both suites. The Axiom fixtures require a source-matched candidate, engine, JVM
and fresh report roots. Blueprints simulation and dependent lifecycle tests require a Bubblewrap host permitted
to create its sandbox namespaces. Windows and macOS package observations run in the separate
`portability-observation` workflow and do not qualify the Linux release.

The hosted Axiom failure diagnosed on 2026-09-23 was caused by Bubblewrap being unable to configure
loopback in the hosted runner's network namespace. The worker sandbox policy has
not been changed; that host remains unqualified for its native worker tests.

## Native version and artifact ownership

Each Python package has one authoritative version in its own `pyproject.toml`:
API, Core, all modules, and profiles. Core alone owns the `workbench`
executable. The repository-root manifest is a workspace marker, not another
Core distribution. Pixi owns development dependencies, not a Workbench version.

VS Code uses `clients/vscode/package.json` with its generated package lock.
IntelliJ uses `clients/intellij-community/build.gradle.kts`; Gradle generates
the version in the packaged plugin descriptor. Module and profile dependency
ranges, API compatibility versions, IDE protocols and retained schema/record
identities are separate contracts.

`tools/release.py` derives a release view, tags and artifact names directly
from these native authorities. No central version file or sync command can
overwrite them. A Suite is only a selection of packages, without another
distribution or version. See [native assembly details](packaging/README.md).

## Prepare a component candidate

1. Identify the affected owners through actual behavior, dependencies and
   build inputs. Change only their versions, relevant dependency bounds and
   release notes. Breaking API/provider changes require deliberate compatibility
   bounds; matching version strings alone do not establish compatibility.
2. Regenerate a changed native lockfile and validate ownership:

   ```text
   python tools/component_versions.py check
   python tools/release.py validate
   python tools/build_paths.py validate
   python tools/public_repository.py validate
   ```

3. Freeze a clean reviewed revision. Build artifacts outside tracked source
   paths, using new output directories so previous bytes cannot be overwritten
   or silently included. Representative component commands are:

   ```text
   python tools/build_native_distribution.py --component workbench-atlas --output .workbench/build/atlas-candidate
   python tools/build_native_distribution.py --suite --output .workbench/build/suite-candidate
   python tools/build_release_clients.py --component workbench-vscode --lane public-v1 --output-dir .workbench/build/vscode-candidate
   python tools/build_release_clients.py --component workbench-intellij-community --lane public-v1 --output-dir .workbench/build/intellij-candidate
   ```

   Native assemblies stage fresh repository inputs and resolve only the selected
   local dependency closure. They record the staged source digest and exact
   target-specific wheel names, versions, sizes and SHA-256 hashes, plus an
   offline pip bootstrap wheel. The obsolete embedded-runtime, Pixi Pack,
   portable-Core and separately named platform archive routes no longer exist.
4. Qualify the actual built bytes from outside the source checkout, without
   source-path injection or development package installs:

   ```text
   python tools/install_workbench.py .workbench/build/suite-candidate --destination /absolute/new/workbench-candidate-env
   python tools/validate_native_artifacts.py .workbench/build/suite-candidate/wheels
   /absolute/new/workbench-candidate-env/bin/python -I validation/native/installed_workflows.py
   python tools/validate_native_packages.py
   python validation/validate.py --full
   git diff --check
   ```

   The installer requires Python 3.12–3.14 with `venv`, does not require
   ensurepip, never updates an existing environment and never deletes retained
   workspace data. Use a new absolute destination and its `Scripts/python.exe`
   on Windows. The installed-workflow harness above tests the actual assembled
   candidate; native-package conformance separately rebuilds isolated package
   closures to test owner independence and cannot replace that artifact test. Stop
   active old work explicitly before selecting a replacement executable.
   Preserve failed installs and failed qualification evidence for inspection.
5. Inspect artifacts for exact package ownership, complete wheel RECORD hashes,
   licenses, notices, provenance, secrets and internal coordination material.
   Run component-specific functional and IDE host checks. Qualify each claimed
   Python/OS/architecture independently; an OS matrix configuration is not a
   completed run. Native conformance does not launch a game and does not claim
   runtime/game qualification.
6. Retain a qualification receipt binding the source revision, assembly manifest
   digest, exact artifact hashes, target host, commands, results and declared
   skips. A builder's `qualified: false` stays false: test results belong in the
   separately bound receipt, not a manually upgraded input flag. Retain the
   original dependency wheels because a later fresh resolve may select newer
   versions permitted by the native manifests.

## Clean-root public history and future updates

The public baseline is an export of reviewed tracked bytes into a fresh
repository. It must not publish the migration branch's private history, other
existing branches or tags. Keep private source history and internal harnesses
outside the public repository. `.gitignore` alone does not remove tracked data
or protect old history.

The initial public `master` is one root commit authored by the publishing
maintainer. Its parentless identity and the published prerelease belong to the
historical release receipt. Future updates must preserve that public root and
verify the reviewed source delta and public-tree exclusions before committing to
`master`. Keep export manifests and scan receipts outside the public worktree.
Agent instruction Markdown, local skills and agent configuration remain excluded;
public usage documentation remains included.

Use the local export tooling on the exact reviewed revision:

```text
python tools/prepare_public_export.py plan --revision REVIEWED_COMMIT
python tools/prepare_public_export.py stage-scan --revision REVIEWED_COMMIT --output /absolute/new/scan-input
```

Run the required pinned/reviewed secret scanner over that exact staged public
Git tree. Review its allowlist and any findings. Produce the expected passing
scan receipt binding the source revision, Git tree, aggregate tree SHA-256,
scanner/version, zero findings and reviewed allowlist; a scan of a different
working directory, artifact or revision does not qualify the export.

```text
python tools/prepare_public_export.py plan --revision REVIEWED_COMMIT --secret-scan-receipt /absolute/reviewed/scan-receipt.json --require-ready
python tools/prepare_public_export.py build --revision REVIEWED_COMMIT --secret-scan-receipt /absolute/reviewed/scan-receipt.json --output /absolute/new/public-export
python tools/prepare_public_export.py verify --output /absolute/new/public-export
```

Run clean-checkout validation and artifact builds from the export itself, and
scan the exact release artifacts separately. Preserve original import provenance
and license notices. The export commands above apply to an initial migration or
an independent new destination. For this repository's existing public `master`,
use an exact reviewed delta and verify its resulting tree against private source.
Do not mirror-push, copy a dirty working directory, or publish private refs.

## Hosted controls and publication

### Local readiness report

Use the read-only readiness checker before a proposed action:

```text
python tools/publication_readiness.py --revision REVIEWED_FULL_COMMIT
python tools/publication_readiness.py --revision REVIEWED_FULL_COMMIT --evidence /absolute/private/evidence.json --require-ready local
python tools/publication_readiness.py --revision REVIEWED_FULL_COMMIT --evidence /absolute/private/evidence.json --component workbench-atlas --require-ready release
```

Without evidence it reports pending gates, never default passing receipts. It
does not execute supplied commands, contact GitHub, send reports, change settings,
create tags, or publish. `--require-ready` returns 1 when the requested scope is
pending; invalid or mismatched evidence returns 2. Ordinary reporting returns 0
even when gates remain pending, so automation must select its required scope.

- `local`: clean exact source, bound source-secret scan and completed full
  validation, including the final IDE phase.
- `source`: local gates, observed source protections and private security route,
  safe community-intake state, and an exact-source publication decision.
- `release`: source gates plus selected components' exact artifact/target checks,
  protected tags/release environment, reviewed publication mechanism and approval
  of the exact artifact hashes. Without `--component`, every native owner is
  selected. Every supplied target for a selected owner must pass; no unlisted
  host or Python version is qualified by inference.
- `community`: clean reviewed policy, observed controls, separate tested security
  and conduct routes, a governing code of conduct and explicit intake approval.

The private evidence format is defined by
[`workbench-publication-evidence-v1.schema.json`](packaging/release/schemas/workbench-publication-evidence-v1.schema.json).
Start with only its four required fields: `format`, `schema_version`,
`source_revision` and `git_tree_oid`. Omitted sections remain pending. Keep the
evidence outside public source or under ignored `.workbench/`; never commit
confidential reporting contacts, raw host observations or approval records merely
to make this command pass. Each file reference binds `path` and `sha256`; relative
paths resolve against the evidence file. Qualification rows explicitly retain
commands, exit results, logs and limitations. Full validation requires the final
`validation/validate.py --full` success, not the earlier Python-only `run.json`.
Each Python artifact also requires `assembly_manifest`, a bound reference to its
retained `wheelhouse.json`; the entire wheelhouse is verified, its staged source
digest must match the reviewed Git tree, and its exact wheel record must match
the candidate. Its target label is exactly `PLATFORM/MACHINE/python-VERSION`,
derived from that assembly (for example `linux/x86_64/python-3.14`). IDE source
association remains operator-attested because their current manifests do not
carry a staged-source digest. Approval records name both `source_author` and
reviewing `actor`; an independent decision cannot name the same identity twice.

These bindings detect changed source and evidence bytes, but do not independently
authenticate an operator's assertion that a command ran, a confidential route
was tested, an approval is eligible, or a hosted control remains active. Review
those assertions and reobserve hosted settings immediately before acting. Do not
copy old receipts onto a new revision, substitute different artifact bytes, or
upgrade the builder's `qualified: false`. A scanner's default exclusions and any
explicitly untested behavior belong in the declared limitations.

Security and conduct routes require their responsible owners and a tested
confidential path; an enabled GitHub setting alone is not a delivery test. Missing
owners/routes stay pending until maintainers adopt them. Same-author bootstrap
decisions are labelled as such and cannot satisfy independent-review requirements.
The tool checks approval scope and artifact hashes but does not grant authority.

`public_repository.py validate` validates the declaration only.
`prepare_public_export.py plan --require-ready` means **sterile export eligibility**,
not readiness to publish or enable participation. In the repository declaration,
`history_pushed` specifically means the reviewed clean-root Workbench migration
baseline was imported and verified, not that the destination has any commits.
`repository_settings_applied` is also independent of public visibility.

Source and release readiness also require two explicit maintainer decisions:
the migration strategy bound to the observed destination HEAD, and the intended
license bound to the reviewed local `LICENSE` digest and observed destination
license digest. The existing bootstrap license and local native SPDX declarations
must be reconciled deliberately; this checker makes no legal determination and
does not change either license. A new root may assume an empty destination only
when the observation says it is empty. Replacing existing public history requires
the separate `replace-public-history` approval action; `publish-source` never
implies it. Missing or contradictory decisions block readiness.

The [candidate workflow](.github/workflows/component-release.yml) accepts an
annotated tag in the selected native component namespace, such as
`workbench-atlas/v0.1.0-rc.1`. It verifies the tag against the owner's manifest,
builds the selected dependency closure or IDE client, installs/verifies the
candidate, and retains its exact selected artifact. It has read-only repository
permissions and does not create a GitHub release or publish to package indexes
or IDE marketplaces. Final-version tags also create candidates, not automatic
publication.

Before a public tag, release or artifact upload, maintainers must observe and
verify these controls in the Orthrus Research destination:

- Protected default branch and native component tag namespaces.
- Required validation checks and an eligible approval path.
- A protected release environment with least-privilege credentials.
- Private vulnerability reporting, secret scanning and reviewed project notices.
- A reviewed publication mechanism that accepts only the qualified candidate
  bytes and verifies their public downloads and hashes.

Public release uses the already qualified bytes, not a rebuild under a final
tag. Record all released package versions and tested combinations, publish
accurate support limitations, and verify downloads after publication. Neither
source visibility, a successful build, nor a configured workflow establishes
that these hosted controls or publication steps have actually occurred.
