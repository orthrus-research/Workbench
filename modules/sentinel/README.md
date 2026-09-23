# Sentinel

Sentinel presents defects, risks, incompatibilities, and unknowns that an
existing Workbench authority has already established. It does not decide that
a pattern is wrong and it does not create another policy database.

The first bounded flow is:

```text
workbench diagnose mixins mod-a.jar mod-b.jar
workbench diagnose mixins mod-a.jar --json
workbench diagnose mixins mod-a.jar --strict
```

It asks the profile-owned Cleanroom Mixin Doctor to scan exact archive bytes,
then translates that report into a short explanation of what was observed,
why it matters, where it was found, and what the developer should do next.
`--json` returns the complete owner report rather than a Sentinel-specific
replacement. `--strict` returns exit 1 for the owner's `review` or `reject`
dispositions.

This flow is static and read-only. It does not load archive code, inspect a
running game, prove that Mixin application succeeds, or authorize a release.
The exact contract and boundary are in
[Sentinel Mixin diagnosis V1](contracts/sentinel-mixin-diagnosis-v1.md).
