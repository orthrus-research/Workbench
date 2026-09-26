"""Source-available Core host for retained repository validation runs.

Validation starts from the source checkout, including when optional modules or
the installed Workbench environment are unavailable. Keep that composition
here; the validation scheduler still owns suite admission and result meaning.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import re
import stat
import sys
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from workbench_api.validation_invocations import ValidationInvocationRecord


_SUITE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MAX_CI_PLAN = 4 * 1024 * 1024


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


def open_validation_invocation(
    root: Path, run_id: str, *, result: Path | None = None,
    configuration_home: Path | None = None,
) -> ValidationInvocationRecord:
    """Compose Core's revisioned writer for a default or exact explicit URI."""

    _source_core()
    from workbench_api import ModuleError
    from workbench_api.durable_resources import DurableResourceError
    from workbench_core.storage.record_stores import CoreRecordStores
    from workbench_core.user_config_home import default_user_config_home
    from workbench_core.validation_invocation_records import CoreValidationInvocationRecord

    selected_root = Path(root).resolve(strict=True)
    selected_home = Path(configuration_home or default_user_config_home()).absolute()
    try:
        provider = CoreRecordStores(
            workspace=selected_root,
            configuration_home=selected_home,
            owner_id="validation",
        )
        if result is None:
            store = provider.open("validation-invocations-v1", selected_root)
            return CoreValidationInvocationRecord(store, run_id)
        selected_result = Path(os.path.abspath(Path(result).expanduser()))
        store = provider.open_validation_invocation_target(selected_result)
        return CoreValidationInvocationRecord(store, run_id, target=selected_result)
    except (DurableResourceError, ModuleError) as exc:
        raise OSError(f"Core invocation store is unavailable: {exc}") from exc


def _ide_stage_host(target: Path):
    _source_core()
    from workbench_core.temporary_leases import CoreTemporaryLeases

    selected_root = target.parent.parent
    return CoreTemporaryLeases(
        workspace=selected_root,
        configuration_home=selected_root / ".ide-toolchain-core",
        locations={"ide-toolchain": target.parent}, owner_id="validation",
    )


def allocate_ide_toolchain_stage(target: Path, archive_sha256: str):
    """Reserve one private extraction stage in Core's local lease catalog."""

    _reject_existing_ide_toolchain_stage(target, archive_sha256)
    _source_core()
    from workbench_core.temporary_leases import TemporaryLeaseError

    host = _ide_stage_host(target)
    prefix = f"ide-{archive_sha256}-"
    try:
        reference = host.allocate(
            "ide-toolchain", prefix + uuid4().hex,
        )
    except TemporaryLeaseError as exc:
        raise OSError(f"Core IDE extraction stage needs review: {exc}") from exc
    return host, reference


def _ide_toolchain_stage_rows(target: Path, archive_sha256: str) -> list[dict]:
    if (
        not isinstance(target, Path) or not target.is_absolute()
        or type(archive_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", archive_sha256) is None
    ):
        raise OSError("IDE toolchain stage target or archive digest is invalid")
    _source_core()
    from workbench_core.temporary_leases import CoreTemporaryLeases, TemporaryLeaseError

    host = _ide_stage_host(target)
    prefix = f"ide-{archive_sha256}-"
    try:
        rows = CoreTemporaryLeases.inventory_catalog(
            host.configuration_home, workspace=host.workspace,
        )
        return [row for row in rows if (
            row["owner_id"] == "validation"
            and row["role"] == "ide-toolchain"
            and Path(row["path"]).parent == target.parent
            and Path(row["path"]).name.startswith(prefix)
        )]
    except TemporaryLeaseError as exc:
        raise OSError(f"Core IDE extraction stage needs review: {exc}") from exc


def _reject_existing_ide_toolchain_stage(target: Path, archive_sha256: str) -> None:
    if any(row["status"] != "disposed" for row in _ide_toolchain_stage_rows(target, archive_sha256)):
        raise OSError("interrupted Core IDE extraction stage requires review")


def reject_existing_ide_toolchain_stage(target: Path, archive_sha256: str) -> None:
    """Refuse an interrupted Core stage before archive acquisition."""

    _reject_existing_ide_toolchain_stage(target, archive_sha256)


def review_ide_toolchain_stages_on_reuse(target: Path, archive_sha256: str) -> None:
    """Refuse a selected target while its Core extraction stage is incomplete."""

    rows = _ide_toolchain_stage_rows(target, archive_sha256)
    if any(row["status"] not in {"retained-unproven", "disposed"} for row in rows):
        raise OSError("incomplete Core IDE extraction stage requires review")
    if sum(row["status"] == "retained-unproven" for row in rows) > 1:
        raise OSError("multiple retained Core IDE extraction stages require review")


def promote_ide_toolchain_directory(
    payload: Path, target: Path, marker: bytes, *, stage_host, stage_reference,
) -> Path:
    """Ask Core for an atomic no-replace move of one prepared extraction.

    The stage's exact Core lease marker remains adjacent to the moved payload.
    A failed promotion retains the prepared stage and every existing target.
    """

    if (
        not isinstance(payload, Path) or not isinstance(target, Path)
        or payload.parent.parent != target.parent
        or type(marker) is not bytes or re.fullmatch(rb"[0-9a-f]{64}\n", marker) is None
        or getattr(stage_reference, "path", None) != payload.parent
    ):
        raise OSError("IDE toolchain stage or lock marker is invalid")
    _source_core()
    from workbench_core.temporary_leases import CoreTemporaryLeases, TemporaryLeaseError
    from workbench_core.prepared_directory_promotion import (
        PreparedDirectoryError, promote_prepared_directory,
    )

    try:
        if not isinstance(stage_host, CoreTemporaryLeases):
            raise OSError("IDE toolchain stage has no Core temporary lease")
        stage_marker = stage_host.prepared_stage_marker(stage_reference)
        return promote_prepared_directory(
            payload, target, marker_name=".workbench-provisioned-sha256",
            marker_bytes=marker, stage_marker=stage_marker,
        )
    except (PreparedDirectoryError, TemporaryLeaseError) as exc:
        raise OSError(f"Core IDE toolchain promotion needs review: {exc}") from exc


def extract_ide_toolchain_archive(
    stage_host, stage_reference, archive: Path, *, archive_sha256: str,
    archive_size: int, extracted_root: str, archive_format: str,
) -> Path:
    """Ask Core to validate and extract a locked archive in its active lease."""

    _source_core()
    from workbench_core.ide_toolchain_extract import (
        IdeToolchainExtractionError, extract_locked_ide_archive,
    )

    try:
        return extract_locked_ide_archive(
            stage_host, stage_reference, archive,
            archive_sha256=archive_sha256, archive_size=archive_size,
            expected_root=extracted_root, archive_format=archive_format,
        )
    except IdeToolchainExtractionError as exc:
        raise OSError(f"Core IDE toolchain extraction needs review: {exc}") from exc


def verify_ide_toolchain_directory(
    archive: Path, target: Path, *, archive_sha256: str,
    archive_size: int, extracted_root: str, archive_format: str,
) -> int:
    """Reopen and compare the exact locked archive with its historical tree."""

    _source_core()
    from workbench_core.ide_toolchain_reader import (
        IdeToolchainReadError, verify_ide_toolchain_tree,
    )

    try:
        return verify_ide_toolchain_tree(
            archive, target, archive_sha256=archive_sha256,
            archive_size=archive_size, expected_root=extracted_root,
            archive_format=archive_format,
        )
    except IdeToolchainReadError as exc:
        raise OSError(f"Core IDE toolchain read needs review: {exc}") from exc


def admit_ide_toolchain_directory(
    archive: Path, target: Path, *, archive_sha256: str,
    archive_size: int, extracted_root: str, archive_format: str,
    stage_lease_id: str | None = None,
) -> dict:
    """Record Core's exact archive/tree readback at the historical target."""

    _source_core()
    from workbench_core.ide_toolchain_admissions import (
        CoreIdeToolchainAdmissions, IdeToolchainAdmissionError,
    )

    try:
        return CoreIdeToolchainAdmissions(target.parent).admit(
            archive, target, archive_sha256=archive_sha256,
            archive_size=archive_size, expected_root=extracted_root,
            archive_format=archive_format, stage_lease_id=stage_lease_id,
        )
    except IdeToolchainAdmissionError as exc:
        raise OSError(f"Core IDE toolchain admission needs review: {exc}") from exc


@contextmanager
def hold_ide_toolchain_directory(
    archive: Path, target: Path, *, archive_sha256: str,
    archive_size: int, extracted_root: str, archive_format: str,
):
    """Keep an existing Core admission locked during IDE client execution."""

    _source_core()
    from workbench_core.ide_toolchain_admissions import (
        CoreIdeToolchainAdmissions, IdeToolchainAdmissionError,
    )

    try:
        with CoreIdeToolchainAdmissions(target.parent).hold(
            archive, target, archive_sha256=archive_sha256,
            archive_size=archive_size, expected_root=extracted_root,
            archive_format=archive_format,
        ) as selected:
            yield selected
    except IdeToolchainAdmissionError as exc:
        raise OSError(f"Core IDE toolchain hold needs review: {exc}") from exc


def publish_ci_plan(
    root: Path, output: Path, payload: bytes, *,
    configuration_home: Path | None = None,
) -> Path:
    """Publish one exact CI selection in Core's historical plan namespace.

    A second identical call can reopen the plan. A different prior plan or an
    interrupted unclaimed stage stays in place for review; CI starts from a
    fresh checkout and never needs to erase earlier selection evidence.
    """

    _source_core()
    from workbench_core.host_filesystem import (
        read_bounded_bytes, read_private_single_link_bytes,
        publish_immutable_bytes, secure_private_path,
    )
    from workbench_core.storage.record_stores import CoreRecordStores
    from workbench_core.user_config_home import default_user_config_home

    selected_root = Path(root).resolve(strict=True)
    target = selected_root / ".workbench/validation/ci/plan.json"
    selected_output = Path(os.path.abspath(Path(output).expanduser()))
    if selected_output != target:
        raise ValueError("CI plan output must use Core's selected historical path")
    if type(payload) is not bytes or len(payload) > _MAX_CI_PLAN:
        raise ValueError("CI plan exceeds its exact byte bound")
    if target.exists() or target.is_symlink():
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("prior CI plan is not an independent regular file")
        if read_bounded_bytes(target, byte_limit=_MAX_CI_PLAN) != payload:
            raise ValueError("a different CI plan is already retained at this path")
    plan_parent = target.parent
    if plan_parent.exists() or plan_parent.is_symlink():
        if plan_parent.is_symlink() or not plan_parent.is_dir():
            raise ValueError("CI plan parent is redirected or unavailable")
        for member in plan_parent.iterdir():
            if member.name.startswith(".plan.json."):
                raise ValueError("interrupted CI plan stage requires review")
    store = CoreRecordStores(
        workspace=selected_root,
        configuration_home=Path(configuration_home or default_user_config_home()).absolute(),
        owner_id="validation",
    ).open("validation-ci-plan-v1", selected_root)
    target = store.root / "plan.json"
    if target.exists() or target.is_symlink():
        # The historical direct writer used the host's umask. Adopt only the
        # exact same ordinary bytes before asking Core for idempotent custody.
        secure_private_path(target, directory=False)
    publish_immutable_bytes(target, payload, byte_limit=_MAX_CI_PLAN, idempotent=True)
    if read_private_single_link_bytes(target, byte_limit=_MAX_CI_PLAN) != payload:
        raise ValueError("Core CI plan changed after publication")
    return target


__all__ = [
    "allocate_validation_run", "allocate_validation_scratch", "publish_ci_plan",
    "publish_validation_timing", "open_validation_invocation", "allocate_ide_toolchain_stage",
    "reject_existing_ide_toolchain_stage",
    "review_ide_toolchain_stages_on_reuse",
    "promote_ide_toolchain_directory", "extract_ide_toolchain_archive",
    "verify_ide_toolchain_directory", "admit_ide_toolchain_directory",
]
