# Experimental Workbench TUI

This branch experiments with [Textual](https://textual.textualize.io/) as an
optional presentation client. The TUI starts a selected Workbench Core command
and reads its JSON interfaces. It does not import Core, Shell, or module Python
packages, and Textual is not a dependency of those components.

## Try the source checkout

Use Python 3.12–3.14 and an isolated environment:

```sh
uv venv .venv-tui
uv pip install --python .venv-tui/bin/python -e 'clients/tui[dev]'
.venv-tui/bin/workbench-tui --source-root "$PWD" --source-python python3
```

For an installed Workbench Core, replace the source options with
`--workbench /absolute/path/to/workbench`. `--workspace /absolute/path` starts
with a particular project. The `workbench-tui` executable needs a real terminal;
the optional `textual-dev` package supports live CSS and widget inspection.
The Textual process and the selected Core command may use different Python
interpreters. The TUI wheel includes `textual-fspicker` for browsing existing
workspace directories and configuration files. Manual path entry remains
available for new paths. `textual-dev` is for development only.

For an offline installation, build the TUI as an explicit Python component:

```sh
python tools/build_native_distribution.py --component workbench-core --component workbench-tui --output /new/path/to/tui-wheelhouse
python /new/path/to/tui-wheelhouse/install_workbench.py /new/path/to/tui-wheelhouse --destination /new/path/to/workbench-env
/new/path/to/workbench-env/bin/workbench-tui
```

Select `workbench-shell` alongside it to include common workflows in one
wheelhouse. The standalone installer checks the
`workbench-tui` launcher and reports its path in `workbench-install.json`.
Textual is installed as Python wheels; `workbench-tui` is the client command.
In a combined installation it discovers the sibling `workbench` command, so
neither command needs to be on the global `PATH`.

## Configuration and moving to a new installation

The wheel supplies the default Workbench theme, layout, key bindings and CSS.
Its default palette is black and charcoal with white accents. The landing page
uses a terminal-cell version of the supplied mark, derived from
`assets/workbench-mark.jpg`. Orthrus Research owns and licenses this artwork
and its terminal rendering under the repository's `LGPL-3.0-only` license;
the image is kept as source artwork and is not a runtime image dependency.
Smaller terminals get a compact mark, and terminals
whose output encoding cannot represent the block glyphs retain the WORKBENCH
wordmark. Saved alternative Textual themes remain the user's choice.
The source `workbench.toml` selects pack and platform authority; it does not
contain TUI appearance. Checkout-local `.workbench/` holds mutable runtime
state and is not a portable preference store.

The client keeps its appearance record at `~/.workbench/tui.json` (or
`%USERPROFILE%\.workbench\tui.json` on Windows). An absolute
`WORKBENCH_CONFIG_HOME` selects another parent directory. The file is absent
until a user changes the Textual theme from the command palette; the client
then saves the theme for later launches. The home-screen clock choice is also
part of this versioned record, defaulting to visible; `Ctrl+T` toggles it.
Unsupported or damaged records fail clearly rather than being replaced. An
unavailable saved theme displays a warning and temporarily uses the packaged
theme. The TUI never stores an
absolute Core executable path; it discovers that command each launch.

Core owns the reviewed setup selection and reports its record path through
`workbench setup --check --json`. The TUI reads Core's selection and effective
locations through the `environment resolve` JSON API rather than opening Core
configuration files. On a new host, copy both the TUI appearance record and
Core's reported configuration directory, then run
`workbench setup --check` before relying on saved workspace, Java, Git or state
paths. Setup records contain absolute host paths and may require reviewed
repair. Select the same custom `WORKBENCH_CONFIG_HOME` on the new host if one
was used. The installer never copies, resets or rewrites these records.

Home offers **Import earlier configuration** for records in Workbench's former
OS-specific user directory. It requests Core's versioned JSON dry run, shows
the source, stable destination, per-file state and digest, and asks before
importing any copies. The client rechecks the dry run immediately before
invoking Core's import. A conflicting destination blocks the bulk import and
is shown for manual review; the client never chooses which record to replace.
Core retains the earlier files and checks the records again before copying.

## Prototype surfaces

- **Home:** Core version, setup readiness, workspace, installed module/profile
  counts, catalog size, blockers, Workspace Home summary, and reviewed import
  of earlier user configuration.
- **Setup:** Full developer, review-only, and saved-selection repair journeys.
  Choose paths, check dependencies, inspect a Core plan, and confirm its exact
  selection and effects before Core applies it. Core rechecks the plan ID.
  Java discovery is read-only. A blank repair field retains the saved value;
  the resulting selection is visible in the plan and confirmation dialog.
- **Modules:** Tabs, tables, and a detail panel compare installed module and
  profile records. Highlighting a row reveals its capabilities or resources;
  missing or unavailable components retain their reported reason.
- **Workflows:** Search the installed command catalog and inspect an action's
  owner, risk, availability, fields, and limits. A small allowlist of read-only
  actions can be reviewed and launched with Core's catalog, action, and review
  digests. Runs use plain output and do not retain console sessions. The
  Manuals overview uses Core's document path with catalog and action binding.

Core remains responsible for environment mutation, command construction, and
owner policy. Catalog actions that need input forms or other document handling
are shown for discovery without a launch button. Core's current JSON setup
interface cannot clear a saved profile configuration, so switching an existing
full developer setup to review-only is not offered. The wizard does not
configure launchers or qualify a pack; those are separate owner paths. The TUI
is experimental and is registered as an opt-in Python client component. The
focused package/install smoke does not qualify a public release or publication.

Textual's layout, CSS, Rich text, tabs, tables, searchable option lists, modal
screens, workers, and headless test driver give us distinct presentation
patterns to evaluate. The terminal controls font availability; Textual can style
with color and Unicode but cannot install or select a font. Check glyph coverage
and color contrast in the terminals that Workbench supports, including
PowerShell and tmux.

## Focused checks

```sh
.venv-tui/bin/python -m unittest discover -s clients/tui/tests -v
python3 tools/validate_public_tree.py
```

These checks exercise the client and the repository boundary; they do not
qualify native, installed, or release behavior.
