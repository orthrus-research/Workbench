"""An experimental Textual presentation for installed Workbench records."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from importlib.util import find_spec
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlparse

from rich.text import Text
from textual import work
from textual import events
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.content import Content
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    OptionList,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)
from textual.widgets.option_list import Option

from .core_client import (
    AtlasObservationSession, CoreClient, CoreClientError, SetupInputs, CommandOutput,
)
from .keyboard_form import KeyboardFormScreen
from .preferences import (
    PreferencesError,
    TuiPreferences,
    load_preferences,
    save_preferences,
)


_CATALOG_SCALAR_KINDS = {"text", "path", "integer", "boolean", "choice"}
_HAS_PICKER = find_spec("textual_fspicker") is not None


def _runnable_catalog_action(action: Mapping[str, Any]) -> bool:
    """Admit only catalog actions this client can collect and execute."""
    if action.get("command_id") == "atlas.observations-session":
        return False  # The interactive JSONL session needs a persistent stdin client.
    if (action.get("risk") != "read-only"
            or action.get("preview") != "none"
            or action.get("availability") not in {"available", "experimental"}):
        return False
    document = action.get("document")
    if document is not None:
        return isinstance(document, str) and bool(document)
    fields = action.get("options")
    return isinstance(fields, list) and all(
        isinstance(field, dict)
        and isinstance(field.get("key"), str)
        and field.get("kind") in _CATALOG_SCALAR_KINDS
        and field.get("nargs") in {"one", "optional"}
        and not field.get("repeat")
        and not field.get("required_group")
        for field in fields
    )


def _catalog_editable_fields(action: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Leave output encoding to the client rather than asking for a JSON flag."""
    return [field for field in action.get("options", [])
            if isinstance(field, dict)
            and not (field.get("key") == "json" and field.get("flags") == ["--json"])]


_DESCRIPTION_COLOR = "#D7BC72"
_METADATA_COLOR = "#70C8C0"


_WORKBENCH_THEME = Theme(
    name="workbench-dark",
    primary="#f2f2f2",
    secondary="#777777",
    accent="#f2f2f2",
    foreground="#e8e8e8",
    background="#080808",
    surface="#101010",
    panel="#191919",
    warning="#dedede",
    error="#f2f2f2",
    success="#c4c4c4",
    dark=True,
    variables={
        "foreground-muted": "#adadad",
        "description": _DESCRIPTION_COLOR,
        "metadata": _METADATA_COLOR,
        "footer-key-foreground": "#ffffff",
    },
)


@dataclass
class EnvironmentView:
    version: Mapping[str, Any] | None = None
    environment: Mapping[str, Any] | None = None
    setup: Mapping[str, Any] | None = None
    modules: list[dict[str, Any]] = field(default_factory=list)
    profiles: list[dict[str, Any]] = field(default_factory=list)
    catalog: Mapping[str, Any] | None = None
    home: Mapping[str, Any] | None = None
    problems: list[str] = field(default_factory=list)
    catalog_loading: bool = False
    modules_loaded: bool = False
    profiles_loaded: bool = False

    @property
    def workspace(self) -> str:
        resolved = self.environment.get("workspace", {}) if self.environment else {}
        if isinstance(resolved, dict) and isinstance(resolved.get("path"), str):
            return resolved["path"]
        return ""


def _line(label: str, value: object, *, style: str = "") -> Text:
    text = Text()
    text.append(f"{label:<15}", style="bold dim")
    text.append(str(value), style=style)
    return text


def _workspace_option_label(name: str, path: str) -> str:
    folder = Path(path).name or path
    return f"{name} · {folder}" if folder != name else name


def _detected_jdk_options(
    inventory: Mapping[str, Any],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Show installed JDK identity without claiming profile compatibility."""
    detected: dict[str, str] = {}
    options: list[tuple[str, str]] = []
    rows = inventory.get("candidates", [])
    if not isinstance(rows, list):
        return detected, options
    for row in rows:
        if (not isinstance(row, dict) or row.get("state") != "available"
                or not row.get("jdk")):
            continue
        probe = row.get("probe") or {}
        home = row.get("java_home") or (probe.get("java_home") if isinstance(probe, dict) else None)
        if not isinstance(home, str) or not home or home in detected.values():
            continue
        key = f"installed-{len(detected)}"
        detected[key] = home
        options.append((f"Detected Java {row.get('feature_version', '?')} · {home}", key))
    return detected, options


def _migration_details(record: Mapping[str, Any]) -> str:
    lines = [
        f"Earlier configuration: {record['source']}",
        f"Stable configuration: {record['destination']}",
        "",
    ]
    for row in record["files"]:
        lines.append(f"• {row['name']}: {row['state']} · {row['sha256']}")
    if not record["files"]:
        lines.append("No earlier configuration records were found for import.")
    lines.append(f"\nCore state: {record['state']}")
    return "\n".join(lines)


class ReviewModal(ModalScreen[bool]):
    """A separate, blocking decision for an exact Core review or plan."""

    def __init__(self, heading: str, body: str, *, confirm_label: str) -> None:
        super().__init__()
        self.heading = heading
        self.body = body
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Static(self.heading, id="review-heading")
            with VerticalScroll(id="review-scroll"):
                yield Static(Text(self.body), id="review-body")
            yield Static("PgUp/Dn Read · ←/→ Choose · Enter OK · Esc Back",
                         classes="keyboard-hint")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="review-cancel")
                yield Button(self.confirm_label, id="review-confirm", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#review-cancel", Button).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"pageup", "pagedown"}:
            scroll = self.query_one("#review-scroll", VerticalScroll)
            (scroll.scroll_page_up if event.key == "pageup" else scroll.scroll_page_down)()
            event.stop()
        elif event.key in {"right", "down"}:
            self.query_one("#review-confirm", Button).focus()
            event.stop()
        elif event.key in {"left", "up"}:
            self.query_one("#review-cancel", Button).focus()
            event.stop()
        elif event.key == "escape":
            self.dismiss(False)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "review-confirm")


class ResourcepackMappingModal(ModalScreen[str | None]):
    """Collect an explicit resource-pack mapping for a newer official release."""

    def __init__(self, review: Mapping[str, Any], *, entered: str | None = None,
                 problem: str | None = None) -> None:
        super().__init__()
        suggestions = review.get("suggestions", {})
        pairs = suggestions.get("suggested", []) if isinstance(suggestions, Mapping) else []
        suggested = ", ".join(
            f"{row['project_id']}:{row['file_id']}" for row in pairs
            if isinstance(row, Mapping)
            and isinstance(row.get("project_id"), int)
            and isinstance(row.get("file_id"), int)
        )
        self.suggested = suggested if entered is None else entered
        self.problem = problem
        required = review.get("required_external_files", [])
        self.required = [
            f"{row['project_id']}:{row['file_id']}" for row in required
            if isinstance(row, Mapping)
            and isinstance(row.get("project_id"), int)
            and isinstance(row.get("file_id"), int)
        ]
        unresolved = suggestions.get("unresolved_project_ids", []) if isinstance(suggestions, Mapping) else []
        self.unresolved = [str(item) for item in unresolved if isinstance(item, int)]

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Static("Review resource packs in this release", id="review-heading")
            with VerticalScroll(id="review-scroll"):
                yield Static(
                    "Core recognized the previous release's resource-pack project IDs. "
                    "Confirm or edit the project:file pairs to place in Prism's resourcepacks "
                    "folder. Every other required file will be placed in mods.\n\n"
                    + ("Previous resource-pack projects missing: " + ", ".join(self.unresolved)
                       + "\n\n" if self.unresolved else "")
                    + "Required IDs in this release:\n" + "\n".join(self.required),
                    id="review-body",
                )
                yield Input(value=self.suggested, placeholder="project:file, project:file",
                            id="pack-policy-pairs")
                yield Static(self.problem or "Enter at least one pair from the required ID list.",
                             id="pack-policy-error")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="pack-policy-cancel")
                yield Button("Review mapping", id="pack-policy-confirm", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#pack-policy-pairs", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "pack-policy-pairs":
            self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pack-policy-cancel":
            self.dismiss(None)
        elif event.button.id == "pack-policy-confirm":
            self.dismiss(self.query_one("#pack-policy-pairs", Input).value.strip())


def _reviewed_resourcepack_pairs(
    entered: str, required_rows: list[Mapping[str, Any]],
) -> tuple[tuple[int, int], ...]:
    offered = {
        (row["project_id"], row["file_id"]) for row in required_rows
        if type(row.get("project_id")) is int and type(row.get("file_id")) is int
    }
    selected: set[tuple[int, int]] = set()
    for field in entered.split(","):
        parts = field.strip().split(":")
        if len(parts) != 2 or not all(part.strip().isdecimal() for part in parts):
            raise ValueError("Enter resource packs as project:file pairs separated by commas.")
        pair = (int(parts[0]), int(parts[1]))
        if pair not in offered:
            raise ValueError(f"{pair[0]}:{pair[1]} is not a required file in this release.")
        if pair in selected:
            raise ValueError("Enter each resource-pack pair once.")
        selected.add(pair)
    if not selected:
        raise ValueError("Select at least one required resource pack.")
    return tuple(sorted(selected))


def _source_platform_summary(platform: Any) -> str:
    if not isinstance(platform, Mapping):
        return ""
    kind = platform.get("kind")
    version = platform.get("component_version")
    if not isinstance(kind, str) or not isinstance(version, str):
        return ""
    return f"{kind.title()} {version}"


class InterruptedSetupModal(ModalScreen[str]):
    """Offer both Core recovery routes for a retained interrupted stage."""

    def __init__(self, heading: str, destination: str) -> None:
        super().__init__()
        self.heading = heading
        self.destination = destination

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Static(self.heading, id="review-heading")
            with VerticalScroll(id="review-scroll"):
                yield Static(
                    f"Destination: {self.destination}\n\n"
                    "Core found files from an interrupted setup. Resume checks those files "
                    "and completes the operation when they match. Keep files and start over "
                    "moves the interrupted stage aside so you can prepare a new attempt. "
                    "The retained files remain in your local Workbench state.",
                    id="review-body",
                )
            yield Static("←/→ Choose  ·  Enter Confirm  ·  Esc Cancel", classes="keyboard-hint")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="setup-recovery-cancel")
                yield Button("Keep files and start over", id="setup-recovery-abandon")
                yield Button("Resume setup", id="setup-recovery-reconcile", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#setup-recovery-cancel", Button).focus()

    def on_key(self, event: events.Key) -> None:
        buttons = ["setup-recovery-cancel", "setup-recovery-abandon", "setup-recovery-reconcile"]
        if event.key in {"right", "down", "left", "up"}:
            current = next((index for index, name in enumerate(buttons)
                            if self.query_one(f"#{name}", Button).has_focus), 0)
            offset = 1 if event.key in {"right", "down"} else -1
            self.query_one(f"#{buttons[(current + offset) % len(buttons)]}", Button).focus()
            event.stop()
        elif event.key == "escape":
            self.dismiss("cancel")
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id.removeprefix("setup-recovery-")
        if action in {"cancel", "abandon", "reconcile"}:
            self.dismiss(action)


class ReleaseUpdateModal(ModalScreen[str]):
    """Present Core's exact published pack release choice."""

    def __init__(self, check: Mapping[str, Any]) -> None:
        super().__init__()
        candidate = check["candidate"]
        self.candidate = candidate
        same_version = candidate["version"] == check["selected_version"]
        self.heading = (
            "Supersymmetry published archive changed" if same_version
            else "Supersymmetry update available"
        )
        self.accept_label = "Use published archive" if same_version else "Version up"
        self.ignore_label = "Ignore this publication" if same_version else "Ignore this release"
        size_mib = candidate["asset_size"] / (1024 * 1024)
        if same_version:
            selected = check.get("selected")
            saved_digest = selected.get("asset_sha256") if isinstance(selected, Mapping) else None
            published_digest = candidate.get("asset_sha256")
            digest_detail = (
                f"Saved SHA-256: {saved_digest}\nPublished SHA-256: {published_digest}\n"
                if isinstance(saved_digest, str) and isinstance(published_digest, str)
                else ""
            )
            explanation = (
                "GitHub now serves different archive bytes under this same release tag.\n"
                + digest_detail
                + "\nUse published archive downloads and checks those bytes, then saves "
                "them as your pack choice. Your workspace stays unchanged. Ignore "
                "keeps your saved choice and stops prompting for those bytes."
            )
        else:
            explanation = (
                "Version up downloads and checks this archive, then saves it as your "
                "pack choice. Your workspace stays unchanged. Ignore keeps your "
                "current choice and stops prompting for this release."
            )
        self.body = (
            f"Saved pack: {check['selected_version']}\n"
            f"Latest published release: {candidate['version']}\n"
            f"Published: {candidate.get('published_at') or 'unknown'}\n"
            f"Client archive: {candidate['asset_name']} ({size_mib:.1f} MiB)\n"
            f"{candidate.get('release_url') or ''}\n\n"
            f"{explanation}"
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Static(self.heading, id="review-heading")
            with VerticalScroll(id="review-scroll"):
                yield Static(Text(self.body), id="review-body")
            yield Static("←/→ Choose  ·  Enter Confirm  ·  Esc Later", classes="keyboard-hint")
            with Horizontal(classes="button-row"):
                yield Button("Later", id="release-later")
                yield Button(self.ignore_label, id="release-ignore")
                yield Button(self.accept_label, id="release-accept", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#release-later", Button).focus()

    def on_key(self, event: events.Key) -> None:
        buttons = ["release-later", "release-ignore", "release-accept"]
        if event.key in {"right", "down", "left", "up"}:
            current = next((index for index, name in enumerate(buttons)
                            if self.query_one(f"#{name}", Button).has_focus), 0)
            offset = 1 if event.key in {"right", "down"} else -1
            self.query_one(f"#{buttons[(current + offset) % len(buttons)]}", Button).focus()
            event.stop()
        elif event.key == "escape":
            self.dismiss("later")
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({
            "release-later": "later",
            "release-ignore": "ignore",
            "release-accept": "accept",
        }[event.button.id])


class _InstancePathTree(DirectoryTree):
    """Show only directories and, when requested, ZIP archives."""

    def __init__(self, path: Path, *, include_zips: bool) -> None:
        self.include_zips = include_zips
        super().__init__(path, id="instance-path-tree")

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for path in paths:
            try:
                if path.is_dir() or (self.include_zips and path.is_file()
                                     and path.suffix.casefold() == ".zip"):
                    yield path
            except OSError:
                continue


class InstancePathPicker(ModalScreen[Path | None]):
    """A keyboard-first file tree for a ZIP or an existing Prism folder."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("backspace", "parent_folder", "Parent folder"),
        Binding("ctrl+g", "focus_location", "Go to folder"),
    ]

    def __init__(self, location: Path, *, choose_zip: bool,
                 heading: str | None = None) -> None:
        super().__init__()
        self.location = location
        self.choose_zip = choose_zip
        self.heading = heading

    def compose(self) -> ComposeResult:
        with Vertical(id="instance-path-dialog"):
            yield Static(
                self.heading or ("Choose a complete Prism ZIP" if self.choose_zip else
                                 "Choose an existing Prism data folder"),
                id="instance-path-heading",
            )
            yield Static(
                "↑↓ highlight · Space open folder · Enter choose ZIP · "
                "Backspace parent · Ctrl+G go to folder · Esc cancel"
                if self.choose_zip else
                "↑↓ highlight · Space open folder · Enter choose folder · "
                "Backspace parent · Ctrl+G go to folder · Esc cancel",
                id="instance-path-hint",
            )
            yield Static("↑↓ Move  ·  Space Open  ·  Enter Choose",
                         id="instance-path-hint-narrow")
            yield _InstancePathTree(self.location, include_zips=self.choose_zip)
            yield Input(value=str(self.location), placeholder="/path/to/folder",
                        id="instance-path-location")
            yield Static("", id="instance-path-status")
            with Horizontal(classes="button-row"):
                yield Button("Use highlighted ZIP" if self.choose_zip else "Use this folder",
                             id="instance-path-choose", disabled=self.choose_zip)
                yield Button("Cancel", id="instance-path-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#instance-path-tree", _InstancePathTree).focus()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.control.id != "instance-path-tree":
            return
        node = event.node
        path = node.data.path if node.data is not None else None
        valid = bool(path and (path.is_file() and path.suffix.casefold() == ".zip"
                              if self.choose_zip else path.is_dir()))
        self.query_one("#instance-path-choose", Button).disabled = not valid

    def _choose_path(self, path: Path) -> None:
        if self.choose_zip:
            if path.is_file() and path.suffix.casefold() == ".zip":
                self.dismiss(path)
            else:
                self.query_one("#instance-path-status", Static).update(
                    "Choose an existing ZIP file. Press Space to open a folder."
                )
        elif path.is_dir():
            self.dismiss(path)
        else:
            self.query_one("#instance-path-status", Static).update(
                "Choose an existing folder, or type a new path on the setup screen."
            )

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._choose_path(event.path)

    def on_directory_tree_directory_selected(self,
                                              event: DirectoryTree.DirectorySelected) -> None:
        if self.choose_zip:
            event.node.expand()
        else:
            self._choose_path(event.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "instance-path-location":
            return
        candidate = Path(event.value).expanduser()
        if not candidate.is_absolute():
            self.query_one("#instance-path-status", Static).update(
                "Enter an absolute Linux folder path. In WSL, Windows drives are under /mnt."
            )
            return
        if candidate.is_dir():
            tree = self.query_one("#instance-path-tree", _InstancePathTree)
            tree.path = candidate
            tree.focus()
        elif self.choose_zip and candidate.is_file() and candidate.suffix.casefold() == ".zip":
            self.dismiss(candidate)
        else:
            self.query_one("#instance-path-status", Static).update(
                "That folder is unavailable. You can type a new Prism path on the setup screen."
                if not self.choose_zip else "Choose an existing ZIP file or folder."
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "instance-path-cancel":
            self.dismiss(None)
        elif event.button.id == "instance-path-choose":
            node = self.query_one("#instance-path-tree", _InstancePathTree).cursor_node
            if node is not None and node.data is not None:
                self._choose_path(node.data.path)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_parent_folder(self) -> None:
        tree = self.query_one("#instance-path-tree", _InstancePathTree)
        node = tree.cursor_node
        current = node.data.path if node is not None and node.data is not None else Path(tree.path)
        parent = current.parent
        tree.path = parent
        self.query_one("#instance-path-location", Input).value = str(parent)
        tree.focus()

    def action_focus_location(self) -> None:
        location = self.query_one("#instance-path-location", Input)
        location.focus()
        location.action_select_all()


class PackInstanceScreen(KeyboardFormScreen):
    """Collect a complete Prism ZIP and install choices through Core."""

    SUB_TITLE = "Set up Supersymmetry"

    KEYBOARD_CANCEL = "pack-instance-back"

    KEYBOARD_FIELDS = (
        "pack-source-mode", "pack-fresh-optional", "pack-zip-path", "pack-zip-browse",
        "pack-workspace", "pack-workspace-register", "pack-java-choice",
        "pack-prism-root", "pack-prism-browse", "pack-installed-choice",
        "pack-fresh-download", "pack-zip-import", "pack-instance-back", "pack-instance-install",
        "pack-instance-save-location", "pack-instance-show",
        "pack-instance-launch",
    )

    def __init__(self, choice: Mapping[str, Any], workspaces: Mapping[str, Any],
                 installations: list[Mapping[str, Any]] | None = None,
                 installed_error: str | None = None) -> None:
        super().__init__()
        self.choice = choice
        self.workspaces = workspaces
        self.installations = installations or []
        self.installed_error = installed_error
        self.busy = False
        self.source_mode = (
            "zip" if choice["choice"].get("source_kind") == "user-prism-zip" else "official"
        )
        self.provider_unavailable = True
        self.provider_checked = False
        self.provider_problem = ""
        self.install_plan_id: str | None = (
            str(self.installations[0]["plan_id"]) if self.installations else None
        )
        self._returning_from_java_choices = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        entries = [row for row in self.workspaces.get("entries", [])
                   if isinstance(row, dict) and isinstance(row.get("name"), str)]
        names = [row["name"] for row in entries]
        selected = self.choice["choice"]
        preferred = selected.get("workspace_name") or self.workspaces.get("default")
        if preferred not in names:
            preferred = names[0] if names else ""
        launcher = selected.get("launcher_root") or str(Path.home() / ".local/share/PrismLauncher")
        yield Header(icon="W")
        with VerticalScroll(id="pack-scroll"):
            yield Static("Set up Supersymmetry", classes="screen-heading")
            yield Static(
                "Choose a pack source, a workspace, and a Prism folder. "
                "Workbench will then prepare and install a separate instance.",
                classes="screen-intro",
            )
            yield Static(
                "↑/↓ Move  ·  Enter Edit/Save  ·  Esc Cancel/Back",
                classes="keyboard-hint",
            )
            yield Static("1  PACK SOURCE", classes="field-label")
            yield Select([
                ("Download the published release", "official"),
                ("Import a complete Prism instance ZIP", "zip"),
            ], value=self.source_mode, allow_blank=False, id="pack-source-mode")
            yield Static("Checking official download availability…", id="pack-source-note")
            yield Checkbox("Include optional mods", value=True,
                           id="pack-fresh-optional")
            yield Static("Complete Prism instance ZIP", classes="field-label", id="pack-zip-label")
            with Horizontal(classes="instance-path-row", id="pack-zip-row"):
                yield Input(placeholder="/absolute/path/to/Supersymmetry-instance.zip",
                            id="pack-zip-path")
                yield Button("Browse ZIPs", id="pack-zip-browse")
            yield Static("2  WORKSPACE AND JAVA", classes="field-label")
            yield Select([(_workspace_option_label(row["name"], row["path"]), row["name"])
                          for row in entries]
                         or [("Add a workspace to continue", "")],
                         value=preferred, allow_blank=False, id="pack-workspace")
            yield Button("Add workspace", id="pack-workspace-register")
            yield Button("Choose Java", id="pack-java-choice",
                         disabled=not bool(names))
            yield Static(
                "Managed Java 25 is the Cleanroom default. You can choose Java 8 or your own path.",
                id="pack-java-note",
            )
            yield Static("3  PRISM LOCATION", classes="field-label")
            with Horizontal(classes="instance-path-row"):
                yield Input(value=launcher, id="pack-prism-root")
                yield Button("Browse", id="pack-prism-browse")
            if self.installations:
                yield Static("Installed instances", classes="field-label")
                yield Select([
                    (f"{row['instance_path']} · {row.get('java_selection_state') or 'Java choice unknown'}",
                     row["plan_id"])
                    for row in self.installations
                ], value=self.install_plan_id, allow_blank=False,
                    id="pack-installed-choice")
        current = selected.get("source_plan_id")
        state = self.choice.get("source_state")
        source_label = (
            "complete Prism instance ZIP" if selected.get("source_kind") == "user-prism-zip"
            else "published release"
        )
        with Vertical(id="pack-action-panel"):
            yield Static(
                (f"Saved source: {source_label}. Ready for installation."
                 if current and state == "retained" else
                 "Choose a pack source and workspace to continue.")
                + (f"\nInstalled instances could not be checked: {self.installed_error}"
                   if self.installed_error else ""),
                id="pack-instance-status",
            )
            with Horizontal(classes="button-row", id="pack-source-actions"):
                yield Button("Download game files", id="pack-fresh-download",
                             variant="primary", disabled=not bool(names))
                yield Button("Import complete ZIP", id="pack-zip-import",
                             disabled=not bool(names))
                yield Button("Back", id="pack-instance-back")
            with Horizontal(classes="button-row", id="pack-ready-actions"):
                yield Button("Install in Prism", id="pack-instance-install",
                             disabled=not bool(selected.get("source_plan_id") and
                                               self.choice.get("source_state") == "retained"))
                yield Button("Save new Prism location", id="pack-instance-save-location",
                             disabled=not bool(selected.get("source_plan_id") and
                                               self.choice.get("source_state") == "retained"))
            with Horizontal(classes="button-row", id="pack-installed-actions"):
                yield Button("Open in Prism", id="pack-instance-show",
                             disabled=self.install_plan_id is None)
                yield Button("Launch game", id="pack-instance-launch",
                             disabled=self.install_plan_id is None)
        yield Footer()

    def on_mount(self) -> None:
        self._update_source_mode()
        self.start_keyboard_navigation()
        self.check_official_availability()

    def _update_source_mode(self) -> None:
        official = self.query_one("#pack-source-mode", Select).value != "zip"
        for field in ("#pack-zip-label", "#pack-zip-row", "#pack-zip-path", "#pack-zip-browse"):
            self.query_one(field).display = not official
        self.query_one("#pack-fresh-optional", Checkbox).display = official
        self.query_one("#pack-fresh-download", Button).display = official
        self.query_one("#pack-zip-import", Button).display = not official
        has_workspace = bool([row for row in self.workspaces.get("entries", [])
                              if isinstance(row, dict) and isinstance(row.get("name"), str)])
        self.query_one("#pack-fresh-download", Button).disabled = (
            not has_workspace or not self.provider_checked or self.busy
        )
        self.query_one("#pack-fresh-download", Button).label = (
            "Check saved official files" if self.provider_unavailable else "Download game files"
        )
        self.query_one("#pack-zip-import", Button).disabled = not has_workspace or self.busy
        saved = self.choice["choice"]
        saved_mode = "zip" if saved.get("source_kind") == "user-prism-zip" else "official"
        retained = bool(saved.get("source_plan_id")
                        and self.choice.get("source_state") == "retained"
                        and saved_mode == ("official" if official else "zip"))
        for field in ("#pack-instance-install", "#pack-instance-save-location"):
            self.query_one(field, Button).display = retained
        self.query_one("#pack-ready-actions", Horizontal).display = retained
        installed = self.install_plan_id is not None
        for field in ("#pack-instance-show", "#pack-instance-launch"):
            self.query_one(field, Button).display = installed
        self.query_one("#pack-installed-actions", Horizontal).display = installed
        if official:
            note = (
                "Official download is unavailable right now. Saved files can still be checked."
                if self.provider_unavailable else
                "Official download is available."
            ) if self.provider_checked else "Checking official download access…"
        else:
            note = (
                "Official download is unavailable right now. A complete Prism ZIP can be imported."
                if self.provider_checked and self.provider_unavailable else
                "Choose a complete Prism ZIP containing the game files."
            )
        if self.provider_problem and official:
            note = f"Could not check download access: {self.provider_problem}. You can check saved files."
        if saved.get("source_plan_id") and self.choice.get("source_state") == "retained" and not retained:
            note += " A source from the other route is saved; switch back to install it."
        self.query_one("#pack-source-note", Static).update(note)

    def _source_instruction(self) -> str:
        official = self.query_one("#pack-source-mode", Select).value != "zip"
        has_workspace = bool(self.workspaces.get("entries"))
        if not has_workspace:
            return "Add a workspace, then choose your pack files."
        if not official:
            return "Choose a complete Prism ZIP, then Import complete ZIP."
        if self.provider_unavailable:
            return "Choose Check saved official files, or switch to ZIP import."
        return "Choose Download game files to prepare the published release."

    @work(exclusive=True, group="pack-provider-preflight")
    async def check_official_availability(self) -> None:
        try:
            checked = await self.core.pack_instance_fresh_provider_status()
            provider = checked["provider"]
            self.provider_unavailable = provider["status"] != "configured"
        except (CoreClientError, TimeoutError) as exc:
            self.provider_unavailable = True
            self.provider_problem = str(exc)
        self.provider_checked = True
        if self.provider_unavailable and not self.choice["choice"].get("source_plan_id"):
            self.query_one("#pack-source-mode", Select).value = "zip"
        if not self.choice["choice"].get("source_plan_id"):
            self.query_one("#pack-instance-status", Static).update(
                self._source_instruction()
            )
        self._update_source_mode()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pack-instance-back":
            self.app.pop_screen()
        elif event.button.id == "pack-workspace-register":
            self.register_workspace()
        elif event.button.id == "pack-java-choice":
            self.open_java_choices()
        elif event.button.id == "pack-zip-browse":
            self.pick_instance_path("zip")
        elif event.button.id == "pack-prism-browse":
            self.pick_instance_path("prism")
        elif event.button.id == "pack-zip-import":
            self.import_zip()
        elif event.button.id == "pack-fresh-download":
            self.download_official()
        elif event.button.id == "pack-instance-install":
            self.install_selected()
        elif event.button.id == "pack-instance-save-location":
            self.save_location()
        elif event.button.id in {"pack-instance-show", "pack-instance-launch"}:
            self.launch_selected("show" if event.button.id == "pack-instance-show" else "launch")

    @work(exclusive=True, group="pack-instance-path-picker")
    async def pick_instance_path(self, kind: str) -> None:
        if self.busy:
            return
        input_id = "pack-zip-path" if kind == "zip" else "pack-prism-root"
        raw = self.query_one(f"#{input_id}", Input).value.strip()
        default = Path.home() / "Downloads" if kind == "zip" else Path.home()
        location = Path(raw).expanduser().absolute() if raw else default
        if kind == "zip" and location.suffix.casefold() == ".zip":
            location = location.parent
        while not location.is_dir() and location != location.parent:
            location = location.parent
        if not location.is_dir():
            location = Path.home()
        selected = await self.app.push_screen_wait(
            InstancePathPicker(location, choose_zip=kind == "zip")
        )
        if selected is not None:
            self.query_one(f"#{input_id}", Input).value = str(selected)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "pack-source-mode":
            self._update_source_mode()
            if self.provider_checked and not self.choice["choice"].get("source_plan_id"):
                self.query_one("#pack-instance-status", Static).update(
                    self._source_instruction()
                )
        elif event.select.id == "pack-installed-choice" and isinstance(event.value, str):
            self.install_plan_id = event.value

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        super().on_screen_resume(event)
        if self._returning_from_java_choices:
            self._returning_from_java_choices = False
            self.refresh_workspace_choices()

    def open_java_choices(self) -> None:
        if self.busy:
            return
        workspace = self.query_one("#pack-workspace", Select).value
        self._returning_from_java_choices = True
        self.app.push_screen(WorkspaceChoicesScreen(
            self.workspaces, selected_name=workspace if isinstance(workspace, str) else None,
        ))

    @work(exclusive=True, group="pack-workspace-refresh")
    async def refresh_workspace_choices(self) -> None:
        status = self.query_one("#pack-instance-status", Static)
        try:
            record = await self.core.workspace_choices()
        except (CoreClientError, TimeoutError) as exc:
            status.update(f"Could not refresh workspace and Java choices: {exc}")
            return
        current = self.query_one("#pack-workspace", Select).value
        entries = [row for row in record.get("entries", [])
                   if isinstance(row, dict) and isinstance(row.get("name"), str)]
        names = [row["name"] for row in entries]
        self.workspaces = record
        selector = self.query_one("#pack-workspace", Select)
        selector.set_options([(_workspace_option_label(row["name"], row["path"]), row["name"])
                              for row in entries]
                             or [("Add a workspace below to continue", "")])
        selector.value = (current if current in names else
                          record.get("default") if record.get("default") in names else
                          names[0] if names else "")
        self.query_one("#pack-java-choice", Button).disabled = not bool(names)
        self._update_source_mode()
        status.update("Workspace and Java choices refreshed. Continue setup with the selected workspace.")

    @work(exclusive=True, group="pack-workspace-register")
    async def register_workspace(self) -> None:
        if self.busy:
            return
        selected = await self.app.push_screen_wait(WorkspaceRegisterScreen(
            self.workspaces, initial_path=self.app.initial_workspace,  # type: ignore[attr-defined]
        ))
        if selected is None:
            return
        name, record = selected
        self.workspaces = record
        self.query_one("#pack-workspace", Select).set_options([
            (_workspace_option_label(row["name"], row["path"]), row["name"])
            for row in record["entries"]
        ])
        self.query_one("#pack-workspace", Select).value = name
        self.query_one("#pack-java-choice", Button).disabled = False
        self._update_source_mode()
        self.query_one("#pack-instance-status", Static).update(
            (f"Workspace {name} is saved. Choose Save instance location to use it "
             "with the retained source." if self.choice.get("source_state") == "retained"
             else f"Workspace {name} is saved. Select a pack source to continue.")
        )

    @work(exclusive=True, group="pack-instance-location")
    async def save_location(self) -> None:
        if self.busy or self.choice.get("source_state") != "retained":
            return
        selected = self.choice["choice"]
        launcher = self.query_one("#pack-prism-root", Input).value.strip()
        workspace = self.query_one("#pack-workspace", Select).value
        status = self.query_one("#pack-instance-status", Static)
        if (not launcher or not Path(launcher).is_absolute()
                or not isinstance(workspace, str) or not workspace):
            status.update("Choose an absolute Prism data folder and saved workspace.")
            return
        approved = await self.app.push_screen_wait(ReviewModal(
            "Save this instance location?",
            f"Source: {selected['source_plan_id']}\n"
            f"Prism data folder: {launcher}\nWorkspace: {workspace}\n\n"
            "Core will keep the selected source and update these saved choices.",
            confirm_label="Save location",
        ))
        if not approved:
            return
        self.busy = True
        try:
            self.choice = await self.core.pack_instance_choice_select(
                selected["source_plan_id"],
                expected_record_id=selected["record_id"],
                launcher_root=launcher, workspace_name=workspace,
                source_kind=selected["source_kind"],
            )
            self.install_plan_id = None
            self.query_one("#pack-instance-show", Button).disabled = True
            self.query_one("#pack-instance-launch", Button).disabled = True
            status.update("Prism folder and workspace saved for the retained source.")
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False

    @work(exclusive=True, group="pack-instance-zip")
    async def import_zip(self) -> None:
        if self.busy:
            return
        archive = self.query_one("#pack-zip-path", Input).value.strip()
        launcher = self.query_one("#pack-prism-root", Input).value.strip()
        workspace = self.query_one("#pack-workspace", Select).value
        status = self.query_one("#pack-instance-status", Static)
        if (not archive or not Path(archive).is_absolute()
                or not launcher or not Path(launcher).is_absolute()
                or not isinstance(workspace, str) or not workspace):
            status.update("Choose an absolute ZIP path, Prism data folder, and saved workspace.")
            return
        self.busy = True
        self.query_one("#pack-zip-import", Button).disabled = True
        try:
            status.update("Core is checking where the Prism ZIP is stored…")
            stage_record = await self.core.pack_instance_zip_stage_plan(archive)
            stage = stage_record["stage"]
            retained_archive = stage["archive_path"]
            if stage["action"] == "copy":
                approved = await self.app.push_screen_wait(ReviewModal(
                    "Copy this ZIP into Linux Workbench state?",
                    f"Source: {stage['source_path']}\n"
                    f"Size: {stage['source_size'] / (1024 * 1024):.1f} MiB\n"
                    f"SHA-256: {stage['source_sha256']}\n\n"
                    "Core will retain the exact ZIP on the Linux filesystem so it can "
                    "review and import files from a Windows drive safely. The source "
                    "ZIP will remain in place.",
                    confirm_label="Copy ZIP to Workbench",
                ))
                if not approved:
                    status.update("ZIP transfer cancelled; the source ZIP was unchanged.")
                    return
                status.update("Core is retaining the ZIP on the Linux filesystem…")
                staged = await self.core.pack_instance_zip_stage_apply(
                    archive, stage["plan_id"],
                )
                retained_archive = staged["stage"]["archive_path"]
            status.update("Core is reviewing the complete ZIP and its gameplay files…")
            reviewed = await self.core.pack_instance_zip_plan(retained_archive)
            plan = reviewed["source"]
            platform = _source_platform_summary(plan.get("source_platform"))
            forge_java_guidance = (
                "\nThis ZIP declares Forge. Before installation, choose Java "
                "to select managed Java 8 or your own Java path if the "
                "pack requires it.\n"
                if isinstance(plan.get("source_platform"), Mapping)
                and plan["source_platform"].get("kind") == "forge" else ""
            )
            approved = await self.app.push_screen_wait(ReviewModal(
                "Import this Prism instance?",
                f"Source version: {plan.get('source_version') or 'user-provided'}\n"
                + (f"Platform: {platform}\n" if platform else "")
                + f"Gameplay files: {plan['file_count']}\n"
                f"Total bytes: {plan.get('total_bytes', 'unknown')}\n"
                f"Archive SHA-256: {plan.get('source_archive_sha256', 'unknown')}\n\n"
                "Core will retain the exact gameplay files in Workbench state and save "
                "this source, Prism folder, and workspace in your local preferences. "
                "A new Prism instance will be installed separately with the ZIP's "
                "platform and launcher settings. Its Java choice can be changed in Textual."
                + forge_java_guidance,
                confirm_label="Import complete ZIP",
            ))
            if not approved:
                status.update("Import cancelled; the ZIP was not retained.")
                return
            status.update("Core is retaining the reviewed ZIP contents…")
            imported = await self.core.pack_instance_zip_import(
                retained_archive, plan["plan_id"],
            )
            chosen = await self.core.pack_instance_choice_select(
                imported["source"]["plan_id"],
                expected_record_id=self.choice["choice"]["record_id"],
                launcher_root=launcher, workspace_name=workspace,
            )
            self.choice = chosen
            self.install_plan_id = None
            self.query_one("#pack-instance-show", Button).disabled = True
            self.query_one("#pack-instance-launch", Button).disabled = True
            self.query_one("#pack-instance-install", Button).disabled = False
            self._update_source_mode()
            status.update(
                f"Retained {imported['source']['file_count']} files. "
                "Your source and setup choices are saved."
            )
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self._update_source_mode()

    @work(exclusive=True, group="pack-instance-fresh")
    async def download_official(self) -> None:
        if self.busy or not self.provider_checked:
            return
        launcher = self.query_one("#pack-prism-root", Input).value.strip()
        workspace = self.query_one("#pack-workspace", Select).value
        status = self.query_one("#pack-instance-status", Static)
        if (not launcher or not Path(launcher).is_absolute()
                or not isinstance(workspace, str) or not workspace):
            status.update("Choose an absolute Prism data folder and saved workspace.")
            return
        self.busy = True
        self.query_one("#pack-fresh-download", Button).disabled = True
        optional_mode = ("default" if self.query_one(
            "#pack-fresh-optional", Checkbox,
        ).value else "omit")
        try:
            provider = (await self.core.pack_instance_fresh_provider_status())["provider"]
            if provider["status"] != "configured":
                self.provider_unavailable = True
                self._update_source_mode()
                approved = await self.app.push_screen_wait(ReviewModal(
                    "Check saved official files?",
                    "Workbench cannot download missing game files until its CurseForge "
                    "access is configured. If all files were saved earlier, setup can "
                    "still finish offline. Continuing may also offer to download the "
                    "published pack archive; that archive alone cannot install the game. "
                    "You can instead import a complete Prism instance ZIP.",
                    confirm_label="Check saved files",
                ))
                if not approved:
                    status.update("Official download needs Workbench provider access. "
                                  "Choose a complete Prism ZIP to install now.")
                    return
            release = await self.core.pack_release_show()
            selected = release["selected"]
            if release["artifact_state"] != "verified":
                approved = await self.app.push_screen_wait(ReviewModal(
                    f"Prepare Supersymmetry {selected['version']}?",
                    f"Core will download and verify the published release ZIP "
                    f"({selected['asset_size'] / (1024 * 1024):.1f} MiB) "
                    "before collecting its selected game files.",
                    confirm_label="Prepare published release",
                ))
                if not approved:
                    return
                status.update("Core is preparing the published release…")
                await self.core.pack_release_prepare(selected["release_id"])
            policy_record = await self.core.pack_instance_fresh_policy_status()
            policy = policy_record["policy"]
            if policy["status"] == "unavailable":
                status.update("Official release policy is unavailable: "
                              + str(policy.get("reason") or "release source is not ready"))
                return
            if policy["status"] == "review_required":
                entered: str | None = None
                problem: str | None = None
                while True:
                    entered = await self.app.push_screen_wait(ResourcepackMappingModal(
                        policy, entered=entered, problem=problem,
                    ))
                    if entered is None:
                        status.update("Resource-pack review cancelled; no release policy was saved.")
                        return
                    try:
                        resourcepack_pairs = _reviewed_resourcepack_pairs(
                            entered, policy["required_external_files"],
                        )
                    except ValueError as exc:
                        problem = str(exc)
                        continue
                    break
                optional_pairs = tuple(sorted(
                    (row["project_id"], row["file_id"])
                    for row in policy["optional_external_files"]
                ))
                status.update("Core is reviewing the selected release layout…")
                planned = await self.core.pack_instance_fresh_policy_plan(
                    resourcepack_pairs, optional_pairs,
                )
                plan = planned["policy"]["policy_plan"]
                placements = ", ".join(f"{project}:{file}" for project, file in resourcepack_pairs)
                approved = await self.app.push_screen_wait(ReviewModal(
                    "Save this release layout?",
                    f"Published pack: {plan['version']}\n"
                    f"Resource-pack IDs: {placements}\n"
                    f"Optional files available: {len(optional_pairs)}\n"
                    f"Override files: {plan['layout_policy']['override_file_count']}\n"
                    f"Core action: {plan['action']}\n\n"
                    "Core will retain this exact layout for the saved release. "
                    "The pack files are downloaded only after the next review.",
                    confirm_label="Save release layout",
                ))
                if not approved:
                    status.update("Release layout review cancelled; no policy was saved.")
                    return
                await self.core.pack_instance_fresh_policy_apply(
                    resourcepack_pairs, optional_pairs, plan["plan_id"],
                )
                policy_record = await self.core.pack_instance_fresh_policy_status()
                if policy_record["policy"]["status"] != "ready":
                    raise CoreClientError("Core could not reopen the reviewed release layout")
            checked = await self.core.pack_instance_fresh_status(optional_mode)
            progress = checked["fresh"]
            if progress["status"] == "unavailable":
                status.update("Official download is unavailable: "
                              + str(progress.get("reason") or "release source is not ready"))
                return
            if (progress["provider_state"] != "available"
                    and progress["ready_file_count"] != progress["selected_file_count"]):
                status.update("Official download awaits Workbench's CurseForge provider access. "
                              "You can import a complete Prism instance ZIP now.")
                return
            if progress["status"] == "invalid":
                status.update("A retained release file changed. Core preserved it for review.")
                return
            total = progress["selected_file_count"]
            ready = progress["ready_file_count"]
            remaining = total - ready
            approved = await self.app.push_screen_wait(ReviewModal(
                (f"Use {ready} saved game files?" if remaining == 0 else
                 f"Download {remaining} selected game files?"),
                f"Published pack: {progress['release_version']}\n"
                f"Already retained: {ready}/{total}\n"
                f"Optional mods: {'included' if optional_mode == 'default' else 'omitted'}\n\n"
                + ("Core will verify and compose the saved files into a source for installation."
                 if remaining == 0 else
                 "Core will download each authorized file, verify it, and retain progress "
                 "so setup can resume after interruption."),
                confirm_label="Use saved files" if remaining == 0 else "Download and retain",
            ))
            if not approved:
                return
            status.update("Core is retaining the published pack overrides…")
            overrides = await self.core.pack_instance_fresh_overrides(optional_mode)
            override_plan_id = overrides["fresh"]["override_plan_id"]
            for row in progress["files"]:
                if row["status"] == "ready":
                    continue
                project_id, file_id = row["project_id"], row["file_id"]
                status.update(f"Downloading selected game files: {ready}/{total}…")
                await self.core.pack_instance_fresh_file(
                    project_id, file_id, optional_mode,
                )
                ready += 1
                status.update(f"Verified selected game files: {ready}/{total}.")
            published = await self.core.pack_instance_fresh_publish(
                override_plan_id, optional_mode,
            )
            source = published["fresh"]["composition_result"]
            self.choice = await self.core.pack_instance_choice_select(
                source["plan_id"], expected_record_id=self.choice["choice"]["record_id"],
                launcher_root=launcher, workspace_name=workspace,
                source_kind="published-release",
            )
            self.install_plan_id = None
            self.query_one("#pack-instance-show", Button).disabled = True
            self.query_one("#pack-instance-launch", Button).disabled = True
            self.query_one("#pack-instance-install", Button).disabled = False
            self._update_source_mode()
            status.update(
                f"Published release {progress['release_version']} is ready: "
                f"{ready} selected game files. The source and setup choices are saved. "
                "Choose Install in Prism."
            )
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self._update_source_mode()

    @work(exclusive=True, group="pack-instance-install")
    async def install_selected(self) -> None:
        if self.busy:
            return
        selected = self.choice["choice"]
        status = self.query_one("#pack-instance-status", Static)
        if not selected.get("source_plan_id") or self.choice.get("source_state") != "retained":
            status.update("Select a retained Supersymmetry source before installation.")
            return
        selected_mode = "zip" if selected.get("source_kind") == "user-prism-zip" else "official"
        if self.query_one("#pack-source-mode", Select).value != selected_mode:
            status.update("Choose the saved pack source before installing it.")
            return
        if (self.query_one("#pack-prism-root", Input).value.strip() != selected.get("launcher_root")
                or self.query_one("#pack-workspace", Select).value != selected.get("workspace_name")):
            status.update("The Prism folder or workspace changed. Save them with a source import first.")
            return
        imported = selected.get("source_kind") == "user-prism-zip"
        preparation = (
            "Core will use the imported ZIP's platform and launcher files. It may "
            "acquire the selected workspace Java before showing the exact Prism "
            "destination for final review."
            if imported else
            "Core will use the published pack with Cleanroom. It may download "
            "the selected Java runtime and pinned Cleanroom client template before "
            "showing the exact Prism destination for final review."
        )
        approved = await self.app.push_screen_wait(ReviewModal(
            "Prepare this instance?",
            preparation,
            confirm_label="Prepare installation",
        ))
        if not approved:
            return
        self.busy = True
        self.query_one("#pack-instance-install", Button).disabled = True
        try:
            root_record = await self.core.pack_instance_root_plan()
            root_plan = root_record["prism_root"]
            if root_plan["action"] == "reconcile":
                recovery = await self.app.push_screen_wait(InterruptedSetupModal(
                    "Interrupted Prism folder setup",
                    root_plan["launcher_root"],
                ))
                if recovery == "cancel":
                    status.update("Prism folder recovery cancelled.")
                    return
                recovered = await self.core.pack_instance_root_recover(
                    root_plan["plan_id"], recovery,
                )
                if recovery == "abandon":
                    status.update(
                        "Interrupted Prism folder files were retained at "
                        + recovered["prism_root"]["retained_stage_path"]
                        + ". Choose Install in Prism again to start over."
                    )
                    return
                root_plan = (await self.core.pack_instance_root_plan())["prism_root"]
            if root_plan["state"] != "ready":
                status.update("Prism folder needs attention: "
                              + ", ".join(root_plan.get("blockers", [])))
                return
            status.update("Core is preparing the selected Java and "
                          + ("imported platform…" if imported else "Cleanroom client…"))
            prepared = await self.core.pack_instance_install_prepare()
            plan = prepared["installation"]
            if plan.get("state") != "ready":
                blockers = ", ".join(str(item) for item in plan.get("blockers", []))
                if "interrupted-install-needs-reconcile" in plan.get("blockers", []):
                    recovery = await self.app.push_screen_wait(InterruptedSetupModal(
                        "Interrupted Supersymmetry installation",
                        plan["instance_path"],
                    ))
                    if recovery == "cancel":
                        status.update("Instance recovery cancelled.")
                        return
                    recovered = await self.core.pack_instance_install_recover(
                        plan["plan_id"], recovery,
                    )
                    result = recovered["installation"]
                    if recovery == "abandon":
                        status.update(
                            "Interrupted instance files were retained at "
                            + result["retained_stage_path"]
                            + ". Choose Install in Prism again to start over."
                        )
                    else:
                        self._show_installed(result, status)
                    return
                status.update("Installation needs attention: " + (blockers or "Core could not prepare it."))
                return
            platform = plan.get("source_platform")
            forge_java_25 = (
                imported and isinstance(platform, Mapping)
                and platform.get("kind") == "forge"
                and plan.get("java_selected_feature") == 25
            )
            java_warning = (
                "\nThis imported ZIP declares Forge and Java 25 is selected. "
                "Check the pack's Java requirement. To use Java 8 or your own "
                "path, cancel and choose Java.\n"
                if forge_java_25 else ""
            )
            approved = await self.app.push_screen_wait(ReviewModal(
                "Install Supersymmetry in Prism?",
                f"Source: {plan.get('source_version') or 'complete imported instance'}\n"
                + (f"Platform: {_source_platform_summary(plan.get('source_platform'))}\n"
                   if _source_platform_summary(plan.get("source_platform")) else "")
                + f"Destination: {plan['instance_path']}\n"
                f"Java: {plan.get('java_selection_state') or 'unavailable'}"
                + (f" (version {plan['java_selected_feature']})"
                   if plan.get("java_selected_feature") is not None else "")
                + f"\nPrism account: {plan.get('account_state', 'unknown')}\n\n"
                + java_warning
                + "Core will create a separate instance. Prism may ask you to sign in "
                "before the first launch. Review Java compatibility for an imported "
                "platform before continuing.",
                confirm_label="Install instance",
            ))
            if not approved:
                status.update("Prepared source retained; installation cancelled.")
                return
            status.update("Core is installing the reviewed instance…")
            installed = await self.core.pack_instance_install_apply(plan["plan_id"])
            self._show_installed(installed["installation"], status)
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self.query_one("#pack-instance-install", Button).disabled = False

    def _show_installed(self, result: Mapping[str, Any], status: Static) -> None:
        self.install_plan_id = str(result["plan_id"])
        self.query_one("#pack-instance-show", Button).disabled = False
        self.query_one("#pack-instance-launch", Button).disabled = False
        self._update_source_mode()
        status.update(
            f"Installed: {result['instance_path']}\n"
            "Use Open in Prism to sign in, then Launch game. Runtime compatibility "
            "remains unconfirmed until an observed game launch."
        )

    @work(exclusive=True, group="pack-instance-launch")
    async def launch_selected(self, mode: str) -> None:
        if self.busy or self.install_plan_id is None:
            return
        status = self.query_one("#pack-instance-status", Static)
        self.busy = True
        self.query_one("#pack-instance-show", Button).disabled = True
        self.query_one("#pack-instance-launch", Button).disabled = True
        try:
            planned = await self.core.pack_instance_launch_plan(self.install_plan_id, mode)
            plan = planned["launch"]
            approved = await self.app.push_screen_wait(ReviewModal(
                "Open the installed instance in Prism?" if mode == "show" else
                "Launch the installed Supersymmetry game?",
                f"Instance: {plan['instance_path']}\n"
                f"Launcher: Prism {plan['prism_version']}\n\n"
                "Prism handles account sign-in. Core will supervise the launcher "
                "and retain a lifecycle receipt.",
                confirm_label="Open Prism" if mode == "show" else "Launch game",
            ))
            if not approved:
                return
            status.update("Core is starting Prism for the selected instance…")
            result = await self.core.pack_instance_launch_run(
                self.install_plan_id, plan["plan_id"], mode,
            )
            observed = result["launch"]
            status.update(
                f"Prism launcher: {observed.get('outcome', 'completed')}\n"
                "A launcher exit does not confirm that Minecraft started."
            )
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self.query_one("#pack-instance-show", Button).disabled = False
            self.query_one("#pack-instance-launch", Button).disabled = False


class ResultScreen(Screen[None]):
    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, heading: str, output: str) -> None:
        super().__init__()
        self.heading = heading
        self.sub_title = heading
        self.output = output

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static(self.heading, classes="screen-heading")
        yield RichLog(
            id="result-log", wrap=True, highlight=False, markup=False,
            auto_scroll=False,
        )
        with Horizontal(classes="button-row"):
            yield Button("Back", id="result-back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#result-log", RichLog).write(self.output)
        self.query_one("#result-back", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "result-back":
            self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


def _evidence_note(record: Mapping[str, Any]) -> str:
    gaps = record.get("evidence_gaps")
    if not isinstance(gaps, list) or not gaps:
        return ""
    lines = []
    for gap in gaps[:5]:
        if isinstance(gap, dict):
            code = str(gap.get("code", "unknown"))
            message = gap.get("message")
            lines.append(f"• {message} ({code})" if isinstance(message, str) and message else
                         f"• {code}")
    if len(gaps) > 5:
        lines.append(f"• {len(gaps) - 5} more limits in the full record")
    return "Evidence limits:\n" + "\n".join(lines) if lines else ""


def _observation_label(node: Mapping[str, Any], *, limit: int | None = None) -> str:
    """Prefer the owner's source-facing label over an internal graph hash."""
    properties = node.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    selected = ""
    for candidate in (properties.get("record_key"), properties.get("label"),
                      node.get("semantic_key")):
        if not isinstance(candidate, str):
            continue
        value = " ".join(candidate.split())
        if not value:
            continue
        if len(value) >= 48 and all(char in "0123456789abcdef" for char in value):
            continue
        selected = value
        break
    if not selected:
        kind = str(properties.get("family") or node.get("kind") or "observation")
        selected = kind.removeprefix("initialization-").replace("-", " ") + " observation"
    if limit is not None and len(selected) > limit:
        return selected[:limit - 1] + "…"
    return selected


def _analysis_summary(owner: str, record: Mapping[str, Any]) -> str:
    """A small, truthful reading surface; the complete owner record stays available."""
    lines: list[str] = []
    if owner == "atlas":
        if record.get("format") == "workbench-atlas-initialization-projection-v1":
            lines.extend((
                "Axiom observations are ready in Atlas.",
                f"Graph folder: {record.get('root', '?')}",
                f"Checked side: {record.get('selected_side', '?')}",
                f"Native outcome: {record.get('native_outcome', '?')}",
                f"Coverage: {record.get('capture_coverage', '?')}",
                "Choose Search this graph to inspect recorded values.",
            ))
            limitations = record.get("limitations")
            if isinstance(limitations, list) and limitations:
                lines.extend(("", "Limits:"))
                lines.extend(f"• {item}" for item in limitations[:5])
            return "\n".join(lines)
        if str(record.get("format", "")).startswith("workbench-atlas-observation-"):
            context = record.get("context")
            selection = record.get("selection")
            if isinstance(selection, dict):
                lines.append(f"Observation: {_observation_label(selection)}")
                lines.append(f"Kind: {selection.get('kind', '?')}")
            if isinstance(context, dict):
                binding = context.get("evidence_binding")
                if isinstance(binding, dict):
                    for key, label in (("native_outcome", "Native outcome"),
                                       ("coverage", "Capture coverage")):
                        if binding.get(key) is not None:
                            lines.append(f"{label}: {binding[key]}")
                if context.get("root"):
                    lines.append(f"Observation graph: {context['root']}")
            if isinstance(selection, dict):
                properties = selection.get("properties")
                if isinstance(properties, dict):
                    for key, label in (("record_key", "Record"), ("family", "Family"),
                                       ("label", "Label"), ("json_pointer", "Source pointer")):
                        value = properties.get(key)
                        if isinstance(value, str) and value:
                            lines.append(f"{label}: {value}")
                    if "raw_value" in properties:
                        lines.append("Captured raw value: open R Full record.")
                relationships = record.get("relationships")
                if isinstance(relationships, dict):
                    for direction in ("incoming", "outgoing"):
                        counts = relationships.get(direction)
                        if isinstance(counts, dict):
                            total = sum(value for value in counts.values()
                                        if isinstance(value, int) and value >= 0)
                            lines.append(f"{direction.capitalize()} links: {total}")
            if record.get("query") is not None:
                lines.append(f"Search: {record['query']}")
            results = record.get("results")
            if isinstance(results, list):
                lines.append(f"Results in this page: {len(results)}")
                for row in results[:50]:
                    if not isinstance(row, dict):
                        continue
                    node = row.get("node") if isinstance(row.get("node"), dict) else row
                    edge = row.get("edge") if isinstance(row.get("edge"), dict) else None
                    relation = f"{edge.get('relation', '?')} → " if edge else ""
                    lines.append(f"• {relation}{_observation_label(node)}")
            page = record.get("page")
            if isinstance(page, dict) and page.get("next_cursor"):
                lines.append("More results exist; narrow the search or request another page.")
            if isinstance(context, dict) and context.get("claim_boundary"):
                lines.extend(("", str(context["claim_boundary"])))
            return "\n".join(lines) or "Atlas returned an observation record."
        context = record.get("context")
        if not isinstance(context, dict):
            context = record
        kind = str(context.get("context_type", "unknown"))
        lines.append(
            "Source-only checkout: text occurrences, not observed recipes."
            if "source-only" in kind else
            "Captured recipe graph: observed relationships from this exact graph."
            if "graph" in kind else
            f"Evidence context: {kind}"
        )
        for key, label in (("root", "Source"), ("recipe_count", "Recipes"),
                           ("graph_set_id", "Graph")):
            if context.get(key) is not None:
                lines.append(f"{label}: {context[key]}")
        if record.get("query") is not None:
            results = record.get("results", [])
            lines.append(f"Search: {record['query']}")
            lines.append(f"Matches shown: {len(results) if isinstance(results, list) else 0}")
            if record.get("truncated"):
                lines.append("More matches exist. Narrow the search or raise its limit.")
        selection = record.get("selection")
        if isinstance(selection, dict):
            selected_name = _atlas_match_name(selection)
            if selected_name == selection.get("selection_id"):
                selected_name = str(selection.get("kind") or "Evidence") + " selection"
            lines.append(f"Selection: {selected_name}")
            lines.append(f"Kind: {selection.get('kind', '?')}")
            snippet = selection.get("snippet")
            if isinstance(snippet, str) and snippet.strip():
                short = " ".join(snippet.split())
                lines.append("Source text: " + (short[:157] + "…" if len(short) > 160 else short))
        if record.get("role"):
            lines.append(f"Evidence role: {record['role']}")
        ownership = record.get("ownership")
        if isinstance(ownership, dict) and ownership.get("status"):
            lines.append(f"Ownership: {ownership['status']}")
        page = record.get("page")
        if isinstance(page, dict):
            lines.append(f"Relationships: {page.get('offset', 0)}–"
                         f"{page.get('offset', 0) + len(record.get('links', []))}"
                         f" of {page.get('total', '?')}")
        if isinstance(record.get("links"), list):
            for row in record["links"][:100]:
                if isinstance(row, dict):
                    node = row.get("node") or {}
                    relationship = row.get("relationship") or {}
                    lines.append(
                        f"{row.get('direction', '?')} {relationship.get('relation', '?')}: "
                        f"{node.get('semantic_key', '?')}"
                    )
        note = _evidence_note(record)
        if note:
            lines.extend(("", note))
    elif owner == "axiom":
        result = record.get("result")
        if not isinstance(result, dict):
            result = record
        presentation = record.get("presentation")
        if not isinstance(presentation, dict):
            presentation = {}
        state = result.get("state", presentation.get("attempt_state", "unknown"))
        lines.append(f"Check state: {state}")
        if record.get("exit_code") not in (None, 0) and not result.get("native_status"):
            lines.append(f"Owner exit code: {record['exit_code']}")
        for key, label in (("native_status", "Native status"),
                           ("attempt_id", "Attempt"), ("findings_count", "Findings"),
                           ("coverage", "Captured evidence"), ("context_id", "Checked context"),
                           ("setup_state", "Setup"), ("setup_id", "Setup ID")):
            value = result.get(key)
            if value is not None:
                lines.append(f"{label}: {value}")
        native = result.get("native")
        native_result = native.get("result") if isinstance(native, dict) else None
        assessment = native_result.get("assessment") if isinstance(native_result, dict) else None
        if isinstance(assessment, dict) and assessment.get("coverage") is not None:
            lines.append(f"Native assessment: {assessment['coverage']}")
        if (result.get("native_outcome") is not None
                and result.get("native_outcome") != result.get("native_status")):
            lines.append(f"Native outcome: {result['native_outcome']}")
        if presentation.get("source_current") is False:
            lines.append("Saved source has changed since this check. Recheck before using locations.")
        if isinstance(result.get("pending"), list) and result["pending"]:
            lines.append("Still needed: " + ", ".join(map(str, result["pending"])))
        if isinstance(result.get("finding_page"), dict):
            page = result["finding_page"]
            payload = page.get("payload") or {}
            rows = payload.get("records", []) if isinstance(payload, dict) else []
            labels = presentation.get("finding_labels") or {}
            if isinstance(rows, list):
                findings = [row["value"] for row in rows if isinstance(row, dict)
                            and isinstance(row.get("value"), dict)]
                errors: list[dict[str, Any]] = []
                warnings: list[dict[str, Any]] = []
                others: list[dict[str, Any]] = []
                for finding in findings:
                    severity = str(finding.get("severity", "")).casefold()
                    if severity in {"error", "fatal", "critical"}:
                        errors.append(finding)
                    elif severity in {"warning", "warn"}:
                        warnings.append(finding)
                    else:
                        others.append(finding)
                shown = errors[:20] + others[:3] + warnings[:6]
                lines.extend(("", f"Findings in this page: {len(findings)}"
                              + (f" · showing {len(shown)}" if len(shown) < len(findings) else "")))
                if errors or warnings:
                    lines.append(f"Page severity: {len(errors)} error, {len(warnings)} warning"
                                 + (f", {len(others)} other" if others else ""))
                for finding in shown:
                    location = finding.get("location") or finding.get("nativeLocation") or {}
                    where = (f" · {location.get('path')}:{location.get('line')}"
                             if isinstance(location, dict) and location.get("path") else "")
                    label = labels.get(finding.get("id")) if isinstance(labels, dict) else None
                    severity = str(finding.get("severity", "finding")).upper()
                    lines.append(f"• {severity}: {label or finding.get('message') or finding.get('id', 'finding')}{where}")
                if len(shown) < len(findings):
                    lines.append(f"{len(findings) - len(shown)} more findings on this page are retained.")
                total = result.get("findings_count")
                if isinstance(total, int) and total > len(findings):
                    lines.append(f"{total - len(findings)} further findings are retained beyond this page.")
        if result.get("detail_state") == "not-loaded":
            lines.append("Open History to revisit this retained attempt.")
        if isinstance(result.get("attempts"), list):
            lines.extend(("", f"Retained checks: {len(result['attempts'])}"))
            for row in result["attempts"][:100]:
                if isinstance(row, dict):
                    lines.append(f"• {row.get('attempt_id', '?')} · {row.get('state', '?')}")
        if presentation.get("attempt_uri"):
            lines.extend(("", "Retained attempt for Atlas import:", str(presentation["attempt_uri"])))
        diagnostics = record.get("diagnostics")
        if diagnostics:
            lines.extend(("", "Diagnostics", str(diagnostics)))
        if result.get("attempt_id"):
            lines.extend(("", "Additional findings remain in the retained check record."))
    return "\n".join(lines) or "The owner returned a record without a compact summary. Open the full record."


class AnalysisResultScreen(Screen[None]):
    """Readable Atlas/Axiom answer with lossless owner JSON one key away."""

    BINDINGS = [
        ("escape", "back", "Back"),
        ("r", "toggle_raw", "Full record"),
        Binding("i", "import_atlas", "Open in Atlas", show=False),
        Binding("s", "search_graph", "Search graph", show=False),
    ]

    def __init__(self, heading: str, owner: str, record: Mapping[str, Any]) -> None:
        super().__init__()
        self.heading = heading
        self.sub_title = heading
        self.owner = owner
        self.record = record
        self.raw = False

    def _can_import_atlas(self) -> bool:
        return (self.owner == "axiom" and _axiom_import_source(self.record) is not None
                and _atlas_import_action(self.app.view.catalog) is not None)

    def _can_search_graph(self) -> bool:
        return (_observation_graph_root(self.record) is not None
                and _atlas_read_action(self.app.view.catalog,
                                       "atlas.observations-search") is not None)

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static(self.heading, classes="screen-heading")
        hint = "↑/↓ Scroll · R Full"
        if self._can_import_atlas():
            hint += " · I Open in Atlas"
        if self._can_search_graph():
            hint += " · S Search graph"
        yield Static(hint + " · Esc Back", classes="keyboard-hint")
        yield RichLog(id="result-log", min_width=1, wrap=True, highlight=False, markup=False,
                      auto_scroll=False)
        with Horizontal(classes="button-row"):
            if self._can_import_atlas():
                yield Button("Open in Atlas", id="analysis-import-atlas")
            if self._can_search_graph():
                yield Button("Search this graph", id="analysis-search-graph")
            yield Button("Full record", id="analysis-raw")
            yield Button("Back", id="analysis-back")
        yield Footer()

    def on_mount(self) -> None:
        self._render_record()
        self.query_one("#result-log", RichLog).focus()

    def _render_record(self) -> None:
        log = self.query_one("#result-log", RichLog)
        log.clear()
        log.write(
            json.dumps(self.record, ensure_ascii=False, indent=2, sort_keys=True)
            if self.raw else _analysis_summary(self.owner, self.record)
        )
        self.query_one("#analysis-raw", Button).label = (
            "Readable view" if self.raw else "Full record"
        )

    def action_toggle_raw(self) -> None:
        self.raw = not self.raw
        self._render_record()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_import_atlas(self) -> None:
        if not self._can_import_atlas():
            return
        source = _axiom_import_source(self.record)
        if source is not None:
            self.app.push_screen(AtlasImportScreen(self.record, source))

    def action_search_graph(self) -> None:
        if not self._can_search_graph():
            return
        root = _observation_graph_root(self.record)
        if root is not None and self.app.view.catalog is not None:
            self.app.push_screen(AtlasObservationSearchScreen(
                self.app.view.catalog, root
            ))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "analysis-import-atlas":
            self.action_import_atlas()
        elif event.button.id == "analysis-search-graph":
            self.action_search_graph()
        elif event.button.id == "analysis-raw":
            self.action_toggle_raw()
        elif event.button.id == "analysis-back":
            self.action_back()


def _axiom_import_source(record: Mapping[str, Any]) -> Path | None:
    """Use the attempt directory supplied by Axiom/Core, never derive one from an ID."""
    result = record.get("result")
    presentation = record.get("presentation")
    if (not isinstance(result, dict) or not isinstance(result.get("snapshot_id"), str)
            or not isinstance(presentation, dict)
            or not isinstance(presentation.get("attempt_uri"), str)):
        return None
    parsed = urlparse(presentation["attempt_uri"])
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    source = Path(unquote(parsed.path))
    return source if source.is_absolute() else None


def _atlas_import_action(catalog: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Only present Atlas admission when the installed catalog can execute it."""
    commands = catalog.get("commands") if isinstance(catalog, dict) else None
    if not isinstance(commands, list):
        return None
    return next((row for row in commands
                 if isinstance(row, dict)
                 and row.get("command_id") == "atlas.observations-import-snapshot"
                 and row.get("availability") in {"available", "experimental"}
                 and row.get("risk") == "mutating"
                 and row.get("preview") == "inert-only"), None)


def _atlas_read_action(catalog: Mapping[str, Any] | None,
                       command_id: str) -> Mapping[str, Any] | None:
    commands = catalog.get("commands") if isinstance(catalog, dict) else None
    if not isinstance(commands, list):
        return None
    return next((row for row in commands
                 if isinstance(row, dict) and row.get("command_id") == command_id
                 and _runnable_catalog_action(row)), None)


def _observation_graph_root(record: Mapping[str, Any]) -> Path | None:
    if (record.get("format") != "workbench-atlas-initialization-projection-v1"
            or record.get("state") != "complete"):
        return None
    raw = record.get("root")
    if not isinstance(raw, str) or not raw or any(c in raw for c in "\0\r\n"):
        return None
    root = Path(raw)
    return root if root.is_absolute() else None


class AtlasImportScreen(KeyboardFormScreen):
    """Review one Axiom-owned snapshot before Atlas publishes a new graph."""

    SUB_TITLE = "Open this check in Atlas"

    KEYBOARD_CANCEL = "atlas-import-back"
    KEYBOARD_FIELDS = ("atlas-import-output", "atlas-import-side",
                       "atlas-import-run", "atlas-import-back")

    def __init__(self, record: Mapping[str, Any], source: Path) -> None:
        super().__init__()
        self.record = record
        self.source = source
        self.busy = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        view = self.app.view  # type: ignore[attr-defined]
        locations = (view.environment or {}).get("locations", {})
        evidence = locations.get("evidence", {}) if isinstance(locations, dict) else {}
        root = evidence.get("path") if isinstance(evidence, dict) else None
        proposed = str(Path(root) / ("atlas-" + self.source.name)) if isinstance(root, str) else ""
        yield Header(icon="W")
        yield Static("Open this check in Atlas", classes="screen-heading")
        yield Static(
            "Atlas will verify Axiom's retained check and publish a new observation graph. "
            "This can take several minutes and use several GB of local disk space. "
            "The original check and pack files stay unchanged.", classes="screen-intro",
        )
        yield Static("↑/↓ Choose  ·  Enter Edit or Run  ·  Esc Back", classes="keyboard-hint")
        with VerticalScroll(id="atlas-import-body"):
            yield Static(f"Retained check: {self.source}", id="atlas-import-source")
            yield Static("New graph folder", classes="field-label")
            yield Input(value=proposed, placeholder="Choose an unused absolute folder",
                        id="atlas-import-output")
            yield Static("Check side", classes="field-label")
            yield Select((('Single check', 'single'), ('Candidate', 'candidate'),
                          ('Baseline', 'baseline')), value="single", id="atlas-import-side")
            yield Static("", id="atlas-import-status")
            with Horizontal(classes="button-row"):
                yield Button("Import in Atlas", id="atlas-import-run")
                yield Button("Back", id="atlas-import-back")
        yield Footer()

    def on_mount(self) -> None:
        self.start_keyboard_navigation()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "atlas-import-back" and not self.busy:
            self.app.pop_screen()
        elif event.button.id == "atlas-import-run":
            self.import_snapshot()

    @work(exclusive=True, group="atlas-import")
    async def import_snapshot(self) -> None:
        if self.busy:
            return
        status = self.query_one("#atlas-import-status", Static)
        raw = self.query_one("#atlas-import-output", Input).value.strip()
        target = Path(raw).expanduser() if raw else Path("")
        if not raw or not target.is_absolute() or target.exists():
            status.update("Choose a new absolute graph folder.")
            return
        side = self.query_one("#atlas-import-side", Select).value
        if side not in {"single", "candidate", "baseline"}:
            status.update("Choose the check side to import.")
            return
        catalog = self.app.view.catalog  # type: ignore[attr-defined]
        action = _atlas_import_action(catalog)
        if action is None:
            status.update("Atlas snapshot import is not installed for this profile.")
            return
        values = {"path": str(self.source), "pack_profile": "supersymmetry",
                  "output": str(target), "side": side, "json": True}
        try:
            review = await self.core.command_review(catalog, action, values)
            approved = await self.app.push_screen_wait(ReviewModal(
                "Import this check into Atlas?",
                f"Retained check: {self.source}\nNew graph: {target}\nSide: {side}\n\n"
                "Atlas will verify the retained evidence through the selected pack profile "
                "and publish a new graph under Core's custody. This can take several minutes "
                "and use several GB of local disk space. The original check and pack files "
                "stay unchanged; Axiom will not run again.",
                confirm_label="Import graph",
            ))
            if not approved:
                return
            self.busy = True
            self.query_one("#atlas-import-run", Button).disabled = True
            status.update("Building the Atlas graph… This can take several minutes; please wait.")
            output = await self.core.import_reviewed_atlas_snapshot(
                catalog, action, values, review
            )
            result = _atlas_record(output, "atlas.observations-import-snapshot")
            if result.get("format") != "workbench-atlas-initialization-projection-v1" or result.get("state") != "complete":
                raise CoreClientError("Atlas did not report a complete observation graph")
            self.app.push_screen(AnalysisResultScreen(
                "Atlas observation graph", "atlas", result
            ))
            status.update("Atlas graph ready. Choose Search this graph in the result.")
        except (CoreClientError, TimeoutError) as exc:
            status.update(f"Atlas import could not complete: {exc}")
        finally:
            self.busy = False
            self.query_one("#atlas-import-run", Button).disabled = False


def _atlas_record(output: CommandOutput, command_id: str) -> Mapping[str, Any]:
    if output.exit_code != 0:
        raise CoreClientError(output.stderr.strip() or output.stdout.strip()
                              or f"Atlas exited {output.exit_code}")
    try:
        events = [json.loads(line) for line in output.stdout.splitlines() if line.strip()]
        if not all(isinstance(event, dict) for event in events):
            raise ValueError("console events are not objects")
        owner_lines = [str(event["message"]) for event in events
                       if event.get("source") == command_id
                       and event.get("stream") == "stdout"
                       and isinstance(event.get("message"), str)]
        record = json.loads("\n".join(owner_lines))
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        raise CoreClientError("Atlas did not return a readable result: "
                              + (output.stderr.strip() or output.stdout.strip())[:800]) from exc
    if not isinstance(record, dict) or not str(record.get("format", "")).startswith("workbench-atlas-"):
        raise CoreClientError("Atlas returned an unsupported result")
    return record


def _atlas_match_name(row: Mapping[str, Any]) -> str:
    key = row.get("semantic_key")
    if isinstance(key, str) and key:
        return key
    source = row.get("source_path")
    line = row.get("line")
    column = row.get("column")
    if isinstance(source, str) and source:
        if isinstance(line, int) and isinstance(column, int):
            return f"{source}:{line}:{column}"
        return f"{source}:{line}" if isinstance(line, int) else source
    return str(row.get("snippet") or row.get("selection_id") or "Unknown match")


def _atlas_search_scope(record: Mapping[str, Any]) -> str:
    context = record.get("context")
    kind = str(context.get("context_type", "")) if isinstance(context, dict) else ""
    if "source-only" in kind:
        scope = "Source text only: matches are file occurrences, not observed recipes."
    elif "graph" in kind:
        scope = "Captured graph: matches show observed recipes and their links."
    else:
        scope = "Atlas search in the selected evidence context."
    if record.get("truncated"):
        scope += " More matches exist; narrow the search."
    return scope


class AtlasSearchScreen(Screen[None]):
    """Let one exact search selection open its owner-backed report or links."""

    SUB_TITLE = "Atlas recipe search"

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, catalog: Mapping[str, Any], path: str,
                 record: Mapping[str, Any]) -> None:
        super().__init__()
        self.catalog = catalog
        self.path = path
        self.record = record
        self.selected: Mapping[str, Any] | None = None

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def _can_browse(self) -> bool:
        context = self.record.get("context") or {}
        return (isinstance(context, dict)
                and "graph" in str(context.get("context_type", ""))
                and any(row.get("command_id") == "atlas.recipes-browse"
                        and _runnable_catalog_action(row)
                        for row in self.catalog.get("commands", [])
                        if isinstance(row, dict)))

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Atlas recipe search", classes="screen-heading")
        yield Static("↑/↓ Choose  ·  Enter Inspect  ·  "
                     + ("B Browse links  ·  " if self._can_browse() else "")
                     + "Esc Back",
                     classes="keyboard-hint")
        yield Static(_atlas_search_scope(self.record), classes="screen-intro",
                     id="atlas-search-context")
        with Horizontal(id="workflow-body"):
            yield OptionList(id="atlas-search-results")
            with Vertical(id="workflow-detail-panel"):
                yield Static("Select a result", id="atlas-search-detail")
                with Horizontal(classes="button-row"):
                    yield Button("Inspect", id="atlas-search-inspect", disabled=True)
                    browse = Button("Browse links", id="atlas-search-browse", disabled=True)
                    browse.display = self._can_browse()
                    yield browse
        with Horizontal(classes="button-row"):
            yield Button("Back", id="atlas-search-back")
        yield Footer()

    def on_mount(self) -> None:
        rows = self.record.get("results", [])
        listing = self.query_one("#atlas-search-results", OptionList)
        if isinstance(rows, list):
            listing.set_options([
                Option(f"{_atlas_match_name(row)}  ·  {row.get('kind', '?')}",
                       id=str(index))
                for index, row in enumerate(rows) if isinstance(row, dict)
            ])
        if listing.option_count:
            listing.highlighted = 0
            self._select(0)
        else:
            self.query_one("#atlas-search-detail", Static).update(
                "No matches in this evidence context. Go back and try another query."
            )
        listing.focus()

    def _select(self, index: int) -> None:
        rows = self.record.get("results", [])
        self.selected = rows[index] if isinstance(rows, list) and 0 <= index < len(rows) else None
        selected = self.selected
        self.query_one("#atlas-search-inspect", Button).disabled = selected is None
        can_browse = self._can_browse()
        self.query_one("#atlas-search-browse", Button).disabled = not (selected and can_browse)
        if selected:
            detail = Text()
            detail.append(_atlas_match_name(selected), style="bold")
            detail.append("\nKind: " + str(selected.get("kind", "?")))
            if selected.get("snippet"):
                detail.append("\n" + str(selected["snippet"]), style=_DESCRIPTION_COLOR)
            detail.append("\n\nEnter to inspect exact evidence. "
                          + ("Browse links to follow observed relationships." if can_browse else
                             "This source-only view has no observed links."))
            self.query_one("#atlas-search-detail", Static).update(detail)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "atlas-search-results" and event.option_id is not None:
            self._select(int(event.option_id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "atlas-search-results":
            self.inspect_selection()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "atlas-search-inspect":
            self.inspect_selection()
        elif event.button.id == "atlas-search-browse":
            self.browse_selection()
        elif event.button.id == "atlas-search-back":
            self.action_back()

    def action_back(self) -> None:
        self.app.pop_screen()

    def inspect_selection(self) -> None:
        self._follow("atlas.recipes-inspect")

    def browse_selection(self) -> None:
        self._follow("atlas.recipes-browse")

    def action_browse(self) -> None:
        if not self.query_one("#atlas-search-browse", Button).disabled:
            self.browse_selection()

    def on_key(self, event: events.Key) -> None:
        if event.key == "b" and self._can_browse():
            self.action_browse()
            event.stop()

    @work(exclusive=True, group="atlas-follow")
    async def _follow(self, command_id: str) -> None:
        if not self.selected or not isinstance(self.selected.get("selection_id"), str):
            return
        action = next((row for row in self.catalog.get("commands", [])
                       if isinstance(row, dict) and row.get("command_id") == command_id), None)
        if not action or not _runnable_catalog_action(action):
            self.app.push_screen(ResultScreen("Atlas unavailable", "This Atlas action is not installed."))
            return
        values: dict[str, Any] = {"path": self.path,
                                  "selection_id": self.selected["selection_id"],
                                  "json": True}
        if command_id == "atlas.recipes-browse":
            context = self.record.get("context") or {}
            if isinstance(context, dict) and isinstance(context.get("graph_set_id"), str):
                values["expect_graph"] = context["graph_set_id"]
        try:
            review = await self.core.command_review(self.catalog, action, values)
            output = await self.core.run_reviewed_command(
                self.catalog, action, values, review, console="jsonl"
            )
            record = _atlas_record(output, command_id)
            self.app.push_screen(AnalysisResultScreen(str(action.get("title", "Atlas result")),
                                                      "atlas", record))
        except (CoreClientError, TimeoutError) as exc:
            self.app.push_screen(ResultScreen("Atlas could not inspect this selection", str(exc)))


class AtlasObservationSearchScreen(KeyboardFormScreen):
    """Explore one admitted Axiom graph through Atlas's read-only query actions."""

    SUB_TITLE = "Atlas observations"

    KEYBOARD_CANCEL = "atlas-observation-back"
    KEYBOARD_FIELDS = ("atlas-observation-query", "atlas-observation-search",
                       "atlas-observation-inspect", "atlas-observation-links",
                       "atlas-observation-more", "atlas-observation-back")

    def __init__(self, catalog: Mapping[str, Any], root: Path) -> None:
        super().__init__()
        self.catalog = catalog
        self.root = root
        self.results: list[Mapping[str, Any]] = []
        self.selected: Mapping[str, Any] | None = None
        self.next_cursor: str | None = None
        self.current_query = ""
        self.busy = False
        self.session: AtlasObservationSession | None = None
        self.session_error: str | None = None
        self._session_worker: Any = None

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Atlas observations", classes="screen-heading")
        yield Static("↑/↓ Select  ·  Enter Edit/Inspect  ·  Esc Back",
                     classes="keyboard-hint")
        yield Static("Recorded values and links from this Axiom check; gameplay behavior is unverified.",
                     classes="screen-intro", id="atlas-observation-scope")
        with Horizontal(classes="axiom-path-row"):
            yield Static("QUERY>", classes="inline-prompt")
            yield Input(placeholder="Material, registration, recipe family…",
                        id="atlas-observation-query")
            yield Button("S Search", id="atlas-observation-search", disabled=True)
        yield Static("Verifying this observation graph…", id="atlas-observation-status")
        with Horizontal(id="workflow-body"):
            yield OptionList(id="atlas-observation-results")
            with Vertical(id="workflow-detail-panel"):
                yield Static("Enter a term and choose Search.", id="atlas-observation-detail")
                with Horizontal(classes="button-row"):
                    yield Button("I Inspect", id="atlas-observation-inspect", disabled=True)
                    yield Button("L Links", id="atlas-observation-links", disabled=True)
                    more = Button("M More", id="atlas-observation-more", disabled=True)
                    more.display = False
                    yield more
        with Horizontal(classes="button-row"):
            yield Button("Esc Back", id="atlas-observation-back")
        yield Footer()

    def on_mount(self) -> None:
        self.start_keyboard_navigation()
        self._session_worker = self._open_session()

    @work(exclusive=True, group="atlas-observation-session")
    async def _open_session(self) -> None:
        status = self.query_one("#atlas-observation-status", Static)
        status.update("Verifying graph once… Large graphs can take several minutes.")
        try:
            session = await self.core.open_atlas_observation_session(self.root)
            if not self.is_mounted:
                await session.close()
                return
            self.session = session
            self.query_one("#atlas-observation-search", Button).disabled = False
            status.update("Graph ready. Enter a query, then press S to search.")
        except (CoreClientError, TimeoutError) as exc:
            self.session_error = str(exc)
            if self.is_mounted:
                status.update(f"Atlas could not open this graph: {exc}")

    async def on_unmount(self) -> None:
        if self._session_worker is not None:
            self._session_worker.cancel()
        if self.session is not None:
            await self.session.close()
            self.session = None

    def on_key(self, event: events.Key) -> None:
        focused = self.app.focused
        if isinstance(focused, OptionList):
            if event.key in {"left", "right"}:
                target = ("atlas-observation-links" if event.key == "right"
                          and not self.query_one("#atlas-observation-links", Button).disabled
                          else "atlas-observation-query")
                self.app.set_focus(None)
                items = self._keyboard_items()
                self._keyboard_highlight(next((index for index, item in enumerate(items)
                                               if item.id == target), 0))
                event.stop()
            elif event.key == "enter":
                self.follow("atlas.observations-inspect")
                event.stop()
                return
        if (self._keyboard_editing is None
                and not isinstance(focused, Input)
                and event.key in {"s", "i", "l", "m"}):
            if event.key == "s":
                self.search()
            elif event.key == "i":
                self.follow("atlas.observations-inspect")
            elif event.key == "l":
                self.follow("atlas.observations-relationships")
            elif self.next_cursor:
                self.search(more=True)
            event.stop()
        # Textual also dispatches KeyboardFormScreen.on_key for form focus.

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "atlas-observation-query" and event.value.strip() != self.current_query:
            self.next_cursor = None
            more = self.query_one("#atlas-observation-more", Button)
            more.disabled = True
            more.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "atlas-observation-back":
            self.app.pop_screen()
        elif event.button.id == "atlas-observation-search":
            self.search()
        elif event.button.id == "atlas-observation-more":
            self.search(more=True)
        elif event.button.id == "atlas-observation-inspect":
            self.follow("atlas.observations-inspect")
        elif event.button.id == "atlas-observation-links":
            self.follow("atlas.observations-relationships")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "atlas-observation-results" or event.option_id is None:
            return
        try:
            selected = self.results[int(event.option_id)]
        except (ValueError, IndexError):
            return
        self.selected = selected
        available = isinstance(selected.get("id"), str)
        self.query_one("#atlas-observation-inspect", Button).disabled = not available
        self.query_one("#atlas-observation-links", Button).disabled = not available
        evidence = selected.get("evidence")
        self.query_one("#atlas-observation-detail", Static).update(
            f"{_observation_label(selected)}\n"
            f"{selected.get('kind', '?')} · "
            f"{len(evidence) if isinstance(evidence, list) else '?'} evidence refs\n"
            "Enter Inspect · → Links"
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "atlas-observation-results":
            self.follow("atlas.observations-inspect")

    @work(exclusive=True, group="atlas-observation-search")
    async def search(self, *, more: bool = False) -> None:
        if self.busy:
            return
        if self.session is None:
            self.query_one("#atlas-observation-status", Static).update(
                self.session_error or "Graph verification is still running. Please wait."
            )
            return
        query = self.query_one("#atlas-observation-query", Input).value.strip()
        status = self.query_one("#atlas-observation-status", Static)
        if not query:
            status.update("Enter a material, registration, or recipe-family term.")
            return
        if more and not self.next_cursor:
            return
        action = _atlas_read_action(self.catalog, "atlas.observations-search")
        if action is None:
            status.update("Atlas observation search is unavailable in this installation.")
            return
        self.busy = True
        try:
            status.update("Searching the verified observation graph…")
            arguments: dict[str, Any] = {"query": query, "limit": 50}
            if more:
                arguments["cursor"] = self.next_cursor
            record = await self.session.request("search", arguments)
            if record.get("format") != "workbench-atlas-observation-search-v1":
                raise CoreClientError("Atlas returned an unsupported observation search")
            rows = record.get("results")
            if not isinstance(rows, list):
                raise CoreClientError("Atlas returned no observation result list")
            if not more:
                self.results = []
            prior_count = len(self.results)
            self.results.extend(row for row in rows if isinstance(row, dict))
            self.current_query = query
            page = record.get("page")
            cursor = page.get("next_cursor") if isinstance(page, dict) else None
            self.next_cursor = cursor if isinstance(cursor, str) and cursor else None
            listing = self.query_one("#atlas-observation-results", OptionList)
            listing.set_options([
                Option(_observation_label(row, limit=30),
                       id=str(index)) for index, row in enumerate(self.results)
            ])
            self.query_one("#atlas-observation-more", Button).disabled = not self.next_cursor
            self.query_one("#atlas-observation-more", Button).display = bool(self.next_cursor)
            status.update(f"{len(self.results)} observations shown" +
                          (" · more available" if self.next_cursor else ""))
            if listing.option_count:
                listing.highlighted = prior_count if more and prior_count < len(self.results) else 0
                listing.focus()
            else:
                self.selected = None
                self.query_one("#atlas-observation-detail", Static).update(
                    "No observations match this term. Try a material or registration name."
                )
        except (CoreClientError, TimeoutError) as exc:
            status.update(f"Atlas search could not complete: {exc}")
        finally:
            self.busy = False

    @work(exclusive=True, group="atlas-observation-follow")
    async def follow(self, command_id: str) -> None:
        if (self.busy or self.session is None or self.selected is None
                or not isinstance(self.selected.get("id"), str)):
            return
        action = _atlas_read_action(self.catalog, command_id)
        if action is None:
            self.query_one("#atlas-observation-status", Static).update(
                "This Atlas observation action is unavailable."
            )
            return
        operation = ("relationships" if command_id == "atlas.observations-relationships"
                     else "inspect")
        arguments: dict[str, Any] = {"selection_id": self.selected["id"]}
        if command_id == "atlas.observations-relationships":
            arguments.update({"direction": "outgoing", "limit": 50})
        self.busy = True
        try:
            record = await self.session.request(operation, arguments)
            self.app.push_screen(AnalysisResultScreen(str(action.get("title", "Atlas result")),
                                                      "atlas", record))
        except (CoreClientError, TimeoutError) as exc:
            self.query_one("#atlas-observation-status", Static).update(
                f"Atlas could not open this observation: {exc}"
            )
        finally:
            self.busy = False


_AXIOM_MATERIAL_CONTEXT = "supersymmetry:material-authoring-pack"


def _axiom_problem(exc: Exception) -> str:
    detail = str(exc)
    if "Core setup has no selected Java" in detail:
        return "Choose Java in Workbench setup, apply that selection, then retry Prepare."
    if "install and enable" in detail.casefold() and "axiom" in detail.casefold():
        return (
            "Axiom is not installed or enabled in this Workbench installation. "
            "Install the Axiom component, restart Workbench, then reopen this check.\n\n"
            + detail
        )
    return detail


class AxiomHistoryScreen(Screen[None]):
    """Open retained native checks without copying attempt IDs."""

    SUB_TITLE = "Axiom check history"

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, session_id: str, record: Mapping[str, Any]) -> None:
        super().__init__()
        self.session_id = session_id
        self.record = record
        result = record.get("result") or {}
        self.attempts = result.get("attempts", []) if isinstance(result, dict) else []
        self.attempt_by_id = {
            row["attempt_id"]: row for row in self.attempts
            if isinstance(row, dict) and isinstance(row.get("attempt_id"), str)
        } if isinstance(self.attempts, list) else {}
        self.selected: str | None = None

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Axiom check history", classes="screen-heading")
        yield Static("↑/↓ Browse  ·  Enter Open  ·  Esc Back", classes="keyboard-hint")
        yield Static("Retained checks for this selected pack. Open one to read its scoped result.",
                     classes="screen-intro")
        yield OptionList(id="axiom-history-list")
        yield Static("", id="axiom-history-status")
        with Horizontal(classes="button-row"):
            yield Button("Open check", id="axiom-history-open", disabled=True)
            yield Button("Back", id="axiom-history-back")
        yield Footer()

    def on_mount(self) -> None:
        listing = self.query_one("#axiom-history-list", OptionList)
        if isinstance(self.attempts, list):
            listing.set_options([
                Option(f"{row.get('state', '?')}  ·  {row.get('attempt_id', '?')}",
                       id=str(row.get("attempt_id")))
                for row in self.attempts
                if isinstance(row, dict) and isinstance(row.get("attempt_id"), str)
            ])
        if listing.option_count:
            listing.highlighted = 0
            self.selected = listing.options[0].id
            self._show_selection()
        else:
            self.query_one("#axiom-history-status", Static).update(
                "No retained checks yet. Return to Axiom setup and run a check."
            )
        listing.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "axiom-history-list":
            self.selected = event.option_id
            self._show_selection()

    def _show_selection(self) -> None:
        row = self.attempt_by_id.get(self.selected) if self.selected else None
        can_open = isinstance(row, dict) and isinstance(row.get("record_id"), str)
        self.query_one("#axiom-history-open", Button).disabled = not can_open
        if isinstance(row, dict) and not can_open:
            reason = row.get("reason") or row.get("original_summary")
            note = (str(reason) if isinstance(reason, str) else
                    json.dumps(reason, ensure_ascii=False) if reason is not None else "")
            self.query_one("#axiom-history-status", Static).update(
                f"{row.get('state', 'Past check')}: retained details cannot be opened. "
                + note[:300]
            )
        elif can_open:
            self.query_one("#axiom-history-status", Static).update(
                "Retained details are available. Press Enter to open this check."
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "axiom-history-list":
            self.open_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "axiom-history-open":
            self.open_selected()
        elif event.button.id == "axiom-history-back":
            self.action_back()

    def action_back(self) -> None:
        self.app.pop_screen()

    @work(exclusive=True, group="axiom-history-open")
    async def open_selected(self) -> None:
        if not self.selected or self.query_one("#axiom-history-open", Button).disabled:
            return
        self.query_one("#axiom-history-status", Static).update("Opening retained check…")
        try:
            record = await self.core.developer_materials_action(
                self.session_id, "show", self.selected
            )
            self.app.push_screen(AnalysisResultScreen("Axiom retained check", "axiom", record))
        except (CoreClientError, TimeoutError) as exc:
            self.app.push_screen(ResultScreen("Could not open check", _axiom_problem(exc)))


class AxiomJourneyScreen(KeyboardFormScreen):
    """Keyboard-first native-check setup, execution, and retained results."""

    SUB_TITLE = "Axiom native check"

    KEYBOARD_CANCEL = "axiom-back"
    KEYBOARD_FIELDS = (
        "axiom-pack", "axiom-pack-browse", "axiom-engine", "axiom-engine-browse",
        "axiom-java-path", "axiom-java-detect", "axiom-check-setup", "axiom-prepare", "axiom-run", "axiom-java",
        "axiom-history", "axiom-back",
    )

    def __init__(self, view: EnvironmentView) -> None:
        super().__init__()
        self.view = view
        self.session_id: str | None = None
        self.session_pack = ""
        self.setup_ready = False
        self.busy = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Axiom native check", classes="screen-heading")
        yield Static(
            "Check your Supersymmetry source with Axiom. Core keeps the findings for later.",
            classes="screen-intro",
        )
        yield Static("↑/↓ Choose  ·  Enter Edit or Open  ·  Esc Back", classes="keyboard-hint")
        with VerticalScroll(id="axiom-journey-body"):
            yield Static("Supersymmetry source checkout", classes="field-label")
            with Horizontal(classes="axiom-path-row"):
                yield Input(value=self.view.workspace, placeholder="Choose a checkout containing pack source",
                            id="axiom-pack")
                yield Button("Browse", id="axiom-pack-browse")
            yield Static("Axiom engine", classes="field-label")
            with Horizontal(classes="axiom-path-row"):
                yield Input(placeholder="Bundled ZIP or installed engine folder",
                            id="axiom-engine")
                yield Button("Browse", id="axiom-engine-browse")
            yield Static("Java executable (optional)", classes="field-label")
            with Horizontal(classes="axiom-path-row"):
                yield Input(placeholder="Saved Java is used if blank",
                            id="axiom-java-path")
                yield Button("Find", id="axiom-java-detect")
            yield Static("Check setup → Prepare if needed → Run check", id="axiom-guide")
            yield Static("Choose a source checkout, then Check setup.", id="axiom-status")
            with Horizontal(classes="button-row"):
                yield Button("Check setup", id="axiom-check-setup")
                yield Button("Prepare", id="axiom-prepare", disabled=True)
                yield Button("Run check", id="axiom-run", disabled=True)
            with Horizontal(classes="button-row"):
                yield Button("Manage Java", id="axiom-java")
                yield Button("History", id="axiom-history")
                yield Button("Back", id="axiom-back")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_actions()
        self.start_keyboard_navigation()
        self.prefill_installed_engine()

    @work(exclusive=True, group="axiom-installed-engine")
    async def prefill_installed_engine(self) -> None:
        try:
            record = await self.core.installed_axiom_engine()
        except (CoreClientError, TimeoutError):
            return
        if (record.get("state") == "verified"
                and not self.query_one("#axiom-engine", Input).value.strip()):
            self.query_one("#axiom-engine", Input).value = str(record["archive_path"])
            self._status("Bundled Axiom engine found. Check setup, then Prepare if needed.")
            self._refresh_actions()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        engine = self.query_one("#axiom-engine", Input).value.strip()
        self.query_one("#axiom-check-setup", Button).disabled = self.busy
        self.query_one("#axiom-history", Button).disabled = self.busy
        self.query_one("#axiom-prepare", Button).disabled = self.busy or not engine
        self.query_one("#axiom-run", Button).disabled = self.busy or not self.setup_ready

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "axiom-pack":
            self.session_id = None
            self.setup_ready = False
        if event.input.id in {"axiom-pack", "axiom-engine"}:
            self._refresh_actions()

    def _status(self, message: str) -> None:
        self.query_one("#axiom-status", Static).update(message)

    async def _ensure_session(self) -> str:
        raw = self.query_one("#axiom-pack", Input).value.strip()
        pack = Path(raw).expanduser().absolute()
        if not raw or not pack.is_dir():
            raise CoreClientError("Choose an existing Supersymmetry source checkout first.")
        if self.session_id and self.session_pack == str(pack):
            return self.session_id
        self._status("Selecting saved pack through Core…")
        selected = await self.core.developer_context_select(str(pack))
        self.session_id = str(selected["session_id"])
        self.session_pack = str(pack)
        return self.session_id

    @work(exclusive=True, group="axiom-path-pick")
    async def _pick_path(self, *, engine: bool) -> None:
        if engine:
            kind = await self.app.push_screen_wait(ChoicePicker(
                "Choose Axiom engine source",
                [("Engine ZIP", "zip"), ("Installed engine folder", "folder")],
                "zip",
            ))
            if kind is None:
                return
            choose_zip = kind == "zip"
            current = self.query_one("#axiom-engine", Input).value.strip()
        else:
            choose_zip = False
            current = self.query_one("#axiom-pack", Input).value.strip()
        location = Path(current).expanduser() if current else Path.home()
        if not location.is_dir():
            location = location.parent if location.parent.is_dir() else Path.home()
        chosen = await self.app.push_screen_wait(InstancePathPicker(
            location, choose_zip=choose_zip,
            heading=("Choose an Axiom engine ZIP" if choose_zip else
                     "Choose an installed Axiom engine folder" if engine else
                     "Choose your Supersymmetry source checkout"),
        ))
        if chosen is not None:
            self.query_one("#axiom-engine" if engine else "#axiom-pack", Input).value = str(chosen)
            if not engine:
                self.session_id = None
                self.setup_ready = False
            self._refresh_actions()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "axiom-back":
            self.app.pop_screen()
        elif action == "axiom-pack-browse":
            self._pick_path(engine=False)
        elif action == "axiom-engine-browse":
            self._pick_path(engine=True)
        elif action == "axiom-check-setup":
            self.check_setup()
        elif action == "axiom-prepare":
            self.prepare()
        elif action == "axiom-run":
            self.run_check()
        elif action == "axiom-history":
            self.history()
        elif action == "axiom-java":
            self.app.open_workspace_choices()
        elif action == "axiom-java-detect":
            self.find_java()

    @work(exclusive=True, group="axiom-java-inventory")
    async def find_java(self) -> None:
        try:
            inventory = await self.core.java_inventory()
            detected, options = _detected_jdk_options(inventory)
            if not options:
                self._status("No installed JDKs found. Manage Java or enter a Java executable path.")
                return
            chosen = await self.app.push_screen_wait(ChoicePicker(
                "Choose an installed JDK", options, options[0][1]
            ))
            if chosen in detected:
                home = Path(detected[chosen])
                executable = home / "bin" / "java"
                if executable.is_file():
                    self.query_one("#axiom-java-path", Input).value = str(executable)
                    self._status(f"Using installed Java from {home}")
                else:
                    self._status("This JDK has no Linux Java executable. Choose another JDK or enter a path.")
        except (CoreClientError, TimeoutError) as exc:
            self._status(f"Could not list installed Java: {exc}")

    @work(exclusive=True, group="axiom-operation")
    async def check_setup(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        try:
            session = await self._ensure_session()
            record = await self.core.developer_materials_action(
                session, "setup-status", "--context", _AXIOM_MATERIAL_CONTEXT
            )
            state = record["result"].get("state", "unknown")
            self.setup_ready = state == "ready"
            self._status(
                "Native check is ready. Choose Run check."
                if state == "ready" else
                "Native check needs preparation. Choose an engine source, then Prepare."
            )
            self.app.push_screen(AnalysisResultScreen("Axiom setup status", "axiom", record))
        except (CoreClientError, TimeoutError) as exc:
            self._status(_axiom_problem(exc))
        finally:
            self._set_busy(False)

    @work(exclusive=True, group="axiom-operation")
    async def prepare(self) -> None:
        if self.busy:
            return
        engine_raw = self.query_one("#axiom-engine", Input).value.strip()
        if not engine_raw:
            self._status("Choose an Axiom engine ZIP or installed engine folder before Prepare.")
            return
        engine = Path(engine_raw).expanduser().absolute()
        if not engine.is_file() and not engine.is_dir():
            self._status("The engine source does not exist. Choose an engine ZIP or folder.")
            return
        engine_arg = "--engine-archive" if engine.is_file() else "--engine-home"
        if engine.is_file() and engine.suffix.casefold() != ".zip":
            self._status("Choose an Axiom engine ZIP file, or an installed engine folder.")
            return
        java_raw = self.query_one("#axiom-java-path", Input).value.strip()
        explicit_java = Path(java_raw).expanduser().absolute() if java_raw else None
        if explicit_java is not None and not explicit_java.is_file():
            self._status("Choose an existing Java executable, or leave Java blank to use a saved choice.")
            return
        try:
            session = await self._ensure_session()
            java = explicit_java
            java_source = "selected executable" if java is not None else "Core setup selection"
            if java is None:
                try:
                    resolved = await self.core.environment_resolve(self.session_pack)
                    candidates = resolved.get("tool_candidates")
                    home = candidates.get("java_home") if isinstance(candidates, dict) else None
                    candidate = Path(home) / "bin" / "java" if isinstance(home, str) else None
                    if candidate is not None and candidate.is_file():
                        java = candidate
                        java_source = "saved workspace choice"
                except (CoreClientError, TimeoutError):
                    pass  # Core setup's selected Java remains the fallback.
            approved = await self.app.push_screen_wait(ReviewModal(
                "Prepare Axiom native check?",
                f"Saved pack: {self.session_pack}\nEngine source: {engine}\n"
                f"Java: {java or java_source} ({java_source})\n\n"
                "Core will acquire the selected native inputs and assemble a runtime for this pack. "
                "This can take several minutes.",
                confirm_label="Prepare setup",
            ))
            if not approved:
                return
            self._set_busy(True)
            self._status("Preparing original native inputs and Axiom runtime…")
            record = await self.core.developer_materials_action(
                session, "setup", "--prepare", "--context", _AXIOM_MATERIAL_CONTEXT,
                engine_arg, str(engine), *(("--java", str(java)) if java is not None else ()),
            )
            state = record["result"].get("state")
            self.setup_ready = False
            self._status(
                "Preparation complete. Choose Check setup, then Run check."
                if state == "configured-not-run" else
                "Preparation is incomplete. Open the result for the remaining inputs."
            )
            self.app.push_screen(AnalysisResultScreen("Axiom preparation", "axiom", record))
        except (CoreClientError, TimeoutError) as exc:
            self._status(_axiom_problem(exc))
        finally:
            self._set_busy(False)

    @work(exclusive=True, group="axiom-operation")
    async def run_check(self) -> None:
        if self.busy:
            return
        try:
            session = await self._ensure_session()
            status = await self.core.developer_materials_action(
                session, "setup-status", "--context", _AXIOM_MATERIAL_CONTEXT
            )
            if status["result"].get("state") != "ready":
                self.setup_ready = False
                self._status("Native setup is not ready. Choose an engine source and Prepare first.")
                self.app.push_screen(AnalysisResultScreen("Axiom setup needed", "axiom", status))
                return
            approved = await self.app.push_screen_wait(ReviewModal(
                "Run native initialization check?",
                f"Saved pack: {self.session_pack}\n"
                f"Scope: {_AXIOM_MATERIAL_CONTEXT}\n\n"
                "Axiom will run the original native initialization in a Core-managed worker. "
                "Core will retain the attempt and its findings for later inspection.",
                confirm_label="Run check",
            ))
            if not approved:
                return
            self._set_busy(True)
            self._status("Axiom is running the native check. This can take several minutes…")
            record = await self.core.developer_materials_action(
                session, "run", "--context", _AXIOM_MATERIAL_CONTEXT
            )
            result = record["result"]
            display_record = record
            attempt_id = result.get("attempt_id")
            if isinstance(attempt_id, str) and attempt_id:
                try:
                    shown = await self.core.developer_materials_action(
                        session, "show", attempt_id
                    )
                    if shown["result"].get("attempt_id") == attempt_id:
                        display_record = shown
                except (CoreClientError, TimeoutError):
                    pass  # The run record remains available if saved detail cannot reopen.
            self._status(f"Check {result.get('state', 'finished')}. Open History to revisit this attempt.")
            self.app.push_screen(AnalysisResultScreen("Axiom native check", "axiom", display_record))
        except (CoreClientError, TimeoutError) as exc:
            self._status(_axiom_problem(exc))
        finally:
            self._set_busy(False)

    @work(exclusive=True, group="axiom-operation")
    async def history(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        try:
            session = await self._ensure_session()
            record = await self.core.developer_materials_action(session, "history")
            self.app.push_screen(AxiomHistoryScreen(session, record))
        except (CoreClientError, TimeoutError) as exc:
            self._status(_axiom_problem(exc))
        finally:
            self._set_busy(False)


class WorkspaceRegisterScreen(KeyboardFormScreen):
    """Register one named workspace through Core's revisioned user choices."""

    SUB_TITLE = "Add a workspace"

    KEYBOARD_CANCEL = "workspace-register-cancel"

    KEYBOARD_FIELDS = (
        "workspace-register-name", "workspace-register-path", "workspace-register-default",
        "workspace-register-save", "workspace-register-cancel",
    )

    def __init__(self, record: Mapping[str, Any], *, initial_path: str = "") -> None:
        super().__init__()
        self.record = record
        self.initial_path = initial_path
        self.busy = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        with VerticalScroll():
            yield Static("Add a workspace", classes="screen-heading")
            yield Static(
                "Choose a short name and a Linux folder for your pack work. "
                "Core saves this choice for later setup; registration does not change the folder.",
                classes="screen-intro",
            )
            yield Static("↑/↓ Move  ·  Enter Edit/Save  ·  Esc Cancel/Back", classes="keyboard-hint")
            yield Static("Workspace name", classes="field-label")
            yield Input(placeholder="susy-dev", id="workspace-register-name")
            yield Static("Workspace folder", classes="field-label")
            yield Input(value=self.initial_path, placeholder="~/Workbench/Supersymmetry",
                        id="workspace-register-path")
            yield Checkbox("Use as my default workspace",
                           value=not bool(self.record.get("entries")),
                           id="workspace-register-default")
            with Horizontal(classes="button-row"):
                yield Button("Save workspace", id="workspace-register-save", variant="primary")
                yield Button("Cancel", id="workspace-register-cancel")
            yield Static("", id="workspace-register-status")
        yield Footer()

    def on_mount(self) -> None:
        self.start_keyboard_navigation()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "workspace-register-cancel":
            self.dismiss(None)
        elif event.button.id == "workspace-register-save":
            self.register()

    @work(exclusive=True, group="workspace-register")
    async def register(self) -> None:
        if self.busy:
            return
        name = self.query_one("#workspace-register-name", Input).value.strip()
        path = self.query_one("#workspace-register-path", Input).value.strip()
        status = self.query_one("#workspace-register-status", Static)
        if (not name or not name[0].islower() or not name[0].isascii()
                or len(name) > 64 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
                                      for char in name)):
            status.update("Use a lowercase name starting with a letter, such as susy-dev.")
            return
        if any(row.get("name") == name for row in self.record.get("entries", [])):
            status.update("That workspace name is already saved. Choose another name.")
            return
        if not (Path(path).is_absolute() or path == "~" or path.startswith("~/")):
            status.update("Enter an absolute Linux folder path or a path starting with ~/.")
            return
        self.busy = True
        self.query_one("#workspace-register-save", Button).disabled = True
        status.update("Core is saving this workspace choice…")
        try:
            saved = await self.core.register_workspace(
                name, path,
                make_default=self.query_one("#workspace-register-default", Checkbox).value,
                expected_record_id=self.record["record_id"],
            )
            self.dismiss((name, saved))
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self.query_one("#workspace-register-save", Button).disabled = False


class WorkspaceChoicesScreen(KeyboardFormScreen):
    """Edit Core's local profile and Java candidates for one named workspace."""

    SUB_TITLE = "Workspace choices"

    KEYBOARD_CANCEL = "choice-back"

    KEYBOARD_FIELDS = (
        "choice-workspace", "choice-register", "choice-java-mode", "choice-java",
        "choice-save", "choice-workspace-more", "choice-profile", "choice-share-more",
        "choice-bind-source-lock", "choice-bind-managed-tools", "choice-export",
        "choice-import", "choice-back",
    )

    def __init__(self, record: Mapping[str, Any], *, selected_name: str | None = None) -> None:
        super().__init__()
        self.record = record
        self.entries = {row["name"]: row for row in record["entries"]}
        self.selected_name = (selected_name if selected_name in self.entries else
                              record.get("default") or next(iter(self.entries), ""))
        self.busy = False
        self.detected_java: dict[str, str] = {}
        self.show_share_options = False
        self.show_workspace_options = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        options = [(_workspace_option_label(name, row["path"]), name)
                   for name, row in self.entries.items()]
        if not options:
            options = [("No named workspace is registered", "")]
        yield Header(icon="W")
        with VerticalScroll():
            yield Static("Workspace choices", classes="screen-heading")
            yield Static(
                "Choose Java for this workspace. Cleanroom recommends managed Java 25. "
                "You can use another installed JDK or enter your own path.",
                classes="screen-intro",
            )
            yield Static("↑/↓ Move  ·  Enter Edit/Save  ·  Esc Cancel/Back", classes="keyboard-hint")
            yield Static("Named workspace", classes="field-label")
            yield Select(options, value=self.selected_name, allow_blank=False, id="choice-workspace")
            yield Button("Add workspace", id="choice-register")
            yield Static("Java selection", classes="field-label")
            yield Select([
                ("Managed Java recommended for this workspace", "default"),
                ("Use managed Java 8", "managed-8"),
                ("Enter my own Java path", "path"),
            ], value="default", allow_blank=False, id="choice-java-mode")
            yield Static("Looking for installed JDKs…", id="choice-java-hint")
            yield Static("Your Java folder", classes="field-label", id="choice-java-label")
            yield Input(placeholder="/path/to/jdk", id="choice-java")
            yield Button("Use recommended Java", id="choice-save", variant="primary",
                         disabled=not bool(self.entries))
            yield Static("", id="choice-status")
            yield Button("More workspace settings", id="choice-workspace-more")
            yield Static("Existing Workbench configuration · optional", classes="field-label",
                         id="choice-profile-label")
            yield Input(placeholder="/path/to/workbench.toml", id="choice-profile")
            yield Button("Sharing options", id="choice-share-more")
            yield Checkbox(
                "Include the exact pack version in a shared setup",
                id="choice-bind-source-lock",
            )
            yield Checkbox(
                "Include exact Workbench tool versions in a shared setup",
                id="choice-bind-managed-tools",
            )
            with Horizontal(classes="button-row"):
                yield Button("Export environment", id="choice-export",
                             disabled=not bool(self.entries))
                yield Button("Import environment", id="choice-import")
                yield Button("Back", id="choice-back")
        yield Footer()

    def on_mount(self) -> None:
        self._update_share_options()
        self._update_workspace_options()
        self._show_selected()
        self.find_java()
        self.start_keyboard_navigation()

    def _java_mode(self) -> str:
        value = self.query_one("#choice-java-mode", Select).value
        return value if isinstance(value, str) else "default"

    def _update_share_options(self) -> None:
        for field in ("#choice-bind-source-lock", "#choice-bind-managed-tools"):
            self.query_one(field, Checkbox).display = self.show_share_options
        self.query_one("#choice-share-more", Button).label = (
            "Hide sharing options" if self.show_share_options else "Sharing options"
        )

    def _update_workspace_options(self) -> None:
        for field in ("#choice-profile-label", "#choice-profile"):
            self.query_one(field).display = self.show_workspace_options
        self.query_one("#choice-workspace-more", Button).label = (
            "Hide workspace settings" if self.show_workspace_options else "More workspace settings"
        )

    def _refresh_java_controls(self) -> None:
        mode = self._java_mode()
        self.query_one("#choice-workspace", Select).disabled = self.busy
        self.query_one("#choice-java-mode", Select).disabled = self.busy
        self.query_one("#choice-profile", Input).disabled = self.busy
        self.query_one("#choice-java", Input).disabled = self.busy or mode != "path"
        for field in ("#choice-java-label", "#choice-java"):
            self.query_one(field).display = mode == "path"
        label = (
            "Use recommended Java" if mode == "default"
            else "Use managed Java 8" if mode == "managed-8"
            else "Use selected Java"
        )
        self.query_one("#choice-save", Button).label = label

    def _show_selected(self) -> None:
        row = self.entries.get(self.selected_name)
        if row is None:
            self.query_one("#choice-status", Static).update(
                "Add a workspace to save profile and Java choices."
            )
            return
        self.query_one("#choice-profile", Input).value = row.get("profile_config") or ""
        self.show_workspace_options = bool(row.get("profile_config"))
        self._update_workspace_options()
        self.query_one("#choice-java", Input).value = row.get("java_home") or ""
        mode = "managed-8" if row.get("managed_java_feature") == 8 else "path" if row.get("java_home") else "default"
        self.query_one("#choice-java-mode", Select).value = mode
        self._refresh_java_controls()
        self.query_one("#choice-status", Static).update(
            "Saved Java path is used as supplied." if mode == "path" else
            "Managed Java 8 is selected. Press Use managed Java 8 to prepare it."
            if mode == "managed-8" else
            "Recommended managed Java is selected. Press Use recommended Java to prepare it."
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "choice-workspace" and isinstance(event.value, str):
            if self.busy or event.value == self.selected_name:
                return
            self.selected_name = event.value
            self._show_selected()
        elif event.select.id == "choice-java-mode" and isinstance(event.value, str):
            if self.busy:
                return
            if event.value in self.detected_java:
                self.query_one("#choice-java", Input).value = self.detected_java[event.value]
            self._refresh_java_controls()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "choice-back":
            self.app.pop_screen()
        elif event.button.id == "choice-register":
            self.register_workspace()
        elif event.button.id == "choice-save":
            self.save_choices()
        elif event.button.id == "choice-export":
            self.export_environment()
        elif event.button.id == "choice-share-more":
            self.show_share_options = not self.show_share_options
            self._update_share_options()
        elif event.button.id == "choice-workspace-more":
            self.show_workspace_options = not self.show_workspace_options
            self._update_workspace_options()
        elif event.button.id == "choice-import":
            self.app.push_screen(EnvironmentImportScreen())

    @work(exclusive=True, group="choice-workspace-register")
    async def register_workspace(self) -> None:
        if self.busy:
            return
        selected = await self.app.push_screen_wait(WorkspaceRegisterScreen(
            self.record, initial_path=self.app.initial_workspace,  # type: ignore[attr-defined]
        ))
        if selected is None:
            return
        name, record = selected
        self.record = record
        self.entries = {row["name"]: row for row in record["entries"]}
        self.selected_name = name
        self.query_one("#choice-workspace", Select).set_options([
            (_workspace_option_label(row["name"], row["path"]), row["name"])
            for row in record["entries"]
        ])
        self.query_one("#choice-workspace", Select).value = name
        for button in ("#choice-save", "#choice-export"):
            self.query_one(button, Button).disabled = False
        self._show_selected()
        self.app.refresh_environment()  # type: ignore[attr-defined]

    @work(exclusive=True, group="workspace-java")
    async def find_java(self) -> None:
        # Discovery only offers choices. It must remain useful even when the
        # saved profile path needs repair; selecting a supplied path is separate.
        inventory_method = getattr(self.core, "java_inventory", None)
        if inventory_method is None:
            self.query_one("#choice-java-hint", Static).update(
                "Installed Java detection is unavailable. Managed Java and "
                "your own path are still available."
            )
            return
        try:
            inventory = await inventory_method()
            detected, found_options = _detected_jdk_options(inventory)
            options = [
                ("Managed Java recommended for this workspace", "default"),
                ("Use managed Java 8", "managed-8"),
            ]
            options.extend(found_options)
            options.append(("Enter my own Java path", "path"))
            mode = self._java_mode()
            self.detected_java = detected
            selector = self.query_one("#choice-java-mode", Select)
            selector.set_options(options)
            selector.value = mode if mode in {"default", "managed-8", "path"} else "path"
            self._refresh_java_controls()
            self.query_one("#choice-java-hint", Static).update(
                f"Found {len(detected)} installed JDKs. Choose one as supplied "
                "or use the recommended managed Java."
                if detected else
                "No other system JDKs found. Managed Java 25 and 8 are available, "
                "or enter your own path."
            )
        except (CoreClientError, TimeoutError) as exc:
            self.query_one("#choice-java-hint", Static).update(
                f"Could not look for installed Java: {exc}. "
                "Managed Java and your own path are still available."
            )

    @work(exclusive=True, group="workspace-save")
    async def save_choices(self) -> None:
        if not self.selected_name or self.busy:
            return
        self.busy = True
        self.query_one("#choice-save", Button).disabled = True
        self._refresh_java_controls()
        profile = self.query_one("#choice-profile", Input).value.strip() or None
        mode = self._java_mode()
        java = (
            self.detected_java[mode] if mode in self.detected_java
            else (self.query_one("#choice-java", Input).value.strip() or None)
            if mode == "path" else None
        )
        if mode == "path" and java is None:
            self.query_one("#choice-status", Static).update("Enter an absolute Java home path.")
            self.busy = False
            self.query_one("#choice-save", Button).disabled = False
            self._refresh_java_controls()
            return
        feature = 8 if mode == "managed-8" else None
        name = self.selected_name
        self.query_one("#choice-status", Static).update(
            f"Preparing managed Java {feature or 'recommended by this configuration'}…" if java is None
            else "Saving your Java path…"
        )
        try:
            row = self.entries[name]
            if (row.get("profile_config") != profile or row.get("java_home") != java
                    or row.get("managed_java_feature") != feature):
                result = await self.core.save_workspace_choice(
                    name, profile_config=profile, java_home=java,
                    managed_java_feature=feature,
                    expected_record_id=self.record["record_id"],
                )
                self.record = result
                self.entries = {entry["name"]: entry for entry in result["entries"]}
                self.app.refresh_environment()  # type: ignore[attr-defined]
            if java is not None:
                self.query_one("#choice-status", Static).update(
                    f"Saved Java path for {name}. Workbench will use it as supplied."
                )
                return
            self.query_one("#choice-status", Static).update(
                f"Preparing managed Java {feature or 'recommended by this configuration'} for {name}…"
            )
            result = await self.core.acquire_workspace_java(
                name, expected_record_id=self.record["record_id"],
            )
            receipt = result["receipt"]
            java_location = Path(unquote(urlparse(
                str(receipt["target"]["java_home_uri"]),
            ).path)).name or "local runtime"
            self.query_one("#choice-status", Static).update(
                f"Java {receipt['policy']['feature_version']} is ready for {name}. "
                f"Workbench {'reused its existing copy' if result['outcome'] == 'reused' else 'acquired a copy'} "
                f"in its runtime library ({java_location})."
            )
            self.query_one("#choice-java-hint", Static).update(
                f"Managed Java {receipt['policy']['feature_version']} is ready. "
                "You can keep this choice or select another Java runtime."
            )
        except (CoreClientError, TimeoutError) as exc:
            detail = str(exc)
            if "manifest cannot be opened safely" in detail.lower():
                hint = (
                    "Check or clear Workbench configuration above. " if profile else
                    "Check the Workbench installation. "
                )
            else:
                hint = ""
            self.query_one("#choice-status", Static).update(
                f"Java choice saved, but Java could not be prepared. {hint}"
                f"Core reported: {detail}. "
                f"Press {self.query_one('#choice-save', Button).label} to retry."
                if (self.entries.get(name, {}).get("profile_config") == profile
                    and self.entries.get(name, {}).get("java_home") == java
                    and self.entries.get(name, {}).get("managed_java_feature") == feature
                    and java is None)
                else str(exc)
            )
        finally:
            self.busy = False
            self.query_one("#choice-save", Button).disabled = False
            self._refresh_java_controls()

    @work(exclusive=True, group="workspace-export")
    async def export_environment(self) -> None:
        if not self.selected_name or self.busy:
            return
        self.busy = True
        self.query_one("#choice-export", Button).disabled = True
        self.query_one("#choice-status", Static).update("Asking Core to export the saved environment selection…")
        try:
            bind_tools = self.query_one("#choice-bind-managed-tools", Checkbox).value
            bind_source = self.query_one("#choice-bind-source-lock", Checkbox).value or bind_tools
            result = await self.core.export_environment_share(
                self.selected_name,
                **({"bind_project_source_lock": True} if bind_source else {}),
                **({"bind_managed_tools": True} if bind_tools else {}),
            )
            source_lock = result["share"].get("lock", {}).get("project_source_lock")
            tool_lock = result["share"].get("lock", {}).get("managed_tool_lock")
            source_line = (
                f"Project source lock: {source_lock['sha256']}\n"
                if isinstance(source_lock, dict) else ""
            )
            tool_line = (
                f"Managed-tool policy lock: {tool_lock['lock_id']}\n"
                if isinstance(tool_lock, dict) else ""
            )
            self.query_one("#choice-status", Static).update(
                f"Share: {result['resource']['path']}\n"
                f"Identity: {result['share']['share_id']}\n"
                f"{source_line}"
                f"{tool_line}"
                "Project, fixture and tool bytes remain separate inputs."
            )
        except (CoreClientError, TimeoutError) as exc:
            self.query_one("#choice-status", Static).update(str(exc))
        finally:
            self.busy = False
            self.query_one("#choice-export", Button).disabled = False


class EnvironmentImportScreen(KeyboardFormScreen):
    """Present Core's read-only import plan before binding local choices."""

    SUB_TITLE = "Import environment selection"

    KEYBOARD_CANCEL = "import-back"

    KEYBOARD_FIELDS = (
        "import-share", "import-name", "import-workspace", "import-config", "import-java",
        "import-acquire-java", "import-plan", "import-apply", "import-back",
    )

    def __init__(self) -> None:
        super().__init__()
        self.plan: Mapping[str, Any] | None = None
        self.plan_options: tuple[str, str, str, str, str, bool] | None = None
        self.busy = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        with VerticalScroll():
            yield Static("Import environment selection", classes="screen-heading")
            yield Static(
                "Core checks the exact profile and Java policy against a local Workbench suite. "
                "This binds choices to an existing workspace. Core can also acquire the locked "
                "managed Java release; acquire project and other dependency bytes separately.",
                classes="screen-intro",
            )
            yield Static("↑/↓ Move  ·  Enter Edit/Save  ·  Esc Cancel/Back", classes="keyboard-hint")
            yield Static("Share file", classes="field-label")
            yield Input(placeholder="/path/to/environment-share.json", id="import-share")
            yield Static("Local workspace name", classes="field-label")
            yield Input(placeholder="my-workspace", id="import-name")
            yield Static("Existing workspace directory", classes="field-label")
            yield Input(placeholder="/path/to/project", id="import-workspace")
            yield Static("Matching Workbench configuration · optional", classes="field-label")
            yield Input(placeholder="/path/to/workbench.toml", id="import-config")
            yield Static("Local Java home · only if the share requires one", classes="field-label")
            yield Input(placeholder="/path/to/jdk", id="import-java")
            yield Checkbox("Acquire managed Java before binding", id="import-acquire-java")
            with Horizontal(classes="button-row"):
                yield Button("Check exact plan", id="import-plan", variant="primary")
                yield Button("Bind selection", id="import-apply", disabled=True)
                yield Button("Back", id="import-back")
            yield Static("", id="import-status")
            yield Static("", id="import-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.start_keyboard_navigation()

    def _options(self) -> tuple[str, str, str, str, str, bool]:
        fields = tuple(
            self.query_one(f"#import-{field}", Input).value.strip()
            for field in ("share", "name", "workspace", "config", "java")
        )
        return (*fields, self.query_one("#import-acquire-java", Checkbox).value)

    def _invalidate_plan(self) -> None:
        self.plan = None
        self.plan_options = None
        self.query_one("#import-apply", Button).disabled = True
        self.query_one("#import-detail", Static).update("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("import-"):
            self._invalidate_plan()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "import-acquire-java":
            self._invalidate_plan()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "import-back" and not self.busy:
            self.app.pop_screen()
        elif event.button.id == "import-plan":
            self.check_plan()
        elif event.button.id == "import-apply":
            self.confirm_import()

    @work(exclusive=True, group="environment-import-plan")
    async def check_plan(self) -> None:
        if self.busy:
            return
        options = self._options()
        self._invalidate_plan()
        self.busy = True
        self.query_one("#import-plan", Button).disabled = True
        self.query_one("#import-status", Static).update("Core is checking the exact environment selection…")
        try:
            source, name, workspace, config, java, acquire = options
            plan = await self.core.plan_environment_import(
                source, name, workspace, config=config, java_home=java,
                **({"acquire_managed_java": True} if acquire else {}),
            )
            if self._options() != options:
                self.query_one("#import-status", Static).update("Inputs changed. Check a new plan.")
                return
            self.plan = plan
            self.plan_options = options
            lines = [
                f"Plan: {plan['plan_id']}",
                f"State: {plan['state']}",
                f"Action: {plan.get('action', '?')}",
                f"Acquire managed Java: {'yes' if acquire else 'no'}",
            ]
            source_lock = plan.get("project_source_lock")
            if isinstance(source_lock, dict):
                lines.append(
                    f"Project source lock: {source_lock.get('sha256', '?')} "
                    f"(commit {source_lock.get('revision', '?')})"
                )
            tool_lock = plan.get("managed_tool_lock")
            if isinstance(tool_lock, dict):
                lines.append(f"Managed-tool policy lock: {tool_lock.get('lock_id', '?')}")
                lines.append("Managed-tool bytes are not acquired by this import.")
            lines.extend(f"Blocked: {item}" for item in plan["blockers"])
            lines.extend(f"Additional input: {item}" for item in plan["unresolved_inputs"])
            self.query_one("#import-detail", Static).update("\n".join(lines))
            self.query_one("#import-status", Static).update(
                "Core blocked this selection." if plan["state"] == "blocked"
                else "Plan ready. Review the exact binding before importing."
            )
        except (CoreClientError, TimeoutError) as exc:
            self.query_one("#import-status", Static).update(str(exc))
        finally:
            self.busy = False
            self.query_one("#import-plan", Button).disabled = False
            self.query_one("#import-apply", Button).disabled = (
                self.plan is None or self.plan.get("state") != "ready"
            )

    @work(exclusive=True, group="environment-import-apply")
    async def confirm_import(self) -> None:
        plan = self.plan
        options = self.plan_options
        if plan is None or options is None or plan.get("state") != "ready" or self.busy:
            return
        source, name, workspace, config, java, acquire = options
        source_lock = plan.get("project_source_lock")
        source_review = (
            f"Project source lock\n{source_lock.get('sha256', '?')}\n"
            f"Commit: {source_lock.get('revision', '?')}\n\n"
            if isinstance(source_lock, dict) else ""
        )
        tool_lock = plan.get("managed_tool_lock")
        tool_review = (
            f"Managed-tool policy lock\n{tool_lock.get('lock_id', '?')}\n"
            "Tool bytes remain unresolved after binding.\n\n"
            if isinstance(tool_lock, dict) else ""
        )
        body = (
            f"Plan ID\n{plan['plan_id']}\n\n"
            f"Share\n{source}\n\n"
            f"Local binding\n{name}: {workspace}\n"
            f"Configuration: {config or 'suite default'}\n"
            f"Java home: {java or 'shared managed choice'}\n\n"
            f"Acquire managed Java: {'yes' if acquire else 'no'}\n\n"
            f"{source_review}"
            f"{tool_review}"
            "Core will recheck the exact lock and registry revision before saving the selection."
        )
        approved = await self.app.push_screen_wait(
            ReviewModal("Bind environment selection?", body, confirm_label="Bind exact plan")
        )
        if not approved or self.plan is not plan or self._options() != options:
            return
        self.busy = True
        self.query_one("#import-apply", Button).disabled = True
        self.query_one("#import-status", Static).update(
            "Core is acquiring the locked Java release and binding the selection…"
            if acquire else "Core is binding the reviewed selection…"
        )
        try:
            result = await self.core.import_environment_share(
                source, name, workspace, expected_plan_id=str(plan["plan_id"]),
                config=config, java_home=java,
                **({"acquire_managed_java": True} if acquire else {}),
            )
            self._invalidate_plan()
            java_status = result.get("managed_java")
            java_line = (
                f"Managed Java {java_status['outcome']}: {java_status['runtime_id']}\n"
                if isinstance(java_status, dict) else ""
            )
            self.query_one("#import-status", Static).update(
                f"Selection {result['outcome']}. Receipt: {result['resource']['path']}\n"
                + java_line
                + "Acquire remaining inputs before running the environment."
            )
            self.app.refresh_environment()  # type: ignore[attr-defined]
        except (CoreClientError, TimeoutError) as exc:
            self.query_one("#import-status", Static).update(str(exc))
        finally:
            self.busy = False


class ModulesScreen(Screen[None]):
    """Compare DataTable and tabs as module/profile presentation primitives."""

    SUB_TITLE = "Installed capabilities"

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, view: EnvironmentView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Installed capabilities", classes="screen-heading")
        yield Static(
            "Core reports each installed module and profile with its availability. "
            "Selecting a tab changes only this presentation.",
            classes="screen-intro",
        )
        with TabbedContent(id="inventory-tabs"):
            with TabPane("Modules", id="modules-tab"):
                with Horizontal(classes="inventory-layout"):
                    yield DataTable(id="modules-table", cursor_type="row")
                    with VerticalScroll(classes="inventory-detail-panel"):
                        yield Static("Select a module", id="module-detail")
            with TabPane("Profiles", id="profiles-tab"):
                with Horizontal(classes="inventory-layout"):
                    yield DataTable(id="profiles-table", cursor_type="row")
                    with VerticalScroll(classes="inventory-detail-panel"):
                        yield Static("Select a profile", id="profile-detail")
        with Horizontal(classes="button-row"):
            yield Button("Back to Home", id="modules-back")
        yield Footer()

    def on_mount(self) -> None:
        modules = self.query_one("#modules-table", DataTable)
        modules.add_columns("Module", "Version", "State")
        for item in self.view.modules:
            modules.add_row(
                str(item.get("id", "?")),
                str(item.get("version", "?")),
                str(item.get("state", "?")),
                key=str(item.get("id", "?")),
            )
        profiles = self.query_one("#profiles-table", DataTable)
        profiles.add_columns("Profile", "Kind", "State")
        for item in self.view.profiles:
            profiles.add_row(
                str(item.get("id", "?")),
                str(item.get("kind", "?")),
                str(item.get("state", "?")),
                key=str(item.get("id", "?")),
            )
        if self.view.modules:
            self._show_module(str(self.view.modules[0].get("id", "?")))
        if self.view.profiles:
            self._show_profile(str(self.view.profiles[0].get("id", "?")))
        modules.focus()

    def _show_module(self, module_id: str) -> None:
        item = next((row for row in self.view.modules if row.get("id") == module_id), None)
        if item is None:
            return
        detail = Text()
        detail.append(str(item.get("id", "?")), style="bold")
        detail.append("\n" + str(item.get("distribution", "")), style="dim")
        detail.append("\nVersion: " + str(item.get("version", "?")))
        detail.append("\nState: " + str(item.get("state", "?")))
        if item.get("reason"):
            detail.append("\n\n" + str(item["reason"]), style="bold")
        capabilities = item.get("capabilities", [])
        if capabilities:
            detail.append("\n\nCapabilities\n", style="bold underline")
            for capability in capabilities:
                detail.append("• " + str(capability) + "\n")
        self.query_one("#module-detail", Static).update(detail)

    def _show_profile(self, profile_id: str) -> None:
        item = next((row for row in self.view.profiles if row.get("id") == profile_id), None)
        if item is None:
            return
        detail = Text()
        detail.append(str(item.get("id", "?")), style="bold")
        detail.append("\n" + str(item.get("distribution", "")), style="dim")
        detail.append("\nKind: " + str(item.get("kind", "?")))
        detail.append("\nVersion: " + str(item.get("version", "?")))
        detail.append("\nState: " + str(item.get("state", "?")))
        if item.get("reason"):
            detail.append("\n\n" + str(item["reason"]), style="bold")
        resources = item.get("resources", [])
        if resources:
            detail.append("\n\nResources\n", style="bold underline")
            for resource in resources:
                detail.append("• " + str(resource) + "\n")
        self.query_one("#profile-detail", Static).update(detail)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "modules-table" and event.row_key.value:
            self._show_module(event.row_key.value)
        elif event.data_table.id == "profiles-table" and event.row_key.value:
            self._show_profile(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modules-back":
            self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class SetupScreen(KeyboardFormScreen):
    """Guided Core check → plan → explicit apply, with one frozen option set."""

    SUB_TITLE = "Environment setup"

    KEYBOARD_CANCEL = "setup-back"

    KEYBOARD_FIELDS = (
        "setup-mode", "setup-workspace", "setup-workspace-browse",
        "setup-profile", "setup-profile-browse", "setup-more", "setup-state-root",
        "setup-java-candidates", "setup-java", "setup-git", "setup-check",
        "setup-plan", "setup-apply", "setup-workflows", "setup-back",
    )

    def __init__(
        self,
        view: EnvironmentView,
        initial_workspace: str = "",
        initial_profile_config: str = "",
    ) -> None:
        super().__init__()
        self.view = view
        self.initial_workspace = initial_workspace
        self.initial_profile_config = initial_profile_config
        self.plan: Mapping[str, Any] | None = None
        self.plan_options: tuple[str, ...] = ()
        self.busy = False
        self.detected_java: dict[str, str] = {}
        self.show_extra_paths = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        saved = self.view.setup.get("selection", {}) if self.view.setup else {}
        if not isinstance(saved, dict):
            saved = {}
        has_saved_profile = bool(saved.get("profile_config"))
        has_profile = has_saved_profile or bool(self.initial_profile_config)
        modes = [("Explore with a workspace · no config needed", "review")]
        modes.append(("Use an existing developer configuration", "full"))
        if self.view.setup and self.view.setup.get("configured"):
            modes.append(("Repair saved setup", "repair"))
        default_mode = "repair" if has_saved_profile else "full" if has_profile else "review"
        yield Header(icon="W")
        with VerticalScroll(id="setup-scroll"):
            yield Static("Environment setup", classes="screen-heading")
            yield Static(
                "New here? Choose a workspace to explore source and workflows. "
                "For development, connect an existing workbench.toml. "
                "Review the plan before Workbench saves your choice.",
                classes="screen-intro",
            )
            yield Static(
                "↑/↓ Move  ·  Enter Edit/Save  ·  Esc Cancel/Back",
                classes="keyboard-hint",
            )
            yield Static("What do you want to set up?", classes="field-label")
            yield Select(modes, value=default_mode, allow_blank=False, id="setup-mode")
            yield Static("Workspace", classes="field-label")
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self.initial_workspace or str(saved.get("workspace") or Path.cwd()),
                    placeholder="Absolute project directory",
                    id="setup-workspace",
                )
                if _HAS_PICKER:
                    yield Button("Browse", id="setup-workspace-browse")
            yield Static("Existing workbench.toml", classes="field-label", id="setup-profile-label")
            with Horizontal(classes="path-row", id="setup-profile-row"):
                yield Input(
                    value=str(saved.get("profile_config") or self.initial_profile_config),
                    placeholder="/path/to/workbench.toml",
                    id="setup-profile",
                )
                if _HAS_PICKER:
                    yield Button("Browse", id="setup-profile-browse")
            yield Button("Show optional paths", id="setup-more")
            yield Static("Runtime state root · optional", classes="field-label", id="setup-state-label")
            yield Input(value=str(saved.get("state_root") or ""), id="setup-state-root")
            yield Static("Installed Java · optional", classes="field-label", id="setup-java-candidates-label")
            yield Select(
                [("Looking for installed JDKs…", "none")], value="none",
                allow_blank=False, disabled=True, id="setup-java-candidates",
            )
            yield Static(
                "Detected JDKs can fill the Java home below. Core checks a selected "
                "path against the profile during setup.",
                id="setup-java-hint",
            )
            yield Static("Java home · optional", classes="field-label", id="setup-java-label")
            yield Input(value=str(saved.get("java_home") or ""), id="setup-java")
            yield Static("Git executable · optional", classes="field-label", id="setup-git-label")
            yield Input(value=str(saved.get("git_executable") or ""), id="setup-git")
            with Horizontal(classes="button-row"):
                yield Button("Check selection", id="setup-check")
                yield Button("Review plan", id="setup-plan", variant="primary")
                yield Button("Apply plan", id="setup-apply", variant="warning", disabled=True)
            with Horizontal(classes="button-row"):
                yield Button("Browse workflows", id="setup-workflows")
                yield Button("Back", id="setup-back")
            yield Static("Choose a workspace, then Check selection.", id="setup-status")
            yield DataTable(id="setup-dependencies", cursor_type="row")
            yield Static("", id="setup-plan-detail")
        yield Footer()

    def on_mount(self) -> None:
        dependencies = self.query_one("#setup-dependencies", DataTable)
        dependencies.display = False
        dependencies.add_columns(
            "Dependency", "State", "Detail / repair"
        )
        self.query_one("#setup-plan-detail", Static).display = False
        self._update_mode()
        if self.view.setup:
            self._show_dependencies(self.view.setup)
        self.find_java()
        self.start_keyboard_navigation()

    def _update_mode(self) -> None:
        mode = self.query_one("#setup-mode", Select).value
        disabled = mode == "review"
        for field in ("#setup-profile-label", "#setup-profile-row", "#setup-profile"):
            self.query_one(field).display = not disabled
        self.query_one("#setup-profile", Input).disabled = disabled
        self.query_one("#setup-java", Input).disabled = disabled
        self.query_one("#setup-java-candidates", Select).disabled = disabled or not self.detected_java
        for field in ("#setup-java-candidates-label", "#setup-java-candidates",
                      "#setup-java-hint", "#setup-java-label", "#setup-java"):
            self.query_one(field).display = not disabled and self.show_extra_paths
        for field in ("#setup-state-label", "#setup-state-root",
                      "#setup-git-label", "#setup-git"):
            self.query_one(field).display = self.show_extra_paths
        self.query_one("#setup-more", Button).label = (
            "Hide optional paths" if self.show_extra_paths else "Show optional paths"
        )
        if _HAS_PICKER:
            browse = self.query_one("#setup-profile-browse", Button)
            browse.display = not disabled
            browse.disabled = disabled or self.busy

    def _inputs(self) -> SetupInputs:
        mode = self.query_one("#setup-mode", Select).value
        if not isinstance(mode, str):
            raise ValueError("choose a setup journey")
        return SetupInputs(
            mode=mode,
            workspace=self.query_one("#setup-workspace", Input).value,
            profile_config=(
                "" if mode == "review" else self.query_one("#setup-profile", Input).value
            ),
            state_root=self.query_one("#setup-state-root", Input).value,
            java_home="" if mode == "review" else self.query_one("#setup-java", Input).value,
            git_executable=self.query_one("#setup-git", Input).value,
        )

    def _invalidate_plan(self) -> None:
        self.plan = None
        self.plan_options = ()
        self.query_one("#setup-apply", Button).disabled = True
        detail = self.query_one("#setup-plan-detail", Static)
        detail.update("")
        detail.display = False

    def _selection_matches(self, options: tuple[str, ...]) -> bool:
        try:
            return self._inputs().option_args() == options
        except ValueError:
            return False

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.query_one("#setup-status", Static).update(
            Text(message, style="bold underline" if error else "bold")
        )

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        buttons = ["setup-check", "setup-plan", "setup-workflows", "setup-back"]
        if _HAS_PICKER:
            buttons.extend(("setup-workspace-browse", "setup-profile-browse"))
        for button_id in buttons:
            self.query_one(f"#{button_id}", Button).disabled = busy
        if _HAS_PICKER and not busy:
            self._update_mode()
        self.query_one("#setup-apply", Button).disabled = (
            busy or self.plan is None or bool(self.plan.get("blockers"))
        )

    def _show_dependencies(self, record: Mapping[str, Any]) -> None:
        table = self.query_one("#setup-dependencies", DataTable)
        table.clear()
        count = 0
        for item in record.get("dependencies", []):
            if not isinstance(item, dict):
                continue
            detail = item.get("detail") or item.get("repair") or ""
            table.add_row(
                str(item.get("label") or item.get("id") or "?"),
                str(item.get("state") or "?"),
                str(detail),
            )
            count += 1
        table.display = bool(count)

    def _show_plan(self, plan: Mapping[str, Any]) -> None:
        lines = [
            f"Plan: {plan.get('plan_id', '?')}",
            f"State: {plan.get('state', '?')}",
            "", "Selected paths",
        ]
        selection = plan.get("selection", {})
        if isinstance(selection, dict):
            for key in ("workspace", "profile_config", "state_root", "java_home", "managed_java_home", "git_executable"):
                lines.append(f"• {key.replace('_', ' ')}: {selection.get(key) or 'none'}")
        lines.extend(("", "Changes"))
        for item in plan.get("actions", []):
            if isinstance(item, dict):
                lines.append(f"• {item.get('effect', item.get('id', '?'))}")
                if item.get("source"):
                    lines.append(f"  Source: {item['source']}")
                if item.get("destination"):
                    lines.append(f"  Destination: {item['destination']}")
        if plan.get("blockers"):
            lines.extend(("", "Blockers: " + ", ".join(map(str, plan["blockers"]))))
        detail = self.query_one("#setup-plan-detail", Static)
        detail.update(Text("\n".join(lines)))
        detail.display = True

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("setup-"):
            self._invalidate_plan()
            if event.input.id == "setup-java":
                selector = self.query_one("#setup-java-candidates", Select)
                selected = selector.value
                if (isinstance(selected, str) and selected in self.detected_java
                        and event.value != self.detected_java[selected]):
                    selector.value = "none"

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "setup-mode":
            self._update_mode()
            self._invalidate_plan()
        elif event.select.id == "setup-java-candidates":
            if isinstance(event.value, str) and event.value in self.detected_java:
                self.query_one("#setup-java", Input).value = self.detected_java[event.value]
            self._invalidate_plan()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-back":
            if not self.busy:
                self.app.pop_screen()
        elif event.button.id == "setup-check":
            self.check_selection()
        elif event.button.id == "setup-plan":
            self.review_plan()
        elif event.button.id == "setup-apply":
            self.confirm_apply()
        elif event.button.id == "setup-workflows":
            if not self.busy:
                self.app.pop_screen()
                self.app.open_workflows()  # type: ignore[attr-defined]
        elif event.button.id == "setup-more":
            self.show_extra_paths = not self.show_extra_paths
            self._update_mode()
        elif event.button.id == "setup-workspace-browse":
            self.pick_path("workspace")
        elif event.button.id == "setup-profile-browse":
            self.pick_path("profile")

    @work(exclusive=True, group="setup-picker")
    async def pick_path(self, field: str) -> None:
        from textual_fspicker import FileOpen, SelectDirectory

        input_id = "setup-workspace" if field == "workspace" else "setup-profile"
        raw = self.query_one(f"#{input_id}", Input).value
        location = Path(raw or Path.cwd()).expanduser().absolute()
        if field == "profile" and raw:
            location = location.parent
        while not location.is_dir() and location != location.parent:
            location = location.parent
        picker = (
            SelectDirectory(location=location, title="Choose an existing workspace")
            if field == "workspace"
            else FileOpen(location=location, title="Choose Workbench configuration")
        )
        selected = await self.app.push_screen_wait(picker)
        if selected is not None:
            self.query_one(f"#{input_id}", Input).value = str(selected)

    @work(exclusive=True, group="setup-request")
    async def check_selection(self) -> None:
        try:
            options = self._inputs().option_args()
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        self._set_busy(True)
        self._set_status("Checking the selected environment…")
        try:
            check = await self.core.setup_check(options)
            if not self._selection_matches(options):
                self._set_status("Selection changed during the check. Check it again.")
                return
            self._show_dependencies(check)
            self._set_status(
                f"Core reports {check.get('state', '?')} for this selection. "
                "A check does not change the environment."
            )
        except (CoreClientError, TimeoutError) as exc:
            self._set_status(str(exc), error=True)
        finally:
            self._set_busy(False)

    @work(exclusive=True, group="setup-request")
    async def review_plan(self) -> None:
        try:
            options = self._inputs().option_args()
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        self._invalidate_plan()
        self._set_busy(True)
        self._set_status("Building an exact Core plan…")
        try:
            plan = await self.core.setup_plan(options)
            if not self._selection_matches(options):
                self._set_status("Selection changed while Core built the plan. Review a new plan.")
                return
            self.plan = plan
            self.plan_options = options
            self._show_dependencies(plan)
            self._show_plan(plan)
            blockers = plan.get("blockers", [])
            if blockers:
                self._set_status("Core blocked apply: " + ", ".join(map(str, blockers)), error=True)
            else:
                self._set_status("Plan ready. Review its exact effects before applying.")
        except (CoreClientError, TimeoutError) as exc:
            self._set_status(str(exc), error=True)
        finally:
            self._set_busy(False)
            self.query_one("#setup-apply", Button).disabled = (
                self.plan is None or bool(self.plan.get("blockers"))
            )

    @work(exclusive=True, group="setup-apply")
    async def confirm_apply(self) -> None:
        plan = self.plan
        if not plan or plan.get("blockers") or self.busy:
            return
        actions = plan.get("actions", [])
        details = "\n".join(
            "• " + str(item.get("effect", item.get("id", "?")))
            + ("\n  Source: " + str(item["source"]) if item.get("source") else "")
            + ("\n  Destination: " + str(item["destination"]) if item.get("destination") else "")
            for item in actions if isinstance(item, dict)
        )
        selection = plan.get("selection", {})
        selected_paths = "\n".join(
            f"{key.replace('_', ' ')}: {selection.get(key) or 'none'}"
            for key in ("workspace", "profile_config", "state_root", "java_home", "managed_java_home", "git_executable")
        ) if isinstance(selection, dict) else "Selection unavailable"
        body = (
            f"Plan ID\n{plan['plan_id']}\n\n"
            f"Selected paths\n{selected_paths}\n\n"
            f"Effects\n{details or 'No changes'}\n\n"
            "Core will recheck the plan and its owner policies before changing state."
        )
        approved = await self.app.push_screen_wait(
            ReviewModal("Apply Workbench setup?", body, confirm_label="Apply exact plan")
        )
        if not approved or self.plan is not plan:
            return
        self._set_busy(True)
        self._set_status("Applying the reviewed setup. Wait for Core's result…")
        try:
            result = await self.core.setup_apply(str(plan["plan_id"]), self.plan_options)
            selection = result.get("record", {}).get("selection", {})
            self._set_status(
                "Setup saved for " + str(selection.get("workspace") or "the selected workspace")
            )
            self._invalidate_plan()
            self.app.refresh_environment()  # type: ignore[attr-defined]
        except (CoreClientError, TimeoutError) as exc:
            self._set_status(str(exc), error=True)
        finally:
            self._set_busy(False)

    @work(exclusive=True, group="setup-java-discovery")
    async def find_java(self) -> None:
        inventory_method = getattr(self.core, "java_inventory", None)
        if inventory_method is None:
            self.query_one("#setup-java-hint", Static).update(
                "Installed Java detection is unavailable. You can still enter "
                "a Java home or use profile-managed Java."
            )
            return
        try:
            inventory = await inventory_method()
            detected, found_options = _detected_jdk_options(inventory)
            options: list[tuple[str, str]] = [("Select an installed JDK", "none")]
            options.extend(found_options)
            self.detected_java = detected
            selector = self.query_one("#setup-java-candidates", Select)
            selector.set_options(options if detected else [("No installed JDKs found", "none")])
            selector.value = "none"
            self._update_mode()
            self.query_one("#setup-java-hint", Static).update(
                "Choose a detected JDK to fill Java home. Core checks it against "
                "the profile during setup."
                if detected else
                "No installed JDKs found. Leave Java home blank for profile-managed "
                "Java, or enter a path. In Repair, a blank field keeps saved Java."
            )
        except (CoreClientError, TimeoutError) as exc:
            self.query_one("#setup-java-hint", Static).update(
                f"Could not look for installed JDKs: {exc}. "
                "You can still enter a Java home or use profile-managed Java."
            )


class CatalogValueScreen(ModalScreen[tuple[bool, Any]]):
    """Edit one catalog field, then return to the keyboard field list."""

    def __init__(self, field: Mapping[str, Any], current: Any = None) -> None:
        super().__init__()
        self.field = field
        self.current = current

    def compose(self) -> ComposeResult:
        field = self.field
        kind = field["kind"]
        required = bool(field.get("required"))
        with Vertical(id="review-dialog"):
            yield Static(str(field.get("label") or field["key"]), id="review-heading")
            yield Static(str(field.get("help") or ""), id="review-body")
            if kind in {"choice", "boolean"}:
                if kind == "boolean":
                    choices = [("Yes", "true"), ("No", "false")]
                else:
                    choices = [(str(value), f"choice-{index}") for index, value
                               in enumerate(field.get("choices", []))]
                if not required:
                    choices.append(("Use the command default", "unset"))
                yield OptionList(*(Option(label, id=value) for label, value in choices),
                                 id="catalog-value-choices")
            else:
                yield Input(
                    value="" if self.current is None else str(self.current),
                    placeholder="Enter a value" if required else "Blank uses the command default",
                    password=bool(field.get("sensitive")),
                    id="catalog-value-input",
                )
            yield Static("", id="catalog-value-error")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="catalog-value-cancel")
                if kind not in {"choice", "boolean"}:
                    yield Button("Save value", id="catalog-value-save", variant="primary")

    def on_mount(self) -> None:
        widget = self.query_one(
            "#catalog-value-choices" if self.field["kind"] in {"choice", "boolean"}
            else "#catalog-value-input"
        )
        widget.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss((False, None))
            event.stop()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "catalog-value-choices":
            return
        if event.option_id == "unset":
            self.dismiss((True, None))
        elif self.field["kind"] == "boolean":
            self.dismiss((True, event.option_id == "true"))
        elif event.option_id and event.option_id.startswith("choice-"):
            index = int(event.option_id.removeprefix("choice-"))
            self.dismiss((True, self.field["choices"][index]))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "catalog-value-input":
            self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "catalog-value-cancel":
            self.dismiss((False, None))
        elif event.button.id == "catalog-value-save":
            self._save()

    def _save(self) -> None:
        raw = self.query_one("#catalog-value-input", Input).value.strip()
        if not raw:
            if self.field.get("required"):
                self.query_one("#catalog-value-error", Static).update("A value is required.")
                return
            self.dismiss((True, None))
            return
        if self.field["kind"] == "integer":
            try:
                value: Any = int(raw)
            except ValueError:
                self.query_one("#catalog-value-error", Static).update("Enter a whole number.")
                return
        else:
            value = raw
        self.dismiss((True, value))


class CatalogInputsScreen(ModalScreen[dict[str, Any] | None]):
    """Collect supported catalog inputs without composing downstream commands."""

    def __init__(self, action: Mapping[str, Any], initial: Mapping[str, Any]) -> None:
        super().__init__()
        self.action = action
        self.fields = _catalog_editable_fields(action)
        self.values = dict(initial)

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog"):
            yield Static(str(self.action.get("title") or "Action inputs"), id="review-heading")
            yield Static(
                "Use ↑/↓ and Enter to edit a field. Return here to review the action.",
                id="catalog-inputs-intro",
            )
            yield OptionList(id="catalog-fields")
            yield Static("", id="catalog-inputs-error")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="catalog-inputs-cancel")
                yield Button("Review action", id="catalog-inputs-review", variant="primary")

    def on_mount(self) -> None:
        self._refresh_fields()
        self.query_one("#catalog-fields", OptionList).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    def _refresh_fields(self) -> None:
        listing = self.query_one("#catalog-fields", OptionList)
        previous = listing.highlighted or 0
        options = []
        for field in self.fields:
            key = field["key"]
            value = self.values.get(key)
            display = (
                "required" if field.get("required") else
                f"default: {field['default']}" if "default" in field else
                "command default"
            ) if value is None else (
                "Yes" if value is True else "No" if value is False else str(value)
            )
            if field.get("sensitive") and value is not None:
                display = "••••"
            options.append(Option(f"{field.get('label') or key}  ·  {display}", id=key))
        options.append(Option("Review and run this action", id="review"))
        listing.set_options(options)
        listing.highlighted = min(previous, len(options) - 1)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "catalog-fields":
            return
        if event.option_id == "review":
            self._submit()
        elif event.option_id:
            self._edit_field(event.option_id)

    @work(exclusive=True, group="catalog-field-edit")
    async def _edit_field(self, key: str) -> None:
        field = next((item for item in self.fields if item["key"] == key), None)
        if field is None:
            return
        changed, value = await self.app.push_screen_wait(
            CatalogValueScreen(field, self.values.get(key))
        )
        if changed:
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value
            self._refresh_fields()
            self.query_one("#catalog-inputs-error", Static).update("")
        self.query_one("#catalog-fields", OptionList).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "catalog-inputs-cancel":
            self.dismiss(None)
        elif event.button.id == "catalog-inputs-review":
            self._submit()

    def _submit(self) -> None:
        missing = [str(field.get("label") or field["key"]) for field in self.fields
                   if field.get("required") and field["key"] not in self.values]
        if missing:
            self.query_one("#catalog-inputs-error", Static).update(
                "Enter required fields: " + ", ".join(missing)
            )
            return
        self.dismiss(dict(self.values))


class WorkflowsScreen(Screen[None]):
    """Present installed catalog actions this Textual client can run."""

    SUB_TITLE = "Workflows"

    def __init__(self, view: EnvironmentView) -> None:
        super().__init__()
        self.view = view
        self.selected: Mapping[str, Any] | None = None

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Workflows", classes="screen-heading")
        yield Static(
            "Choose an action to run. This list shows actions Textual can collect "
            "and launch here.",
            classes="screen-intro",
        )
        yield Static("↑/↓ Browse  ·  Enter Open  ·  / Search  ·  Esc Return", classes="keyboard-hint")
        yield Input(placeholder="Search actions, suites, and descriptions", id="workflow-search")
        with Horizontal(id="workflow-body"):
            yield OptionList(id="workflow-list")
            with Vertical(id="workflow-detail-panel"):
                yield Static("Select a workflow", id="workflow-detail")
                yield Static("Select an action", id="workflow-brief")
                with Horizontal(classes="button-row"):
                    yield Button("Run action", id="workflow-run", disabled=True)
                    yield Button("Back", id="workflow-back")
        yield Footer()

    def on_mount(self) -> None:
        self._filter("")
        self.query_one("#workflow-list", OptionList).focus()

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self.call_after_refresh(
                self._filter, self.query_one("#workflow-search", Input).value
            )

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self.query_one("#workflow-list", OptionList).focus()

    def on_key(self, event: events.Key) -> None:
        search = self.query_one("#workflow-search", Input)
        if event.key in {"slash", "/"} and not search.has_focus:
            search.focus()
            event.stop()
        elif event.key == "escape" and search.has_focus:
            self.query_one("#workflow-list", OptionList).focus()
            event.stop()
        elif event.key == "escape":
            self.app.pop_screen()
            event.stop()

    def _actions(self) -> Iterable[Mapping[str, Any]]:
        catalog = self.view.catalog
        if not catalog:
            return ()
        actions = [
            item for item in catalog.get("commands", [])
            if isinstance(item, dict) and _runnable_catalog_action(item)
        ]
        enabled = {row.get("id") for row in self.view.modules
                   if row.get("state") == "available"}
        profiles = {row.get("id") for row in self.view.profiles
                    if row.get("state") == "available"}
        if "axiom" in enabled and {"supersymmetry", "cleanroom"} <= profiles:
            actions.append({
                "command_id": "axiom.native-check-journey",
                "title": "Check saved pack edits",
                "summary": "Prepare and run Axiom native initialization checks, then inspect retained findings.",
                "suite_id": "axiom",
                "authority": "Axiom through Core developer context",
                "risk": "guided",
                "preview": "none",
                "availability": "available",
                "options": [],
                "document": None,
            })
        return actions

    @staticmethod
    def _option_prompt(action: Mapping[str, Any], width: int) -> Text:
        """Keep action and suite in the same columns for every list row."""
        width = max(24, width)
        suite_width = min(20, max(11, width - 32))
        title_width = max(11, width - suite_width - 2)
        title = Text(str(action.get("title", "?")), no_wrap=True)
        title.truncate(title_width, overflow="ellipsis", pad=True)
        suite = Text(str(action.get("suite_id", "?")), no_wrap=True)
        suite.truncate(suite_width, overflow="ellipsis")
        prompt = Text(no_wrap=True)
        prompt.append_text(title)
        prompt.append("  ")
        prompt.append_text(suite)
        return prompt

    def _filter(self, query: str) -> None:
        listing = self.query_one("#workflow-list", OptionList)
        previous_id = self.selected.get("command_id") if self.selected else None
        words = query.casefold().split()
        matches = []
        for action in self._actions():
            haystack = " ".join(
                str(action.get(key, "")) for key in ("title", "summary", "command_id", "suite_id")
            ).casefold()
            if all(word in haystack for word in words):
                matches.append(action)
        width = listing.content_size.width or listing.region.width - 2 or self.app.size.width - 8
        options = []
        for item in matches[:200]:
            options.append(Option(
                self._option_prompt(item, width),
                id=str(item.get("command_id")),
            ))
        listing.set_options(options)
        self.selected = None
        self.query_one("#workflow-run", Button).disabled = True
        self.query_one("#workflow-run", Button).label = "Run action"
        if not options:
            message = "No matching action can run here. Try another search."
        elif len(matches) > 200:
            message = f"Showing the first 200 of {len(matches)} actions. Refine your search."
        else:
            message = "Highlight an action, then press Enter to open it."
        self.query_one("#workflow-detail", Static).update(Text(message))
        self.query_one("#workflow-brief", Static).update(Text(message))
        if options:
            selected_index = next(
                (index for index, option in enumerate(options) if option.id == previous_id),
                0,
            )
            listing.highlighted = selected_index
            self._select(options[selected_index].id)

    def _select(self, command_id: str | None) -> None:
        self.selected = next(
            (item for item in self._actions() if item.get("command_id") == command_id),
            None,
        )
        action = self.selected
        if not action:
            self.query_one("#workflow-run", Button).disabled = True
            self.query_one("#workflow-brief", Static).update("Select an action")
            return
        description_style = _DESCRIPTION_COLOR if self.app.current_theme.dark else ""
        metadata_style = _METADATA_COLOR if self.app.current_theme.dark else ""
        brief = Text()
        brief.append(str(action.get("title", command_id)), style="bold")
        summary = str(action.get("summary") or "")
        if summary:
            brief.append("\n" + summary, style=description_style)
        brief.append(
            "\n"
            f"{str(action.get('availability', 'available')).capitalize()}"
            f" · {str(action.get('suite_id', '?'))}"
            f" · {str(action.get('risk', '?')).replace('-', ' ')}",
            style=metadata_style,
        )
        self.query_one("#workflow-brief", Static).update(brief)
        detail = Text()
        detail.append(str(action.get("title", command_id)), style="bold")
        if summary:
            detail.append("\n\n" + summary, style=description_style)
        detail.append(
            "\n\n"
            f"{str(action.get('availability', 'available')).capitalize()}"
            f" · {str(action.get('risk', '?')).replace('-', ' ')}",
            style=metadata_style,
        )
        for label, key in (
            ("Module", "suite_id"),
            ("Owner", "authority"),
            ("ID", "command_id"),
        ):
            detail.append("\n")
            detail.append(f"{label}:  {action.get(key, '?')}", style=metadata_style)
        if action.get("limitations"):
            detail.append("\n\nLimits\n", style="bold underline")
            for item in action["limitations"]:
                detail.append("• " + str(item) + "\n")
        if action.get("options"):
            detail.append("\nDeclared fields\n", style="bold underline")
            for option in action["options"]:
                if isinstance(option, dict):
                    suffix = " · required" if option.get("required") else ""
                    detail.append(
                        f"• {option.get('label', option.get('key', '?'))} "
                        f"({option.get('kind', '?')}){suffix}\n"
                    )
        self.query_one("#workflow-detail", Static).update(detail)
        self.query_one("#workflow-run", Button).disabled = False
        self.query_one("#workflow-run", Button).label = (
            "Open document" if action.get("document") else
            "Open check" if action.get("command_id") == "axiom.native-check-journey" else
            "Run action"
        )

    def _launchable(self, action: Mapping[str, Any]) -> bool:
        return (action.get("command_id") == "axiom.native-check-journey"
                or _runnable_catalog_action(action))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "workflow-search":
            self._filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "workflow-search":
            event.stop()
            self.query_one("#workflow-list", OptionList).focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "workflow-list":
            self._select(event.option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "workflow-list":
            self._select(event.option_id)
            if self.selected is not None:
                self.run_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "workflow-back":
            self.app.pop_screen()
        elif event.button.id == "workflow-run":
            self.run_selected()

    @work(exclusive=True, group="workflow-run")
    async def run_selected(self) -> None:
        action = self.selected
        catalog = self.view.catalog
        if not action or not catalog or not self._launchable(action):
            return
        if action.get("command_id") == "axiom.native-check-journey":
            self.app.push_screen(AxiomJourneyScreen(self.view))
            return
        values: dict[str, Any] = {}
        if (self.view.workspace and Path(self.view.workspace).is_dir()
                and any(field.get("key") == "workspace"
                        for field in action.get("options", []))):
            values["workspace"] = self.view.workspace
        self.query_one("#workflow-run", Button).disabled = True
        try:
            if action.get("document"):
                output = await self.core.open_document(catalog, action)
                self.app.push_screen(
                    ResultScreen(str(action.get("title", "Document")), output.stdout)
                )
                return
            if _catalog_editable_fields(action):
                collected = await self.app.push_screen_wait(
                    CatalogInputsScreen(action, values)
                )
                if collected is None:
                    return
                values = collected
            atlas_json = (action.get("suite_id") == "atlas"
                          and any(field.get("key") == "json" and field.get("flags") == ["--json"]
                                  for field in action.get("options", []) if isinstance(field, dict)))
            if atlas_json:
                values["json"] = True
            review = await self.core.command_review(catalog, action, values)
            body = (
                f"Owner: {action.get('authority', '?')}\n"
                f"Risk: {review.get('risk', '?')}\n"
                f"Command: {action.get('command_id', '?')}\n\n"
                f"Exact command\n{review.get('execute_command', '?')}\n\n"
                f"Review digest\n{review.get('review_digest', '?')}"
            )
            approved = await self.app.push_screen_wait(
                ReviewModal("Run this Workbench action?", body, confirm_label="Run action")
            )
            if not approved:
                return
            output: CommandOutput = await self.core.run_reviewed_command(
                catalog, action, values, review,
                **({"console": "jsonl"} if atlas_json else {}),
            )
            if atlas_json:
                record = _atlas_record(output, str(action.get("command_id")))
                if (action.get("command_id") == "atlas.recipes-search"
                        and isinstance(record.get("results"), list)
                        and isinstance(values.get("path"), str)):
                    self.app.push_screen(AtlasSearchScreen(catalog, values["path"], record))
                else:
                    self.app.push_screen(AnalysisResultScreen(
                        str(action.get("title", "Atlas result")), "atlas", record
                    ))
                return
            result = (
                f"Exit code: {output.exit_code}\n\n"
                f"{output.stdout}"
                + (f"\nDiagnostics\n{output.stderr}" if output.stderr else "")
            )
            self.app.push_screen(ResultScreen(str(action.get("title", "Result")), result))
        except (CoreClientError, TimeoutError) as exc:
            self.app.push_screen(ResultScreen("Workbench action could not run", str(exc)))
        finally:
            self.query_one("#workflow-run", Button).disabled = not self._launchable(action)


class HomeScreen(Screen[None]):
    SUB_TITLE = "Home"
    def on_resize(self, event: events.Resize) -> None:
        self.app._size_brand()  # type: ignore[attr-defined]

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self.app._present_pending_pack_release()  # type: ignore[attr-defined]


_HOME_ACTION_HELP = {
    "pack-instance": "Choose a pack source and install it in Prism.",
    "setup": "Configure an existing developer workspace.",
    "workspace-choices": "Save a workspace and prepare its Java runtime.",
    "pack-release": "View or verify the published archive. Game files are installed separately.",
    "workflows": "Run available Workbench actions.",
    "modules": "Inspect installed Workbench modules.",
    "home": "Open details for the selected workspace.",
    "migrate": "Import settings from an earlier Workbench install.",
    "refresh": "Check Core and available actions again.",
}


class WorkbenchApp(App[None]):
    CSS_PATH = "workbench.tcss"
    TITLE = "Workbench"
    SUB_TITLE = ""
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (80, "-wide")]
    BINDINGS = [
        ("ctrl+t", "toggle_clock", "Clock"),
        ("r", "refresh_environment", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def format_title(self, title: str, sub_title: str) -> Content:
        if not sub_title:
            return Content(title)
        accent = _DESCRIPTION_COLOR if self.current_theme.dark else self.current_theme.primary
        return Content.assemble((title, "bold"), "\n", (sub_title, f"bold {accent}"))

    def get_default_screen(self) -> Screen[None]:
        return HomeScreen(id="_default")

    def __init__(
        self,
        core: CoreClient,
        *,
        initial_workspace: str = "",
        initial_profile_config: str = "",
        preferences: TuiPreferences | None = None,
        preference_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.register_theme(_WORKBENCH_THEME)
        self.preferences = preferences or TuiPreferences()
        self.preference_path = preference_path
        self._unavailable_saved_theme = self.get_theme(self.preferences.theme) is None
        self.theme = "workbench-dark" if self._unavailable_saved_theme else self.preferences.theme
        self.core = core
        self.initial_workspace = initial_workspace
        self.initial_profile_config = initial_profile_config
        self.view = EnvironmentView()
        self._migration_busy = False
        self._pack_release_checked = False
        self._pending_pack_release: Mapping[str, Any] | None = None
        self._home_initial_choice_selected = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=self.preferences.show_clock, icon="W", id="home-header")
        with VerticalScroll(id="home-scroll"):
            with Vertical(id="home-welcome"):
                yield Static("Welcome to Workbench", id="home-title")
                yield Static(
                    _HOME_ACTION_HELP["pack-instance"],
                    id="home-subtitle",
                )
            with Horizontal(id="home-panels"):
                with Vertical(id="actions-panel"):
                    yield Static("START HERE", classes="panel-title")
                    yield Static("↑/↓ Choose  ·  Enter Open", id="home-keyboard-hint")
                    yield OptionList(
                        Option("Set up Supersymmetry", id="pack-instance", disabled=True),
                        Option("Set up developer environment", id="setup", disabled=True),
                        Option("Choose workspace and Java", id="workspace-choices", disabled=True),
                        Option("View published pack archive", id="pack-release", disabled=True),
                        Option("Browse and run workflows", id="workflows", disabled=True),
                        Option("Explore installed modules", id="modules", disabled=True),
                        Option("Open workspace summary", id="home", disabled=True),
                        Option("Import earlier settings", id="migrate", disabled=True),
                        Option("Refresh status", id="refresh"),
                        id="home-actions",
                    )
                with Vertical(id="environment-panel"):
                    yield Static("CURRENT ENVIRONMENT", classes="panel-title")
                    yield Static("Connecting to Core…", id="environment-summary")
                    yield Static("", id="environment-dependencies")
            yield Static("", id="home-note")
        yield Footer()

    def on_mount(self) -> None:
        self._size_brand()
        self.theme_changed_signal.subscribe(self, self._remember_theme)
        if self._unavailable_saved_theme:
            self.notify(
                f"Saved theme {self.preferences.theme!r} is unavailable; using Workbench dark",
                severity="warning",
            )
        self.refresh_environment()
        self.call_after_refresh(lambda: self.query_one("#home-actions", OptionList).focus())

    def _size_brand(self) -> None:
        if not self.screen_stack:
            return
        home = self.screen_stack[0]
        if not home.query("#home-panels"):
            return
        home.set_class(self.size.width < 80, "narrow-brand")

    def _remember_theme(self, theme: Theme) -> None:
        if theme.name == self.preferences.theme or (
            self._unavailable_saved_theme and theme.name == "workbench-dark"
        ):
            return
        try:
            self.preferences = save_preferences(
                replace(self.preferences, theme=theme.name),
                self.preference_path,
            )
        except PreferencesError as exc:
            self.notify(f"Could not save TUI theme: {exc}", severity="warning")
        else:
            self._unavailable_saved_theme = False

    async def action_toggle_clock(self) -> None:
        try:
            self.preferences = save_preferences(
                replace(self.preferences, show_clock=not self.preferences.show_clock),
                self.preference_path,
            )
        except PreferencesError as exc:
            self.notify(f"Could not save clock preference: {exc}", severity="warning")
            return
        home = self.screen_stack[0]
        await home.query_one("#home-header", Header).remove()
        await home.mount(
            Header(show_clock=self.preferences.show_clock, icon="W", id="home-header"),
            before="#home-scroll",
        )

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("Set up Workbench", "Open the setup wizard", self.open_setup)
        yield SystemCommand("Workspace choices", "Choose a saved workspace profile and Java", self.open_workspace_choices)
        yield SystemCommand("Supersymmetry pack", "View or prepare the saved release", self.open_pack_release)
        yield SystemCommand("Set up Supersymmetry instance", "Choose an instance source and installation", self.open_pack_instance)
        yield SystemCommand("Explore modules", "Show installed modules and profiles", self.open_modules)
        yield SystemCommand("Browse workflows", "Search the installed action catalog", self.open_workflows)
        yield SystemCommand(
            "Import earlier configuration",
            "Review and import earlier Workbench user records",
            self.open_config_migration,
        )

    def action_refresh_environment(self) -> None:
        self._pack_release_checked = False
        self.refresh_environment()

    def open_setup(self) -> None:
        if self.view.setup is None:
            self.notify("Waiting for Core setup status", severity="warning")
            return
        self.push_screen(
            SetupScreen(self.view, self.initial_workspace, self.initial_profile_config)
        )

    @work(exclusive=True, group="workspace-choices")
    async def open_workspace_choices(self) -> None:
        if self.view.version is None:
            self.notify("Waiting for Workbench Core", severity="warning")
            return
        try:
            choices = await self.core.workspace_choices()
        except (CoreClientError, TimeoutError) as exc:
            self.push_screen(ResultScreen("Workspace choices unavailable", str(exc)))
            return
        self.push_screen(WorkspaceChoicesScreen(choices))

    @work(exclusive=True, group="pack-release-choice")
    async def open_pack_release(self) -> None:
        if self.view.version is None:
            self.notify("Waiting for Workbench Core", severity="warning")
            return
        try:
            choice = await self.core.pack_release_show()
        except (CoreClientError, TimeoutError) as exc:
            self.push_screen(ResultScreen("Pack choice unavailable", str(exc)))
            return
        selected = choice["selected"]
        version = selected["version"]
        if choice["artifact_state"] == "verified":
            self.push_screen(ResultScreen(
                "Supersymmetry pack ready",
                f"Saved release: {version}\n"
                f"Verified archive: {selected['artifact_path']}\n\n"
                "Your workspace files are unchanged.",
            ))
            return
        if choice["artifact_state"] == "changed":
            self.push_screen(ResultScreen(
                "Supersymmetry archive needs review",
                f"Saved release: {version}\n\n"
                "The retained archive no longer matches the saved release. Core has "
                "preserved it and will not replace it automatically. Review the "
                "stable artifact store before trying to prepare this release again.",
            ))
            return
        approved = await self.push_screen_wait(ReviewModal(
            f"Prepare Supersymmetry {version}?",
            ("This release was saved under another Workbench location. "
             "A checked copy will be prepared here.\n\n"
             if choice["artifact_state"] == "other_root" else "")
            + "Workbench will download and check the published pack ZIP "
            f"({selected['asset_size'] / (1024 * 1024):.1f} MiB), then keep it in your "
            "Workbench files. Your workspace files will not change.",
            confirm_label="Prepare verified archive",
        ))
        if not approved:
            return
        self.notify("Core is verifying and preparing the Supersymmetry release archive…")
        try:
            result = await self.core.pack_release_prepare(selected["release_id"])
        except (CoreClientError, TimeoutError) as exc:
            self.push_screen(ResultScreen(
                "Supersymmetry pack could not be prepared",
                f"{exc}\n\nReturn Home to review the saved pack choice again.",
            ))
            return
        self.push_screen(ResultScreen(
            "Supersymmetry pack prepared",
            f"Saved release: {result['selected_version']}\n"
            f"Verified archive: {result['artifact_path']}\n\n"
            "Your workspace files were not changed.",
        ))

    @work(exclusive=True, group="pack-instance-open")
    async def open_pack_instance(self) -> None:
        if self.view.version is None:
            self.notify("Waiting for Workbench Core", severity="warning")
            return
        try:
            choice = await self.core.pack_instance_choice_show()
            workspaces = await self.core.workspace_choices()
        except (CoreClientError, TimeoutError) as exc:
            self.push_screen(ResultScreen("Pack setup unavailable", str(exc)))
            return
        try:
            installed = await self.core.pack_instance_install_status()
            installed_error = None
        except (CoreClientError, TimeoutError) as exc:
            installed = {"installations": []}
            installed_error = str(exc)
        self.push_screen(PackInstanceScreen(
            choice, workspaces, installed["installations"], installed_error,
        ))

    def open_modules(self) -> None:
        if not (self.view.modules_loaded and self.view.profiles_loaded):
            self.notify("Waiting for module inventory", severity="warning")
            return
        self.push_screen(ModulesScreen(self.view))

    def open_workflows(self) -> None:
        if self.view.catalog is None:
            self.notify("Waiting for command catalog", severity="warning")
            return
        self.push_screen(WorkflowsScreen(self.view))

    def open_config_migration(self) -> None:
        if self.view.version is None:
            self.notify("Waiting for Workbench Core", severity="warning")
            return
        if not self._migration_busy:
            self.inspect_config_migration()

    @work(exclusive=True, group="config-migration")
    async def inspect_config_migration(self) -> None:
        self._migration_busy = True
        self._render_home()
        try:
            preview = await self.core.migration_preview()
            details = _migration_details(preview)
            if preview["state"] == "conflict":
                self.push_screen(ResultScreen(
                    "Configuration import blocked",
                    details + "\n\nCore found a different file at a stable destination. "
                    "Review the earlier and stable records, resolve the conflict, "
                    "then return Home to check again. No import was attempted.",
                ))
                return
            if preview["state"] != "ready":
                self.push_screen(ResultScreen("Configuration import", details))
                return
            approved = await self.push_screen_wait(ReviewModal(
                "Import earlier Workbench configuration?",
                details + "\n\nCore will recheck these files before copying. "
                "Existing stable files are preserved.",
                confirm_label="Import verified copies",
            ))
            if not approved:
                return
            try:
                result = await self.core.migration_import(preview)
            except (CoreClientError, TimeoutError) as exc:
                self.push_screen(ResultScreen(
                    "Configuration import could not complete",
                    f"{exc}\n\nReturn Home and choose Import earlier configuration "
                    "to review the current records again.",
                ))
            else:
                self.push_screen(ResultScreen("Configuration import complete", _migration_details(result)))
            self.refresh_environment()
        except (CoreClientError, TimeoutError) as exc:
            self.push_screen(ResultScreen(
                "Configuration import unavailable",
                f"{exc}\n\nCheck the earlier configuration records or Core installation, "
                "then return Home to try again.",
            ))
        finally:
            self._migration_busy = False
            self._render_home()

    @work(exclusive=True, group="environment-refresh")
    async def refresh_environment(self) -> None:
        view = EnvironmentView(catalog_loading=True)
        self.view = view
        self._render_home()
        try:
            view.version = await self.core.version()
        except (CoreClientError, TimeoutError) as exc:
            view.problems.append(str(exc))
            view.catalog_loading = False
            self.view = view
            self._render_home()
            return
        if not self._pack_release_checked:
            self._pack_release_checked = True
            self.check_pack_release()
        requests = (
            ("environment", lambda: self.core.environment_resolve(self.initial_workspace)),
            ("setup", lambda: self.core.setup_check(
                ("--workspace", self.initial_workspace) if self.initial_workspace else ()
            )),
            ("modules", self.core.modules),
            ("profiles", self.core.profiles),
            ("catalog", self.core.catalog),
        )
        for name, request in requests:
            try:
                setattr(view, name, await request())
                if name == "modules":
                    view.modules_loaded = True
                elif name == "profiles":
                    view.profiles_loaded = True
            except (CoreClientError, TimeoutError) as exc:
                view.problems.append(f"{name}: {exc}")
            if name == "catalog":
                view.catalog_loading = False
            self._render_home()
        if view.workspace and Path(view.workspace).is_dir():
            try:
                view.home = await self.core.workspace_home(view.workspace)
            except (CoreClientError, TimeoutError) as exc:
                view.problems.append(f"Workspace Home: {exc}")
        self.view = view
        self._render_home()

    @work(exclusive=True, group="pack-release-check")
    async def check_pack_release(self) -> None:
        try:
            check = await self.core.pack_release_check()
        except (CoreClientError, TimeoutError):
            # Startup and offline use remain available when GitHub cannot be reached.
            return
        if not isinstance(check, dict) or check.get("status") != "update_available":
            return
        self._pending_pack_release = check
        self._present_pending_pack_release()

    def _present_pending_pack_release(self) -> None:
        if self._pending_pack_release is not None and isinstance(self.screen, HomeScreen):
            self._offer_pending_pack_release()

    @work(exclusive=True, group="pack-release-offer")
    async def _offer_pending_pack_release(self) -> None:
        if not isinstance(self.screen, HomeScreen) or self._pending_pack_release is None:
            return
        check = self._pending_pack_release
        self._pending_pack_release = None
        choice = await self.push_screen_wait(ReleaseUpdateModal(check))
        release_id = check["candidate"]["release_id"]
        if choice == "later":
            return
        try:
            if choice == "ignore":
                result = await self.core.pack_release_ignore(release_id)
                self.notify(
                    f"Ignored Supersymmetry {result['candidate']['version']} until the published release changes."
                )
                return
            self.notify("Core is verifying and preparing the Supersymmetry release archive…")
            result = await self.core.pack_release_accept(release_id)
        except (CoreClientError, TimeoutError) as exc:
            self.push_screen(ResultScreen(
                "Supersymmetry choice could not be saved",
                f"{exc}\n\nReturn Home and refresh to check the latest release again.",
            ))
            return
        self.push_screen(ResultScreen(
            "Supersymmetry release prepared",
            f"Saved pack: {result['selected_version']}\n"
            f"Verified archive: {result['artifact_path']}\n\n"
            "Your current workspace files were not changed.",
        ))

    def _render_home(self) -> None:
        if not self.screen_stack:
            return
        home = self.screen_stack[0]
        if not home.query("#home-actions"):
            return
        view = self.view
        actions = home.query_one("#home-actions", OptionList)
        for option_id, ready in (
            ("setup", view.setup is not None),
            ("workspace-choices", view.version is not None),
            ("pack-release", view.version is not None),
            ("pack-instance", view.version is not None),
            ("modules", view.modules_loaded and view.profiles_loaded),
            ("workflows", view.catalog is not None),
            ("home", view.home is not None),
            ("migrate", view.version is not None and not self._migration_busy),
        ):
            if ready:
                actions.enable_option(option_id)
            else:
                actions.disable_option(option_id)
        if (not self._home_initial_choice_selected and view.version is not None
                and not view.catalog_loading):
            self._home_initial_choice_selected = True
            actions.highlighted = 0
            self.call_after_refresh(lambda: setattr(actions, "highlighted", 0))
        overview = Text()
        overview.append_text(_line("Core", (view.version or {}).get("version", "unavailable")))
        setup = view.setup or {}
        review_only = bool(setup.get("configured")
                           and not setup.get("selection", {}).get("profile_config"))
        overview.append("\n")
        setup_status = (
            "Saved" if review_only
            else "Saved · " + str(setup.get("state") or "unknown") if setup.get("configured")
            else "Not saved" if view.setup
            else "unavailable" if any(row.startswith("setup:") for row in view.problems)
            else "loading…"
        )
        overview.append_text(_line("Setup", setup_status))
        if setup.get("configured"):
            overview.append("\n")
            journey = (
                "Developer" if setup.get("selection", {}).get("profile_config")
                else "Review-only"
            )
            overview.append_text(_line("Setup mode", journey))
        overview.append("\n")
        workspace_display = (
            Path(view.workspace).name or view.workspace if view.workspace else "not selected"
        )
        overview.append_text(_line("Workspace", workspace_display))
        overview.append("\n")
        modules_status = (
            f"{sum(row.get('state') == 'available' for row in view.modules)}/{len(view.modules)} available"
            if view.modules_loaded else "unavailable" if any(row.startswith("modules:") for row in view.problems) else "loading…"
        )
        overview.append_text(_line("Modules", modules_status))
        overview.append("\n")
        profiles_status = (
            f"{sum(row.get('state') == 'available' for row in view.profiles)}/{len(view.profiles)} available"
            if view.profiles_loaded else "unavailable" if any(row.startswith("profiles:") for row in view.problems) else "loading…"
        )
        overview.append_text(_line("Profiles", profiles_status))
        overview.append("\n")
        commands = (view.catalog or {}).get("commands", [])
        runnable_count = sum(
            _runnable_catalog_action(item) for item in commands if isinstance(item, dict)
        )
        catalog_status = (
            "loading catalog…" if view.catalog_loading
            else f"{runnable_count} available" if view.catalog is not None
            else "unavailable"
        )
        overview.append_text(_line("Workflows", catalog_status))
        home.query_one("#environment-summary", Static).update(overview)

        dependencies = Text()
        blockers = setup.get("blockers", [])
        if review_only:
            dependencies.append("\nReview mode is ready.", style="dim")
        elif blockers:
            dependencies.append("\nDEVELOPER SETUP CHECK\n", style="bold underline")
            dependency_rows = {
                row.get("id"): row for row in setup.get("dependencies", [])
                if isinstance(row, dict)
            }
            for item in blockers:
                row = dependency_rows.get(item, {})
                label = row.get("label") or item
                detail = row.get("detail") or row.get("repair")
                dependencies.append(
                    f"• {label}: {detail}\n" if detail else f"• {label}\n",
                    style="bold",
                )
        elif view.setup and setup.get("configured"):
            dependencies.append("\nNo setup blockers reported.", style="dim")
        elif view.setup:
            dependencies.append("\nNo setup has been saved yet.", style="dim")
        else:
            dependencies.append("\nWaiting for setup status.", style="dim")
        home.query_one("#environment-dependencies", Static).update(dependencies)

        note = Text()
        if view.home:
            workspace = view.home.get("workspace", {})
            status = view.home.get("status", {})
            note.append(
                f"Workspace Home: {workspace.get('display_name') or view.workspace} · "
                f"{status.get('state', '?')}"
            )
        elif view.workspace and not Path(view.workspace).is_dir():
            note.append("The selected workspace folder does not exist yet.")
        if view.problems:
            note.append("\n" + "\n".join(view.problems), style="bold underline")
        home.query_one("#home-note", Static).update(note)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "home-actions":
            return
        action = event.option_id
        if action == "setup":
            self.open_setup()
        elif action == "workspace-choices":
            self.open_workspace_choices()
        elif action == "pack-release":
            self.open_pack_release()
        elif action == "pack-instance":
            self.open_pack_instance()
        elif action == "modules":
            self.open_modules()
        elif action == "workflows":
            self.open_workflows()
        elif action == "home":
            if self.view.home:
                self.push_screen(
                    ResultScreen(
                        "Workspace Home",
                        json.dumps(self.view.home, ensure_ascii=False, indent=2),
                    )
                )
            else:
                self.open_setup()
        elif action == "refresh":
            self.action_refresh_environment()
        elif action == "migrate":
            self.open_config_migration()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "home-actions" and event.option_id in _HOME_ACTION_HELP:
            if self.screen_stack and self.screen_stack[0].query("#home-subtitle"):
                self.screen_stack[0].query_one("#home-subtitle", Static).update(
                    _HOME_ACTION_HELP[event.option_id]
                )


def _core_command(arguments: argparse.Namespace) -> tuple[str, ...]:
    if arguments.source_root is not None:
        entry = arguments.source_root.expanduser().absolute() / "tools" / "workbench.py"
        if not entry.is_file():
            raise ValueError(f"no Workbench source launcher at {entry}")
        return (arguments.source_python, str(entry))
    sibling = Path(sys.executable).parent / ("workbench.exe" if os.name == "nt" else "workbench")
    selected = (
        arguments.workbench
        or os.environ.get("WORKBENCH_EXECUTABLE")
        or (str(sibling) if sibling.is_file() else None)
        or shutil.which("workbench")
    )
    if not selected:
        raise ValueError("select an installed Core with --workbench or use --source-root for development")
    return (selected,)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimental Workbench Textual client")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--workbench", help="path to one installed Workbench Core executable")
    choice.add_argument("--source-root", type=Path, help="explicit source checkout for local development")
    parser.add_argument("--source-python", default="python3", help="Python for --source-root")
    parser.add_argument("--workspace", type=Path, help="initial workspace to inspect and configure")
    arguments = parser.parse_args(argv)
    try:
        command = _core_command(arguments)
        preferences = load_preferences()
    except (ValueError, PreferencesError) as exc:
        parser.error(str(exc))
    workspace = str(arguments.workspace.expanduser().absolute()) if arguments.workspace else ""
    source_root = arguments.source_root.expanduser().absolute() if arguments.source_root else None
    source_config = source_root / "workbench.toml" if source_root else None
    profile_hint = str(source_config) if source_config and source_config.is_file() else ""
    WorkbenchApp(
        CoreClient(command, cwd=source_root),
        initial_workspace=workspace,
        initial_profile_config=profile_hint,
        preferences=preferences,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
