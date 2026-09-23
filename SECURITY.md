# Workbench security and data custody

Workbench analyzes source, dependencies, logs, runtime captures, worlds,
candidate code, and proof records. Some of that material is private, large, or
unsafe to transmit.

## Supported versions

Workbench has not published its first Orthrus Research release. No version is
currently a supported public security line. Reports against the latest
reviewed revision are still useful, but accepting a report is not a claim that
the revision is release-qualified.

After public release, this table will name each supported component line
independently. Similar Core, VS Code, and IntelliJ version numbers do not imply
compatibility or a shared support window.

| Version | Supported |
| --- | --- |
| No public release yet | No |

## Defaults

- Authoritative local use does not require telemetry or a hosted service.
- Provisioned targets, captures, simulations, worlds, caches, and private
  proof payloads stay under ignored `.workbench/` storage.
- Credentials are never evidence, parameters, proof content, fixtures, or
  telemetry.
- Remote processing requires an explicit named destination and purpose.
- Developer worlds are not test fixtures unless the owner explicitly
  authorizes a disposable copy.
- Generated code is reviewed before mutation; target drift and path escape
  fail closed.

## Reporting

Use GitHub's **Security > Report a vulnerability** form at
`https://github.com/orthrus-research/workbench/security/advisories/new`. This
is the intended confidential route. Private vulnerability reporting must be
enabled and tested before a public release; publication is blocked without a
working confidential security route.

If the private form is unavailable after launch, open a content-free issue
asking a maintainer to restore the private reporting route. Do not include the
affected component, vulnerability class, reproduction, or evidence in that
issue.

Include the affected component version or revision, exact platform/profile,
smallest reproduction scope, likely impact, and any evidence that can be
shared safely. Do not attach credentials, personal worlds, private source,
licensed game or mod artifacts, or unredacted local paths.

Maintainers target an acknowledgment within three business days, an initial
assessment within seven business days, and a status update at least every
fourteen days while a confirmed report remains open. Complexity and
cross-project coordination can change remediation time. The reporter and
maintainers should coordinate disclosure; public disclosure should wait until
a fix or clearly documented mitigation is available unless active harm
requires a faster warning.

## Scope

Security reports may cover the CLI and local service, IDE clients, generated
code mutation boundaries, path handling, dependency and artifact verification,
release workflows, or leakage from private Workbench storage. Product support
questions and ordinary correctness bugs belong in the routes described by
[SUPPORT.md](SUPPORT.md).

## Dependency and artifact policy

Workbench does not vendor Minecraft, mod, JDK, Gradle distribution, or other
large dependency binaries. Profiles and environment manifests identify
artifacts by source, version, digest, and license; provisioning retrieves them
into local ignored storage.

Release artifacts must be built from a clean reviewed checkout, carry their
applicable notices and provenance, and be verified against descriptor-bound
digests. Credentials use least-privilege protected environments and are never
stored in source, fixtures, logs, proof records, or release bundles.
