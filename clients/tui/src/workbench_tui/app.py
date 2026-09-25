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
            yield Static("Existing Java home · optional", classes="field-label")
            yield Input(value=str(saved.get("java_home") or ""), id="setup-java")
            yield Static("Git executable · optional", classes="field-label")
            yield Input(value=str(saved.get("git_executable") or ""), id="setup-git")
            with Horizontal(classes="button-row"):
                yield Button("Check selection", id="setup-check")
                yield Button("Review plan", id="setup-plan", variant="primary")
                yield Button("Apply plan", id="setup-apply", variant="warning", disabled=True)
            with Horizontal(classes="button-row"):
                yield Button("Find Java", id="setup-java-list")
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

    def _update_mode(self) -> None:
        mode = self.query_one("#setup-mode", Select).value
        disabled = mode == "review"
        self.query_one("#setup-profile", Input).disabled = disabled
        self.query_one("#setup-java", Input).disabled = disabled
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
        buttons = ["setup-check", "setup-plan", "setup-java-list", "setup-workflows", "setup-back"]
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

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "setup-mode":
            self._update_mode()
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
        elif event.button.id == "setup-java-list":
            self.show_java_inventory()
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

    @work(exclusive=True, group="setup-request")
    async def show_java_inventory(self) -> None:
        profile = self.query_one("#setup-profile", Input).value
        self._set_busy(True)
        try:
            inventory = await self.core.java_inventory(profile)
            self.app.push_screen(
                ResultScreen(
                    "Detected Java installations",
                    json.dumps(inventory, ensure_ascii=False, indent=2),
                )
            )
        except (CoreClientError, TimeoutError) as exc:
            self._set_status(str(exc), error=True)
        finally:
            self._set_busy(False)


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
        yield SystemCommand("Explore modules", "Show installed modules and profiles", self.open_modules)
        yield SystemCommand("Browse workflows", "Search the installed action catalog", self.open_workflows)
        yield SystemCommand(
            "Import earlier configuration",
            "Review and import earlier Workbench user records",
            self.open_config_migration,
        )

    def action_refresh_environment(self) -> None:
        self.refresh_environment()

    def open_setup(self) -> None:
        if self.view.setup is None:
            self.notify("Waiting for Core setup status", severity="warning")
            return
        self.push_screen(
            SetupScreen(self.view, self.initial_workspace, self.initial_profile_config)
        )

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
            self.refresh_environment()
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
