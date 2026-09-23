# Contributing to Workbench

Workbench changes are reviewed first for whether they help developers build,
run, understand, and fix features. Fidelity and safety matter, but they should
produce better software rather than process for its own sake.

Participation follows the [code of conduct](CODE_OF_CONDUCT.md).

## Before changing a module

1. Identify whether the change belongs to a product-generic module, a platform
   profile, or a pack profile.
2. Name the best-known platform rule, implementation pattern, source example,
   standard, or policy guiding the behavior.
3. Preserve existing identity-bearing formats unless the work explicitly
   introduces a new version.
4. Add focused regression tests proportional to the risk and known failures.

See [Module development](docs/guides/module-development.md) for package metadata,
entry points, resource ownership and isolated validation. Core does not import
product implementations; profiles own target policy and modules own domain behavior.

## Capability language

Documentation and diagnostics distinguish:

- known;
- known absent;
- unavailable;
- unresolved;
- bounded;
- truncated;
- inapplicable; and
- conflicted.

An empty result must not erase those distinctions. A source pattern may drive
an explicitly experimental implementation, but it is not yet a stable
standard. A successful historical Forge result is useful migration knowledge,
not a guarantee of Cleanroom behavior.

## Local and generated data

Use `.workbench/` for provisioned repositories, dependency artifacts, runtime
captures, worlds, simulations, caches, sessions, and private proof payloads.
Only compact, reviewed, license-compatible fixtures belong in Git.

Do not commit credentials, mod or game binaries, downloaded toolchains,
personal worlds, raw large captures, or absolute workstation paths.

## Validation

See the [validation and testing guide](docs/architecture/VALIDATION-AND-TESTING.md)
for focused suites and the optional IDE, exhaustive, and repository-audit
lanes.

Install Pixi once; the checked-in manifest and lock provision the exact Python
and validation closure. Then run:

```bash
pixi run --locked --no-config validate
git diff --check
```

Use `pixi run --locked --no-config -e workspace ...` when a native locked Git package is
available for the host. Add optional tools through a named feature or
environment and review both `pixi.toml` and `pixi.lock`; do not hand-edit the
lock or expand the universal default for one specialized workflow.

If a check cannot run, document why and do not broaden the resulting claim.
Do not require release-depth qualification for a reversible experiment; run
deeper checks when the change reaches the corresponding runtime, client, host,
or packaging boundary.

## Licensing and provenance

Workbench currently carries forward the former repository's GNU LGPL v3
license. Preserve third-party notices and component licenses. Imported groups
must have a migration-manifest entry with their source repository, commit,
roots, and disposition.

By submitting a contribution, you agree to license it under
`LGPL-3.0-only`, the repository's outbound license, and represent that you
have the right to do so. Identify generated or adapted material and retain the
source, license, and transformation needed to review its public use. Do not
submit an asset merely because its bytes are available or were supplied to
you; copyright, license, and trademark permission must be explicit.
