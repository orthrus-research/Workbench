# Workbench brand assets

The root README displays the canonical Workbench SVG at 64 pixels. The
[Orthrus Research organization](https://github.com/orthrus-research) is linked
there as the parent project.

`workbench-brand-v1.json` remains the source for the current Workbench client
icon set. It is an independently constructed set of declared geometric
primitives and does not trace or incorporate the earlier user-supplied woven
mark.

The mark depicts a workbench top and legs around a compact `W`. Its source
record, canonical SVG, IntelliJ SVG, and VS Code PNG are licensed with the
repository under `LGPL-3.0-only`.

Regenerate the client assets with:

```text
python3 tools/render_brand_assets.py render
```

Check that tracked outputs still match the source record with:

```text
python3 tools/render_brand_assets.py check
```

The renderer uses only Python's standard library. It emits deterministic SVG
and a 256-pixel, five-times-supersampled RGBA PNG.
