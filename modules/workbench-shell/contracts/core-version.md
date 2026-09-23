# Native Core version

`workbench version --json` reports the installed `workbench-core` distribution
metadata as an exact object with `component_id` and `version`:

```json
{"component_id":"workbench-core","version":"0.1.3"}
```

The [schema](../schemas/core-version.schema.json) describes only package
identity, not artifact verification, profile support or release qualification.
Native release versions use `major.minor.patch`, optionally followed by an
`aN`, `bN`, or `rcN` prerelease suffix where N is positive.
API, modules, profiles and IDE clients version independently in their own
native manifests. Release assemblies record exact combinations; they do not
overwrite those package authorities.

Source-checkout execution explicitly exposes the same native manifests through
distribution metadata. Source-bound registry records read `core/pyproject.toml`
and the participating owner manifests directly. Root `pyproject.toml` configures
repository tooling and is not a distribution or version authority.
