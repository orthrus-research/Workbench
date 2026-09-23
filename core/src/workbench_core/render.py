"""Adaptive, injection-safe terminal renderers for console events."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from itertools import islice
import json
import os
import shutil
import sys
import textwrap
import time
from typing import Any, Iterable, Mapping, TextIO

try:  # Curses is optional on some Python platforms.
    import curses
except ImportError:  # pragma: no cover - platform dependent
    curses = None  # type: ignore[assignment]

try:
    from workbench_api.events import sanitize_terminal
except ImportError:  # The events module is installed as part of the same package.
    def sanitize_terminal(value: str) -> str:
        return "".join(character for character in value if character in "\t" or ord(character) >= 32)


SEVERITY_ORDER = {
    "trace": 0,
    "debug": 1,
    "info": 2,
    "notice": 3,
    "warning": 4,
    "error": 5,
    "fatal": 6,
}


class RenderError(RuntimeError):
    """The requested terminal renderer cannot be used."""


@dataclass(frozen=True)
class EventFilter:
    search: str | None = None
    minimum_severity: str | None = None
    subsystems: frozenset[str] = frozenset()
    signal_only: bool = False

    def matches(self, event: Mapping[str, Any]) -> bool:
        if self.search:
            needle = self.search.casefold()
            haystack = " ".join(
                str(event.get(key, ""))
                for key in ("message", "subsystem", "kind", "logger", "thread")
            ).casefold()
            if needle not in haystack:
                return False
        if self.minimum_severity:
            actual = SEVERITY_ORDER.get(str(event.get("severity", "info")), 2)
            required = SEVERITY_ORDER.get(self.minimum_severity, 0)
            if actual < required:
                return False
        if self.subsystems and str(event.get("subsystem")) not in self.subsystems:
            return False
        if self.signal_only and not bool(event.get("signal")):
            return False
        return True


@dataclass
class RenderStats:
    events: int = 0
    shown: int = 0
    hidden: int = 0
    repeated: int = 0
    outcome_failures: int = 0


class Renderer:
    """Common live-renderer interface used by the process supervisor."""

    cancellation_requested = False
    force_requested = False

    def start(self, context: Mapping[str, Any]) -> None:
        del context

    def consume(self, event: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def pulse(self) -> None:
        pass

    def finish(self, summary: Mapping[str, Any]) -> None:
        del summary

    def close(self) -> None:
        pass


class PlainRenderer(Renderer):
    """Append-only renderer suitable for IDE terminals and redirected output."""

    def __init__(
        self,
        output: TextIO,
        *,
        event_filter: EventFilter | None = None,
        color: bool = False,
        cluster: bool = True,
        raw: bool = False,
    ) -> None:
        self.output = output
        self.filter = event_filter or EventFilter(signal_only=True)
        self.color = color
        self.cluster = cluster
        self.raw = raw
        self.stats = RenderStats()
        self._last: Mapping[str, Any] | None = None
        self._last_count = 0

    def start(self, context: Mapping[str, Any]) -> None:
        if self.raw:
            return
        command = context.get("command", "console session")
        retained = context.get("retained")
        suffix = f" · retained {retained}" if retained else " · not retained"
        self.output.write(f"Workbench live console · {sanitize_terminal(str(command))}{suffix}\n")
        self.output.flush()

    def consume(self, event: Mapping[str, Any]) -> None:
        self.stats.events += 1
        if event.get("outcome_failure"):
            self.stats.outcome_failures += 1
        if not self.filter.matches(event):
            self.stats.hidden += 1
            return
        fingerprint = str(event.get("cluster_key", ""))
        if (
            self.cluster
            and self._last is not None
            and fingerprint
            and fingerprint == str(self._last.get("cluster_key", ""))
        ):
            # Keep the last occurrence so the folded recap reports the actual
            # final sequence and any presentation-only locator resolution from
            # that occurrence. Every member remains retained independently.
            self._last = event
            self._last_count += 1
            self.stats.repeated += 1
            return
        self._flush_cluster()
        self._last = event
        self._last_count = 1
        self.stats.shown += 1
        self.output.write(self._line(event) + "\n")
        for locator in _resolved_locators(event):
            self.output.write(f"            ↳ {locator}\n")
        self.output.flush()

    def finish(self, summary: Mapping[str, Any]) -> None:
        self._flush_cluster()
        if self.raw:
            return
        outcome = sanitize_terminal(str(summary.get("outcome", "complete")))
        exit_code = summary.get("effective_exit_code")
        self.output.write(
            f"Console {outcome} · exit {exit_code} · {self.stats.events} events"
            f" · {self.stats.hidden} filtered · {self.stats.repeated} repeated\n"
        )
        retained = summary.get("retained")
        if retained:
            self.output.write(f"Retained: {sanitize_terminal(str(retained))}\n")
        self.output.flush()

    def _line(self, event: Mapping[str, Any]) -> str:
        message = sanitize_terminal(str(event.get("message", "")))
        if self.raw:
            return message
        elapsed = event.get("elapsed_ms")
        if isinstance(elapsed, (int, float)):
            timestamp = _duration(float(elapsed) / 1000)
        else:
            timestamp = str(event.get("sequence", "?")).rjust(7)
        severity = str(event.get("severity", "info")).upper()[:5].ljust(5)
        subsystem = str(event.get("subsystem", "runtime"))[:12].ljust(12)
        prefix = f"{timestamp:>8} {severity} {subsystem}"
        if self.color:
            prefix = _color_prefix(prefix, str(event.get("severity", "info")))
        return f"{prefix}  {message}"

    def _flush_cluster(self) -> None:
        if self._last_count > 1:
            last = self._last or {}
            self.output.write(
                f"                     ↳ exact repeat ×{self._last_count}"
                f" (last sequence {last.get('sequence', '?')})\n"
            )
        self._last_count = 0


class JsonlRenderer(Renderer):
    """Automation renderer: stdout contains event objects and nothing else."""

    def __init__(self, output: TextIO, *, event_filter: EventFilter | None = None) -> None:
        self.output = output
        self.filter = event_filter or EventFilter()
        self.stats = RenderStats()

    def consume(self, event: Mapping[str, Any]) -> None:
        self.stats.events += 1
        if not self.filter.matches(event):
            self.stats.hidden += 1
            return
        self.stats.shown += 1
        contract_event = {
            key: value for key, value in event.items() if not str(key).startswith("_")
        }
        self.output.write(json.dumps(contract_event, separators=(",", ":"), sort_keys=True) + "\n")
        self.output.flush()


class CursesRenderer(Renderer):
    """A compact full-screen timeline with non-blocking keyboard interaction."""

    MAX_MEMORY_EVENTS = 20_000
    MAX_MEMORY_BYTES = 16 * 1024 * 1024
    MIN_DRAW_INTERVAL_SECONDS = 1 / 30

    def __init__(
        self,
        *,
        event_filter: EventFilter | None = None,
        output: TextIO = sys.stdout,
    ) -> None:
        if curses is None:
            raise RenderError("this Python build has no curses support")
        self.filter = event_filter or EventFilter(signal_only=True)
        self.output = output
        self.stats = RenderStats()
        self.all_events: deque[Mapping[str, Any]] = deque()
        self.events: deque[Mapping[str, Any]] = deque()
        self._all_event_sizes: deque[int] = deque()
        self._memory_bytes = 0
        self._visible_severity_counts: Counter[str] = Counter()
        self.follow = True
        self.selected = 0
        self.overlay: str | None = None
        self.search_mode = False
        self.search_buffer = self.filter.search or ""
        self._dirty = True
        self._last_draw = 0.0
        self._context: Mapping[str, Any] = {}
        self._summary: Mapping[str, Any] | None = None
        self._screen = None
        self._started = False
        self._active = True
        self._exit_requested = False
        self._recap_written = False

    def start(self, context: Mapping[str, Any]) -> None:
        assert curses is not None
        try:
            screen = curses.initscr()
            # Mark ownership immediately: any later setup failure must still
            # unwind the terminal modes established by initscr.
            self._screen = screen
            self._context = dict(context)
            self._started = True
            curses.noecho()
            curses.cbreak()
            with _ignore_curses_error():
                curses.curs_set(0)
            screen.keypad(True)
            screen.nodelay(True)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                for pair, foreground in enumerate(
                    (curses.COLOR_WHITE, curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_RED),
                    1,
                ):
                    curses.init_pair(pair, foreground, -1)
            self._draw(force=True)
        except Exception as exc:
            self.close()
            raise RenderError(f"cannot initialize full-screen console: {exc}") from exc

    def consume(self, event: Mapping[str, Any]) -> None:
        self.stats.events += 1
        if event.get("outcome_failure"):
            self.stats.outcome_failures += 1
        projected = dict(event)
        size = _event_memory_size(projected)
        self.all_events.append(projected)
        self._all_event_sizes.append(size)
        self._memory_bytes += size
        while (
            len(self.all_events) > self.MAX_MEMORY_EVENTS
            or self._memory_bytes > self.MAX_MEMORY_BYTES
        ):
            dropped = self.all_events.popleft()
            self._memory_bytes -= self._all_event_sizes.popleft()
            if self.events and self.events[0] is dropped:
                removed = self.events.popleft()
                severity = str(removed.get("severity", "info"))
                self._visible_severity_counts[severity] -= 1
                if self._visible_severity_counts[severity] <= 0:
                    del self._visible_severity_counts[severity]
                self.selected = max(0, self.selected - 1)
        if not self.filter.matches(event):
            self.stats.hidden += 1
            return
        self.stats.shown += 1
        self.events.append(projected)
        self._visible_severity_counts[str(projected.get("severity", "info"))] += 1
        if self.follow:
            self.selected = max(0, len(self.events) - 1)
        self._dirty = True
        self.pulse()

    def pulse(self) -> None:
        if not self._started or self._screen is None:
            return
        self._read_keys()
        self._draw()

    def finish(self, summary: Mapping[str, Any]) -> None:
        self._summary = dict(summary)
        self._context = {**self._context, **summary}
        self._active = False
        self.overlay = "complete"
        self._dirty = True
        self._draw(force=True)

    def browse(self) -> None:
        """Keep a completed retained replay open until the user presses q."""

        while self._started and not self._exit_requested:
            self.pulse()
            time.sleep(0.03)

    def close(self) -> None:
        if not self._started:
            return
        assert curses is not None
        try:
            if self._screen is not None:
                try:
                    self._screen.keypad(False)
                except Exception:
                    pass
            for operation in (curses.nocbreak, curses.echo, curses.endwin):
                try:
                    operation()
                except Exception:
                    # Continue attempting every restoration operation; one
                    # curses failure must not strand the remaining modes.
                    pass
        finally:
            self._started = False
            self._screen = None
        if self._summary is not None and not self._recap_written:
            outcome = sanitize_terminal(str(self._summary.get("outcome", "complete")))
            exit_code = self._summary.get("effective_exit_code")
            command = sanitize_terminal(str(self._context.get("command", "session")))
            self.output.write(f"Workbench live console · {command}\n")
            self.output.write(
                f"Console {outcome} · exit {exit_code} · "
                f"{self.stats.events} events\n"
            )
            retained = self._summary.get("retained")
            if retained:
                self.output.write(f"Retained: {sanitize_terminal(str(retained))}\n")
            self.output.flush()
            self._recap_written = True

    def _read_keys(self) -> None:
        assert curses is not None and self._screen is not None
        while True:
            try:
                key = self._screen.getch()
            except curses.error:
                return
            if key == -1:
                return
            if self.search_mode:
                if key in (10, 13, curses.KEY_ENTER):
                    self.search_mode = False
                    self.filter = EventFilter(
                        search=self.search_buffer or None,
                        minimum_severity=self.filter.minimum_severity,
                        subsystems=self.filter.subsystems,
                        signal_only=self.filter.signal_only,
                    )
                    self._refilter()
                elif key in (27,):
                    self.search_mode = False
                elif key in (curses.KEY_BACKSPACE, 8, 127):
                    self.search_buffer = self.search_buffer[:-1]
                elif 32 <= key <= 126:
                    self.search_buffer += chr(key)
                self._dirty = True
                continue
            if key in (ord("j"), curses.KEY_DOWN):
                self.follow = False
                self.selected = min(max(0, len(self.events) - 1), self.selected + 1)
            elif key in (ord("k"), curses.KEY_UP):
                self.follow = False
                self.selected = max(0, self.selected - 1)
            elif key in (curses.KEY_NPAGE,):
                self.follow = False
                self.selected = min(max(0, len(self.events) - 1), self.selected + 10)
            elif key in (curses.KEY_PPAGE,):
                self.follow = False
                self.selected = max(0, self.selected - 10)
            elif key == ord("g"):
                self.follow = False
                self.selected = 0
            elif key == ord("G"):
                self.follow = True
                self.selected = max(0, len(self.events) - 1)
            elif key == ord(" "):
                self.follow = not self.follow
                if self.follow:
                    self.selected = max(0, len(self.events) - 1)
            elif key == ord("/"):
                self.search_mode = True
                self.search_buffer = self.filter.search or ""
            elif key == ord("f"):
                levels = (None, "warning", "error")
                current = levels.index(self.filter.minimum_severity) if self.filter.minimum_severity in levels else 0
                self.filter = EventFilter(
                    search=self.filter.search,
                    minimum_severity=levels[(current + 1) % len(levels)],
                    subsystems=self.filter.subsystems,
                    signal_only=self.filter.signal_only,
                )
                self._refilter()
            elif key in (10, 13, curses.KEY_ENTER):
                self.overlay = "detail" if self.overlay != "detail" else None
            elif key == ord("?"):
                self.overlay = "help" if self.overlay != "help" else None
            elif key == ord("x"):
                if self.overlay == "stop-confirm":
                    self.cancellation_requested = True
                else:
                    self.overlay = "stop-confirm"
            elif key == ord("q"):
                if self._active:
                    self.overlay = "active-no-detach"
                else:
                    self._exit_requested = True
            elif key == 27:
                self.overlay = None
            self._dirty = True

    def _refilter(self) -> None:
        # Events rejected by an earlier filter are still in the retained journal,
        # not this bounded viewport. Replay provides whole-session refiltering.
        matching = [event for event in self.all_events if self.filter.matches(event)]
        self.events = deque(matching)
        self._visible_severity_counts = Counter(
            str(event.get("severity", "info")) for event in matching
        )
        self.selected = max(0, len(self.events) - 1)
        self.follow = True

    def _draw(self, *, force: bool = False) -> None:
        if not self._started or self._screen is None:
            return
        now = time.monotonic()
        if not force:
            elapsed = now - self._last_draw
            if elapsed < self.MIN_DRAW_INTERVAL_SECONDS:
                return
            if not self._dirty and elapsed < 0.5:
                return
        assert curses is not None
        screen = self._screen
        height, width = screen.getmaxyx()
        screen.erase()
        if height < 10 or width < 45:
            _safe_addstr(screen, 0, 0, "Workbench console: terminal too small", width)
            _safe_addstr(screen, 2, 0, "Resize to at least 45×10. Ingestion continues.", width)
            screen.refresh()
            return
        title = " WORKBENCH LIVE CONSOLE "
        command = sanitize_terminal(str(self._context.get("command", "session")))
        _safe_addstr(screen, 0, 0, title + command, width, curses.A_REVERSE)
        counts = self._visible_severity_counts
        status = (
            f"events {self.stats.events}  shown {len(self.events)}  "
            f"warn {counts['warning']}  error {counts['error'] + counts['fatal']}  "
            f"follow {'on' if self.follow else 'paused'}"
        )
        _safe_addstr(screen, 1, 0, status, width)
        filter_text = self.filter.search or "all"
        level = self.filter.minimum_severity or "all levels"
        _safe_addstr(screen, 2, 0, f"search {filter_text!r} · {level} · sequence is collector order", width)
        top = 4
        bottom = height - 2
        viewport = bottom - top
        start = max(0, self.selected - viewport + 1)
        for row, event in enumerate(islice(self.events, start, start + viewport), top):
            index = start + row - top
            marker = "▶" if index == self.selected else " "
            severity = str(event.get("severity", "info")).upper()[:4].ljust(4)
            subsystem = str(event.get("subsystem", "runtime"))[:10].ljust(10)
            message = sanitize_terminal(str(event.get("message", "")))
            attribute = curses.A_REVERSE if index == self.selected else 0
            _safe_addstr(screen, row, 0, f"{marker} {severity} {subsystem} {message}", width, attribute)
        footer = "/ search  f severity  j/k move  space follow  enter detail  x stop  ? help"
        if self.search_mode:
            footer = f"Search literal: {self.search_buffer}_"
        _safe_addstr(screen, height - 1, 0, footer, width, curses.A_REVERSE)
        if self.overlay:
            self._draw_overlay(self.overlay, height, width)
        screen.refresh()
        self._dirty = False
        self._last_draw = now

    def _draw_overlay(self, kind: str, height: int, width: int) -> None:
        assert curses is not None and self._screen is not None
        if kind == "help":
            lines = [
                "Keyboard", "j/k or arrows: navigate · g/G: first/latest", "space: pause viewport (ingestion continues)", "/: literal search · f: cycle severity", "enter: event detail · x: stop confirmation", "q: detach is unavailable during active V1 runs · esc: close",
            ]
        elif kind == "stop-confirm":
            lines = ["Stop this exact process group?", "Press x again to request graceful interruption.", "The supervisor may escalate only after its bounded timeout.", "Esc cancels this dialog."]
        elif kind == "active-no-detach":
            lines = ["Detach is unavailable in V1.", "Continue watching, or use x for the explicit stop flow."]
        elif kind == "complete":
            lines = ["Process complete.", "The terminal will return to a durable static recap."]
        else:
            event = self.events[self.selected] if self.events else {}
            lines = [
                f"Event {event.get('sequence', '?')} · {event.get('severity', 'info')} · {event.get('subsystem', 'runtime')}",
                *textwrap.wrap(sanitize_terminal(str(event.get("message", ""))), max(20, width - 12)),
            ]
            for locator in _resolved_locators(event):
                lines.append(f"source: {locator}")
            lines.append(f"provenance: {event.get('parse_provenance', 'raw')}")
        box_width = min(width - 4, max(44, max((len(line) for line in lines), default=0) + 4))
        box_height = min(height - 4, len(lines) + 4)
        y = max(1, (height - box_height) // 2)
        x = max(1, (width - box_width) // 2)
        with _ignore_curses_error():
            window = self._screen.derwin(box_height, box_width, y, x)
            window.erase()
            window.box()
            for row, line in enumerate(lines[: box_height - 2], 1):
                _safe_addstr(window, row, 2, line, box_width - 4)
            window.refresh()


class _ignore_curses_error:
    def __enter__(self):
        return self

    def __exit__(self, exception_type, _exception, _traceback):
        return exception_type is not None and curses is not None and issubclass(exception_type, curses.error)


def _safe_addstr(window, y: int, x: int, value: str, width: int, attribute: int = 0) -> None:
    if curses is None:
        return
    clean = sanitize_terminal(value).replace("\t", "    ")
    try:
        window.addnstr(y, x, clean, max(0, width - x - 1), attribute)
    except curses.error:
        pass


def can_use_tui(input_stream: TextIO, output: TextIO) -> bool:
    if curses is None or os.name != "posix":
        return False
    if not input_stream.isatty() or not output.isatty():
        return False
    if os.environ.get("TERM", "").casefold() in {"", "dumb", "unknown"}:
        return False
    size = shutil.get_terminal_size((80, 24))
    return size.columns >= 45 and size.lines >= 10


def make_renderer(
    mode: str,
    *,
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
    event_filter: EventFilter | None = None,
    color: str = "auto",
) -> Renderer:
    if mode not in {"auto", "tui", "plain", "jsonl", "messages"}:
        raise RenderError(f"unknown renderer mode: {mode}")
    selected = "tui" if mode == "auto" and can_use_tui(input_stream, output) else mode
    if selected == "auto":
        selected = "plain"
    if selected == "tui":
        if not can_use_tui(input_stream, output):
            if mode == "tui":
                raise RenderError("full-screen console requires a capable interactive terminal")
            selected = "plain"
        else:
            return CursesRenderer(event_filter=event_filter, output=output)
    if selected == "jsonl":
        return JsonlRenderer(output, event_filter=event_filter)
    use_color = (
        color == "always"
        or (
            color == "auto"
            and output.isatty()
            and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM", "").casefold() != "dumb"
        )
    )
    return PlainRenderer(
        output,
        event_filter=event_filter,
        color=use_color,
        cluster=selected != "messages",
        raw=selected == "messages",
    )


def render_replay(
    events: Iterable[Mapping[str, Any]],
    *,
    renderer: Renderer,
    context: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    try:
        renderer.start(context)
        for event in events:
            renderer.consume(event)
        renderer.finish(summary)
        if isinstance(renderer, CursesRenderer):
            renderer.browse()
    finally:
        renderer.close()


def _resolved_locators(event: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    locators = event.get("_resolved_source_locators", event.get("source_locators", []))
    if not isinstance(locators, list):
        return result
    for locator in locators:
        if not isinstance(locator, Mapping):
            continue
        path = locator.get("path") or locator.get("candidate")
        line = locator.get("line")
        state = locator.get("resolution", "candidate")
        if path:
            rendered = sanitize_terminal(str(path))
            if line:
                rendered += f":{line}"
            if state not in {"resolved", "exact", "unique"}:
                rendered += f" ({state})"
            result.append(rendered)
    return result


def _event_memory_size(event: Mapping[str, Any]) -> int:
    """Conservative bounded-viewport charge without serializing whole events."""

    size = 512
    pending: list[Any] = list(event.values())
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            size += len(value.encode("utf-8", errors="replace"))
        elif isinstance(value, Mapping):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)
        else:
            size += 32
    return size


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:6.1f}s"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes:3d}:{remainder:02d}"


def _color_prefix(value: str, severity: str) -> str:
    code = {
        "warning": "33",
        "error": "31",
        "fatal": "1;31",
        "notice": "36",
    }.get(severity, "2" if severity in {"trace", "debug"} else "0")
    return f"\x1b[{code}m{value}\x1b[0m"


__all__ = [
    "CursesRenderer",
    "EventFilter",
    "JsonlRenderer",
    "PlainRenderer",
    "RenderError",
    "Renderer",
    "can_use_tui",
    "make_renderer",
    "render_replay",
]
