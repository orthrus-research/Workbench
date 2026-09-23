# Workbench Core

The sole distribution is defined by [Core's native manifest](../../../../core/pyproject.toml).
It owns environment provisioning, coordination, cleanup and the `workbench`
executable. It depends on API but not product modules or profiles.

Its package version, wheel filename and `workbench-core/v{version}` tag are
derived directly from that manifest. Root `pyproject.toml` and `pixi.toml` do
not carry another Core version or build another Core package. Modules and
profiles are independently selected native packages, never Core-owned files.
