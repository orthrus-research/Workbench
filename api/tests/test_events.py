from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "api/src"
sys.path.insert(0, str(SOURCE))

from workbench_api.events import (  # noqa: E402
    FORMAT_VERSION,
    ConsoleEvent,
    EventNormalizer,
    IncrementalEventDecoder,
    cluster_events,
    event_matches,
    extract_source_locators,
    filter_events,
    sanitize_terminal,
)


class EventPipelineTests(unittest.TestCase):
    def test_no_implicit_domain_classification(self) -> None:
        normalizer = self.normalizer()
        for line in ("BUILD FAILED in 2s", "---- Minecraft Crash Report ----", "PopulateChunkEvent cascading worldgen detected"):
            event = normalizer.normalize(line)
            self.assertEqual(event.subsystem, "generic")
            self.assertFalse(event.outcome_failure)
        self.assertTrue(normalizer.normalize("WORKBENCH_REQUIRED_CHECK_FAILED fixture").outcome_failure)

    def test_explicit_classifier_cannot_rewrite_source_or_erase_failure(self) -> None:
        for classifier in (
            lambda value: replace(value, message="rewritten"),
            lambda value: replace(value, outcome_failure=False),
            lambda value: replace(value, severity="invented"),
            lambda value: replace(value, basis=("erased",)),
        ):
            event = EventNormalizer(classifiers=(classifier,)).normalize("WORKBENCH_REQUIRED_CHECK_FAILED fixture")
            self.assertTrue(event.outcome_failure)
            self.assertIn("owner-classifier-unavailable", event.limitations)
            self.assertEqual(event.message, "WORKBENCH_REQUIRED_CHECK_FAILED fixture")

    def test_broken_classifier_does_not_lose_raw_bytes_or_block_next_owner(self) -> None:
        def broken(value):
            raise RuntimeError("optional owner is broken")

        def annotate(value):
            return replace(value, subsystem="workbench", basis=(*value.basis, "classify.owner-test-v1"))

        decoder = IncrementalEventDecoder(EventNormalizer(classifiers=(broken, annotate)), "fixture", "stdout")
        event, = decoder.feed(b"hello\r\n")
        self.assertEqual(decoder.take_raw_records()[0].raw_bytes, b"hello\r\n")
        self.assertEqual(event.subsystem, "workbench")
        self.assertIn("owner-classifier-unavailable", event.limitations)

    @staticmethod
    def normalizer() -> EventNormalizer:
        monotonic_values = iter(range(10_000, 20_000))
        return EventNormalizer(
            wall_clock=lambda: datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
            monotonic_clock=lambda: next(monotonic_values),
        )

    def test_event_contract_sanitizes_terminal_input_and_keeps_raw_separate(self) -> None:
        raw = (
            b"\x1b[31mred\x1b[0m "
            b"\x1b]8;;file:///tmp/pwn\x07link\x1b]8;;\x07"
            b"\x00\x08\xe2\x80\xae!\n"
        )
        decoder = IncrementalEventDecoder(
            self.normalizer(),
            "minecraft",
            "stderr",
            raw_path="stderr.raw",
        )

        events = decoder.feed(raw)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.message, r"red link\x00\x08\u202e!")
        self.assertNotIn("\x1b", event.message)
        self.assertIn("terminal-control-sequences-removed", event.limitations)
        self.assertIn("control-characters-rendered-as-escapes", event.limitations)
        self.assertEqual(event.raw_locator.byte_start, 0)
        self.assertEqual(event.raw_locator.byte_end, len(raw))
        self.assertEqual(event.raw_locator.boundary, "lf")

        projected = event.as_dict()
        self.assertNotIn("raw_bytes", projected)
        self.assertEqual(projected["format_version"], FORMAT_VERSION)
        self.assertEqual(projected["raw_locator"]["artifact"], "stderr.raw")
        records = decoder.take_raw_records()
        self.assertEqual([record.raw_bytes for record in records], [raw])
        self.assertEqual(records[0].content_bytes, raw[:-1])
        self.assertEqual(records[0].terminator_bytes, b"\n")

    def test_sanitizer_drops_unterminated_osc_and_escapes_controls(self) -> None:
        self.assertEqual(sanitize_terminal("safe\x1b]0;hostile"), "safe")
        self.assertEqual(sanitize_terminal("a\tb\nc\x7f"), r"a\x09b\x0ac\x7f")

    def test_incremental_decoder_accepts_every_utf8_split(self) -> None:
        raw = "café 😀\n".encode("utf-8")
        decoder = IncrementalEventDecoder(
            self.normalizer(),
            "game",
            "stdout",
            raw_path="stdout.raw",
            retain_raw_records=True,
        )
        events: list[ConsoleEvent] = []
        for byte in raw:
            events.extend(decoder.feed(bytes([byte])))

        self.assertEqual([event.message for event in events], ["café 😀"])
        self.assertEqual(events[0].raw_locator.byte_end, len(raw))
        self.assertEqual(
            b"".join(record.raw_bytes for record in decoder.take_raw_records()), raw
        )
        self.assertEqual(decoder.finish(), [])
        self.assertEqual(decoder.finish(), [])

    def test_crlf_lone_cr_lf_and_eof_have_exact_offsets_and_lines(self) -> None:
        raw = b"alpha\r\nbeta\rgamma\ndelta"
        decoder = IncrementalEventDecoder(
            self.normalizer(),
            "process",
            "stdout",
            raw_path="stdout.raw",
            retain_raw_records=True,
        )

        events = decoder.feed(b"alpha\r")
        self.assertEqual(events, [])
        events += decoder.feed(b"\nbeta\rgamma\ndelta")
        events += decoder.finish()

        self.assertEqual(
            [event.message for event in events],
            ["alpha", "beta", "gamma", "delta"],
        )
        self.assertEqual(
            [event.raw_locator.boundary for event in events],
            ["crlf", "cr", "lf", "eof"],
        )
        self.assertEqual([event.raw_locator.line for event in events], [1, 2, 3, 4])
        ranges = [
            (event.raw_locator.byte_start, event.raw_locator.byte_end)
            for event in events
        ]
        self.assertEqual(ranges[0], (0, 7))
        self.assertEqual(ranges[-1][1], len(raw))
        self.assertEqual(
            b"".join(record.raw_bytes for record in decoder.take_raw_records()), raw
        )

    def test_oversized_record_is_bounded_and_chunked_without_loss(self) -> None:
        raw = b"abcdefghijkl\r\nnext\n"
        decoder = IncrementalEventDecoder(
            self.normalizer(),
            "build",
            "stdout",
            max_record_bytes=5,
            raw_path="stdout.raw",
        )

        events = decoder.feed(raw)

        self.assertEqual(
            [event.message for event in events], ["abcde", "fghij", "kl", "next"]
        )
        self.assertEqual(
            [event.raw_locator.boundary for event in events],
            ["limit", "limit", "crlf", "lf"],
        )
        self.assertEqual([event.raw_locator.line for event in events], [1, 1, 1, 2])
        self.assertEqual([event.raw_locator.chunk for event in events], [1, 2, 3, 1])
        records = decoder.take_raw_records()
        self.assertTrue(all(len(record.content_bytes) <= 5 for record in records))
        self.assertEqual(b"".join(record.raw_bytes for record in records), raw)
        self.assertIn("logical-record-chunked-at-byte-limit", events[2].limitations)

    def test_utf8_codepoint_can_span_hard_size_chunks(self) -> None:
        raw = "A😀B\n".encode("utf-8")
        decoder = IncrementalEventDecoder(
            self.normalizer(), "game", "stdout", max_record_bytes=2
        )

        events = decoder.feed(raw)

        self.assertEqual("".join(event.message for event in events), "A😀B")
        self.assertEqual(
            b"".join(record.raw_bytes for record in decoder.take_raw_records()), raw
        )
        self.assertTrue(
            any(
                "utf8-codepoint-spans-record-chunks" in event.limitations
                for event in events
            )
        )

    def test_append_order_is_not_reordered_by_source_timestamp(self) -> None:
        normalizer = self.normalizer()
        stdout = IncrementalEventDecoder(normalizer, "game", "stdout")
        stderr = IncrementalEventDecoder(normalizer, "game", "stderr")

        first = stdout.feed(b"[23:59:59] [main/INFO] [FML]: first\n")[0]
        second = stderr.feed(b"[00:00:01] [main/ERROR] [FML]: second\n")[0]

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(first.source_timestamp, "23:59:59")
        self.assertEqual(second.source_timestamp, "00:00:01")
        self.assertLess(first.monotonic_ns, second.monotonic_ns)

    def test_duplicate_clustering_preserves_members_count_and_occurrences(self) -> None:
        normalizer = self.normalizer()
        duplicate_one = normalizer.normalize(
            "ERROR: recoverable condition",
            source="game",
            stream="stderr",
            raw_locator={"byte_start": 0, "byte_end": 29, "line": 1},
        )
        different = normalizer.normalize(
            "INFO: continued",
            source="game",
            stream="stderr",
            raw_locator={"byte_start": 29, "byte_end": 45, "line": 2},
        )
        duplicate_two = normalizer.normalize(
            "ERROR: recoverable condition",
            source="game",
            stream="stderr",
            raw_locator={"byte_start": 45, "byte_end": 74, "line": 3},
        )

        clusters = cluster_events([duplicate_one, different, duplicate_two])

        self.assertEqual(len(clusters), 2)
        repeated = clusters[0]
        self.assertEqual(repeated.count, 2)
        self.assertEqual(repeated.first, duplicate_one)
        self.assertEqual(repeated.last, duplicate_two)
        self.assertEqual(repeated.first_sequence, 1)
        self.assertEqual(repeated.last_sequence, 3)
        self.assertEqual(repeated.members, (duplicate_one, duplicate_two))
        self.assertEqual(repeated["count"], 2)




    def test_source_frames_are_unique_unresolved_candidates(self) -> None:
        text = (
            "at dev.example.Mod.run(Mod.java:42) caused by "
            "scripts/post init.groovy:9:3 and Mod.java:42"
        )

        locators = extract_source_locators(text)

        self.assertEqual(
            [(row.path, row.line, row.column) for row in locators],
            [("Mod.java", 42, None), ("scripts/post init.groovy", 9, 3)],
        )
        self.assertTrue(all(not Path(row.path).is_absolute() for row in locators))
        event = self.normalizer().normalize(text, source="game", stream="stderr")
        self.assertEqual(event.source_locators, locators)
        self.assertIn("source-locators-are-unresolved-candidates", event.limitations)

    def test_source_candidate_scan_is_bounded_for_default_maximum_plain_record(self) -> None:
        started = time.perf_counter()
        self.assertEqual(extract_source_locators("x" * (256 * 1024)), ())
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(
            [
                (row.path, row.line, row.column)
                for row in extract_source_locators(
                    "at /home/user/My Project/src/Foo.java:12:3 and "
                    r"C:\Users\Trevor\My Project\Bar.groovy:9"
                )
            ],
            [
                ("/home/user/My Project/src/Foo.java", 12, 3),
                (r"C:\Users\Trevor\My Project\Bar.groovy", 9, None),
            ],
        )
        hostile = self.normalizer().normalize(
            "Foo.java:" + "9" * 5000,
            source="game",
            stream="stderr",
        )
        self.assertEqual(hostile.source_locators, ())
        self.assertIn(
            "source-locator-candidates-truncated-to-contract-limit",
            hostile.limitations,
        )

    def test_literal_filter_does_not_treat_search_as_regex(self) -> None:
        normalizer = self.normalizer()
        events = [
            normalizer.normalize(
                "literal [value]", source="game", stream="stdout"
            ),
            normalizer.normalize(
                "ERROR: other", source="gradle", stream="stderr"
            ),
        ]

        self.assertTrue(event_matches(events[0], search="[VALUE]"))
        self.assertFalse(event_matches(events[0], search=".*"))
        self.assertEqual(filter_events(events, severities={"error"}), [events[1]])
        self.assertEqual(
            filter_events(events, search="other", subsystems="generic"), [events[1]]
        )

    def test_normalized_dict_contains_every_v1_field(self) -> None:
        event = self.normalizer().normalize(
            "plain", source="process", stream="stdout", monotonic_ns=123
        )
        self.assertEqual(
            set(event.as_dict()),
            {
                "format_version",
                "event_id",
                "sequence",
                "ingested_at",
                "monotonic_ns",
                "source_timestamp",
                "source",
                "stream",
                "raw_locator",
                "kind",
                "severity",
                "subsystem",
                "logger",
                "thread",
                "message",
                "parse_provenance",
                "classification_basis",
                "cluster_key",
                "signal",
                "outcome_failure",
                "source_locators",
                "limitations",
            },
        )
        self.assertEqual(event.monotonic_ns, 123)


if __name__ == "__main__":
    unittest.main()
