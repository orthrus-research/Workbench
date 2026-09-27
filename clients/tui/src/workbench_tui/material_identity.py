"""Keyboard-first source review of Supersymmetry registry identities."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .core_client import CoreClientError


_MAX_VISIBLE = 200
_IDENTITY_KINDS = {
    "gtceu-material-id": "MATERIAL ID",
    "gtceu-material-registry-name": "MATERIAL NAME",
    "gtceu-metaitem-id": "METAITEM ID",
    "gtceu-metaitem-name": "METAITEM NAME",
    "crafting-recipe-id": "RECIPE ID",
}
_DECLARATION_RULES = {
    "gtceu-material-definition": ("MATERIAL", ("numeric_id", "registry_name")),
    "supersymmetry-metaitem-definition": ("METAITEM", ("numeric_id", "registry_name")),
    "groovyscript-crafting-registration": ("RECIPE", ("recipe_id",)),
}


@dataclass(frozen=True)
class IdentityRow:
    kind: str
    label: str
    search: str
    record: Mapping[str, Any]


def _source_location(source: Mapping[str, Any]) -> str:
    path = source.get("path", "unknown source")
    line = source.get("line")
    column = source.get("column")
    return f"{path}:{line}:{column}" if isinstance(line, int) and isinstance(column, int) else (
        f"{path}:{line}" if isinstance(line, int) else str(path)
    )


def _option_label(label: str, width: int) -> str:
    """Keep one finding per list row; the detail retains the complete value."""
    compact = " ".join(label.split())
    return compact if len(compact) <= width else compact[:width - 1].rstrip() + "…"


def _identity_rows(report: Mapping[str, Any]) -> tuple[list[IdentityRow], dict[str, int]]:
    """Project only facts present in the owner report; do not infer runtime state."""
    report_format = report.get("format")
    if report_format not in {"workbench-groovy-pack-program-report-v1",
                             "workbench-groovy-identity-summary-v1"}:
        raise ValueError("Core returned an unrecognized Groovy program report")
    if report_format == "workbench-groovy-pack-program-report-v1":
        candidate = report.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError("Core returned no source program")
        collisions = candidate.get("collisions")
        effects = candidate.get("effects")
    else:
        collisions = report.get("collisions")
        effects = report.get("identity_declarations", report.get("material_declarations"))
    if not isinstance(collisions, list) or not isinstance(effects, list):
        raise ValueError("Core returned an incomplete registry source inventory")

    rows: list[IdentityRow] = []
    counts = {"duplicate": 0, "unresolved": 0, "declaration": 0}
    for collision in collisions:
        if not isinstance(collision, dict):
            continue
        identity_kind = collision.get("identity_kind")
        if identity_kind not in _IDENTITY_KINDS:
            continue
        value = collision.get("value")
        sites = collision.get("occurrences")
        if not isinstance(sites, list):
            sites = []
        label = f"DUPLICATE {_IDENTITY_KINDS[identity_kind]} {value} · {len(sites)} sites"
        locations = " ".join(
            _source_location(site.get("source", {}))
            for site in sites if isinstance(site, dict) and isinstance(site.get("source"), dict)
        )
        rows.append(IdentityRow("duplicate", label,
                                f"{label} {locations}".casefold(), collision))
        counts["duplicate"] += 1

    for effect in effects:
        if not isinstance(effect, dict) or effect.get("rule_id") not in _DECLARATION_RULES:
            continue
        source = effect.get("source")
        fields = effect.get("fields")
        states = effect.get("field_states")
        if not isinstance(source, dict) or not isinstance(fields, dict) or not isinstance(states, dict):
            continue
        location = _source_location(source)
        rule_label, identity_fields = _DECLARATION_RULES[effect["rule_id"]]
        unresolved = [field for field in identity_fields
                      if states.get(field) == "unresolved"]
        if unresolved:
            label = (f"UNRESOLVED {rule_label} "
                     f"{', '.join(unresolved).replace('_', ' ')} · {location}")
            kind = "unresolved"
        else:
            identity = " · ".join(str(fields.get(field, "?")) for field in identity_fields)
            label = f"SOURCE {rule_label} {identity} · {location}"
            kind = "declaration"
        search = f"{label} {effect.get('expression', '')}".casefold()
        rows.append(IdentityRow(kind, label, search, effect))
        counts[kind] += 1
    return rows, counts


def _row_details(row: IdentityRow, *, preview: bool) -> str:
    record = row.record
    if row.kind == "duplicate":
        sites = record.get("occurrences", [])
        sites = sites if isinstance(sites, list) else []
        identity_kind = _IDENTITY_KINDS.get(record.get("identity_kind"), "identity").lower()
        lines = [f"STATIC duplicate {identity_kind} candidate: {record.get('value')}",
                 f"Source sites: {len(sites)} · execution: {record.get('execution_state', 'unknown')}"]
        for index, site in enumerate(sites[:1] if preview else sites, 1):
            if isinstance(site, dict) and isinstance(site.get("source"), dict):
                identity = site.get("identity")
                identifier = ""
                if isinstance(identity, dict):
                    identifier = " · " + ", ".join(
                        f"{name.replace('_', ' ')} {value}"
                        for name, value in identity.items()
                    )
                lines.append(f"{index}. {_source_location(site['source'])}{identifier}")
        if preview and len(sites) > 1:
            lines.append(f"+{len(sites) - 1} more source sites · Enter to inspect")
    else:
        source = record.get("source", {})
        fields = record.get("fields", {})
        source = source if isinstance(source, dict) else {}
        fields = fields if isinstance(fields, dict) else {}
        if row.kind == "unresolved":
            states = record.get("field_states", {})
            states = states if isinstance(states, dict) else {}
            rule = _DECLARATION_RULES.get(record.get("rule_id"), ("IDENTITY", ()))
            missing = [field.replace("_", " ") for field in rule[1]
                       if states.get(field) == "unresolved"]
            lines = ["STATIC identity is not a bounded literal",
                     f"Unresolved: {', '.join(missing)}",
                     _source_location(source)]
        else:
            rule = _DECLARATION_RULES.get(record.get("rule_id"), ("IDENTITY", ()))
            identity = " · ".join(str(fields.get(field, "?")) for field in rule[1])
            lines = [f"SOURCE {rule[0].lower()} declaration · {identity}",
                     _source_location(source)]
        if not preview:
            lines.append(f"Expression: {record.get('expression', '')}")
            lines.append(f"Load stage: {record.get('lifecycle', {}).get('stage', '?')}")
    if not preview or row.kind != "duplicate":
        lines.append("Runtime registry has not been checked.")
    return "\n".join(lines)


class MaterialIdentityDetailScreen(Screen[None]):
    """Source sites for one selected candidate, with its owner JSON on demand."""

    SUB_TITLE = "Registry source detail"
    BINDINGS = [("escape", "back", "Back"), ("r", "toggle_raw", "Full record")]

    def __init__(self, row: IdentityRow, *, raw: bool = False) -> None:
        super().__init__()
        self.row = row
        self.raw = raw

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("STATIC SOURCE · runtime registry unverified", id="material-detail-scope")
        yield Static("↑/↓ Scroll · R Full record · Esc Back", classes="keyboard-hint")
        yield RichLog(id="material-detail-log", min_width=1, wrap=True,
                      highlight=False, markup=False, auto_scroll=False)
        with Horizontal(id="material-detail-actions", classes="button-row"):
            yield Button("Full record" if not self.raw else "Readable view",
                         id="material-detail-raw")
            yield Button("Back", id="material-detail-back")
        yield Footer()

    def on_mount(self) -> None:
        self._render_view()
        self.query_one("#material-detail-log", RichLog).focus()

    def _render_view(self) -> None:
        log = self.query_one("#material-detail-log", RichLog)
        log.clear()
        log.write(json.dumps(self.row.record, ensure_ascii=False, indent=2, sort_keys=True)
                  if self.raw else _row_details(self.row, preview=False))
        self.query_one("#material-detail-raw", Button).label = (
            "Readable view" if self.raw else "Full record"
        )

    def action_toggle_raw(self) -> None:
        self.raw = not self.raw
        self._render_view()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "material-detail-raw":
            self.action_toggle_raw()
        elif event.button.id == "material-detail-back":
            self.action_back()


class MaterialIdentityScreen(Screen[None]):
    """Static Supersymmetry registry identity inventory from Core."""

    SUB_TITLE = "Registry source review"
    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, workspace: str) -> None:
        super().__init__()
        self.workspace = workspace
        self.report: Mapping[str, Any] | None = None
        self.rows: list[IdentityRow] = []
        self.shown_rows: list[IdentityRow] = []
        self.selected = 0
        self.counts = {"duplicate": 0, "unresolved": 0, "declaration": 0}

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("STATIC SOURCE REVIEW · runtime registry unverified", id="material-scope")
        yield Static("↑/↓ Choose · Enter Inspect · / Find · P Path · S Scan\n"
                     "N Native check · Esc Back",
                     classes="keyboard-hint")
        with Horizontal(id="material-path-row"):
            yield Static("SOURCE>", classes="field-label")
            yield Input(value=self.workspace, placeholder="Path to Supersymmetry source",
                        id="material-source")
            yield Button("Scan source", id="material-scan")
        with Horizontal(id="material-query-row"):
            yield Static("FIND>", classes="field-label")
            yield Input(placeholder="ID, name, or source path", id="material-query")
        yield Static("Ready to inspect source.", id="material-summary")
        yield OptionList(id="material-findings")
        yield RichLog(id="material-detail", min_width=1, wrap=True, highlight=False,
                      markup=False, auto_scroll=False)
        with Horizontal(id="material-actions", classes="button-row"):
            yield Button("Inspect", id="material-inspect", disabled=True)
            yield Button("Full record", id="material-full", disabled=True)
            yield Button("Native check", id="material-native")
            yield Button("Back", id="material-back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#material-findings", OptionList).add_option(
            Option(Text("READY · scan source to index identities"), disabled=True)
        )
        self.query_one("#material-detail", RichLog).write(
            "Core can show duplicate literal candidates and trace parsed IDs to "
            "source. Use Native check separately for runtime evidence."
        )
        if self.workspace:
            self.query_one("#material-scan", Button).focus()
            self._status("Press Enter to scan source. Large packs can take several minutes.")
        else:
            self.query_one("#material-source", Input).focus()
            self._status("Choose a Supersymmetry source directory, then scan.")

    def action_back(self) -> None:
        if isinstance(self.app.focused, Input):
            self.query_one("#material-findings", OptionList).focus()
        else:
            self.app.pop_screen()

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.app.focused, Input):
            return
        if event.key == "p":
            self.query_one("#material-source", Input).focus()
            event.stop()
        elif event.key == "slash":
            self.query_one("#material-query", Input).focus()
            event.stop()
        elif event.key == "s":
            self.scan()
            event.stop()
        elif event.key == "n":
            self._native_check()
            event.stop()
        elif event.key == "r":
            self._inspect(raw=True)
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "material-query":
            self._show_rows()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "material-source":
            self.scan()
            event.stop()
        elif event.input.id == "material-query":
            self.query_one("#material-findings", OptionList).focus()
            event.stop()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "material-findings" and event.option_id:
            self._select(event.option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "material-findings" and event.option_id:
            self._select(event.option_id)
            self._inspect()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "material-scan":
            self.scan()
        elif event.button.id == "material-inspect":
            self._inspect()
        elif event.button.id == "material-full":
            self._inspect(raw=True)
        elif event.button.id == "material-native":
            self._native_check()
        elif event.button.id == "material-back":
            self.app.pop_screen()

    @work(exclusive=True, group="material-identity-scan")
    async def scan(self) -> None:
        source = self.query_one("#material-source", Input).value.strip()
        if not source:
            self._status("Choose a Supersymmetry source directory, then scan.")
            return
        self._status("Scanning source through Core… Large packs can take several minutes.")
        self.query_one("#material-scan", Button).disabled = True
        self.query_one("#material-findings", OptionList).clear_options()
        self.query_one("#material-findings", OptionList).add_option(
            Option(Text("SCANNING · source inventory in progress"), disabled=True)
        )
        try:
            report = await self.app.core.json_record(  # type: ignore[attr-defined]
                "groovy", "dev", "--profile", "supersymmetry", "--source", source,
                "--identity-summary-json", timeout=300,
            )
            if not isinstance(report, dict):
                raise ValueError("Core returned no Groovy program report")
            rows, counts = _identity_rows(report)
        except (CoreClientError, TimeoutError, ValueError) as exc:
            self.report = None
            self.rows = []
            self.counts = {"duplicate": 0, "unresolved": 0, "declaration": 0}
            self._show_rows()
            self.query_one("#material-findings", OptionList).add_option(
                Option(Text("SCAN FAILED · correct source and retry"), disabled=True)
            )
            self.query_one("#material-detail", RichLog).write(str(exc))
            self._status("Source scan could not finish. Read the detail below.")
        else:
            self.report = report
            self.rows = rows
            self.counts = counts
            self.workspace = source
            self._show_rows()
            self.query_one("#material-findings", OptionList).focus()
        finally:
            self.query_one("#material-scan", Button).disabled = False

    def _status(self, value: str) -> None:
        self.query_one("#material-summary", Static).update(Text(value))

    def _show_rows(self) -> None:
        query = self.query_one("#material-query", Input).value.strip().casefold()
        matches = [row for row in self.rows if query in row.search]
        self.shown_rows = matches[:_MAX_VISIBLE]
        listing = self.query_one("#material-findings", OptionList)
        label_width = max(12, listing.size.width - 4)
        listing.clear_options()
        listing.add_options(Option(Text(_option_label(row.label, label_width)),
                                   id=f"material-row-{index}")
                            for index, row in enumerate(self.shown_rows))
        if self.shown_rows:
            listing.highlighted = 0
            self._select("material-row-0")
        else:
            self.selected = 0
            self.query_one("#material-detail", RichLog).clear()
            self.query_one("#material-inspect", Button).disabled = True
            self.query_one("#material-full", Button).disabled = True
            if query and self.report is not None:
                self.query_one("#material-detail", RichLog).write(
                    "No source declaration matches this search. This does not prove "
                    "that the ID or name is absent from the runtime registry."
                )
        if self.report is not None:
            owner_summary = self.report.get("summary")
            files = owner_summary.get("files") if isinstance(owner_summary, dict) else None
            file_prefix = f"{files} files · " if isinstance(files, int) else ""
            prefix = (file_prefix + f"{self.counts['duplicate']} duplicate candidates · "
                      f"{self.counts['unresolved']} unresolved identities · "
                      f"{self.counts['declaration']} declarations")
            suffix = f" · showing {len(self.shown_rows)}/{len(matches)}" if query or len(matches) > _MAX_VISIBLE else ""
            self._status(prefix + suffix)

    def _select(self, option_id: str) -> None:
        try:
            index = int(option_id.removeprefix("material-row-"))
        except ValueError:
            return
        if not 0 <= index < len(self.shown_rows):
            return
        self.selected = index
        log = self.query_one("#material-detail", RichLog)
        log.clear()
        log.write(_row_details(self.shown_rows[index], preview=True))
        self.query_one("#material-inspect", Button).disabled = False
        self.query_one("#material-full", Button).disabled = False

    def _inspect(self, *, raw: bool = False) -> None:
        if self.shown_rows and 0 <= self.selected < len(self.shown_rows):
            self.app.push_screen(MaterialIdentityDetailScreen(self.shown_rows[self.selected], raw=raw))

    def _native_check(self) -> None:
        source = self.query_one("#material-source", Input).value.strip()
        self.app.open_axiom_check(source or None)  # type: ignore[attr-defined]


__all__ = ["MaterialIdentityScreen"]
