# Workbench profiles

Profiles bind generic Workbench capabilities to an exact platform or pack.
They hold version constraints, source locks, adapters, policies, fixtures, and
defaults that would be unsafe as universal module behavior.

- `platforms/cleanroom/` defines the CleanroomMC target, candidate-specific
  fixtures, and platform diagnostics.
- `packs/supersymmetry/` defines the first pack binding and remains an explicit
  opt-in profile.

Generic reusable behavior belongs under `modules/`. Provisioned repositories,
downloaded dependencies, clients, servers, worlds, logs, and runtime captures
belong under ignored `.workbench/` storage.
