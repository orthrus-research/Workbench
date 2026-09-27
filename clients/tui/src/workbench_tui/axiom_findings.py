"""Keyboard-first presentation of Core's retained Axiom diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from textual import work
from textual.app import ComposeResult
from textual.content import Content
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .core_client import CoreClient, CoreClientError


_GROUP_NAMES = {
    None: "All findings",
    "error-located": "Errors with source",
    "error-unlocated": "Other errors",
    "warning-located": "Warnings with source",
    "warning-unlocated": "Other warnings",
    "information-located": "Notes with source",
    "information-unlocated": "Other notes",
}


def _location(finding: Mapping[str, Any]) -> str:
    location = finding.get("location")
    if not isinstance(location, Mapping) or not location.get("path"):
        return "No retained source location"
    parts = [str(location["path"])]
    for key in ("line", "column"):
        if location.get(key) is not None:
            parts.append(str(location[key]))
    return ":".join(parts)


def _message(finding: Mapping[str, Any], labels: Mapping[str, Any]) -> str:
    identity = finding.get("id")
    label = labels.get(identity) if isinstance(identity, str) else None
    if isinstance(label, str) and label:
        return label
    message = finding.get("message")
    return str(message) if message is not None else "Retained finding without a message"


def _one_line(value: str, *, limit: int = 150) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit - 1] + "…"


def _list_location(finding: Mapping[str, Any]) -> str:
    location = finding.get("location")
    if not isinstance(location, Mapping) or not location.get("path"):
        return "no source"
    name = Path(str(location["path"])).name
    line = location.get("line")
    return f"{name}:{line}" if line is not None else name


class AxiomFindingsScreen(Screen[None]):
    """Browse every retained diagnostic page and open exact native evidence."""

    SUB_TITLE = "Axiom findings"
    BINDINGS = [
        ("escape", "back", "Back"),
        ("g", "next_group", "Group"),
        ("n", "next_page", "Next page"),
        ("p", "previous_page", "Previous page"),
        ("s", "source", "Source"),
    ]

    def __init__(self, session_id: str, attempt_id: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.attempt_id = attempt_id
        self.revision: str | None = None
        self.group: str | None = None
        self.page_offset = 0
        self.previous_offsets: list[int] = []
        self.next_offset: int | None = None
        self.findings: list[Mapping[str, Any]] = []
        self.labels: Mapping[str, Any] = {}
        self.source_current: bool | None = None
        self.selected_index: int | None = None
        self.available_groups: list[str | None] = [None]
        self.page_loading = False

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("↑/↓ Choose · Enter Evidence · S Source · G Group · N/P Pages · Esc Back",
                     id="axiom-findings-hint")
        yield Static("Opening retained findings…", id="axiom-findings-summary", markup=False)
        yield OptionList(id="axiom-findings-list")
        yield Static("Select a finding to inspect its exact retained evidence.",
                     id="axiom-findings-selected", markup=False)
        yield Static("", id="axiom-findings-status", markup=False)
        with Horizontal(id="axiom-findings-actions"):
            yield Button("Evidence", id="axiom-findings-open", disabled=True)
            yield Button("Source", id="axiom-findings-source", disabled=True)
            yield Button("Previous", id="axiom-findings-previous", disabled=True)
            yield Button("Next", id="axiom-findings-next", disabled=True)
            yield Button("Back", id="axiom-findings-back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#axiom-findings-list", OptionList).focus()
        self.load_page(0, None, [])

    @work(exclusive=True, group="axiom-findings-page")
    async def load_page(
        self, offset: int, group: str | None, previous_offsets: list[int],
    ) -> None:
        self.page_loading = True
        status = self.query_one("#axiom-findings-status", Static)
        status.update("Reading saved findings…")
        try:
            options = [self.attempt_id, "--offset", str(offset)]
            if self.revision is not None:
                options.extend(("--revision", self.revision))
            if group is not None:
                options.extend(("--group", group))
            record = await self.core.developer_materials_action(
                self.session_id, "diagnostics", *options,
            )
            result = record["result"]
            if (result.get("format") != "workbench-material-diagnostic-view-v1"
                    or not isinstance(result.get("id"), str)
                    or not isinstance(result.get("findings"), list)
                    or type(result.get("offset")) is not int
                    or result["offset"] != offset
                    or result.get("group") != group):
                raise CoreClientError("Core returned an unsupported findings page")
            if self.revision is not None and result["id"] != self.revision:
                raise CoreClientError("The retained findings changed revision while browsing")
            next_offset = result.get("next_offset")
            if next_offset is not None and (type(next_offset) is not int or next_offset <= offset):
                raise CoreClientError("Core returned an invalid next findings page")
            if any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                   for row in result["findings"]):
                raise CoreClientError("Core returned an incomplete finding")
            presentation = record.get("presentation")
            labels = presentation.get("finding_labels") if isinstance(presentation, dict) else None
            counts = result.get("finding_counts")
            if isinstance(counts, dict):
                self.available_groups = [None] + [
                    name for name in _GROUP_NAMES if name is not None
                    and type(counts.get(name)) is int and counts[name] > 0
                ]
            else:
                # Older sealed revisions have no group index; all-page access works.
                self.available_groups = [None]
            self.revision = result["id"]
            self.group = group
            self.page_offset = offset
            self.previous_offsets = previous_offsets
            self.next_offset = next_offset
            self.findings = result["findings"]
            self.labels = labels if isinstance(labels, dict) else {}
            self.source_current = (presentation.get("source_current")
                                   if isinstance(presentation, dict) else None)
            self._present_page(result)
            status.update(
                "Saved source changed since this check; locations refer to retained text."
                if self.source_current is False else
                "Enter opens original native evidence. S shows retained source when located."
            )
        except (CoreClientError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            status.update(f"Could not open findings: {exc}")
        finally:
            self.page_loading = False

    def _present_page(self, result: Mapping[str, Any]) -> None:
        count = result.get("group_count")
        if type(count) is not int:
            count = result.get("findings_count", "?")
        first = self.page_offset + 1 if self.findings else 0
        last = self.page_offset + len(self.findings)
        outcome = result.get("native_outcome") or result.get("native_status") or "Saved check"
        counts = result.get("finding_counts")
        tally = ""
        if isinstance(counts, dict):
            errors = sum(counts.get(name, 0) for name in ("error-located", "error-unlocated"))
            warnings = sum(counts.get(name, 0) for name in ("warning-located", "warning-unlocated"))
            tally = f" · {errors} errors, {warnings} warnings"
        self.query_one("#axiom-findings-summary", Static).update(
            f"{outcome} · {_GROUP_NAMES[self.group]} {first}–{last} / {count}{tally}"
        )
        listing = self.query_one("#axiom-findings-list", OptionList)
        listing.set_options([
            Option(Content.from_text(
                f"{str(row.get('severity', 'finding')).upper():<7} "
                f"{_one_line(_message(row, self.labels), limit=65)} · "
                f"{_list_location(row)}"
            ), id=f"finding-{index}")
            for index, row in enumerate(self.findings)
        ])
        self.selected_index = None
        if self.findings:
            listing.highlighted = 0
            self._show_selection(0)
        else:
            self.query_one("#axiom-findings-selected", Static).update(
                "No findings in this group." if self.group else "No findings were retained for this check."
            )
            self._update_buttons()
        listing.focus()

    def _show_selection(self, index: int) -> None:
        if not 0 <= index < len(self.findings):
            return
        self.selected_index = index
        finding = self.findings[index]
        self.query_one("#axiom-findings-selected", Static).update(
            f"{str(finding.get('severity', 'finding')).upper()} · {_location(finding)}\n"
            + _message(finding, self.labels)
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        selected = self._selected()
        self.query_one("#axiom-findings-open", Button).disabled = selected is None
        self.query_one("#axiom-findings-source", Button).disabled = (
            selected is None or not isinstance(selected.get("location"), dict)
            or not selected["location"].get("path")
        )
        self.query_one("#axiom-findings-previous", Button).disabled = not self.previous_offsets
        self.query_one("#axiom-findings-next", Button).disabled = self.next_offset is None

    def _selected(self) -> Mapping[str, Any] | None:
        return (self.findings[self.selected_index]
                if self.selected_index is not None and 0 <= self.selected_index < len(self.findings)
                else None)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "axiom-findings-list" and event.option_id:
            self._show_selection(int(event.option_id.removeprefix("finding-")))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "axiom-findings-list":
            self.action_open()

    def action_open(self) -> None:
        finding = self._selected()
        if finding is None or self.revision is None:
            return
        self.app.push_screen(AxiomFindingDetailScreen(
            self.session_id, self.attempt_id, self.revision, finding,
            _message(finding, self.labels), source_current=self.source_current,
        ))

    def action_source(self) -> None:
        finding = self._selected()
        if (finding is None or not isinstance(finding.get("location"), dict)
                or not finding["location"].get("path")
                or self.revision is None):
            return
        detail = AxiomFindingDetailScreen(
            self.session_id, self.attempt_id, self.revision, finding,
            _message(finding, self.labels), source_current=self.source_current,
            show_source=True,
        )
        self.app.push_screen(detail)

    def action_next_group(self) -> None:
        if self.page_loading or len(self.available_groups) == 1:
            return
        index = self.available_groups.index(self.group)
        self.load_page(0, self.available_groups[(index + 1) % len(self.available_groups)], [])

    def action_next_page(self) -> None:
        if not self.page_loading and self.next_offset is not None:
            self.load_page(self.next_offset, self.group,
                           [*self.previous_offsets, self.page_offset])

    def action_previous_page(self) -> None:
        if not self.page_loading and self.previous_offsets:
            self.load_page(self.previous_offsets[-1], self.group, self.previous_offsets[:-1])

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "axiom-findings-open":
                self.action_open()
            case "axiom-findings-source":
                self.action_source()
            case "axiom-findings-previous":
                self.action_previous_page()
            case "axiom-findings-next":
                self.action_next_page()
            case "axiom-findings-back":
                self.action_back()


class AxiomFindingDetailScreen(Screen[None]):
    """Show original native diagnostic and its retained source location."""

    SUB_TITLE = "Axiom finding evidence"
    BINDINGS = [
        ("escape", "back", "Back"),
        ("s", "source", "Source"),
        ("r", "raw", "Full record"),
    ]

    def __init__(
        self, session_id: str, attempt_id: str, revision: str,
        finding: Mapping[str, Any], label: str, *,
        source_current: bool | None, show_source: bool = False,
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.attempt_id = attempt_id
        self.revision = revision
        self.finding = finding
        self.label = label
        self.source_current = source_current
        self.show_source = show_source
        self.raw = False
        self.evidence: Mapping[str, Any] | None = None
        self.source_record: Mapping[str, Any] | None = None

    @property
    def core(self) -> CoreClient:
        return self.app.core  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static("↑/↓ Scroll · S Retained source · R Full record · Esc Back",
                     id="axiom-finding-hint")
        yield RichLog(id="axiom-finding-detail", min_width=1, wrap=True,
                      highlight=False, markup=False, auto_scroll=False)
        yield Static("Opening original native evidence…", id="axiom-finding-status", markup=False)
        with Horizontal(id="axiom-finding-actions"):
            yield Button("Retained source", id="axiom-finding-source",
                         disabled=not isinstance(self.finding.get("location"), dict)
                         or not self.finding["location"].get("path"))
            yield Button("Full record", id="axiom-finding-raw", disabled=True)
            yield Button("Back", id="axiom-finding-back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#axiom-finding-detail", RichLog).focus()
        self._render_detail()
        self.load_evidence()
        if self.show_source:
            self.load_source()

    @work(exclusive=True, group="axiom-finding-evidence")
    async def load_evidence(self) -> None:
        try:
            record = await self.core.developer_materials_action(
                self.session_id, "diagnostic", self.attempt_id,
                "--revision", self.revision,
                "--diagnostic", str(self.finding["id"]),
            )
            result = record["result"]
            if (result.get("format") != "workbench-material-diagnostic-evidence-v1"
                    or result.get("diagnostic_id") != self.revision
                    or not isinstance(result.get("finding"), dict)
                    or result["finding"].get("id") != self.finding["id"]):
                raise CoreClientError("Core returned evidence for a different finding")
            self.evidence = result
            self.query_one("#axiom-finding-status", Static).update(
                "Original native evidence loaded."
            )
            if not self.show_source:
                self._render_detail()
        except (CoreClientError, TimeoutError, KeyError, TypeError) as exc:
            self.query_one("#axiom-finding-status", Static).update(
                f"Could not open original evidence: {exc}"
            )

    @work(exclusive=True, group="axiom-finding-source")
    async def load_source(self) -> None:
        if (not isinstance(self.finding.get("location"), dict)
                or not self.finding["location"].get("path")):
            return
        if self.source_record is not None:
            self.show_source = True
            self._render_detail()
            return
        self.query_one("#axiom-finding-status", Static).update("Reading retained source…")
        try:
            record = await self.core.developer_materials_action(
                self.session_id, "source", self.attempt_id,
                "--revision", self.revision,
                "--diagnostic", str(self.finding["id"]),
            )
            result = record["result"]
            if (result.get("format") != "workbench-material-source-view-v1"
                    or result.get("result_id") != self.revision
                    or not isinstance(result.get("text"), str)
                    or not isinstance(result.get("source"), dict)
                    or result["source"].get("id") != self.finding["id"]):
                raise CoreClientError("Core returned source for a different finding")
            self.source_record = result
            self.show_source = True
            self._render_detail()
            self.query_one("#axiom-finding-status", Static).update(
                "Retained source from this check; current files may differ."
            )
        except (CoreClientError, TimeoutError, KeyError, TypeError) as exc:
            self.query_one("#axiom-finding-status", Static).update(
                f"Could not open retained source: {exc}"
            )

    def _render_detail(self) -> None:
        log = self.query_one("#axiom-finding-detail", RichLog)
        log.clear()
        if self.show_source and self.source_record is not None:
            text = self.source_record["text"]
            lines = text.splitlines()
            location = self.finding.get("location") or {}
            line = location.get("line") if isinstance(location, dict) else None
            if self.raw or type(line) is not int or line < 1:
                numbered = enumerate(lines, start=1)
            else:
                start, stop = max(0, line - 6), min(len(lines), line + 5)
                numbered = ((index + 1, lines[index]) for index in range(start, stop))
            log.write(f"Retained source · {_location(self.finding)}\n"
                      + ("Complete file\n" if self.raw else "Lines around the finding · R shows full file\n")
                      + "\n".join(f"{'>' if number == line else ' '} {number:>5}  {value}"
                                  for number, value in numbered))
            self.query_one("#axiom-finding-source", Button).label = "Finding"
        elif self.raw and self.evidence is not None:
            log.write(json.dumps(self.evidence, ensure_ascii=False, indent=2, sort_keys=True))
            self.query_one("#axiom-finding-source", Button).label = "Retained source"
        else:
            finding = self.evidence.get("finding", self.finding) if self.evidence else self.finding
            original = finding.get("message")
            lines = [
                f"{str(finding.get('severity', 'finding')).upper()} · {_location(finding)}",
                f"Side: {finding.get('side', 'checked source')}",
                f"Channel: {finding.get('channel', 'native check')}",
                "",
                self.label,
            ]
            if isinstance(original, str) and original and original != self.label:
                lines.extend(("", "Original message:", original))
            if self.source_current is False:
                lines.extend(("", "Saved source has changed. This location belongs to the retained check."))
            native = self.evidence.get("native") if self.evidence else None
            if isinstance(native, dict):
                lines.extend(("", "Original native diagnostic:"))
                shown = 0
                for key, title in (("message", "Message"), ("severity", "Severity"),
                                   ("channel", "Channel"), ("locations", "Locations"),
                                   ("compilerFindings", "Compiler findings")):
                    if native.get(key) is not None:
                        shown += 1
                        value = native[key]
                        lines.append(f"{title}: " + (
                            value if isinstance(value, str) else
                            json.dumps(value, ensure_ascii=False)
                        ))
                if not shown:
                    preview = json.dumps(native, ensure_ascii=False, indent=2)
                    lines.append(preview[:1200] + ("…" if len(preview) > 1200 else ""))
                lines.append("R opens every original native field.")
            else:
                lines.extend(("", "Loading exact native diagnostic…"))
            log.write("\n".join(lines))
            self.query_one("#axiom-finding-source", Button).label = "Retained source"
        self.query_one("#axiom-finding-raw", Button).label = (
            "Excerpt" if self.show_source and self.raw else
            "Readable view" if self.raw else "Full record"
        )
        self.query_one("#axiom-finding-raw", Button).disabled = (
            self.evidence is None and self.source_record is None
        )

    def action_source(self) -> None:
        if (not isinstance(self.finding.get("location"), dict)
                or not self.finding["location"].get("path")):
            return
        if self.show_source and self.source_record is not None:
            self.show_source = False
            self.raw = False
            self._render_detail()
        else:
            self.load_source()

    def action_raw(self) -> None:
        if self.evidence is None and self.source_record is None:
            return
        self.raw = not self.raw
        self._render_detail()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "axiom-finding-source":
                self.action_source()
            case "axiom-finding-raw":
                self.action_raw()
            case "axiom-finding-back":
                self.action_back()
