"""Observe unittest phases without replacing discovery or fixture semantics."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import unittest

# unittest removes framework frames when formatting assertion tracebacks. Our
# wrappers must be skipped too, or the next unittest hook hides the test frame.
__unittest = True


class PhaseClock:
    """Accumulate exclusive phase time, subtracting nested observations."""

    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.seconds: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self._stack: list[list[float]] = []

    @contextmanager
    def span(self, name: str):
        frame = [self.clock(), 0.0]
        self._stack.append(frame)
        try:
            yield
        finally:
            elapsed = self.clock() - frame[0]
            self._stack.pop()
            if self._stack:
                self._stack[-1][1] += elapsed
            self.seconds[name] = self.seconds.get(name, 0.0) + max(0.0, elapsed - frame[1])
            self.calls[name] = self.calls.get(name, 0) + 1

    def document(self):
        return {
            name: {"seconds": round(seconds, 6), "calls": self.calls[name]}
            for name, seconds in sorted(self.seconds.items())
        }


@contextmanager
def measure_tests(suite: unittest.TestSuite, clock: PhaseClock):
    """Wrap instance hooks temporarily; retain custom suite/case behavior.

    unittest's module transition hook may itself tear down the previous module
    and class. Exclusive timing prevents those nested hooks being counted twice.
    Per-test ``seconds`` continues to include that test's setup and cleanup.
    """
    saved = []
    absent = object()

    def instrument(value):
        if isinstance(value, unittest.TestSuite):
            hooks = {
                "_handleModuleFixture": "module_setup",
                "_handleModuleTearDown": "module_teardown_cleanup",
                "_handleClassSetUp": "class_setup",
                "_tearDownPreviousClass": "class_teardown_cleanup",
            }
            for child in value:
                instrument(child)
        else:
            hooks = {
                "_callSetUp": "test_setup",
                "_callTestMethod": "test_body",
                "_callTearDown": "test_teardown",
                "doCleanups": "test_cleanup",
            }
        for attribute, phase in hooks.items():
            original = getattr(value, attribute, None)
            if original is None:
                continue
            saved.append((value, attribute, value.__dict__.get(attribute, absent)))

            @wraps(original)
            def measured(*args, _original=original, _phase=phase, **kwargs):
                with clock.span(_phase):
                    return _original(*args, **kwargs)

            setattr(value, attribute, measured)

    try:
        instrument(suite)
        yield
    finally:
        for value, attribute, original in reversed(saved):
            if original is absent:
                delattr(value, attribute)
            else:
                setattr(value, attribute, original)


def inventory_digest(test_ids) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(sorted(test_ids), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def environment_provenance(root: Path) -> dict:
    """Record diagnostic inputs, never credentials or the whole environment."""
    packages = {}
    for name in ("packaging", "PyYAML", "jsonschema", "referencing"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    inputs = {}
    for name in ("pixi.toml", "pixi.lock"):
        path = root / name
        if path.is_file():
            inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    host_tools = {}
    for name in ("git", "node"):
        executable = shutil.which(name)
        observation = {"executable": executable, "version": None}
        if executable:
            try:
                result = subprocess.run([executable, "--version"], text=True, capture_output=True, timeout=5, check=False)
                observation.update(version=result.stdout.strip()[:256], exit_code=result.returncode)
            except (OSError, subprocess.TimeoutExpired) as error:
                observation["error"] = type(error).__name__
        host_tools[name] = observation
    return {
        "python": {"executable": sys.executable, "version": platform.python_version()},
        "platform": {"system": platform.system(), "machine": platform.machine(), "release": platform.release()},
        "packages": packages,
        "host_tools": host_tools,
        "declared_environment_sha256": inputs,
        "pixi_environment": os.environ.get("PIXI_ENVIRONMENT_NAME"),
        "cache_conditions": "Existing tool caches retained; no cold-cache claim.",
        "physical_fixture_configured": all(os.environ.get(key) for key in (
            "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW", "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME"
        )),
    }
