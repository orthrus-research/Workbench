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

Home starts on **Set up Supersymmetry**, which collects a pack source, workspace,
Java choice and Prism location for installation. **Set up developer environment**
handles an existing Workbench project configuration separately.

Home also offers **Choose workspace and Java** for registered named
workspaces. Textual reads and saves these local choices through Core's
`settings workspace` JSON interface with a revision check. Java choices are
the recommended managed Java 25 for Cleanroom, an optional managed Java 8
release, a detected local JDK, or a supplied local path. Textual asks Core to
look for installed JDKs when this screen opens; discovery does not require a
valid profile configuration. One action saves the choice and, for managed Java,
asks Core to acquire it into the stable Workbench state root. If acquisition
fails after saving, the same action retries without saving again. Selecting a
detected JDK uses the supplied-path route; it requires no profile compatibility
check and remains unverified until a consuming operation uses it. Core saves
each workspace's choice in the stable `workspaces.json` record. Selecting
Java 8 does not establish Cleanroom compatibility.

The main setup, workspace and instance forms start in keyboard navigation:
Up and Down move the highlighted field, Enter opens a choice or text field,
and Enter in a text field saves that value and returns to navigation. Escape
restores the field's previous value while editing or returns to the previous
screen from navigation. Review prompts start on Cancel; Left and Right move
between decisions. The Home and Workflows
action lists also start focused for arrow and Enter navigation; `/` moves to
Workflows search. Mouse and Tab controls remain available.

At startup, Textual asks Core to check the latest published Supersymmetry
release. When a newer release is available, **Version up** asks Core to
download and verify the published client archive, then saves that release as
the user's pack choice in the stable configuration home. It does not replace
files in the current workspace. If the archive bytes change under the same
tag, Textual shows the saved and published digests and offers **Use published
archive** instead. **Ignore this release** or **Ignore this publication**
remembers the exact published identity and asks again when it changes;
**Later** changes nothing. The offer waits until Home is active if another
screen is open.
Refresh can retry a check. An unavailable GitHub response does not block local
use. The published client archive is a CurseForge distribution;
developers can still explicitly choose a source branch for their own work.
The archive by itself does not establish a runnable Cleanroom fixture.
Home's **View published pack archive** action reads the saved choice
offline and asks before Core downloads an unprepared selection. It can prepare
the pinned default or a deliberately retained older release without changing
the selected version. If the retained archive changes, Textual shows a review
state and Core preserves those bytes rather than replacing them automatically.
If the state root changes, Textual can prepare a verified copy in the newly
selected root after review.

**Export environment** asks Core to publish a small, path-free selection file
for the saved workspace. **Import environment** takes that file, a local
workspace name and an existing project directory. Textual displays Core's
exact import plan, blockers and unresolved inputs, then requires a review of
the plan ID before binding the local choice. Core rechecks the profile and Java
lock when importing. A reviewed checkbox can ask Core to acquire the locked
managed Java release before binding. The file does not contain project,
fixture, tool or Java archives; project, fixture and tool bytes still need
local acquisition. A user-supplied Java path is redacted from the exported
file and must be supplied again on import without managed acquisition.

## Prototype surfaces

- **Home:** Core version, setup readiness, workspace, installed module/profile
  counts, catalog size, blockers, Workspace Home summary, and reviewed import
  of earlier user configuration.
- **Setup:** Full developer, review-only, and saved-selection repair journeys.
  Choose paths, check dependencies, inspect a Core plan, and confirm its exact
  selection and effects before Core applies it. Core rechecks the plan ID.
  Detected JDKs appear on the setup screen and can fill the Java home field.
  Core checks a chosen path against the profile during setup. Discovery is
  read-only and does not depend on a valid profile. A blank repair field
  retains the saved value; the resulting selection is visible in the plan and
  confirmation dialog.
- **Workspace choices:** Select a named workspace, use a detected or supplied
  Java path, or save and acquire managed Java in one action. Core's versioned
  user registry holds the choice. Export or import a Core-reviewed environment
  selection separately.
- **Supersymmetry instance:** Browse a tree of user folders for a complete
  Prism instance ZIP or an existing Prism data folder. The tree includes
  hidden folders, supports arrows, Space to expand, Backspace for the parent,
  and Ctrl+G to jump to an absolute folder. In WSL, Windows drives can be
  reached under `/mnt`. A new Prism folder can still be typed directly. When
  Workbench's official file access is unavailable, setup starts with the ZIP
  route and explains how to check files saved from an earlier official run.
- **Modules:** Tabs, tables, and a detail panel compare installed module and
  profile records. Highlighting a row reveals its capabilities or resources;
  missing or unavailable components retain their reported reason.
- **Workflows:** Search actions that Textual can open or run. Read-only actions
  with ordinary path, text, number, yes/no, and choice fields collect their
  inputs in a keyboard-driven list, then run through Core's exact review.
  Documents open through Core's catalog binding. Actions requiring repeated
  inputs or changes to local files stay out of this runnable list until their
  interaction is implemented. Core also marks actions unavailable when their
  installed command or document is absent. Interactive Atlas sessions are not
  shown because the TUI has no persistent input stream for them. Runs do not
  retain console sessions.
- **Atlas recipe search:** Choose a pack source or captured graph in Workflows,
  enter a term, then use arrows and Enter to inspect a result. A captured graph
  also offers recorded links. Source-only results are labeled as text matches,
  not observed recipes. Atlas records are read through Core's reviewed JSONL
  command route; the full owner record is one key away from the readable view.
- **Axiom native check:** The dedicated Workflows journey selects a saved
  Supersymmetry checkout and Axiom engine ZIP or folder with file trees. Check
  setup, prepare the runtime, run a check, and reopen retained checks from
  History. A bundle installation prefills its exact engine ZIP only after Core
  verifies the retained installation receipts and archive bytes. Java comes
  from Core's saved choice for that workspace, the Core
  setup selection, or an executable selected in the TUI. Native outcome,
  coverage, findings and original messages remain visible even when the check
  fails. Prepare and Run each show their effects before execution.
- **Axiom to Atlas:** A completed Axiom result with a retained snapshot offers
  **Open in Atlas** when the installed Atlas import action is available. Core
  reviews the exact original attempt and a new graph folder before Atlas
  imports the snapshot. The graph result offers **Search this graph**; arrows
  and Enter then open Atlas observations and their recorded outgoing links.
  This does not rerun Axiom or infer gameplay behavior from recorded values.

Core remains responsible for environment mutation, command construction, and
owner policy. Core's current JSON setup
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
