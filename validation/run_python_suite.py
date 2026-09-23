#!/usr/bin/env python3

"""Run one registered Python suite and retain per-test timing evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path

from orchestration import REPORT_FORMAT
from suite_catalog import ROOT, SUITES_BY_NAME, PythonTestSuite
from suite_measurement import PhaseClock, environment_provenance, inventory_digest, measure_tests

REPORT_ROOT = ROOT / ".workbench/validation/test-timings"
SLOW_TEST_SECONDS = 0.5
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SOURCE_FINGERPRINT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
)


class TimingResult(unittest.TextTestResult):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._started: dict[str, float] = {}
        self._outcomes: dict[str, str] = {}
        self.timings: list[dict[str, object]] = []
        self.reasons: dict[str, str] = {}
        self.fixture_events: list[dict[str, str]] = []
        self.subtests: list[dict[str, str]] = []

    def startTest(self, test: unittest.case.TestCase) -> None:
        self._started[test.id()] = time.perf_counter()
        super().startTest(test)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        self._outcomes.setdefault(test.id(), "passed")
        super().addSuccess(test)

    def addFailure(self, test: unittest.case.TestCase, err: object) -> None:
        self._outcomes[test.id()] = "failed"
        super().addFailure(test, err)

    def addError(self, test: unittest.case.TestCase, err: object) -> None:
        self._outcomes[test.id()] = "error"
        if test.id() not in self._started:
            self.fixture_events.append({"test": test.id(), "outcome": "error", "reason": self._exc_info_to_string(err, test)})
        super().addError(test, err)

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        if isinstance(test, unittest.case._SubTest):
            self._outcomes.setdefault(test.test_case.id(), "passed")
            self.subtests.append({"test": test.test_case.id(), "subtest": test.id(), "outcome": "skipped", "reason": reason})
        else:
            self._outcomes[test.id()] = "skipped"
            self.reasons[test.id()] = reason
            if test.id() not in self._started:
                self.fixture_events.append({"test": test.id(), "outcome": "skipped", "reason": reason})
        super().addSkip(test, reason)

    def addExpectedFailure(self, test: unittest.case.TestCase, err: object) -> None:
        self._outcomes[test.id()] = "expected-failure"
        self.reasons[test.id()] = self._exc_info_to_string(err, test)
        super().addExpectedFailure(test, err)

    def addSubTest(self, test, subtest, err) -> None:
        if err is not None:
            outcome = "failed" if issubclass(err[0], test.failureException) else "error"
            self._outcomes[test.id()] = outcome
            self.subtests.append({"test": test.id(), "subtest": subtest.id(), "outcome": outcome, "reason": self._exc_info_to_string(err, test)})
        super().addSubTest(test, subtest, err)

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:
        self._outcomes[test.id()] = "unexpected-success"
        super().addUnexpectedSuccess(test)

    def stopTest(self, test: unittest.case.TestCase) -> None:
        test_id = test.id()
        started = self._started.pop(test_id, time.perf_counter())
        self.timings.append(
            {
                "test": test_id,
                "outcome": self._outcomes.pop(test_id, "error"),
                "seconds": round(time.perf_counter() - started, 6),
                **({"reason": self.reasons.pop(test_id)} if test_id in self.reasons else {}),
            }
        )
        super().stopTest(test)


def _configure_suite(name: str, *, temporary_root: Path | None = None) -> None:
    suite = SUITES_BY_NAME[name]
    # ``python -m unittest`` historically put the repository root on sys.path.
    # Preserve that contract when running through this script so tests may
    # import repository-owned tools without relying on the caller's shell.
    paths = [str(ROOT), str(ROOT / "api/src"), str(ROOT / "core/src"), str(ROOT / "modules/material-semantics/src"), *(str(ROOT / value) for value in suite.python_paths)]
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    if name != "core-api":
        from workbench_core.host_services import install_local_host_services
        install_local_host_services()
    if name not in {"core-api", "core"}:
        # Product/profile suites explicitly exercise the source manifests as
        # native distributions. API/Core suites remain profile-independent.
        from workbench_core.development import enable_source_checkout
        enable_source_checkout(ROOT)
    existing = os.environ.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    if paths:
        os.environ["PYTHONPATH"] = os.pathsep.join(paths)
    temporary = temporary_root
    if temporary is None and suite.repository_temp:
        temporary = ROOT / ".workbench/tmp"
    if temporary is not None:
        temporary.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary.chmod(0o700)
        for variable in ("TMPDIR", "TMP", "TEMP"):
            os.environ[variable] = str(temporary)
        # tempfile may have resolved its directory during suite configuration.
        tempfile.tempdir = None


def _run_id_argument(value: str) -> str:
    if RUN_ID_PATTERN.fullmatch(value) is None or value in {".", ".."}:
        raise argparse.ArgumentTypeError(
            "must match [A-Za-z0-9][A-Za-z0-9._-]{0,127} and may not be "
            "'.' or '..'"
        )
    return value


def _source_fingerprint_argument(value: str) -> str:
    if SOURCE_FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "must be 1 to 256 characters and contain only letters, numbers, "
            "periods, underscores, colons, or hyphens"
        )
    return value


def _path_argument(value: str) -> Path:
    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return Path(value).expanduser()


def _validate_output_paths(
    parser: argparse.ArgumentParser,
    *,
    report: Path | None,
    temporary_root: Path | None,
) -> None:
    if report is not None:
        if report.exists() and report.is_dir():
            parser.error(f"--report must name a file, not a directory: {report}")
        if report.parent.exists() and not report.parent.is_dir():
            parser.error(f"--report parent is not a directory: {report.parent}")
    if temporary_root is not None and temporary_root.exists():
        if not temporary_root.is_dir():
            parser.error(
                "--temporary-root must name a directory, not a file: "
                f"{temporary_root}"
            )


def _write_report(
    name: str,
    result: TimingResult,
    *,
    discovered: int,
    elapsed: float,
    report: Path | None = None,
    run_id: str,
    source_fingerprint: str,
    collected_ids: tuple[str, ...] | None = None,
    phases: PhaseClock | None = None,
    state: str | None = None,
) -> Path:
    target = report if report is not None else REPORT_ROOT / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = list(result.timings)
    if collected_ids is not None:
        recorded = {row["test"] for row in rows}
        active = getattr(result, "_started", {})
        rows.extend({"test": test_id, "outcome": "error" if test_id in active else "not-run", "seconds": 0.0,
                     "reason": "Execution was interrupted before a terminal result." if test_id in active else "Execution did not reach this collected test; inspect fixture/stage diagnostics."}
                    for test_id in collected_ids if test_id not in recorded)
    successful = (state not in {"failed", "interrupted"} and result.wasSuccessful()
                  and all(row["outcome"] in {"passed", "skipped", "expected-failure"} for row in rows))
    document = {
        "format": REPORT_FORMAT,
        "suite": name,
        "authority": SUITES_BY_NAME[name].authority,
        "run_id": run_id,
        "source_fingerprint": source_fingerprint,
        "discovered_tests": discovered,
        "executed_tests": result.testsRun,
        "successful": successful,
        "state": state or ("passed" if successful else "failed"),
        "seconds": round(elapsed, 6),
        "tests": sorted(rows, key=lambda row: str(row["test"])),
        "fixture_events": getattr(result, "fixture_events", []),
        "subtests": getattr(result, "subtests", []),
        "phases": phases.document() if phases is not None else {},
        "environment": environment_provenance(ROOT),
        "collected_ids": list(collected_ids) if collected_ids is not None else sorted(row["test"] for row in rows),
    }
    document["inventory_digest"] = inventory_digest(document["collected_ids"])
    _write_json(target, document)
    return target


def _write_json(target: Path, document: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_inventory(name: str, test_ids: tuple[str, ...], report: Path, *, run_id: str, source_fingerprint: str) -> Path:
    """Seal the collection before invoking tests, separate from terminal rows."""
    if len(set(test_ids)) != len(test_ids):
        raise ValueError("suite collection repeats a test ID")
    target = report.with_suffix(".inventory.json")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to replace a previously admitted inventory: {target}")
    _write_json(target, {
        "format": "workbench-python-test-inventory-v1",
        "suite": name,
        "authority": SUITES_BY_NAME[name].authority,
        "run_id": run_id,
        "source_fingerprint": source_fingerprint,
        "test_ids": sorted(test_ids),
        "inventory_digest": inventory_digest(test_ids),
    })
    return target


def _await_admission(path: Path, *, test_ids: tuple[str, ...], run_id: str, source_fingerprint: str) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("scheduler did not admit the collected inventory")
        time.sleep(0.01)
    document = json.loads(path.read_text(encoding="utf-8"))
    expected = {"format": "workbench-python-test-admission-v1", "run_id": run_id,
                "source_fingerprint": source_fingerprint, "test_ids": sorted(test_ids),
                "inventory_digest": inventory_digest(test_ids)}
    if document != expected:
        raise RuntimeError("scheduler admission differs from the collected inventory")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _tests_in(suite: unittest.TestSuite) -> list[unittest.case.TestCase]:
    tests: list[unittest.case.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(_tests_in(item))
        else:
            tests.append(item)
    return tests


def _discover_tests(selected: PythonTestSuite) -> unittest.TestSuite:
    """Discover only the exact test files assigned by the suite catalog."""

    discovered = unittest.TestSuite()
    for test_file in selected.test_files():
        discovered.addTests(
            unittest.defaultTestLoader.discover(
                str(selected.path),
                pattern=test_file.name,
            )
        )
    return discovered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=tuple(SUITES_BY_NAME))
    parser.add_argument("--admission-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="import and count the suite without executing test cases",
    )
    parser.add_argument(
        "--report",
        type=_path_argument,
        help="write timing evidence to PATH instead of the per-suite report location",
    )
    parser.add_argument(
        "--temporary-root",
        type=_path_argument,
        help="set TMPDIR, TMP, and TEMP to PATH for this suite",
    )
    parser.add_argument(
        "--run-id",
        type=_run_id_argument,
        help="record the orchestrated validation run token in timing evidence",
    )
    parser.add_argument(
        "--source-fingerprint",
        type=_source_fingerprint_argument,
        help="record the validated source fingerprint in timing evidence",
    )
    args = parser.parse_args()
    if (args.run_id is None) != (args.source_fingerprint is None):
        parser.error("--run-id and --source-fingerprint must be provided together")
    if not args.collect_only and args.run_id is None:
        parser.error(
            "--run-id and --source-fingerprint are required when executing a suite"
        )
    _validate_output_paths(
        parser,
        report=args.report,
        temporary_root=args.temporary_root,
    )
    selected = SUITES_BY_NAME[args.suite]
    phases = PhaseClock()
    try:
        with phases.span("configuration"):
            _configure_suite(selected.name, temporary_root=args.temporary_root)
    except OSError as exc:
        parser.error(f"could not prepare suite temporary directory: {exc}")
    with phases.span("import_collection"):
        discovered = _discover_tests(selected)
    count = discovered.countTestCases()
    if count == 0:
        print(f"TEST SUITE FAILED: {selected.name} discovered no tests", file=sys.stderr)
        return 1
    failed_imports = [
        test
        for test in _tests_in(discovered)
        if type(test).__module__ == "unittest.loader"
        and type(test).__name__ == "_FailedTest"
    ]
    if failed_imports:
        for failed in failed_imports:
            exception = getattr(failed, "_exception", "unknown import failure")
            print(
                f"TEST SUITE COLLECTION FAILED: {failed.id()}: {exception}",
                file=sys.stderr,
            )
        return 1
    if args.collect_only:
        print(f"[{selected.name}] collected {count} tests without import errors.")
        return 0

    collected_ids = tuple(sorted(test.id() for test in _tests_in(discovered)))
    target = args.report if args.report is not None else REPORT_ROOT.parent / "runs" / args.run_id / "reports" / f"{selected.name}.json"
    assert args.run_id is not None and args.source_fingerprint is not None
    _write_inventory(selected.name, collected_ids, target,
                     run_id=args.run_id, source_fingerprint=args.source_fingerprint)
    if args.admission_file is not None:
        _await_admission(args.admission_file, test_ids=collected_ids,
                         run_id=args.run_id, source_fingerprint=args.source_fingerprint)

    print(
        f"[{selected.name}] {selected.authority}: {selected.purpose}",
        flush=True,
    )
    started = time.perf_counter()
    runner = unittest.TextTestRunner(verbosity=1, resultclass=TimingResult)
    result = runner._makeResult()
    runner._makeResult = lambda: result
    interrupted = False
    try:
        with measure_tests(discovered, phases), phases.span("runner_overhead"):
            runner.run(discovered)
    except BaseException as exc:
        interrupted = True
        result.fixture_events.append({"test": "suite-execution", "outcome": "error", "reason": f"{type(exc).__name__}: {exc}"})
        traceback.print_exc()
    elapsed = time.perf_counter() - started
    assert isinstance(result, TimingResult)
    assert args.run_id is not None and args.source_fingerprint is not None
    report = _write_report(
        selected.name,
        result,
        discovered=count,
        elapsed=elapsed,
        report=target,
        run_id=args.run_id,
        source_fingerprint=args.source_fingerprint,
        collected_ids=collected_ids,
        phases=phases,
        state="interrupted" if interrupted else None,
    )
    slow = sorted(
        (
            row
            for row in result.timings
            if float(row["seconds"]) >= SLOW_TEST_SECONDS
        ),
        key=lambda row: float(row["seconds"]),
        reverse=True,
    )[:10]
    if slow:
        print(f"[{selected.name}] slowest tests (>= {SLOW_TEST_SECONDS:.1f}s):")
        for row in slow:
            print(f"  {float(row['seconds']):9.3f}s  {row['test']}")
    print(
        f"[{selected.name}] {result.testsRun}/{count} executed in {elapsed:.3f}s; "
        f"timings: {_display_path(report)}",
        flush=True,
    )
    return 0 if not interrupted and result.wasSuccessful() and len(result.timings) == count else 1


if __name__ == "__main__":
    raise SystemExit(main())
