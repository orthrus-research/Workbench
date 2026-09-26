"""Source-available Core host for retained repository validation runs.

Validation starts from the source checkout, including when optional modules or
the installed Workbench environment are unavailable. Keep that composition
here; the validation scheduler still owns suite admission and result meaning.
"""

from __future__ import annotations

from pathlib import Path
import re
import stat
import sys


_SUITE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _source_core() -> None:
    source_root = Path(__file__).resolve().parents[1]
    for source in (source_root / "api/src", source_root / "core/src"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))


def allocate_validation_run(root: Path, run_id: str):
    """Register a fresh run at its historical path before exposing the tree."""

    _source_core()

    from workbench_core.host_filesystem import secure_private_path
    from workbench_core.output_routing import _private_directory
    from workbench_core.user_config_home import default_user_config_home
    from workbench_core.working_allocations import CoreWorkingAllocations

    selected_root = Path(root).resolve(strict=True)
    runs = selected_root / ".workbench/validation/runs"
    _private_directory(runs)
    # Older validation runs used a 0755 parent. Protect the parent in place;
    # its historical children and their exact paths remain unchanged.
    secure_private_path(runs, directory=True)
    host = CoreWorkingAllocations(
        workspace=selected_root,
        configuration_home=default_user_config_home(),
        locations={"evidence": runs},
        location_sources={"evidence": "repository-validation"},
        owner_id="validation",
    )
    allocation = host.allocate(
        "python-suite-run", run_id, requested_path=runs / run_id,
    )
    return host, allocation


def allocate_validation_scratch(
    root: Path, run_id: str, *, temporary_storage_root: Path,
):
    """Lease the historical external and repository suite scratch roots."""

    _source_core()
    from workbench_core.temporary_leases import CoreTemporaryLeases
    from workbench_core.user_config_home import default_user_config_home

    selected_root = Path(root).resolve(strict=True)
    external = Path(temporary_storage_root).absolute()
    retained_run = selected_root / ".workbench/validation/runs" / run_id
    if retained_run.is_symlink() or not retained_run.is_dir():
        raise ValueError("retained validation run must exist before scratch allocation")
    host = CoreTemporaryLeases(
        workspace=selected_root,
        configuration_home=default_user_config_home(),
        locations={"system": external, "repository": retained_run},
        owner_id="validation",
    )
    external_lease = host.allocate("system", run_id)
    try:
        repository_lease = host.allocate("repository", "repository-tmp")
    except BaseException:
        # No suite was launched yet. Keep the original failure if disposal
        # itself fails; Core's reservation remains available for recovery.
        try:
            host.reconcile(external_lease.lease_id, drained=lambda: True)
        except Exception:
            pass
        raise
    return host, (external_lease, repository_lease)


def publish_validation_timing(root: Path, suite_name: str, payload: bytes) -> Path:
    """Replace the scheduler's latest report in its registered Core namespace."""

    if type(suite_name) is not str or _SUITE_NAME.fullmatch(suite_name) is None:
        raise ValueError("validation suite name is not a portable record key")
    if type(payload) is not bytes:
        raise ValueError("validation timing report must be exact bytes")
    _source_core()
    from workbench_core.host_filesystem import replace_private_bytes, secure_private_path
    from workbench_core.storage.record_stores import CoreRecordStores
    from workbench_core.user_config_home import default_user_config_home

    selected_root = Path(root).resolve(strict=True)
    store = CoreRecordStores(
        workspace=selected_root,
        configuration_home=default_user_config_home(),
        owner_id="validation",
    ).open("validation-timings-v1", selected_root)
    target = store.root / f"{suite_name}.json"
    # Existing V1 timing reports preceded Core registration and may be 0644.
    # Core upgrades an ordinary historical report before replacing it.
    previous_size = 0
    if target.exists() or target.is_symlink():
        visible = target.lstat()
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1:
            raise OSError("historical validation timing report is not an ordinary file")
        previous_size = visible.st_size
        secure_private_path(target, directory=False)
    replace_private_bytes(target, payload, byte_limit=max(len(payload), previous_size))
    return target


__all__ = ["allocate_validation_run", "allocate_validation_scratch", "publish_validation_timing"]
