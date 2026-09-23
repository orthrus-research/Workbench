"""Fail-closed primitives for concurrent repository validation runs.

This module deliberately does not own subprocess creation.  It provides the
identity, report, and scheduling contracts used by the validator so those
contracts can be tested without launching the full repository test matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
from typing import Iterable, Mapping, Sequence
from suite_measurement import inventory_digest


REPORT_FORMAT = "workbench-python-test-timing-v3"
LEGACY_REPORT_FORMAT = "workbench-python-test-timing-v2"
FINGERPRINT_FORMAT = "workbench-validation-source-v1"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
ALLOWED_TEST_OUTCOMES = frozenset(
    {
        "passed",
        "failed",
        "error",
        "skipped",
        "expected-failure",
        "unexpected-success",
        "not-run",
    }
)
SUCCESSFUL_TEST_OUTCOMES = frozenset(
    {"passed", "skipped", "expected-failure"}
)


class OrchestrationFailure(RuntimeError):
    """A validation run cannot safely accept its orchestration evidence."""


def validate_run_id(run_id: str) -> str:
    """Return a path-safe validation run identity or fail closed."""

    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise OrchestrationFailure(
            "validation run ID must match "
            "[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
        )
    if run_id in {".", ".."}:
        raise OrchestrationFailure("validation run ID may not be a path segment")
    return run_id


def new_run_id(
    *,
    now: datetime | None = None,
    entropy: str | None = None,
) -> str:
    """Create a sortable, cross-platform-safe validation run identity."""

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise OrchestrationFailure("validation run timestamp must be timezone-aware")
    instant = instant.astimezone(timezone.utc)
    suffix = entropy if entropy is not None else secrets.token_hex(4)
    if not isinstance(suffix, str) or not re.fullmatch(r"[0-9a-f]{8}", suffix):
        raise OrchestrationFailure(
            "validation run entropy must be eight lowercase hex digits"
        )
    return validate_run_id(instant.strftime("%Y%m%dT%H%M%S%fZ-") + suffix)


@dataclass(frozen=True)
class ValidationRunPaths:
    """The isolated storage owned by one validation invocation."""

    run_id: str
    root: Path
    reports: Path
    logs: Path
    temporary: Path

    def report_for(self, suite_name: str) -> Path:
        return self.reports / f"{_validate_token(suite_name, 'suite name')}.json"

    def log_for(self, suite_name: str) -> Path:
        return self.logs / f"{_validate_token(suite_name, 'suite name')}.log"

    def temporary_for(self, suite_name: str) -> Path:
        return self.temporary / _validate_token(suite_name, "suite name")


def create_run_paths(
    storage_root: Path,
    run_id: str,
    *,
    temporary_storage_root: Path | None = None,
) -> ValidationRunPaths:
    """Create a new run tree, refusing to reuse stale run-scoped evidence."""

    validated = validate_run_id(run_id)
    root = Path(storage_root) / validated
    temporary = (
        Path(temporary_storage_root) / validated
        if temporary_storage_root is not None
        else root / "tmp"
    )
    try:
        root.mkdir(parents=True, exist_ok=False)
        reports = root / "reports"
        logs = root / "logs"
        for path in (reports, logs):
            path.mkdir()
        temporary.mkdir(parents=True, mode=0o700, exist_ok=False)
        temporary.chmod(0o700)
    except FileExistsError as exc:
        raise OrchestrationFailure(
            f"validation run storage already exists for {validated}"
        ) from exc
    except OSError as exc:
        raise OrchestrationFailure(
            f"could not create validation run storage for {validated}: {exc}"
        ) from exc
    return ValidationRunPaths(validated, root, reports, logs, temporary)


def fingerprint_paths(root: Path, paths: Iterable[str | Path]) -> str:
    """Hash explicit regular files by normalized relative identity and bytes.

    The caller chooses the source boundary.  Missing files, duplicate paths,
    paths outside ``root``, directories, and symlinks are rejected rather than
    silently changing that boundary.
    """

    resolved_root = Path(root).resolve(strict=True)
    normalized: dict[str, Path] = {}
    for supplied in paths:
        relative, candidate = _source_path(resolved_root, supplied)
        if relative in normalized:
            raise OrchestrationFailure(
                f"source fingerprint path appears more than once: {relative}"
            )
        normalized[relative] = candidate

    digest = hashlib.sha256()
    digest.update(FINGERPRINT_FORMAT.encode("ascii") + b"\0")
    for relative in sorted(normalized):
        path = normalized[relative]
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OrchestrationFailure(
                f"source fingerprint path changed while it was read: {relative}"
            )
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def require_source_fingerprint(
    root: Path,
    paths: Iterable[str | Path],
    expected: str,
) -> None:
    """Reject a checkout whose selected source identity changed during a run."""

    observed = fingerprint_paths(root, paths)
    if observed != expected:
        raise OrchestrationFailure(
            "validation source fingerprint drifted: "
            f"expected {expected}, observed {observed}"
        )


@dataclass(frozen=True)
class TestTiming:
    test: str
    outcome: str
    seconds: float
    reason: str | None = None


@dataclass(frozen=True)
class SuiteReport:
    suite: str
    authority: str
    run_id: str
    source_fingerprint: str
    discovered_tests: int
    executed_tests: int
    successful: bool
    seconds: float
    tests: tuple[TestTiming, ...]
    document: Mapping[str, object] = field(default_factory=dict, repr=False, compare=False)


def load_suite_report(
    path: Path,
    *,
    expected_suite: str,
    expected_run_id: str,
    expected_source_fingerprint: str,
    expected_authority: str | None = None,
    not_before_ns: int | None = None,
    require_success: bool = True,
    expected_test_ids: Sequence[str] | None = None,
) -> SuiteReport:
    """Load and validate one run-bound timing report.

    Run identity, source identity, and optional file timestamp prevent a stale
    report from satisfying a new invocation.  Completeness checks ensure every
    discovered test has one terminal timing row.
    """

    report_path = Path(path)
    if report_path.is_symlink() or not report_path.is_file():
        raise OrchestrationFailure(
            f"suite report is not a regular file: {report_path}"
        )
    stat = report_path.stat()
    if not_before_ns is not None:
        if isinstance(not_before_ns, bool) or not isinstance(not_before_ns, int):
            raise OrchestrationFailure(
                "report freshness boundary must be integer nanoseconds"
            )
        if stat.st_mtime_ns < not_before_ns:
            raise OrchestrationFailure(
                f"suite report predates this validation run: {report_path}"
            )
    try:
        document = json.loads(
            report_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, OrchestrationFailure) as exc:
        if isinstance(exc, OrchestrationFailure):
            raise
        raise OrchestrationFailure(
            f"could not read suite report {report_path}: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise OrchestrationFailure(f"suite report must be a JSON object: {report_path}")

    required = {
        "format",
        "suite",
        "authority",
        "run_id",
        "source_fingerprint",
        "discovered_tests",
        "executed_tests",
        "successful",
        "seconds",
        "tests",
    }
    missing = sorted(required - document.keys())
    if missing:
        raise OrchestrationFailure(
            f"suite report is incomplete; missing fields: {', '.join(missing)}"
        )
    current = document.get("format") == REPORT_FORMAT
    if not current and (expected_test_ids is not None or document.get("format") != LEGACY_REPORT_FORMAT):
        _require_equal(document, "format", REPORT_FORMAT)
    _require_equal(document, "suite", expected_suite)
    _require_equal(document, "run_id", validate_run_id(expected_run_id))
    _require_equal(document, "source_fingerprint", expected_source_fingerprint)
    if expected_authority is not None:
        _require_equal(document, "authority", expected_authority)
    authority = document["authority"]
    if not isinstance(authority, str) or not authority:
        raise OrchestrationFailure("suite report authority must be a nonempty string")

    discovered = _nonnegative_integer(document, "discovered_tests")
    executed = _nonnegative_integer(document, "executed_tests")
    if discovered == 0:
        raise OrchestrationFailure("suite report discovered no tests")
    if executed > discovered or (not current and discovered != executed):
        raise OrchestrationFailure(
            "suite report is incomplete: "
            f"discovered {discovered}, executed {executed}"
        )
    successful = document["successful"]
    if not isinstance(successful, bool):
        raise OrchestrationFailure("suite report successful must be a boolean")
    seconds = _nonnegative_number(document, "seconds")

    rows = document["tests"]
    if not isinstance(rows, list):
        raise OrchestrationFailure("suite report tests must be an array")
    if len(rows) != (discovered if current else executed):
        raise OrchestrationFailure(
            "suite report timing rows are incomplete: "
            f"expected {executed}, found {len(rows)}"
        )
    timings: list[TestTiming] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise OrchestrationFailure(f"suite report tests[{index}] must be an object")
        missing_row = {"test", "outcome", "seconds"} - row.keys()
        if missing_row:
            raise OrchestrationFailure(
                f"suite report tests[{index}] is incomplete; missing "
                + ", ".join(sorted(missing_row))
            )
        test = row["test"]
        outcome = row["outcome"]
        if not isinstance(test, str) or not test:
            raise OrchestrationFailure(
                f"suite report tests[{index}].test must be a nonempty string"
            )
        if test in seen:
            raise OrchestrationFailure(f"suite report repeats test identity: {test}")
        seen.add(test)
        if not isinstance(outcome, str) or outcome not in ALLOWED_TEST_OUTCOMES:
            raise OrchestrationFailure(
                f"suite report has unsupported outcome for {test}: {outcome!r}"
            )
        row_seconds = _nonnegative_number(row, "seconds", prefix=f"tests[{index}].")
        reason = row.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise OrchestrationFailure(f"suite report reason for {test} must be a string")
        if current and outcome in {"skipped", "expected-failure", "not-run"} and not reason:
            raise OrchestrationFailure(f"suite report must explain {outcome} for {test}")
        timings.append(TestTiming(test, outcome, row_seconds, reason))

    outcome_success = all(
        timing.outcome in SUCCESSFUL_TEST_OUTCOMES for timing in timings
    )
    if current:
        collected = document.get("collected_ids")
        if (not isinstance(collected, list) or not all(isinstance(value, str) and value for value in collected)
                or len(set(collected)) != len(collected) or set(collected) != seen):
            raise OrchestrationFailure("suite report collected IDs disagree with terminal test IDs")
        if document.get("inventory_digest") != inventory_digest(collected):
            raise OrchestrationFailure("suite report inventory digest mismatch")
        if expected_test_ids is not None and (len(set(expected_test_ids)) != len(expected_test_ids) or set(expected_test_ids) != seen):
            raise OrchestrationFailure("suite report test IDs differ from the admitted inventory")
        if executed != sum(row.outcome != "not-run" for row in timings):
            raise OrchestrationFailure("suite report executed count disagrees with terminal rows")
        state = document.get("state")
        if state not in {"passed", "failed", "interrupted"}:
            raise OrchestrationFailure("suite report has an invalid terminal state")
        events = document.get("fixture_events")
        if not isinstance(events, list) or any(not isinstance(event, dict) or event.get("outcome") not in {"error", "skipped"}
                                              or not isinstance(event.get("reason"), str) for event in events):
            raise OrchestrationFailure("suite report has invalid fixture diagnostics")
        phases = document.get("phases")
        if not isinstance(phases, dict):
            raise OrchestrationFailure("suite report must contain phase measurements")
        for name, phase in phases.items():
            if not isinstance(phase, dict):
                raise OrchestrationFailure(f"invalid suite phase: {name}")
            _nonnegative_number(phase, "seconds")
            _nonnegative_integer(phase, "calls")
        outcome_success = outcome_success and not any(event["outcome"] == "error" for event in events) and state != "interrupted"
        if (state == "passed") != successful:
            raise OrchestrationFailure("suite report state disagrees with successful flag")
    if successful != outcome_success:
        raise OrchestrationFailure(
            "suite report successful flag disagrees with terminal test outcomes"
        )
    if require_success and not successful:
        raise OrchestrationFailure(f"suite report records failure: {expected_suite}")
    return SuiteReport(
        suite=expected_suite,
        authority=authority,
        run_id=expected_run_id,
        source_fingerprint=expected_source_fingerprint,
        discovered_tests=discovered,
        executed_tests=executed,
        successful=successful,
        seconds=seconds,
        tests=tuple(timings),
        document=document,
    )


def load_test_inventory(path: Path, *, expected_suite: str, expected_run_id: str,
                        expected_source_fingerprint: str, expected_authority: str) -> tuple[str, ...]:
    """Read the independent collection captured before suite execution."""
    if path.is_symlink() or not path.is_file():
        raise OrchestrationFailure(f"suite inventory is not a regular file: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise OrchestrationFailure(f"could not read suite inventory: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise OrchestrationFailure("suite inventory must be an object")
    for field, expected in (("format", "workbench-python-test-inventory-v1"), ("suite", expected_suite),
                            ("run_id", expected_run_id), ("source_fingerprint", expected_source_fingerprint),
                            ("authority", expected_authority)):
        _require_equal(document, field, expected)
    values = document.get("test_ids")
    if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values) or len(values) != len(set(values)):
        raise OrchestrationFailure("suite inventory must contain unique nonempty test IDs")
    if document.get("inventory_digest") != inventory_digest(values):
        raise OrchestrationFailure("suite inventory digest mismatch")
    return tuple(values)


@dataclass(frozen=True)
class SchedulingItem:
    """The concurrency-relevant contract for one registered suite."""

    name: str
    order: int
    estimated_seconds: float = 0.0
    exclusive: bool = False
    resource_locks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_token(self.name, "suite name")
        if (
            isinstance(self.order, bool)
            or not isinstance(self.order, int)
            or self.order < 0
        ):
            raise OrchestrationFailure(
                "suite scheduling order must be a nonnegative integer"
            )
        if not isinstance(self.exclusive, bool):
            raise OrchestrationFailure(
                "suite exclusive scheduling flag must be a boolean"
            )
        if (
            isinstance(self.estimated_seconds, bool)
            or not isinstance(self.estimated_seconds, (int, float))
            or not math.isfinite(float(self.estimated_seconds))
            or self.estimated_seconds < 0
        ):
            raise OrchestrationFailure(
                "suite estimated seconds must be finite and nonnegative"
            )
        if len(set(self.resource_locks)) != len(self.resource_locks):
            raise OrchestrationFailure(f"suite {self.name} repeats a resource lock")
        for lock in self.resource_locks:
            _validate_token(lock, "resource lock")


def select_runnable(
    pending: Sequence[SchedulingItem],
    active: Sequence[SchedulingItem],
    *,
    jobs: int,
) -> tuple[SchedulingItem, ...]:
    """Select the next deterministic launch set for a bounded worker pool.

    Catalog order is the tie-breaker.  An exclusive item is a barrier: once it
    reaches the head of the remaining catalog order, later work cannot pass it.
    Locked items may be bypassed by independent later work, avoiding an idle
    worker without weakening lock exclusion.
    """

    if isinstance(jobs, bool) or not isinstance(jobs, int) or jobs < 1:
        raise OrchestrationFailure("validation worker count must be a positive integer")
    _validate_scheduling_set((*pending, *active))
    ordered = tuple(sorted(pending, key=lambda item: (item.order, item.name)))
    running = tuple(sorted(active, key=lambda item: (item.order, item.name)))
    if len(running) >= jobs or any(item.exclusive for item in running):
        return ()
    if running and ordered and ordered[0].exclusive:
        return ()

    selected: list[SchedulingItem] = []
    occupied = {
        lock for item in running for lock in item.resource_locks
    }
    slots = jobs - len(running)
    for item in ordered:
        if len(selected) >= slots:
            break
        if item.exclusive:
            if running or selected:
                break
            return (item,)
        locks = set(item.resource_locks)
        if occupied.isdisjoint(locks):
            selected.append(item)
            occupied.update(locks)
    return tuple(selected)


def deterministic_batches(
    items: Sequence[SchedulingItem],
    *,
    jobs: int,
) -> tuple[tuple[SchedulingItem, ...], ...]:
    """Produce stable conservative waves for tests and static inspection."""

    remaining = list(items)
    batches: list[tuple[SchedulingItem, ...]] = []
    while remaining:
        selected = select_runnable(remaining, (), jobs=jobs)
        if not selected:  # Defensive: valid items always permit one launch.
            raise OrchestrationFailure("validation scheduler made no forward progress")
        batches.append(selected)
        selected_names = {item.name for item in selected}
        remaining = [item for item in remaining if item.name not in selected_names]
    return tuple(batches)


@dataclass(frozen=True)
class ProcessOutcome:
    """Terminal subprocess evidence with timeout kept distinct from exit code."""

    suite: str
    returncode: int | None
    elapsed_seconds: float
    timeout_seconds: int
    timed_out: bool = False

    def __post_init__(self) -> None:
        _validate_token(self.suite, "suite name")
        if self.returncode is not None and (
            isinstance(self.returncode, bool) or not isinstance(self.returncode, int)
        ):
            raise OrchestrationFailure(
                "suite return code must be an integer or null"
            )
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(float(self.elapsed_seconds))
            or self.elapsed_seconds < 0
        ):
            raise OrchestrationFailure(
                "suite elapsed seconds must be finite and nonnegative"
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
        ):
            raise OrchestrationFailure("suite timeout must be a positive integer")
        if not isinstance(self.timed_out, bool):
            raise OrchestrationFailure("suite timeout flag must be a boolean")

    @property
    def successful(self) -> bool:
        return not self.timed_out and self.returncode == 0


def require_successful_process(outcome: ProcessOutcome) -> None:
    """Reject timeout, missing exit status, and nonzero exit distinctly."""

    if outcome.timed_out:
        raise OrchestrationFailure(
            f"suite {outcome.suite} timed out after {outcome.timeout_seconds}s"
        )
    if outcome.returncode is None:
        raise OrchestrationFailure(
            f"suite {outcome.suite} ended without a process exit status"
        )
    if outcome.returncode != 0:
        raise OrchestrationFailure(
            f"suite {outcome.suite} exited with status {outcome.returncode}"
        )


def _source_path(root: Path, supplied: str | Path) -> tuple[str, Path]:
    path = Path(supplied)
    if path.is_absolute():
        candidate = path
    else:
        candidate = root / path
    try:
        relative_path = candidate.relative_to(root)
    except ValueError as exc:
        raise OrchestrationFailure(
            f"source fingerprint path escapes repository root: {supplied}"
        ) from exc
    if any(part in {"", ".", ".."} for part in relative_path.parts):
        raise OrchestrationFailure(f"invalid source fingerprint path: {supplied}")
    cursor = root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise OrchestrationFailure(
                f"source fingerprint path may not contain a symlink: {supplied}"
            )
    if not candidate.exists():
        raise OrchestrationFailure(
            f"source fingerprint path is missing: {supplied}"
        )
    if not candidate.is_file():
        raise OrchestrationFailure(
            f"source fingerprint path is not a regular file: {supplied}"
        )
    resolved = candidate.resolve(strict=True)
    try:
        canonical_relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise OrchestrationFailure(
            f"source fingerprint path escapes repository root: {supplied}"
        ) from exc
    return canonical_relative, resolved


def _validate_token(value: object, label: str) -> str:
    if not isinstance(value, str) or not TOKEN_PATTERN.fullmatch(value):
        raise OrchestrationFailure(
            f"{label} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,255}}"
        )
    if value in {".", ".."}:
        raise OrchestrationFailure(f"{label} may not be a path segment")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise OrchestrationFailure(f"suite report repeats JSON key: {key}")
        document[key] = value
    return document


def _require_equal(document: Mapping[str, object], key: str, expected: object) -> None:
    if document[key] != expected:
        raise OrchestrationFailure(
            f"suite report {key} mismatch: expected {expected!r}, "
            f"found {document[key]!r}"
        )


def _nonnegative_integer(document: Mapping[str, object], key: str) -> int:
    value = document[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OrchestrationFailure(f"suite report {key} must be a nonnegative integer")
    return value


def _nonnegative_number(
    document: Mapping[str, object],
    key: str,
    *,
    prefix: str = "",
) -> float:
    value = document[key]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise OrchestrationFailure(
            f"suite report {prefix}{key} must be finite and nonnegative"
        )
    return float(value)


def _validate_scheduling_set(items: Sequence[SchedulingItem]) -> None:
    names: set[str] = set()
    orders: set[int] = set()
    for item in items:
        if item.name in names:
            raise OrchestrationFailure(
                f"validation schedule repeats suite: {item.name}"
            )
        if item.order in orders:
            raise OrchestrationFailure(
                f"validation schedule repeats catalog order: {item.order}"
            )
        names.add(item.name)
        orders.add(item.order)
