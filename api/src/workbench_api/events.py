"""Lossless ingestion and conservative classification for the live console.

The normalized event contract intentionally does not contain process bytes.  A
consumer retains those bytes in a per-stream artifact and uses ``raw_locator``
to retrieve them.  :class:`IncrementalEventDecoder` also exposes the exact
records through :meth:`take_raw_records` for callers that do not already own a
transcript writer.

Classification here covers structured log levels and Workbench stage markers.
Platform-specific classification is an explicitly supplied callback; importing
the API never imports, discovers, or selects a domain profile.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import codecs
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any, Callable


FORMAT_VERSION = "workbench-live-console-event-v1"
DEFAULT_MAX_RECORD_BYTES = 64 * 1024
MAX_SOURCE_LOCATORS = 32
MAX_PARSED_LABEL_CHARACTERS = 8192

PARSE_PROVENANCE = frozenset({"raw", "parsed", "heuristic"})
SEVERITIES = frozenset(
    {"trace", "debug", "info", "warning", "error", "fatal", "unknown"}
)
KINDS = frozenset(
    {
        "text",
        "log",
        "build",
        "compiler_diagnostic",
        "mixin",
        "groovy",
        "registry",
        "worldgen",
        "exception",
        "stack_frame",
        "stage",
    }
)
SUBSYSTEMS = frozenset(
    {
        "generic",
        "gradle",
        "compiler",
        "minecraft",
        "cleanroom-fml",
        "mixin",
        "cleanmix",
        "groovy",
        "registry",
        "worldgen",
        "java",
        "workbench",
    }
)
BOUNDARIES = frozenset({"lf", "crlf", "cr", "limit", "eof"})


@dataclass(frozen=True, slots=True)
class RawLocator(Mapping[str, Any]):
    """A half-open range in a separately retained raw stream artifact.

    ``byte_end`` includes the record's CR/LF terminator.  The payload itself is
    bounded by the decoder's ``max_record_bytes``; a final CRLF can make the
    located range two bytes larger.
    """

    artifact: str | None
    byte_start: int
    byte_end: int
    line: int
    chunk: int = 1
    boundary: str = "eof"

    def __post_init__(self) -> None:
        if self.artifact is not None and not isinstance(self.artifact, str):
            raise TypeError("raw locator artifact must be a string or None")
        if self.artifact is not None and (
            not self.artifact
            or len(self.artifact) > 8192
            or any(character in self.artifact for character in "\r\n\x00")
        ):
            raise ValueError("raw locator artifact must be bounded single-line text")
        if not isinstance(self.byte_start, int) or self.byte_start < 0:
            raise ValueError("raw locator byte_start must be a non-negative integer")
        if not isinstance(self.byte_end, int) or self.byte_end < self.byte_start:
            raise ValueError("raw locator byte_end must be at least byte_start")
        if not isinstance(self.line, int) or self.line < 1:
            raise ValueError("raw locator line must be a positive integer")
        if not isinstance(self.chunk, int) or self.chunk < 1:
            raise ValueError("raw locator chunk must be a positive integer")
        if self.boundary not in BOUNDARIES:
            raise ValueError(f"unknown raw record boundary: {self.boundary!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "line": self.line,
            "chunk": self.chunk,
            "boundary": self.boundary,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self):
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 6


@dataclass(frozen=True, slots=True)
class SourceLocator(Mapping[str, Any]):
    """An unresolved path/line candidate parsed from rendered output."""

    path: str
    line: int
    column: int | None = None
    label: str = ""

    def __post_init__(self) -> None:
        for name, value in (("path", self.path), ("label", self.label)):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 8192
                or any(character in value for character in "\r\n\x00")
            ):
                raise ValueError(
                    f"source locator {name} must be bounded single-line text"
                )
        if not isinstance(self.line, int) or self.line < 1:
            raise ValueError("source locator line must be a positive integer")
        if self.column is not None and (
            not isinstance(self.column, int) or self.column < 1
        ):
            raise ValueError("source locator column must be positive or None")

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "label": self.label,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self):
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 4


@dataclass(frozen=True, slots=True)
class RawRecord:
    """Exact bytes and decoded text for one bounded record.

    ``raw_bytes`` includes the terminator named by ``locator.boundary``.  Text
    contains only the payload.  A UTF-8 codepoint may span ``limit`` records;
    the incremental decoder emits that codepoint with the record that completes
    it while the exact constituent bytes remain available here.
    """

    raw_bytes: bytes
    text: str
    locator: RawLocator
    decode_had_errors: bool = False
    utf8_from_previous_chunk: bool = False
    utf8_continues: bool = False

    @property
    def terminator_bytes(self) -> bytes:
        length = {"lf": 1, "cr": 1, "crlf": 2}.get(
            self.locator.boundary, 0
        )
        return self.raw_bytes[-length:] if length else b""

    @property
    def content_bytes(self) -> bytes:
        length = len(self.terminator_bytes)
        return self.raw_bytes[:-length] if length else self.raw_bytes


@dataclass(frozen=True, slots=True)
class ConsoleEvent:
    """One normalized append-order live-console event."""

    format_version: str
    event_id: str
    sequence: int
    ingested_at: str
    monotonic_ns: int
    source_timestamp: str | None
    source: str
    stream: str
    raw_locator: RawLocator
    kind: str
    severity: str
    subsystem: str
    logger: str | None
    thread: str | None
    message: str
    parse_provenance: str
    classification_basis: tuple[str, ...]
    cluster_key: str
    signal: bool
    outcome_failure: bool
    source_locators: tuple[SourceLocator, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"unsupported event format: {self.format_version!r}")
        if not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("event sequence must be a positive integer")
        if not isinstance(self.monotonic_ns, int) or self.monotonic_ns < 0:
            raise ValueError("event monotonic_ns must be a non-negative integer")
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind: {self.kind!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown event severity: {self.severity!r}")
        if self.subsystem not in SUBSYSTEMS:
            raise ValueError(f"unknown event subsystem: {self.subsystem!r}")
        if self.parse_provenance not in PARSE_PROVENANCE:
            raise ValueError(
                f"unknown event parse provenance: {self.parse_provenance!r}"
            )
        if not isinstance(self.source, str) or not _IDENTIFIER_RE.fullmatch(self.source):
            raise ValueError("event source must be an identifier-safe string")
        if not isinstance(self.stream, str) or not _STREAM_RE.fullmatch(self.stream):
            raise ValueError("event stream must be a bounded stream identifier")
        if not isinstance(self.signal, bool) or not isinstance(
            self.outcome_failure, bool
        ):
            raise TypeError("event signal and outcome_failure must be booleans")

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-ready V1 projection (never the raw bytes)."""

        return {
            "format_version": self.format_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "ingested_at": self.ingested_at,
            "monotonic_ns": self.monotonic_ns,
            "source_timestamp": self.source_timestamp,
            "source": self.source,
            "stream": self.stream,
            "raw_locator": self.raw_locator.as_dict(),
            "kind": self.kind,
            "severity": self.severity,
            "subsystem": self.subsystem,
            "logger": self.logger,
            "thread": self.thread,
            "message": self.message,
            "parse_provenance": self.parse_provenance,
            "classification_basis": list(self.classification_basis),
            "cluster_key": self.cluster_key,
            "signal": self.signal,
            "outcome_failure": self.outcome_failure,
            "source_locators": [row.as_dict() for row in self.source_locators],
            "limitations": list(self.limitations),
        }

    # A familiar spelling for callers that treat events as serialized records.
    to_dict = as_dict


@dataclass(frozen=True, slots=True)
class EventCluster(Mapping[str, Any]):
    """An exact normalized duplicate cluster in first-seen order."""

    cluster_key: str
    members: tuple[ConsoleEvent | Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("an event cluster must contain at least one member")

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def first(self) -> ConsoleEvent | Mapping[str, Any]:
        return self.members[0]

    @property
    def last(self) -> ConsoleEvent | Mapping[str, Any]:
        return self.members[-1]

    @property
    def first_sequence(self) -> int:
        return int(_event_value(self.first, "sequence"))

    @property
    def last_sequence(self) -> int:
        return int(_event_value(self.last, "sequence"))

    def as_dict(self) -> dict[str, Any]:
        def project(member: ConsoleEvent | Mapping[str, Any]) -> dict[str, Any]:
            if isinstance(member, ConsoleEvent):
                return member.as_dict()
            return dict(member)

        return {
            "cluster_key": self.cluster_key,
            "count": self.count,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "first_ingested_at": _event_value(self.first, "ingested_at"),
            "last_ingested_at": _event_value(self.last, "ingested_at"),
            "members": [project(member) for member in self.members],
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self):
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 7


@dataclass(frozen=True, slots=True)
class EventClassification:
    """Immutable parsed record offered to explicit owner classifiers.

Owners may refine label fields and append basis identities. They cannot change
the parsed message, logger, thread or timestamp, or erase existing provenance.
"""
    message: str
    source_timestamp: str | None
    kind: str
    severity: str
    subsystem: str
    logger: str | None
    thread: str | None
    parse_provenance: str
    basis: tuple[str, ...]
    signal: bool
    outcome_failure: bool


_LOG4J_RE = re.compile(
    r"^\s*\[(?P<timestamp>[^\]\r\n]+)\]\s*"
    r"\[(?P<thread>[^/\]\r\n]+)/(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]\s*"
    r"\[(?P<logger>[^\]\r\n]+)\]\s*:\s?(?P<message>.*)$",
    re.IGNORECASE,
)
_LOG4J_WITHOUT_LOGGER_RE = re.compile(
    r"^\s*\[(?P<timestamp>[^\]\r\n]+)\]\s*"
    r"\[(?P<thread>[^/\]\r\n]+)/(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]\s*"
    r":\s?(?P<message>.*)$",
    re.IGNORECASE,
)
_WORKBENCH_STAGE_RE = re.compile(
    r"^\s*(?:\[workbench\]\s+(?:stage\s+)?|WORKBENCH_STAGE\s+)"
    r"(?P<stage>[A-Za-z0-9_.:/-]+)\s+(?P<state>started|running|completed|complete|succeeded|success|failed|blocked)\s*$",
    re.IGNORECASE,
)
_REQUIRED_CHECK_RE = re.compile(
    r"^\s*(?:\[workbench\]\s+)?required[- ]check(?:\s+[A-Za-z0-9_.:/-]+)?"
    r"\s+(?:failed|failure)\s*$",
    re.IGNORECASE,
)
_REQUIRED_CHECK_MACHINE_RE = re.compile(
    r"^\s*WORKBENCH_REQUIRED_CHECK_FAILED(?:\s+[A-Za-z0-9_.:/-]+)?\s*$"
)
_SOURCE_SUFFIX_RE = re.compile(
    r"\.(?:java|groovy|gradle|kt|scala|json|json5|xml|ya?ml|cfg|conf|properties)"
    r":(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?",
    re.IGNORECASE,
)
_BARE_SOURCE_STEM = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.~@+-"
)
_FINAL_SOURCE_STEM = _BARE_SOURCE_STEM | {" "}
_RELATIVE_SOURCE_PATH = _BARE_SOURCE_STEM | {"/", "\\"}
MAX_SOURCE_CANDIDATES = 256
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}$")
_STREAM_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")


_LEVELS = {
    "TRACE": "trace",
    "DEBUG": "debug",
    "INFO": "info",
    "WARN": "warning",
    "WARNING": "warning",
    "ERROR": "error",
    "FATAL": "fatal",
}


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _sanitize_terminal(text: str) -> tuple[str, bool, bool]:
    """Return safe text plus (terminal-sequence, control-character) flags."""

    if not isinstance(text, str):
        raise TypeError("terminal text must be a string")

    output: list[str] = []
    index = 0
    terminal_sequence = False
    control_character = False
    length = len(text)

    while index < length:
        character = text[index]
        codepoint = ord(character)

        # Seven-bit escape forms.  OSC and the other string commands are
        # discarded through BEL or ST; an unterminated sequence is discarded
        # through the end of this rendering unit.
        if character == "\x1b":
            terminal_sequence = True
            if index + 1 >= length:
                break
            introducer = text[index + 1]
            if introducer == "[":  # CSI
                index += 2
                while index < length:
                    final = ord(text[index])
                    index += 1
                    if 0x40 <= final <= 0x7E:
                        break
                continue
            if introducer in "]PX^_":  # OSC, DCS, SOS, PM, APC
                index += 2
                while index < length:
                    if text[index] == "\x07":
                        index += 1
                        break
                    if (
                        text[index] == "\x1b"
                        and index + 1 < length
                        and text[index + 1] == "\\"
                    ):
                        index += 2
                        break
                    index += 1
                continue
            # Fe, Fs and character-set escape commands are at least two bytes.
            index += 2
            continue

        # Eight-bit CSI / OSC / string controls when they arrived as valid
        # Unicode.  Invalid single-byte C1 input is rendered as a visible hex
        # escape by the surrogateescape branch below.
        if codepoint == 0x9B:  # CSI
            terminal_sequence = True
            index += 1
            while index < length:
                final = ord(text[index])
                index += 1
                if 0x40 <= final <= 0x7E:
                    break
            continue
        if codepoint in {0x90, 0x98, 0x9D, 0x9E, 0x9F}:
            terminal_sequence = True
            index += 1
            while index < length:
                if ord(text[index]) in {0x07, 0x9C}:
                    index += 1
                    break
                if (
                    text[index] == "\x1b"
                    and index + 1 < length
                    and text[index + 1] == "\\"
                ):
                    index += 2
                    break
                index += 1
            continue

        # ``surrogateescape`` is used by the byte decoder so invalid bytes can
        # be identified without losing the separately retained source bytes.
        if 0xDC80 <= codepoint <= 0xDCFF:
            output.append(f"\\x{codepoint - 0xDC00:02x}")
            control_character = True
            index += 1
            continue

        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs"} or codepoint == 0x7F:
            if codepoint <= 0xFF:
                output.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                output.append(f"\\u{codepoint:04x}")
            else:
                output.append(f"\\U{codepoint:08x}")
            control_character = True
            index += 1
            continue

        output.append(character)
        index += 1

    return "".join(output), terminal_sequence, control_character


def sanitize_terminal(text: str) -> str:
    """Strip ANSI/OSC commands and visibly escape other rendering controls."""

    return _sanitize_terminal(text)[0]


def _classify(rendered: str) -> EventClassification:
    message = rendered
    source_timestamp: str | None = None
    logger: str | None = None
    thread: str | None = None
    severity = "unknown"
    kind = "text"
    subsystem = "generic"
    provenance = "raw"
    basis: list[str] = []
    outcome_failure = False

    log_match = _LOG4J_RE.match(rendered)
    if log_match is None:
        log_match = _LOG4J_WITHOUT_LOGGER_RE.match(rendered)
    if log_match is not None:
        source_timestamp = log_match.group("timestamp").strip() or None
        thread = log_match.group("thread").strip() or None
        logger_value = log_match.groupdict().get("logger")
        logger = logger_value.strip() if logger_value else None
        if source_timestamp and len(source_timestamp) > MAX_PARSED_LABEL_CHARACTERS:
            source_timestamp = source_timestamp[:MAX_PARSED_LABEL_CHARACTERS]
            basis.append("normalize.parsed-label-truncated-v1")
        if thread and len(thread) > MAX_PARSED_LABEL_CHARACTERS:
            thread = thread[:MAX_PARSED_LABEL_CHARACTERS]
            basis.append("normalize.parsed-label-truncated-v1")
        if logger and len(logger) > MAX_PARSED_LABEL_CHARACTERS:
            logger = logger[:MAX_PARSED_LABEL_CHARACTERS]
            basis.append("normalize.parsed-label-truncated-v1")
        message = log_match.group("message")
        severity = _LEVELS[log_match.group("level").upper()]
        kind = "log"
        provenance = "parsed"
        basis.append("parse.log4j-line-v1")

    stage_match = _WORKBENCH_STAGE_RE.match(message)
    if stage_match is not None:
        state = stage_match.group("state").casefold()
        kind = "stage"
        subsystem = "workbench"
        provenance = "parsed"
        basis.append("parse.workbench-stage-v1")
        if state == "failed":
            severity = "error"
            outcome_failure = True
            basis.append("outcome.workbench-stage-failed-v1")
        elif state == "blocked":
            severity = "warning"
        else:
            severity = "info"

    required_check = bool(
        _REQUIRED_CHECK_RE.match(message)
        or _REQUIRED_CHECK_MACHINE_RE.match(message)
    )
    if required_check:
        kind = "stage"
        subsystem = "workbench"
        severity = "error"
        provenance = "parsed"
        outcome_failure = True
        basis.extend(
            ("parse.workbench-required-check-v1", "outcome.required-check-failed-v1")
        )

    # A bare level word is useful for filtering but is never an outcome oracle.
    if severity == "unknown":
        level_word = re.match(
            r"^\s*(?:\[(?P<bracket>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]|"
            r"(?P<plain>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\s*:)\s*(?:.*)$",
            message,
            re.IGNORECASE,
        )
        if level_word:
            level = level_word.group("bracket") or level_word.group("plain")
            severity = _LEVELS[level.upper()]
            provenance = "heuristic" if provenance == "raw" else provenance
            basis.append("classify.level-token-v1")

    basis_tuple = _ordered_unique(basis)
    signal = outcome_failure or severity in {"warning", "error", "fatal"} or kind == "stage"
    return EventClassification(
        message=message,
        source_timestamp=source_timestamp,
        kind=kind,
        severity=severity,
        subsystem=subsystem,
        logger=logger,
        thread=thread,
        parse_provenance=provenance,
        basis=basis_tuple,
        signal=signal,
        outcome_failure=outcome_failure,
    )


def _validate_refinement(previous: EventClassification, value: EventClassification) -> None:
    if not isinstance(value, EventClassification):
        raise TypeError("owner classifier returned an incompatible record")
    for name in ("message", "source_timestamp", "logger", "thread"):
        if getattr(value, name) != getattr(previous, name):
            raise ValueError("owner classifier changed parsed source material")
    if (
        value.kind not in KINDS
        or value.severity not in SEVERITIES
        or value.subsystem not in SUBSYSTEMS
        or value.parse_provenance not in PARSE_PROVENANCE
        or (previous.parse_provenance == "parsed" and value.parse_provenance != "parsed")
        or (previous.parse_provenance == "heuristic" and value.parse_provenance == "raw")
        or type(value.outcome_failure) is not bool
        or type(value.signal) is not bool
        or not isinstance(value.basis, tuple)
        or len(value.basis) > 64
        or any(not isinstance(item, str) or not _IDENTIFIER_RE.fullmatch(item) for item in value.basis)
        or value.basis[:len(previous.basis)] != previous.basis
        or (previous.outcome_failure and not value.outcome_failure)
        or value.signal != (value.outcome_failure or value.severity in {"warning", "error", "fatal"} or value.kind == "stage")
    ):
        raise ValueError("owner classifier returned an invalid refinement")


def _extract_source_locators(
    text: str,
) -> tuple[tuple[SourceLocator, ...], bool]:
    if not isinstance(text, str):
        raise TypeError("source locator text must be a string")
    locators: list[SourceLocator] = []
    seen: set[tuple[str, int, int | None]] = set()
    limited = False
    for candidate_number, match in enumerate(_SOURCE_SUFFIX_RE.finditer(text), 1):
        if candidate_number > MAX_SOURCE_CANDIDATES:
            limited = True
            break
        path_end = match.start()
        path_start = _source_candidate_start(text, path_end)
        if path_start is None:
            continue
        path = text[path_start:path_end] + match.group(0).split(":", 1)[0]
        label = text[path_start:match.end()]
        if (
            len(path) > MAX_PARSED_LABEL_CHARACTERS
            or len(label) > MAX_PARSED_LABEL_CHARACTERS
        ):
            limited = True
            continue
        line_text = match.group("line")
        column_text = match.group("column")
        if len(line_text) > 18 or (
            column_text is not None and len(column_text) > 18
        ):
            limited = True
            continue
        line = int(line_text)
        column = int(column_text) if column_text else None
        identity = (path, line, column)
        if identity in seen:
            continue
        if len(locators) == MAX_SOURCE_LOCATORS:
            limited = True
            break
        seen.add(identity)
        locators.append(
            SourceLocator(
                path=path,
                line=line,
                column=column,
                label=label,
            )
        )
    return tuple(locators), limited


def _source_candidate_start(text: str, extension_start: int) -> int | None:
    """Find one bounded path start for a suffix already located in linear time.

    V1 source labels are capped at 8,192 characters, so no candidate needs an
    unbounded or backtracking scan.  Absolute paths may retain spaces; relative
    directory components remain space-free, matching the original contract.
    """

    lower = max(0, extension_start - MAX_PARSED_LABEL_CHARACTERS)
    cursor = extension_start
    while cursor > lower and text[cursor - 1] in _FINAL_SOURCE_STEM:
        cursor -= 1
    if cursor == extension_start:
        return None

    separator = cursor - 1
    if separator < lower or text[separator] not in {"/", "\\"}:
        bare = extension_start
        while bare > cursor and text[bare - 1] in _BARE_SOURCE_STEM:
            bare -= 1
        return bare if bare < extension_start else None

    # Prefer an absolute root.  A drive prefix is exact; a POSIX root is the
    # earliest slash after a strong prose delimiter inside the bounded window.
    window = text[lower:extension_start]
    drive_matches = list(re.finditer(r"[A-Za-z]:[\\/]", window))
    if drive_matches:
        return lower + drive_matches[-1].start()
    strong = lower
    for marker in ("\r", "\n", "(", ")", "[", "]", ":"):
        position = text.rfind(marker, lower, extension_start)
        if position >= strong:
            strong = position + 1
    root = text.find("/", strong, extension_start)
    while root >= 0:
        if root == strong or text[root - 1].isspace() or text[root - 1] in "([{'\"=,":
            return root
        root = text.find("/", root + 1, extension_start)

    cursor = separator
    while cursor > lower and text[cursor - 1] in _RELATIVE_SOURCE_PATH:
        cursor -= 1
    return cursor


def extract_source_locators(text: str) -> tuple[SourceLocator, ...]:
    """Parse unique path/line candidates without touching the filesystem."""

    return _extract_source_locators(text)[0]


def _coerce_raw_locator(
    value: RawLocator | Mapping[str, Any] | None,
    *,
    text: str,
    default_line: int,
) -> RawLocator:
    if isinstance(value, RawLocator):
        return value
    row = dict(value or {})
    byte_start = row.get("byte_start", row.get("start_byte", row.get("offset", 0)))
    encoded_length = len(text.encode("utf-8", errors="surrogatepass"))
    byte_end = row.get("byte_end", row.get("end_byte", int(byte_start) + encoded_length))
    return RawLocator(
        artifact=(
            None
            if row.get("artifact", row.get("raw_path")) is None
            else str(row.get("artifact", row.get("raw_path")))
        ),
        byte_start=int(byte_start),
        byte_end=int(byte_end),
        line=int(row.get("line", default_line)),
        chunk=int(row.get("chunk", 1)),
        boundary=str(row.get("boundary", "eof")),
    )


def _cluster_key(
    *,
    source: str,
    stream: str,
    classification: EventClassification,
    source_locators: tuple[SourceLocator, ...],
) -> str:
    identity = {
        "source": source,
        "stream": stream,
        "kind": classification.kind,
        "severity": classification.severity,
        "subsystem": classification.subsystem,
        "logger": classification.logger,
        "thread": classification.thread,
        "message": classification.message,
        "parse_provenance": classification.parse_provenance,
        "classification_basis": list(classification.basis),
        "outcome_failure": classification.outcome_failure,
        "source_locators": [row.as_dict() for row in source_locators],
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class EventNormalizer:
    """Normalize records in call order and allocate monotonically increasing IDs."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        start_sequence: int = 1,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], int] | None = None,
        classifiers: Iterable[Callable[[EventClassification], EventClassification]] = (),
    ) -> None:
        if not isinstance(start_sequence, int) or start_sequence < 1:
            raise ValueError("start_sequence must be a positive integer")
        # Retained as context for callers, but never used to resolve or mutate
        # source candidates parsed from untrusted process output.
        self.root = None if root is None else Path(root)
        self._next_sequence = start_sequence
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic_ns
        self._lock = threading.Lock()
        self._classifiers = tuple(classifiers)
        if not all(callable(classifier) for classifier in self._classifiers):
            raise TypeError("event classifiers must be callable")

    @property
    def next_sequence(self) -> int:
        with self._lock:
            return self._next_sequence

    def normalize(
        self,
        text: str,
        sequence: int | None = None,
        source: str = "process",
        stream: str = "stdout",
        raw_locator: RawLocator | Mapping[str, Any] | None = None,
        monotonic_ns: int | None = None,
        *,
        ingested_at: str | None = None,
        decode_had_errors: bool = False,
        utf8_from_previous_chunk: bool = False,
        utf8_continues: bool = False,
    ) -> ConsoleEvent:
        """Normalize one decoded record.

        Explicit sequences must remain increasing.  Omitting ``sequence`` lets
        a shared normalizer serialize interleaved stream decoders in append
        order.
        """

        if not isinstance(text, str):
            raise TypeError("event text must be a string")
        if not isinstance(source, str) or not _IDENTIFIER_RE.fullmatch(source):
            raise ValueError("event source must be an identifier-safe string")
        if not isinstance(stream, str) or not _STREAM_RE.fullmatch(stream):
            raise ValueError("event stream must be a bounded stream identifier")

        rendered, had_terminal_sequence, had_control_character = _sanitize_terminal(text)

        with self._lock:
            if sequence is None:
                assigned_sequence = self._next_sequence
            else:
                if not isinstance(sequence, int) or sequence != self._next_sequence:
                    raise ValueError(
                        "explicit event sequence must be the next append position"
                    )
                assigned_sequence = sequence
            self._next_sequence = assigned_sequence + 1
            captured_monotonic = (
                self._monotonic_clock() if monotonic_ns is None else monotonic_ns
            )
            if not isinstance(captured_monotonic, int) or captured_monotonic < 0:
                raise ValueError("monotonic_ns must be a non-negative integer")
            if ingested_at is None:
                moment = self._wall_clock()
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                ingested = moment.astimezone(timezone.utc).isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z")
            else:
                ingested = ingested_at

        locator = _coerce_raw_locator(
            raw_locator, text=text, default_line=assigned_sequence
        )
        classification = _classify(rendered)
        source_locators, source_locators_limited = _extract_source_locators(rendered)
        limitations: list[str] = []
        for classifier in self._classifiers:
            try:
                refined = classifier(classification)
                _validate_refinement(classification, refined)
                classification = refined
            except (Exception, SystemExit):
                # A broken optional annotation must never lose the raw stream
                # or break process supervision. Make the loss visible.
                if "owner-classifier-unavailable" not in limitations:
                    limitations.append("owner-classifier-unavailable")
        if had_terminal_sequence:
            limitations.append("terminal-control-sequences-removed")
        if had_control_character:
            limitations.append("control-characters-rendered-as-escapes")
        if decode_had_errors:
            limitations.append("invalid-utf8-rendered-as-byte-escapes")
        if locator.boundary == "limit" or locator.chunk > 1:
            limitations.append("logical-record-chunked-at-byte-limit")
        if utf8_from_previous_chunk or utf8_continues:
            limitations.append("utf8-codepoint-spans-record-chunks")
        if source_locators:
            limitations.append("source-locators-are-unresolved-candidates")
        if source_locators_limited:
            limitations.append("source-locator-candidates-truncated-to-contract-limit")
        if "normalize.parsed-label-truncated-v1" in classification.basis:
            limitations.append("parsed-label-truncated-to-contract-limit")

        key = _cluster_key(
            source=source,
            stream=stream,
            classification=classification,
            source_locators=source_locators,
        )
        return ConsoleEvent(
            format_version=FORMAT_VERSION,
            event_id=f"event-{assigned_sequence:012d}",
            sequence=assigned_sequence,
            ingested_at=ingested,
            monotonic_ns=captured_monotonic,
            source_timestamp=classification.source_timestamp,
            source=source,
            stream=stream,
            raw_locator=locator,
            kind=classification.kind,
            severity=classification.severity,
            subsystem=classification.subsystem,
            logger=classification.logger,
            thread=classification.thread,
            message=classification.message,
            parse_provenance=classification.parse_provenance,
            classification_basis=classification.basis,
            cluster_key=key,
            signal=classification.signal,
            outcome_failure=classification.outcome_failure,
            source_locators=source_locators,
            limitations=_ordered_unique(limitations),
        )

    def normalize_record(
        self,
        record: RawRecord,
        *,
        source: str = "process",
        stream: str = "stdout",
    ) -> ConsoleEvent:
        return self.normalize(
            record.text,
            source=source,
            stream=stream,
            raw_locator=record.locator,
            decode_had_errors=record.decode_had_errors,
            utf8_from_previous_chunk=record.utf8_from_previous_chunk,
            utf8_continues=record.utf8_continues,
        )

    def normalize_stage(
        self,
        stage: str,
        state: str,
        *,
        source: str = "workbench",
        stream: str = "system",
        raw_locator: RawLocator | Mapping[str, Any] | None = None,
        monotonic_ns: int | None = None,
    ) -> ConsoleEvent:
        """Create a schema-valid synthetic supervisor stage event."""

        if not isinstance(stage, str) or not _IDENTIFIER_RE.fullmatch(stage):
            raise ValueError("stage must be an identifier-safe string")
        normalized_state = state.casefold() if isinstance(state, str) else ""
        if normalized_state not in {
            "started",
            "running",
            "completed",
            "complete",
            "succeeded",
            "success",
            "failed",
            "blocked",
        }:
            raise ValueError("unknown Workbench stage state")
        if raw_locator is None:
            raw_locator = RawLocator(
                artifact=None,
                byte_start=0,
                byte_end=0,
                line=1,
                chunk=1,
                boundary="eof",
            )
        return self.normalize(
            f"[workbench] stage {stage} {normalized_state}",
            source=source,
            stream=stream,
            raw_locator=raw_locator,
            monotonic_ns=monotonic_ns,
        )


class IncrementalEventDecoder:
    """Incrementally frame bytes and normalize bounded append-order events."""

    def __init__(
        self,
        normalizer: EventNormalizer,
        source: str,
        stream: str,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
        *,
        raw_path: str | Path | None = None,
        start_offset: int = 0,
        start_line: int = 1,
        retain_raw_records: bool = False,
    ) -> None:
        if not isinstance(normalizer, EventNormalizer):
            raise TypeError("normalizer must be an EventNormalizer")
        if not isinstance(source, str) or not _IDENTIFIER_RE.fullmatch(source):
            raise ValueError("source must be an identifier-safe string")
        if not isinstance(stream, str) or not _STREAM_RE.fullmatch(stream):
            raise ValueError("stream must be a bounded stream identifier")
        if not isinstance(max_record_bytes, int) or max_record_bytes < 1:
            raise ValueError("max_record_bytes must be a positive integer")
        if not isinstance(start_offset, int) or start_offset < 0:
            raise ValueError("start_offset must be a non-negative integer")
        if not isinstance(start_line, int) or start_line < 1:
            raise ValueError("start_line must be a positive integer")
        if not isinstance(retain_raw_records, bool):
            raise TypeError("retain_raw_records must be a boolean")

        self.normalizer = normalizer
        self.source = source
        self.stream = stream
        self.max_record_bytes = max_record_bytes
        self._retain_raw_records = retain_raw_records
        self._artifact = None if raw_path is None else str(raw_path)
        self._buffer = bytearray()
        self._consume_offset = start_offset
        self._input_offset = start_offset
        self._line = start_line
        self._chunk = 1
        self._decoder = codecs.getincrementaldecoder("utf-8")(
            errors="surrogateescape"
        )
        self._closed = False
        self._retained_raw_records: list[RawRecord] = []
        self._last_raw_records: tuple[RawRecord, ...] = ()

    @property
    def pending_byte_count(self) -> int:
        return len(self._buffer)

    @property
    def last_raw_records(self) -> tuple[RawRecord, ...]:
        return self._last_raw_records

    def take_raw_records(self) -> list[RawRecord]:
        """Return exact records retained outside normalized event dictionaries.

        By default this returns records from the latest ``feed`` or ``finish``
        call, which keeps a long-running decoder bounded.  Construct with
        ``retain_raw_records=True`` when a caller explicitly needs an
        accumulating in-memory queue; production session writers ordinarily
        own the raw transcript already.
        """

        if self._retain_raw_records:
            records = self._retained_raw_records
            self._retained_raw_records = []
        else:
            records = list(self._last_raw_records)
            self._last_raw_records = ()
        return records

    # A discoverable synonym for queue-oriented consumers.
    drain_raw_records = take_raw_records

    def _set_location(
        self,
        raw_path: str | Path | None,
        offset: int | None,
    ) -> None:
        artifact = self._artifact if raw_path is None else str(raw_path)
        if artifact != self._artifact:
            if self._buffer:
                raise ValueError("cannot change raw_path while a record is buffered")
            self._artifact = artifact
            new_offset = 0 if offset is None else offset
            if not isinstance(new_offset, int) or new_offset < 0:
                raise ValueError("offset must be a non-negative integer")
            self._consume_offset = new_offset
            self._input_offset = new_offset
            return
        if offset is not None:
            if not isinstance(offset, int) or offset < 0:
                raise ValueError("offset must be a non-negative integer")
            if offset != self._input_offset:
                if self._input_offset == self._consume_offset and not self._buffer:
                    self._consume_offset = offset
                    self._input_offset = offset
                else:
                    raise ValueError(
                        "feed offset is not contiguous with previously supplied bytes"
                    )

    def _find_boundary(
        self, *, final: bool
    ) -> tuple[int, int | None, str | None] | None:
        for index, value in enumerate(self._buffer):
            if value == 0x0A:
                return index, 1, "lf"
            if value == 0x0D:
                if index + 1 < len(self._buffer):
                    if self._buffer[index + 1] == 0x0A:
                        return index, 2, "crlf"
                    return index, 1, "cr"
                if final:
                    return index, 1, "cr"
                return index, None, None
        return None

    def _emit(
        self,
        content_length: int,
        terminator_length: int,
        boundary: str,
    ) -> RawRecord:
        total_length = content_length + terminator_length
        raw_bytes = bytes(self._buffer[:total_length])
        content = bytes(self._buffer[:content_length])
        del self._buffer[:total_length]

        previous_state = self._decoder.getstate()[0]
        final_text = boundary != "limit"
        text = self._decoder.decode(content, final=final_text)
        following_state = self._decoder.getstate()[0] if not final_text else b""
        if final_text:
            self._decoder.reset()

        locator = RawLocator(
            artifact=self._artifact,
            byte_start=self._consume_offset,
            byte_end=self._consume_offset + total_length,
            line=self._line,
            chunk=self._chunk,
            boundary=boundary,
        )
        self._consume_offset += total_length
        if boundary == "limit":
            self._chunk += 1
        else:
            if boundary in {"lf", "crlf", "cr"}:
                self._line += 1
            self._chunk = 1

        record = RawRecord(
            raw_bytes=raw_bytes,
            text=text,
            locator=locator,
            decode_had_errors=any(0xDC80 <= ord(char) <= 0xDCFF for char in text),
            utf8_from_previous_chunk=bool(previous_state),
            utf8_continues=bool(following_state),
        )
        if self._retain_raw_records:
            self._retained_raw_records.append(record)
        return record

    def _drain(self, *, final: bool) -> list[RawRecord]:
        emitted: list[RawRecord] = []
        while self._buffer:
            found = self._find_boundary(final=final)
            if found is not None:
                content_length, terminator_length, boundary = found
                if content_length > self.max_record_bytes:
                    emitted.append(self._emit(self.max_record_bytes, 0, "limit"))
                    continue
                if terminator_length is None or boundary is None:
                    # A possible CRLF split across feed calls.  Its payload may
                    # still need chunking before we wait for the next byte.
                    if content_length > self.max_record_bytes:
                        emitted.append(
                            self._emit(self.max_record_bytes, 0, "limit")
                        )
                        continue
                    break
                emitted.append(
                    self._emit(content_length, terminator_length, boundary)
                )
                continue

            if len(self._buffer) > self.max_record_bytes:
                emitted.append(self._emit(self.max_record_bytes, 0, "limit"))
                continue
            if final:
                emitted.append(self._emit(len(self._buffer), 0, "eof"))
            break
        return emitted

    def _events_for(self, records: Iterable[RawRecord]) -> list[ConsoleEvent]:
        events: list[ConsoleEvent] = []
        for record in records:
            events.append(
                self.normalizer.normalize(
                    record.text,
                    source=self.source,
                    stream=self.stream,
                    raw_locator=record.locator,
                    decode_had_errors=record.decode_had_errors,
                    utf8_from_previous_chunk=record.utf8_from_previous_chunk,
                    utf8_continues=record.utf8_continues,
                )
            )
        return events

    def feed(
        self,
        data: bytes | bytearray | memoryview,
        raw_path: str | Path | None = None,
        offset: int | None = None,
    ) -> list[ConsoleEvent]:
        """Consume a byte fragment and return all newly complete events."""

        if self._closed:
            raise ValueError("cannot feed a finished event decoder")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("event decoder accepts bytes-like input")
        self._set_location(raw_path, offset)
        fragment = bytes(data)
        self._buffer.extend(fragment)
        self._input_offset += len(fragment)
        records = self._drain(final=False)
        self._last_raw_records = tuple(records)
        return self._events_for(records)

    def finish(self) -> list[ConsoleEvent]:
        """Flush a final unterminated record; subsequent calls are harmless."""

        if self._closed:
            self._last_raw_records = ()
            return []
        records = self._drain(final=True)
        self._closed = True
        self._last_raw_records = tuple(records)
        return self._events_for(records)


def _event_value(event: ConsoleEvent | Mapping[str, Any], field: str) -> Any:
    if isinstance(event, ConsoleEvent):
        return getattr(event, field)
    if isinstance(event, Mapping):
        return event[field]
    raise TypeError("expected a ConsoleEvent or event mapping")


def cluster_events(
    events: Iterable[ConsoleEvent | Mapping[str, Any]],
) -> list[EventCluster]:
    """Cluster exact normalized duplicates without dropping any members."""

    grouped: OrderedDict[str, list[ConsoleEvent | Mapping[str, Any]]] = OrderedDict()
    for event in events:
        key = _event_value(event, "cluster_key")
        if not isinstance(key, str) or not key:
            raise ValueError("every clustered event must have a cluster_key")
        grouped.setdefault(key, []).append(event)
    return [
        EventCluster(cluster_key=key, members=tuple(members))
        for key, members in grouped.items()
    ]


def _selection(values: Iterable[str] | str | None, label: str) -> set[str] | None:
    if values is None:
        return None
    source = (values,) if isinstance(values, str) else values
    selected: set[str] = set()
    for value in source:
        if not isinstance(value, str):
            raise TypeError(f"{label} filters must contain strings")
        selected.add(value.casefold())
    return selected


def event_matches(
    event: ConsoleEvent | Mapping[str, Any],
    search: str | None = None,
    severities: Iterable[str] | str | None = None,
    subsystems: Iterable[str] | str | None = None,
) -> bool:
    """Apply case-insensitive literal text and exact facet filters."""

    severity_filter = _selection(severities, "severity")
    subsystem_filter = _selection(subsystems, "subsystem")
    severity = str(_event_value(event, "severity")).casefold()
    subsystem = str(_event_value(event, "subsystem")).casefold()
    if severity_filter is not None and severity not in severity_filter:
        return False
    if subsystem_filter is not None and subsystem not in subsystem_filter:
        return False
    if search is None:
        return True
    if not isinstance(search, str):
        raise TypeError("literal event search must be a string or None")

    fields = [
        _event_value(event, "message"),
        _event_value(event, "source"),
        _event_value(event, "stream"),
        _event_value(event, "kind"),
        _event_value(event, "severity"),
        _event_value(event, "subsystem"),
    ]
    for optional in ("logger", "thread"):
        try:
            value = _event_value(event, optional)
        except KeyError:
            value = None
        if value is not None:
            fields.append(value)
    try:
        locators = _event_value(event, "source_locators")
    except KeyError:
        locators = ()
    for locator in locators:
        if isinstance(locator, SourceLocator):
            fields.extend((locator.path, locator.label))
        elif isinstance(locator, Mapping):
            fields.extend((locator.get("path", ""), locator.get("label", "")))
    haystack = "\n".join(str(field) for field in fields).casefold()
    return search.casefold() in haystack


def filter_events(
    events: Iterable[ConsoleEvent | Mapping[str, Any]],
    search: str | None = None,
    severities: Iterable[str] | str | None = None,
    subsystems: Iterable[str] | str | None = None,
) -> list[ConsoleEvent | Mapping[str, Any]]:
    """Return events matching :func:`event_matches`, preserving input order."""

    return [
        event
        for event in events
        if event_matches(
            event,
            search=search,
            severities=severities,
            subsystems=subsystems,
        )
    ]


# Explicitly named aliases for callers discovering the module by capability.
literal_filter = filter_events
IncrementalByteDecoder = IncrementalEventDecoder
LiveConsoleEvent = ConsoleEvent


__all__ = [
    "EventClassification",
    "BOUNDARIES",
    "ConsoleEvent",
    "DEFAULT_MAX_RECORD_BYTES",
    "EventCluster",
    "EventNormalizer",
    "FORMAT_VERSION",
    "IncrementalByteDecoder",
    "IncrementalEventDecoder",
    "KINDS",
    "LiveConsoleEvent",
    "MAX_PARSED_LABEL_CHARACTERS",
    "MAX_SOURCE_LOCATORS",
    "PARSE_PROVENANCE",
    "RawLocator",
    "RawRecord",
    "SEVERITIES",
    "SUBSYSTEMS",
    "SourceLocator",
    "cluster_events",
    "event_matches",
    "extract_source_locators",
    "filter_events",
    "literal_filter",
    "sanitize_terminal",
]
