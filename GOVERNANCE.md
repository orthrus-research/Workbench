# Workbench governance

Workbench is stewarded by Orthrus Research at
[`orthrus-research/workbench`](https://github.com/orthrus-research/workbench).
Until organization teams and protected repository roles are configured,
`zestehl` is the bootstrap maintainer. `CODEOWNERS` routes review but does not
prove that an independent approval occurred.

## Product authority

Workbench composes narrow authorities instead of creating a second source of
truth:

- Atlas owns observed and derived knowledge, including uncertainty.
- Blueprints owns convention-aware construction from stable or explicitly
  experimental patterns.
- Manuals teaches practical use and never authorizes code.
- Sentinel diagnoses against declared rules.
- Crucible owns controlled experiments and evidence custody.
- Relay preserves identity across source and runtime.
- Workbench Shell carries context, capability discovery, consent, and results
  between those authorities and developer surfaces.
- Platform and pack profiles own their specific compatibility inputs.

Supersymmetry is the first pack profile and proving ground. It is never an
implicit default for another project.

For Cleanroom construction, guidance is applied in this order:

1. exact Cleanroom platform invariants;
2. exact framework and mod API requirements;
3. a stable pack standard or an explicitly experimental pattern;
4. repository-local integration rules; and
5. permitted developer choice.

Observed examples and maintainer judgment may justify an experimental pattern.
Only implementation experience and focused tests can make it supported;
documentation alone cannot.

## Claims and admission

Experimental work may use a declared provisional profile. A supported release
requires a tested supported profile. Historical Forge observations remain
valid only for the exact legacy profile that produced them and do not become
Cleanroom claims by analogy.

A public module, profile, adapter, standard, or capability must have:

- explicit identity, version, dependencies, and source authority;
- a bounded success and failure contract;
- tests for the scope it claims;
- inspectable assumptions and limitations; and
- defined privacy, storage, and mutation boundaries.

A contract, source skeleton, generated report, or successful build proves only
what it directly checks. Crashes, corrupt output, failed builds, and failed
required checks remain failures. Reversible local experiments may use
proportionate validation, but their results cannot be relabelled as supported
evidence.

## Maintainer authority

Maintainers accept repository changes, administer automation, protect source
and contributor data, and keep public documentation aligned with observed
behavior. Named authority owners decide claims inside their boundary. Release
maintainers bind a reviewed revision to the declared component metadata and
artifacts; they do not replace profile, implementation, or compatibility
owners.

During the single-maintainer bootstrap, `zestehl` may record an explicit
same-author maintainer decision after all required checks pass. That decision
is not an independent review and cannot satisfy a required pull-request or
CODEOWNERS approval. Those requirements must not be enabled until a second
eligible maintainer or Orthrus Research team can supply them.

Maintainer and automation access follows least privilege. Changes to
governance, security, branch or tag protection, signing, release authority, or
credential scope must be durable and reviewable. Organization teams should
replace single-person ownership as qualified maintainers join without silently
changing the product-authority boundaries above.

Public issue and pull-request participation requires a published code of
conduct, a responsible owner, and a tested confidential conduct-reporting
route. Private vulnerability reporting is a separate security channel and is
not a substitute.

Bootstrap maintainer decision (2026-09-23): issue creation is open to all GitHub
users. The [code of conduct](CODE_OF_CONDUCT.md) names `zestehl` as the
responsible owner and `zestehl@pm.me` as the private conduct-reporting address.
On 2026-09-23, `zestehl` confirmed that this is their main inbox and that it
receives incoming email. No conduct report was sent merely to test the route.

## Independent component releases

Workbench independently versions API, Core, each product module and profile,
and both IDE clients. Each component owns its native version, release notes,
tag namespace, artifact family and release decision. Release tooling derives
the current inventory from those native manifests; no central version ledger
can override them.

[Compatibility evidence](packaging/release/compatibility/README.md) records
exact component combinations tested together. A native wheelhouse is only an
assembly of such packages; it has no version or approval path of its own.

See [Releasing Workbench](RELEASING.md) for the maintainer procedure. Release
metadata and local automation do not authorize publication while the hosted
repository controls remain unconfigured.

## Changing governance

Changes to supported standards or profiles, product authority, release policy,
security policy, or this document require an explicit maintainer decision in
the pull request or a public decision record. Once independent review is
available and enforced, ordinary changes use a reviewed pull request whose
required checks pass. Until then, the bootstrap decision above must identify
the exact reviewed revision and must not claim independent approval.

Unresolved objections and unavailable required authorities remain visible.
Automation or a majority count cannot overrule a scoped technical owner. A
stewardship transition must name successor maintainers, preserve license and
provenance obligations, and leave a durable record before repository control
or credentials move.
