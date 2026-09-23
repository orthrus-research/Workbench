"""Terminal, source-bound results for the complete validation command."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

from orchestration import new_run_id
from suite_execution import _atomic_write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class Invocation:
    def __init__(self, root: Path, result: Path | None, phases: tuple[str, ...], selection=None):
        self.run_id = new_run_id()
        self.path = result or root / ".workbench/validation/invocations" / f"{self.run_id}.json"
        if result is not None and self.path.resolve().is_relative_to(root.resolve()):
            relative = self.path.resolve().relative_to(root.resolve())
            ignored = subprocess.run(["git", "check-ignore", "--quiet", "--", str(relative)], cwd=root, check=False)
            if ignored.returncode != 0:
                raise OSError("result inside the checkout must use ignored diagnostic storage, such as .workbench/validation/")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # An explicit result path is a fresh invocation output, never a cache.
        with self.path.open("x", encoding="utf-8"):
            pass
        self.document = {
            "format": "workbench-validation-invocation-v1", "run_id": self.run_id,
            "state": "running", "started_at": now(), "source_fingerprint": None,
            "python_run_path": str(root / ".workbench/validation/runs" / self.run_id),
            "selection": selection,
            "phases": {name: {"state": "pending"} for name in phases},
        }
        self.started = time.perf_counter()
        self.write()

    def write(self):
        _atomic_write_json(self.path, self.document)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        for phase in self.document["phases"].values():
            if phase["state"] == "pending":
                phase.update(state="not-run", reason="An earlier stage failed or execution was interrupted.")
        passed = error is None and all(row["state"] == "passed" for row in self.document["phases"].values())
        self.document.update(state="passed" if passed else "failed", completed_at=now(), wall_seconds=time.perf_counter() - self.started)
        if error:
            self.document["error"] = f"{type(error).__name__}: {error}"
        self.write()
        if not passed and error is None:
            raise RuntimeError("Validation ended without every selected stage passing")
        return False

    @contextmanager
    def phase(self, name):
        row = self.document["phases"][name]
        if row["state"] != "pending":
            raise RuntimeError(f"Validation phase was already entered: {name}")
        row.update(state="running", started_at=now())
        started = time.perf_counter()
        self.write()
        try:
            yield
        except BaseException as error:
            row.update(state="failed", error=f"{type(error).__name__}: {error}")
            raise
        else:
            row["state"] = "passed"
        finally:
            row.update(completed_at=now(), wall_seconds=time.perf_counter() - started)
            self.write()
