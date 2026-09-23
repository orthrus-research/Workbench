from __future__ import annotations

import io
from unittest import mock
import unittest

import workbench_core.render as render_module
from workbench_core.render import CursesRenderer, EventFilter, PlainRenderer, RenderError


class _FakeCursesError(Exception):
    pass


class _FakeScreen:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def keypad(self, value: bool) -> None:
        self.calls.append(f"keypad:{value}")


class _DrawScreen:
    def __init__(self) -> None:
        self.refreshes = 0

    def getmaxyx(self):
        return (24, 100)

    def erase(self) -> None:
        pass

    def addnstr(self, *unused) -> None:
        pass

    def refresh(self) -> None:
        self.refreshes += 1


class _PartialCurses:
    error = _FakeCursesError

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.screen = _FakeScreen(self.calls)

    def initscr(self):
        self.calls.append("initscr")
        return self.screen

    def noecho(self) -> None:
        self.calls.append("noecho")
        raise self.error("simulated partial initialization")

    def nocbreak(self) -> None:
        self.calls.append("nocbreak")

    def echo(self) -> None:
        self.calls.append("echo")

    def endwin(self) -> None:
        self.calls.append("endwin")


class RenderTests(unittest.TestCase):
    def test_plain_exact_repeat_reports_last_occurrence(self) -> None:
        output = io.StringIO()
        renderer = PlainRenderer(
            output,
            event_filter=EventFilter(signal_only=False),
            color=False,
        )
        base = {
            "severity": "info",
            "subsystem": "test",
            "message": "same",
            "cluster_key": "same-key",
            "signal": False,
        }
        renderer.consume({**base, "sequence": 1})
        renderer.consume({**base, "sequence": 7})
        renderer.finish({"outcome": "complete", "effective_exit_code": 0})
        self.assertIn("exact repeat ×2 (last sequence 7)", output.getvalue())

    def test_partial_curses_initialization_restores_every_mode(self) -> None:
        fake = _PartialCurses()
        with mock.patch.object(render_module, "curses", fake):
            renderer = CursesRenderer(output=io.StringIO())
            with self.assertRaises(RenderError):
                renderer.start({"command": "fixture"})
        self.assertEqual(
            fake.calls,
            ["initscr", "noecho", "keypad:False", "nocbreak", "echo", "endwin"],
        )
        self.assertFalse(renderer._started)

    def test_curses_draw_is_frame_rate_bounded_during_bursts(self) -> None:
        renderer = CursesRenderer(output=io.StringIO())
        screen = _DrawScreen()
        renderer._screen = screen
        renderer._started = True
        renderer._context = {"command": "fixture"}
        with mock.patch(
            "workbench_core.render.time.monotonic",
            side_effect=(0.0, 0.001, 0.01, 0.04),
        ):
            renderer._draw(force=True)
            for _ in range(3):
                renderer._dirty = True
                renderer._draw()
        renderer._started = False
        self.assertEqual(screen.refreshes, 2)

    def test_curses_viewport_has_a_byte_budget(self) -> None:
        renderer = CursesRenderer(
            output=io.StringIO(),
            event_filter=EventFilter(signal_only=False),
        )
        renderer.pulse = lambda: None
        payload = "x" * (300 * 1024)
        for sequence in range(100):
            renderer.consume(
                {
                    "sequence": sequence,
                    "message": payload,
                    "severity": "info",
                    "subsystem": "fixture",
                    "cluster_key": str(sequence),
                    "signal": False,
                }
            )
        self.assertLessEqual(renderer._memory_bytes, renderer.MAX_MEMORY_BYTES)
        self.assertLess(len(renderer.all_events), 100)
        self.assertEqual(len(renderer.events), len(renderer.all_events))


if __name__ == "__main__":
    unittest.main()
