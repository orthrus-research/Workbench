## Summary

Describe the developer problem and the observable result of this change.

## Scope and authority

- Owning module or profile:
- Stable or experimental lane:
- Source rule, standard, profile, or evidence used:
- Identity-bearing formats affected:

## Validation

List the focused checks and repository checks run, including any required
check that was unavailable and why.

## Public and release impact

- Release unit(s): Core / VS Code / IntelliJ IDEA Community / none
- Compatibility or migration impact:
- Documentation or changelog updated:

## Review checklist

- [ ] Product-generic behavior is under `modules/`; platform- or pack-specific
      authority is under `profiles/`.
- [ ] Existing identity-bearing formats are unchanged, or a successor version
      and migration are included.
- [ ] New external imports are recorded in `MIGRATION-MANIFEST.json` with
      license and provenance.
- [ ] No credentials, personal worlds, licensed game/mod binaries, generated
      workspace state, or absolute workstation paths are committed.
- [ ] Tests are proportional to risk, and failures or unavailable checks are
      reported without broadening the claim.
