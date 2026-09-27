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

from rich.text import Text
from textual import work
from textual import events
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from .brand import COMPACT_MARK, MARK
from .core_client import CoreClient, CoreClientError, SetupInputs, CommandOutput
from .preferences import (
    PreferencesError,
    TuiPreferences,
    load_preferences,
    save_preferences,
)


# This first interaction slice uses actions whose read-only intent and input
# binding can be shown without inventing an owner-specific form.
_LAUNCHABLE_READ_ONLY = {
    "environment.status",
    "workspace.open",
    "storage.list",
    "diagnose.latest",
    "manuals.overview",
}
_HAS_PICKER = find_spec("textual_fspicker") is not None


def _supports_block_logo() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "▀▄█".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


_WORKBENCH_THEME = Theme(
    name="workbench-dark",
    primary="#ffffff",
    secondary="#707070",
    accent="#ffffff",
    foreground="#eeeeee",
    background="#090909",
    surface="#171717",
    panel="#242424",
    warning="#dedede",
    error="#ffffff",
    success="#c4c4c4",
    dark=True,
    variables={"foreground-muted": "#adadad", "footer-key-foreground": "#ffffff"},
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
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="review-cancel")
                yield Button(self.confirm_label, id="review-confirm", variant="warning")

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
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="setup-recovery-cancel")
                yield Button("Keep files and start over", id="setup-recovery-abandon")
                yield Button("Resume setup", id="setup-recovery-reconcile", variant="warning")

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
                "GitHub lists different client archive bytes under the same release tag.\n"
                + digest_detail
                + "\nUse published archive asks Core to verify and retain those exact bytes, "
                "then saves them as your pack choice. Your current workspace files are "
                "not changed. Ignore this publication keeps your saved choice and "
                "suppresses only this exact published release identity."
            )
        else:
            explanation = (
                "Version up asks Core to verify and retain the release archive, then "
                "saves it as your pack choice. Your current workspace files are not changed. "
                "Ignore this release keeps your saved choice and suppresses only this "
                "exact published release identity."
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
            with Horizontal(classes="button-row"):
                yield Button("Later", id="release-later")
                yield Button(self.ignore_label, id="release-ignore")
                yield Button(self.accept_label, id="release-accept", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({
            "release-later": "later",
            "release-ignore": "ignore",
            "release-accept": "accept",
        }[event.button.id])


class PackInstanceScreen(Screen):
    """Collect a complete Prism ZIP and install choices through Core."""

    def __init__(self, choice: Mapping[str, Any], workspaces: Mapping[str, Any],
                 installations: list[Mapping[str, Any]] | None = None,
                 installed_error: str | None = None) -> None:
        super().__init__()
        self.choice = choice
        self.workspaces = workspaces
        self.installations = installations or []
        self.installed_error = installed_error
        self.busy = False
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
        with VerticalScroll():
            yield Static("Set up Supersymmetry", classes="screen-heading")
            yield Static(
                "Workbench will download the published pack when its provider is available. "
                "You can also import a complete Prism instance ZIP as one source. "
                "Core retains its gameplay files and prepares a new Linux Prism instance.",
                classes="screen-intro",
            )
            yield Checkbox("Include the pack's optional mod", value=True,
                           id="pack-fresh-optional")
            yield Static("Complete Prism instance ZIP", classes="field-label")
            yield Input(placeholder="/absolute/path/to/Supersymmetry-instance.zip", id="pack-zip-path")
            yield Static("Prism launcher data folder", classes="field-label")
            yield Input(value=launcher, id="pack-prism-root")
            yield Static("Workspace and Java choice", classes="field-label")
            yield Select([(f"{row['name']} · {row['path']}", row["name"]) for row in entries]
                         or [("Add a workspace below to continue", "")],
                         value=preferred, allow_blank=False, id="pack-workspace")
            yield Button("Add workspace", id="pack-workspace-register")
            yield Button("Change Java choice", id="pack-java-choice",
                         disabled=not bool(names))
            yield Static(
                "Java 25 is the Cleanroom default. Choose managed Java 8 or your "
                "own Java path for an imported instance that needs another runtime.",
                classes="screen-intro",
            )
            if self.installations:
                yield Static("Installed instances", classes="field-label")
                yield Select([
                    (f"{row['instance_path']} · {row.get('java_selection_state') or 'Java choice unknown'}",
                     row["plan_id"])
                    for row in self.installations
                ], value=self.install_plan_id, allow_blank=False,
                    id="pack-installed-choice")
            with Horizontal(classes="button-row"):
                yield Button("Download official release", id="pack-fresh-download",
                             variant="primary", disabled=not bool(names))
                yield Button("Review and import ZIP", id="pack-zip-import",
                             disabled=not bool(names))
                yield Button("Prepare and install selected source", id="pack-instance-install",
                             disabled=not bool(selected.get("source_plan_id") and
                                               self.choice.get("source_state") == "retained"))
                yield Button("Save instance location", id="pack-instance-save-location",
                             disabled=not bool(selected.get("source_plan_id") and
                                               self.choice.get("source_state") == "retained"))
                yield Button("Open in Prism", id="pack-instance-show",
                             disabled=self.install_plan_id is None)
                yield Button("Launch game", id="pack-instance-launch",
                             disabled=self.install_plan_id is None)
                yield Button("Back", id="pack-instance-back")
            current = selected.get("source_plan_id")
            state = self.choice.get("source_state")
            yield Static(
                (f"Saved source: {current}\nCore state: {state}" if current else
                 "No complete instance source is selected yet.")
                + (f"\nInstalled instances could not be checked: {self.installed_error}"
                   if self.installed_error else ""),
                id="pack-instance-status",
            )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pack-instance-back":
            self.app.pop_screen()
        elif event.button.id == "pack-workspace-register":
            self.register_workspace()
        elif event.button.id == "pack-java-choice":
            self.open_java_choices()
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

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "pack-installed-choice" and isinstance(event.value, str):
            self.install_plan_id = event.value

    def on_screen_resume(self, event: events.ScreenResume) -> None:
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
        selector.set_options([(f"{row['name']} · {row['path']}", row["name"])
                              for row in entries]
                             or [("Add a workspace below to continue", "")])
        selector.value = (current if current in names else
                          record.get("default") if record.get("default") in names else
                          names[0] if names else "")
        for button in ("#pack-fresh-download", "#pack-zip-import", "#pack-java-choice"):
            self.query_one(button, Button).disabled = not bool(names)
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
            (f"{row['name']} · {row['path']}", row["name"])
            for row in record["entries"]
        ])
        self.query_one("#pack-workspace", Select).value = name
        self.query_one("#pack-fresh-download", Button).disabled = False
        self.query_one("#pack-zip-import", Button).disabled = False
        self.query_one("#pack-java-choice", Button).disabled = False
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
                "\nThis ZIP declares Forge. Before installation, choose Change Java "
                "choice to select managed Java 8 or your own Java path if the "
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
            status.update(
                f"Retained {imported['source']['file_count']} files. "
                "Your source and setup choices are saved."
            )
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self.query_one("#pack-zip-import", Button).disabled = False

    @work(exclusive=True, group="pack-instance-fresh")
    async def download_official(self) -> None:
        if self.busy:
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
            approved = await self.app.push_screen_wait(ReviewModal(
                f"Download {total - ready} selected game files?",
                f"Published pack: {progress['release_version']}\n"
                f"Already retained: {ready}/{total}\n"
                f"Optional mod: {'included' if optional_mode == 'default' else 'omitted'}\n\n"
                "Core will download each authorized file, verify it, and retain progress "
                "so setup can resume after interruption.",
                confirm_label="Download and retain",
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
            status.update(
                f"Published release {progress['release_version']} is ready: "
                f"{ready} selected game files. The source and setup choices are saved. "
                "Choose Prepare and install selected source."
            )
        except (CoreClientError, TimeoutError) as exc:
            status.update(str(exc))
        finally:
            self.busy = False
            self.query_one("#pack-fresh-download", Button).disabled = False

    @work(exclusive=True, group="pack-instance-install")
    async def install_selected(self) -> None:
        if self.busy:
            return
        selected = self.choice["choice"]
        status = self.query_one("#pack-instance-status", Static)
        if not selected.get("source_plan_id") or self.choice.get("source_state") != "retained":
            status.update("Select a retained Supersymmetry source before installation.")
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
                        + ". Choose Prepare and install again to start over."
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
                            + ". Choose Prepare and install again to start over."
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
                "path, cancel and choose Change Java choice.\n"
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
    def __init__(self, heading: str, output: str) -> None:
        super().__init__()
        self.heading = heading
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "result-back":
            self.app.pop_screen()


class WorkspaceRegisterScreen(Screen[tuple[str, Mapping[str, Any]] | None]):
    """Register one named workspace through Core's revisioned user choices."""

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


class WorkspaceChoicesScreen(Screen[None]):
    """Edit Core's local profile and Java candidates for one named workspace."""

    def __init__(self, record: Mapping[str, Any], *, selected_name: str | None = None) -> None:
        super().__init__()
        self.record = record
        self.entries = {row["name"]: row for row in record["entries"]}
        self.selected_name = (selected_name if selected_name in self.entries else
                              record.get("default") or next(iter(self.entries), ""))
        self.busy = False
        self.detected_java: dict[str, str] = {}

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        options = [(f"{name} · {row['path']}", name) for name, row in self.entries.items()]
        if not options:
            options = [("No named workspace is registered", "")]
        yield Header(icon="W")
        with VerticalScroll():
            yield Static("Workspace choices", classes="screen-heading")
            yield Static(
                "Choose Java for this workspace. Workbench recommends managed Java 25 "
                "for Cleanroom. Installed JDKs appear below when found; you can also "
                "enter a path without a version check.",
                classes="screen-intro",
            )
            yield Static("Named workspace", classes="field-label")
            yield Select(options, value=self.selected_name, allow_blank=False, id="choice-workspace")
            yield Button("Add workspace", id="choice-register")
            yield Static("Workbench configuration · blank clears", classes="field-label")
            yield Input(placeholder="/path/to/workbench.toml", id="choice-profile")
            yield Static("Java selection", classes="field-label")
            yield Select([
                ("Use Java recommended by this configuration", "default"),
                ("Use managed Java 8", "managed-8"),
                ("Enter my own Java path", "path"),
            ], value="default", allow_blank=False, id="choice-java-mode")
            yield Static("Looking for installed JDKs…", id="choice-java-hint")
            yield Static("Java home · entered or detected", classes="field-label")
            yield Input(placeholder="/path/to/jdk", id="choice-java")
            yield Checkbox(
                "Bind the selected pack source lock in the exported share",
                id="choice-bind-source-lock",
            )
            yield Checkbox(
                "Bind Core's exact managed-tool policy and source lock",
                id="choice-bind-managed-tools",
            )
            with Horizontal(classes="button-row"):
                yield Button("Use recommended Java", id="choice-save", variant="primary",
                             disabled=not bool(self.entries))
                yield Button("Export environment", id="choice-export",
                             disabled=not bool(self.entries))
                yield Button("Import environment", id="choice-import")
                yield Button("Back", id="choice-back")
            yield Static("", id="choice-status")
        yield Footer()

    def on_mount(self) -> None:
        self._show_selected()
        self.find_java()

    def _java_mode(self) -> str:
        value = self.query_one("#choice-java-mode", Select).value
        return value if isinstance(value, str) else "default"

    def _refresh_java_controls(self) -> None:
        mode = self._java_mode()
        self.query_one("#choice-workspace", Select).disabled = self.busy
        self.query_one("#choice-java-mode", Select).disabled = self.busy
        self.query_one("#choice-profile", Input).disabled = self.busy
        self.query_one("#choice-java", Input).disabled = self.busy or mode != "path"
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
        self.query_one("#choice-java", Input).value = row.get("java_home") or ""
        mode = "managed-8" if row.get("managed_java_feature") == 8 else "path" if row.get("java_home") else "default"
        self.query_one("#choice-java-mode", Select).value = mode
        self._refresh_java_controls()
        self.query_one("#choice-status", Static).update(
            "Saved Java path is used as supplied." if mode == "path" else
            "Java 8 is selected. Continue to prepare it." if mode == "managed-8" else
            "This configuration's recommended Java is selected. Continue to prepare it."
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "choice-workspace" and isinstance(event.value, str):
            if self.busy:
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
            (f"{row['name']} · {row['path']}", row["name"])
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
                ("Use Java recommended by this configuration", "default"),
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
                "No installed JDKs found. Choose recommended managed Java, optional Java 8, "
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
            self.query_one("#choice-status", Static).update(
                f"Java {receipt['policy']['feature_version']} is ready. "
                f"Workbench {'reused its existing copy' if result['outcome'] == 'reused' else 'acquired a copy'} "
                f"at {receipt['target']['java_home_uri']}."
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


class EnvironmentImportScreen(Screen[None]):
    """Present Core's read-only import plan before binding local choices."""

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


class SetupScreen(Screen[None]):
    """Guided Core check → plan → explicit apply, with one frozen option set."""

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

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        saved = self.view.setup.get("selection", {}) if self.view.setup else {}
        if not isinstance(saved, dict):
            saved = {}
        has_profile = bool(saved.get("profile_config"))
        modes = [("Full developer · workspace + profile", "full")]
        if not has_profile:
            modes.append(("Review-only · source and evidence", "review"))
        if self.view.setup and self.view.setup.get("configured"):
            modes.append(("Repair saved setup", "repair"))
        default_mode = "repair" if has_profile else "full"
        yield Header(icon="W")
        with VerticalScroll(id="setup-scroll"):
            yield Static("Set up Workbench", classes="screen-heading")
            yield Static(
                "Choose a workspace and, for development, an existing Workbench "
                "configuration. Core checks the selection and owns every change. "
                "Repair keeps saved values when a field is blank; review the resulting selection.",
                classes="screen-intro",
            )
            yield Static("Setup journey", classes="field-label")
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
            yield Static("Workbench configuration · required for Full developer", classes="field-label")
            with Horizontal(classes="path-row"):
                yield Input(
                    value=str(saved.get("profile_config") or self.initial_profile_config),
                    placeholder="/path/to/workbench.toml",
                    id="setup-profile",
                )
                if _HAS_PICKER:
                    yield Button("Browse", id="setup-profile-browse")
            yield Static("Runtime state root · optional", classes="field-label")
            yield Input(value=str(saved.get("state_root") or ""), id="setup-state-root")
            yield Static("Installed Java · optional", classes="field-label")
            yield Select(
                [("Looking for installed JDKs…", "none")], value="none",
                allow_blank=False, disabled=True, id="setup-java-candidates",
            )
            yield Static(
                "Detected JDKs can fill the Java home below. Core checks a selected "
                "path against the profile during setup.",
                id="setup-java-hint",
            )
            yield Static("Java home · optional", classes="field-label")
            yield Input(value=str(saved.get("java_home") or ""), id="setup-java")
            yield Static("Git executable · optional", classes="field-label")
            yield Input(value=str(saved.get("git_executable") or ""), id="setup-git")
            with Horizontal(classes="button-row"):
                yield Button("Check selection", id="setup-check")
                yield Button("Review plan", id="setup-plan", variant="primary")
                yield Button("Apply plan", id="setup-apply", variant="warning", disabled=True)
            with Horizontal(classes="button-row"):
                yield Button("Browse workflows", id="setup-workflows")
                yield Button("Back", id="setup-back")
            yield Static("Choose a journey, then review a Core plan.", id="setup-status")
            yield DataTable(id="setup-dependencies", cursor_type="row")
            yield Static("", id="setup-plan-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#setup-dependencies", DataTable).add_columns(
            "Dependency", "State", "Detail / repair"
        )
        self._update_mode()
        if self.view.setup:
            self._show_dependencies(self.view.setup)
        self.find_java()

    def _update_mode(self) -> None:
        mode = self.query_one("#setup-mode", Select).value
        disabled = mode == "review"
        self.query_one("#setup-profile", Input).disabled = disabled
        self.query_one("#setup-java", Input).disabled = disabled
        self.query_one("#setup-java-candidates", Select).disabled = disabled or not self.detected_java
        if _HAS_PICKER:
            self.query_one("#setup-profile-browse", Button).disabled = disabled or self.busy

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
        self.query_one("#setup-plan-detail", Static).update("")

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
        for item in record.get("dependencies", []):
            if not isinstance(item, dict):
                continue
            detail = item.get("detail") or item.get("repair") or ""
            table.add_row(
                str(item.get("label") or item.get("id") or "?"),
                str(item.get("state") or "?"),
                str(detail),
            )

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
        self.query_one("#setup-plan-detail", Static).update(Text("\n".join(lines)))

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


class WorkflowsScreen(Screen[None]):
    """Explore the complete installed action catalog and run a bounded slice."""

    def __init__(self, view: EnvironmentView) -> None:
        super().__init__()
        self.view = view
        self.selected: Mapping[str, Any] | None = None

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("Workbench workflows", classes="screen-heading")
        yield Static(
            "Search the installed catalog. Every action retains its owner, "
            "availability, risk, and declared inputs.",
            classes="screen-intro",
        )
        yield Input(placeholder="Search actions, suites, and descriptions", id="workflow-search")
        with Horizontal(id="workflow-body"):
            yield OptionList(id="workflow-list")
            with Vertical(id="workflow-detail-panel"):
                yield Static("Select a workflow", id="workflow-detail")
                with Horizontal(classes="button-row"):
                    yield Button("Run action", id="workflow-run", disabled=True)
                    yield Button("Back", id="workflow-back")
        yield Footer()

    def on_mount(self) -> None:
        self._filter("")

    def _actions(self) -> Iterable[Mapping[str, Any]]:
        catalog = self.view.catalog
        if not catalog:
            return ()
        return (
            item for item in catalog.get("commands", []) if isinstance(item, dict)
        )

    def _filter(self, query: str) -> None:
        words = query.casefold().split()
        matches = []
        for action in self._actions():
            haystack = " ".join(
                str(action.get(key, "")) for key in ("title", "summary", "command_id", "suite_id")
            ).casefold()
            if all(word in haystack for word in words):
                matches.append(action)
        options = [
            Option(
                Text(
                    f"{item.get('title', '?')}  ·  {item.get('suite_id', '?')}\n"
                    f"{item.get('summary', '')}",
                ),
                id=str(item.get("command_id")),
            )
            for item in matches[:200]
        ]
        self.query_one("#workflow-list", OptionList).set_options(options)
        self.selected = None
        self.query_one("#workflow-run", Button).disabled = True
        if not options:
            message = "No matching installed action. Try another search."
        elif len(matches) > 200:
            message = f"Showing the first 200 of {len(matches)} actions. Refine your search."
        else:
            message = "Select a workflow to inspect its owner, inputs, and limits."
        self.query_one("#workflow-detail", Static).update(Text(message))

    def _select(self, command_id: str | None) -> None:
        self.selected = next(
            (item for item in self._actions() if item.get("command_id") == command_id),
            None,
        )
        action = self.selected
        if not action:
            return
        detail = Text()
        detail.append(str(action.get("title", command_id)), style="bold")
        detail.append("\n\n" + str(action.get("summary") or ""))
        for label, key in (
            ("ID", "command_id"),
            ("Suite", "suite_id"),
            ("Owner", "authority"),
            ("Availability", "availability"),
            ("Risk", "risk"),
        ):
            detail.append("\n")
            detail.append_text(_line(label, action.get(key, "?")))
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
        launchable = self._launchable(action)
        if not launchable:
            detail.append(
                "\nThis workflow needs a dedicated input or owner flow in the TUI.",
                style="dim",
            )
        self.query_one("#workflow-detail", Static).update(detail)
        self.query_one("#workflow-run", Button).disabled = not launchable
        self.query_one("#workflow-run", Button).label = (
            "Open document" if action.get("document") else "Run action"
        )

    def _launchable(self, action: Mapping[str, Any]) -> bool:
        if (
            action.get("command_id") not in _LAUNCHABLE_READ_ONLY
            or action.get("risk") != "read-only"
            or action.get("preview") != "none"
            or action.get("availability") not in {"available", "experimental"}
        ):
            return False
        if action.get("document") is not None:
            return action.get("command_id") == "manuals.overview"
        if action.get("command_id") == "workspace.open":
            return bool(self.view.workspace and Path(self.view.workspace).is_dir())
        return True

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "workflow-search":
            self._filter(event.value)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "workflow-list":
            self._select(event.option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "workflow-list":
            self._select(event.option_id)

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
        values = (
            {"workspace": self.view.workspace}
            if action.get("command_id") == "workspace.open"
            else {}
        )
        self.query_one("#workflow-run", Button).disabled = True
        try:
            if action.get("document"):
                output = await self.core.open_document(catalog, action)
                self.app.push_screen(
                    ResultScreen(str(action.get("title", "Document")), output.stdout)
                )
                return
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
                catalog, action, values, review
            )
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
    def on_resize(self, event: events.Resize) -> None:
        self.app._size_brand()  # type: ignore[attr-defined]

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self.app._present_pending_pack_release()  # type: ignore[attr-defined]


class WorkbenchApp(App[None]):
    CSS_PATH = "workbench.tcss"
    TITLE = "Workbench"
    SUB_TITLE = "Developer environment"
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (64, "-wide")]
    BINDINGS = [
        ("ctrl+t", "toggle_clock", "Clock"),
        ("r", "refresh_environment", "Refresh"),
        ("q", "quit", "Quit"),
    ]

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
        self._block_logo_supported = _supports_block_logo()
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=self.preferences.show_clock, icon="W", id="home-header")
        with VerticalScroll(id="home-scroll"):
            with Horizontal(id="brand-banner"):
                yield Static(Text("\n".join(MARK), no_wrap=True), id="brand-logo")
                with Vertical(id="brand-copy"):
                    yield Static("WORKBENCH", id="hero")
                    yield Static("Local development console", id="tagline")
                    yield Static(
                        "SET UP  ·  EXPLORE MODULES  ·  RUN WORKFLOWS",
                        id="brand-steps",
                    )
            with Horizontal(id="home-panels"):
                with Vertical(id="environment-panel"):
                    yield Static("CURRENT ENVIRONMENT", classes="panel-title")
                    yield Static("Connecting to Core…", id="environment-summary")
                    yield Static("", id="environment-dependencies")
                with Vertical(id="actions-panel"):
                    yield Static("START HERE", classes="panel-title")
                    yield OptionList(
                        Option("Set up or repair environment", id="setup", disabled=True),
                        Option("Choose workspace profile and Java", id="workspace-choices", disabled=True),
                        Option("View or prepare Supersymmetry pack", id="pack-release", disabled=True),
                        Option("Set up Supersymmetry instance", id="pack-instance", disabled=True),
                        Option("Explore installed modules", id="modules", disabled=True),
                        Option("Browse and run workflows", id="workflows", disabled=True),
                        Option("Open Workspace Home", id="home", disabled=True),
                        Option("Import earlier configuration", id="migrate", disabled=True),
                        Option("Refresh environment", id="refresh"),
                        id="home-actions",
                    )
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

    def _size_brand(self) -> None:
        if not self.screen_stack:
            return
        home = self.screen_stack[0]
        logos = home.query("#brand-logo")
        if not logos:
            return
        compact = self.size.height < 30 or self.size.width < 90
        plain = not self._block_logo_supported or self.size.width < 58
        narrow = self.size.width < 64
        home.set_class(compact, "compact-brand")
        home.set_class(plain, "plain-brand")
        home.set_class(narrow, "narrow-brand")
        logos.first().update(Text("\n".join(COMPACT_MARK if compact else MARK), no_wrap=True))
        panels = home.query_one("#home-panels", Horizontal)
        environment = home.query_one("#environment-panel", Vertical)
        actions = home.query_one("#actions-panel", Vertical)
        first = actions if narrow else environment
        second = environment if narrow else actions
        if panels.children[0] is not first:
            panels.move_child(first, before=second)

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
            ("The saved archive belongs to another Workbench state root. "
             "Core will prepare a verified copy in the current state root.\n\n"
             if choice["artifact_state"] == "other_root" else "")
            + f"Core will download and verify the saved published client archive "
            f"({selected['asset_size'] / (1024 * 1024):.1f} MiB), then retain it "
            "in Workbench's stable artifact store. Your workspace files will not change.",
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
        overview = Text()
        overview.append_text(_line("Core", (view.version or {}).get("version", "unavailable")))
        setup = view.setup or {}
        overview.append("\n")
        setup_status = setup.get("state") or (
            "unavailable" if any(row.startswith("setup:") for row in view.problems) else "loading…"
        )
        overview.append_text(_line("Setup", setup_status))
        overview.append("\n")
        journey = (
            "Full developer" if setup.get("selection", {}).get("profile_config")
            else "Review-only / unselected" if view.setup else "unknown"
        )
        overview.append_text(_line("Journey", journey))
        overview.append("\n")
        workspace_source = (
            view.environment.get("workspace", {}).get("source", "")
            if view.environment else ""
        )
        workspace_display = view.workspace or "not selected"
        if workspace_source:
            workspace_display += f" ({workspace_source})"
        overview.append_text(_line("Workspace", workspace_display))
        overview.append("\n")
        modules_status = (
            f"{sum(row.get('state') == 'available' for row in view.modules)} available / {len(view.modules)} installed"
            if view.modules_loaded else "unavailable" if any(row.startswith("modules:") for row in view.problems) else "loading…"
        )
        overview.append_text(_line("Modules", modules_status))
        overview.append("\n")
        profiles_status = (
            f"{sum(row.get('state') == 'available' for row in view.profiles)} available / {len(view.profiles)} installed"
            if view.profiles_loaded else "unavailable" if any(row.startswith("profiles:") for row in view.problems) else "loading…"
        )
        overview.append_text(_line("Profiles", profiles_status))
        overview.append("\n")
        commands = (view.catalog or {}).get("commands", [])
        catalog_status = (
            "loading catalog…" if view.catalog_loading
            else f"{len(commands)} catalog actions" if view.catalog is not None
            else "unavailable"
        )
        overview.append_text(_line("Workflows", catalog_status))
        home.query_one("#environment-summary", Static).update(overview)

        dependencies = Text()
        blockers = setup.get("blockers", [])
        if blockers:
            dependencies.append("\nNEEDS ATTENTION\n", style="bold underline")
            for item in blockers:
                dependencies.append("• " + str(item) + "\n", style="bold")
        elif view.setup:
            dependencies.append("\nNo setup blockers reported.", style="dim")
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
        elif view.workspace:
            note.append("Workspace Home will appear after the selected workspace exists.")
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
