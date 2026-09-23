# Supersymmetry experimental Blueprints inputs

This directory contains profile-scoped experimental construction inputs. It
is not release authority and does not expand stable profile support.

- [`GROOVY-ORGANIZATION.md`](GROOVY-ORGANIZATION.md) records the organization
  observed in the exact pinned Supersymmetry revision.
- [`patterns/material-backed-fluid-v1.json`](patterns/material-backed-fluid-v1.json)
  is the guarded three-file convention patch used by the profile's
  material-fluid flow.
- [`standards/registry.json`](standards/registry.json), its `0.1.0` standard,
  and its templates remain available only when the profile explicitly selects
  the experimental create-only route. They are not the stable construction
  path.

No stable material-backed-fluid standard is currently admitted. The generic
[`modules/blueprints`](../../../../../modules/blueprints/README.md) engine has
no built-in registry or pack-owned templates. The latest disposable
Supersymmetry example is
[`material-fluid-recipe-radon.json`](../examples/material-fluid-recipe-radon.json).

Generated plans, stages, and receipts belong under ignored `.workbench/`
storage. Regenerate them for the exact checkout instead of treating a retained
receipt identity as portable evidence.

Run the focused convention-patch check from the Workbench root:

```bash
python3 -m unittest discover -s modules/blueprints/tests \
  -p 'test_convention_patch.py'
```
