"""Foreground Core supervision for an installed Supersymmetry Prism instance.

Prism owns account selection and game startup. Core verifies the installed
instance and pinned launcher, owns the foreground launcher process group, and
retains lifecycle records without capturing account-bound launcher output.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import signal as process_signal
import sys
from threading import Event, current_thread, main_thread
import time
from typing import Any, Mapping
from uuid import uuid4

from .durable_files import _directory as pinned_directory
from .durable_records import (
    private_record_lock, publish_create_once_bytes, read_private_single_link_bytes,
)
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .output_routing import _private_directory
from .pack_release_client_install import RECEIPT_FORMAT as INSTALL_RECEIPT_FORMAT
from .pack_release_client_install import _launcher as initialized_launcher
from .pack_release_local import _canonical, _object
from .render import Renderer
from .runner import RunResult, RunnerError, supervise_process
from .sessions import EphemeralSession
from .tooling_provision import ASSETS, PRISM_VERSION, ToolingProvisionError, host_key, inspect_tools


PLAN_FORMAT = "workbench-pack-release-client-launch-plan-v1"
REQUEST_FORMAT = "workbench-pack-release-client-launch-request-v1"
RESULT_FORMAT = "workbench-pack-release-client-launch-result-v1"
_PLAN_PREFIX = "workbench-pack-release-client-launch-plan:sha256:"
_INSTALL_PLAN_ID = re.compile(r"workbench-pack-release-client-install-plan:sha256:[0-9a-f]{64}\Z")
_PLAN_ID = re.compile(r"workbench-pack-release-client-launch-plan:sha256:[0-9a-f]{64}\Z")
_INSTANCE_ID = re.compile(r"workbench-supersymmetry-[A-Za-z0-9._-]{1,100}\Z")
_RECORD_LIMIT = 64 * 1024
_MODES = frozenset({"show", "launch"})
_INTERRUPT_GRACE_SECONDS = 30
_TERMINATE_GRACE_SECONDS = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@contextmanager
def _sigterm_cancellation(cancelled: Event):
    """Turn a CLI SIGTERM into supervisor cancellation on its main thread.

    Library callers on worker threads can provide their own cancellation event.
    The prior process handler is restored after the launch receipt is written.
    """

    if current_thread() is not main_thread():
        yield
        return
    previous = process_signal.getsignal(process_signal.SIGTERM)

    def request_cancel(signum: int, frame: object) -> None:
        del signum, frame
        cancelled.set()

    process_signal.signal(process_signal.SIGTERM, request_cancel)
    try:
        yield
    finally:
        process_signal.signal(process_signal.SIGTERM, previous)


def _install_receipt(state_root: Path, launcher_root: Path,
                     expected_install_plan_id: str) -> tuple[dict[str, Any], str]:
    if (type(expected_install_plan_id) is not str
            or _INSTALL_PLAN_ID.fullmatch(expected_install_plan_id) is None):
        raise ValueError("select an exact installed Supersymmetry plan ID")
    path = (state_root / "pack-release-client-installs" /
            expected_install_plan_id.rsplit(":", 1)[-1] / "receipt.json")
    raw = read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT)
    receipt = _object(raw, "release client install receipt")
    instance_text = receipt.get("instance_path")
    if type(instance_text) is not str:
        raise ValueError("release client installation has no instance path")
    instance = Path(instance_text)
    instance_id = instance.name
    if (receipt.get("format") != INSTALL_RECEIPT_FORMAT
            or receipt.get("schema_version") != 1
            or receipt.get("plan_id") != expected_install_plan_id
            or receipt.get("installation_state") != "installed"
            or receipt.get("runtime_qualification_state") != "not-qualified"
            or _INSTANCE_ID.fullmatch(instance_id) is None
            or instance != launcher_root / "instances" / instance_id
            or raw != _canonical(receipt) + b"\n"):
        raise ValueError("release client installation receipt is unavailable or changed")
    directory = pinned_directory(instance, create=False)
    os.close(directory)
    marker = read_private_single_link_bytes(
        instance / ".workbench-release-install.json", byte_limit=_RECORD_LIMIT,
    )
    if marker != raw:
        raise ValueError("release client instance differs from its Core install receipt")
    for name in ("instance.cfg", "mmc-pack.json"):
        selected = instance / name
        if selected.is_symlink() or not selected.is_file():
            raise ValueError("release client instance lacks Prism launcher metadata")
    return receipt, "sha256:" + sha256(raw).hexdigest()


def plan_release_client_launch(
    *, state_root: Path, launcher_root: Path, expected_install_plan_id: str,
    mode: str = "show",
) -> dict[str, Any]:
    """Review exact installed instance and verified managed Prism executable."""

    if (sys.platform != "linux" or os.name != "posix"
            or type(mode) is not str or mode not in _MODES
            or not isinstance(state_root, Path) or not state_root.is_absolute()
            or not isinstance(launcher_root, Path) or not launcher_root.is_absolute()
            or _mount_type(state_root) not in _SUPPORTED_FILESYSTEMS
            or not initialized_launcher(launcher_root)):
        raise ValueError("release client launch needs an initialized Linux Prism root")
    selected = host_key()
    if selected != "linux-x64":
        raise ValueError("release client launch needs managed Linux x64 Prism")
    receipt, receipt_digest = _install_receipt(
        state_root, launcher_root, expected_install_plan_id,
    )
    try:
        tool = inspect_tools(state_root, key=selected)["tools"]["prism"]
    except (OSError, ValueError, ToolingProvisionError) as exc:
        raise ValueError("managed Prism launcher is unavailable") from exc
    executable_text = tool.get("executable")
    executable = Path(executable_text) if type(executable_text) is str else None
    if (tool.get("state") != "ready" or executable is None
            or not executable.is_absolute() or executable.is_symlink()
            or not executable.is_file() or not os.access(executable, os.X_OK)):
        raise ValueError("managed Prism launcher is unavailable")
    instance = Path(receipt["instance_path"])
    body = {
        "format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "mode": mode, "install_plan_id": expected_install_plan_id,
        "install_receipt_sha256": receipt_digest,
        "instance_id": instance.name, "instance_path": str(instance),
        "launcher_root": str(launcher_root),
        "prism_executable": str(executable), "prism_version": PRISM_VERSION,
        "prism_archive_sha256": "sha256:" + ASSETS[selected]["prism"]["sha256"],
        "account_handling": "prism", "runtime_qualification_state": "not-qualified",
    }
    return {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}


class _LaunchControl(Renderer):
    def __init__(self, cancelled: Event, timeout_seconds: float | None) -> None:
        self.cancelled = cancelled
        self.deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        self.cancellation_requested = False
        self.force_requested = False

    def consume(self, event: Mapping[str, Any]) -> None:
        del event

    def pulse(self) -> None:
        if self.cancelled.is_set() or (
                self.deadline is not None and time.monotonic() >= self.deadline):
            self.cancellation_requested = True


def run_release_client_launch(
    *, state_root: Path, launcher_root: Path, expected_install_plan_id: str,
    expected_launch_plan_id: str, mode: str = "show", cancelled: Event | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Supervise Prism in the foreground; return after its launcher process exits.

    A zero exit is a launcher outcome, not proof that Minecraft started or that
    the game process ended. Prism may move game work outside its process group.
    """

    if (type(expected_launch_plan_id) is not str
            or _PLAN_ID.fullmatch(expected_launch_plan_id) is None
            or timeout_seconds is not None
            and (type(timeout_seconds) not in {int, float}
                 or not 0 < timeout_seconds <= 24 * 60 * 60)):
        raise ValueError("select an exact reviewed launcher plan and timeout")
    signal = Event() if cancelled is None else cancelled
    if not isinstance(signal, Event):
        raise ValueError("launch cancellation must be a threading event")
    if signal.is_set():
        raise ValueError("Prism launch was cancelled before review")
    reviewed = plan_release_client_launch(
        state_root=state_root, launcher_root=launcher_root,
        expected_install_plan_id=expected_install_plan_id, mode=mode,
    )
    if reviewed["plan_id"] != expected_launch_plan_id:
        raise ValueError("Prism launch inputs changed after review")
    evidence_root = state_root / "pack-release-client-launches"
    _private_directory(evidence_root)
    lock_path = evidence_root / ("." + expected_install_plan_id.rsplit(":", 1)[-1] + ".lock")
    with _sigterm_cancellation(signal), private_record_lock(lock_path):
        plan = plan_release_client_launch(
            state_root=state_root, launcher_root=launcher_root,
            expected_install_plan_id=expected_install_plan_id, mode=mode,
        )
        if plan != reviewed:
            raise ValueError("Prism launch inputs changed before execution")
        if signal.is_set():
            raise ValueError("Prism launch was cancelled before execution")
        launch_id = uuid4().hex
        directory = evidence_root / launch_id
        _private_directory(directory)
        request = {
            "format": REQUEST_FORMAT, "schema_version": 1,
            "launch_id": launch_id, "launch_plan_id": expected_launch_plan_id,
            "install_plan_id": expected_install_plan_id,
            "mode": mode, "instance_id": plan["instance_id"],
            "prism_version": plan["prism_version"],
            "prism_archive_sha256": plan["prism_archive_sha256"],
            "prism_executable": plan["prism_executable"],
            "requested_at": _now(), "account_handling": "prism",
        }
        publish_create_once_bytes(
            directory / "requested.json", _canonical(request) + b"\n",
            byte_limit=_RECORD_LIMIT,
        )
        command = [plan["prism_executable"], "--dir", plan["launcher_root"],
                   "--show" if mode == "show" else "--launch", plan["instance_id"]]
        outcome: dict[str, Any]
        try:
            observed: RunResult = supervise_process(
                command, cwd=Path(plan["prism_executable"]).parent,
                root=Path(plan["prism_executable"]).parent,
                session=EphemeralSession(), renderer=_LaunchControl(signal, timeout_seconds),
                source="managed-prism-launcher", environment=os.environ,
                capture_output=True, output_mode="raw", merged_output=True,
                require_group_closure=False,
                interrupt_grace_seconds=_INTERRUPT_GRACE_SECONDS,
                terminate_grace_seconds=_TERMINATE_GRACE_SECONDS,
            )
            outcome = {"outcome": observed.outcome,
                       "launcher_exit_code": observed.process_exit_code,
                       "effective_exit_code": observed.effective_exit_code,
                       "cancellation": observed.cancellation}
        except (OSError, RunnerError):
            outcome = {"outcome": "supervisor-failed", "launcher_exit_code": None,
                       "effective_exit_code": None, "cancellation": None}
            completion = {**request, **outcome, "format": RESULT_FORMAT,
                          "completed_at": _now(),
                          "runtime_qualification_state": "not-qualified",
                          "game_lifecycle_state": "unobserved",
                          "supervision_scope": "foreground-prism-invocation"}
            publish_create_once_bytes(directory / "completed.json",
                                      _canonical(completion) + b"\n",
                                      byte_limit=_RECORD_LIMIT)
            raise
        completion = {**request, **outcome, "format": RESULT_FORMAT,
                      "completed_at": _now(),
                      "runtime_qualification_state": "not-qualified",
                      "game_lifecycle_state": "unobserved",
                      "supervision_scope": "foreground-prism-invocation"}
        publish_create_once_bytes(directory / "completed.json",
                                  _canonical(completion) + b"\n",
                                  byte_limit=_RECORD_LIMIT)
    return {**completion, "receipt_path": str(directory / "completed.json")}


__all__ = ["plan_release_client_launch", "run_release_client_launch"]
