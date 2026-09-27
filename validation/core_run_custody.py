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
_MAX_CI_COLLECTION = 4 * 1024 * 1024
_MAX_DIRECT_COLLECTION = 32 * 1024 * 1024
SOURCE_CI_COLLECTION_FILES = {
    "validation-native-fixtures": "native-fixtures-not-run.json",
    "blueprints-native-fixtures": "blueprints-native-fixtures-not-run.json",
}
_RUN_RECORD_SUFFIX = {
    "inventory": ".inventory.json",
    "admission": ".admitted.json",
    "report": ".json",
}


def _source_core() -> None:
    source_root = Path(__file__).resolve().parents[1]
    for source in (source_root / "api/src", source_root / "core/src"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))


def selected_core_configuration_home() -> Path:
    """Freeze the user's Core home before suite imports can change the process."""

    _source_core()
    from workbench_core.user_config_home import default_user_config_home

    return default_user_config_home()


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


def allocate_standalone_suite_run(
    root: Path, run_id: str, suite_name: str,
) -> tuple[str, Path, Path]:
    """Reserve the default direct-suite report under a fresh Core run tree."""

    from orchestration import create_run_paths

    if (type(suite_name) is not str or _SUITE_NAME.fullmatch(suite_name) is None
            or suite_name in {".", ".."}):
        raise ValueError("validation suite name is not a portable run key")
    selected_root = Path(root).resolve(strict=True)
    host, allocation = allocate_validation_run(selected_root, run_id)
    paths = create_run_paths(
        selected_root / ".workbench/validation/runs", run_id,
        allocated_root=allocation.path,
    )
    return (
        allocation.allocation_id,
        paths.report_for(suite_name),
        host.catalog.resources.configuration_home,
    )


def _validation_run_allocation(
    root: Path, run_id: str, allocation_id: str,
    configuration_home: Path | None,
):
    if (type(run_id) is not str or _SUITE_NAME.fullmatch(run_id) is None
            or run_id in {".", ".."}):
        raise ValueError("validation run ID is invalid")
    _source_core()
    from workbench_core.user_config_home import default_user_config_home
    from workbench_core.working_allocations import CoreWorkingAllocations

    selected_root = Path(root).resolve(strict=True)
    selected_home = default_user_config_home() if configuration_home is None else Path(configuration_home)
    if not selected_home.is_absolute() or ".." in selected_home.parts:
        raise ValueError("Core validation configuration home must be an absolute stable path")
    runs = selected_root / ".workbench/validation/runs"
    host = CoreWorkingAllocations(
        workspace=selected_root,
        configuration_home=selected_home,
        locations={"evidence": runs},
        location_sources={"evidence": "repository-validation"},
        owner_id="validation",
    )
    allocation = host.open(allocation_id)
    run_root = runs / run_id
    if (
        allocation.family != "python-suite-run"
        or allocation.label != run_id
        or allocation.path != run_root
    ):
        raise ValueError("suite record does not belong to the selected Core validation run")
    return host, allocation, run_root


def _opened_validation_run(
    root: Path, run_id: str, allocation_id: str,
    configuration_home: Path | None,
) -> Path:
    return _validation_run_allocation(root, run_id, allocation_id, configuration_home)[2]


@contextmanager
def open_validation_run_log(
    root: Path, run_id: str, allocation_id: str, suite_name: str, *,
    selected_path: Path, configuration_home: Path | None = None,
):
    """Open a live suite log once inside the exact Core validation run."""

    if (type(suite_name) is not str or _SUITE_NAME.fullmatch(suite_name) is None
            or suite_name in {".", ".."}):
        raise ValueError("validation suite name is not a portable log key")
    host, allocation, run_root = _validation_run_allocation(
        root, run_id, allocation_id, configuration_home,
    )
    target = run_root / "logs" / f"{suite_name}.log"
    if Path(os.path.abspath(selected_path)) != target:
        raise ValueError("suite log path differs from the selected Core run")
    with host.create_once_stream(
        allocation, target, expected_family="python-suite-run",
    ) as stream:
        yield stream


def _publish_validation_run_bytes(
    target: Path, payload: bytes, *, expected_sha256: str | None,
    label: str,
) -> None:
    from workbench_core.host_filesystem import (
        count_interrupted_create_once_stages, publish_create_once_bytes,
        read_private_single_link_bytes, replace_private_bytes,
    )

    if type(payload) is not bytes:
        raise ValueError("validation run record must be exact bytes")
    if count_interrupted_create_once_stages(target):
        raise OSError(f"interrupted Core {label} stage requires review")
    if expected_sha256 is None:
        publish_create_once_bytes(target, payload, byte_limit=len(payload))
    else:
        prior_size = target.lstat().st_size
        replace_private_bytes(
            target, payload, byte_limit=max(len(payload), prior_size),
            expected_sha256=expected_sha256,
        )
    if read_private_single_link_bytes(target, byte_limit=len(payload)) != payload:
        raise OSError(f"Core {label} changed after publication")


def publish_validation_run_record(
    root: Path, run_id: str, allocation_id: str, suite_name: str,
    kind: str, payload: bytes, *, selected_path: Path,
    expected_sha256: str | None = None,
    configuration_home: Path | None = None,
) -> Path:
    """Publish or compare-and-replace one V1 suite record in its Core run.

    The child and scheduler independently reopen the same allocation before
    writing. A create-once stage left by an interruption blocks fresh writes;
    the caller must choose a new run rather than infer what the old stage meant.
    """

    if (
        type(suite_name) is not str or _SUITE_NAME.fullmatch(suite_name) is None
        or suite_name in {".", ".."}
        or kind not in _RUN_RECORD_SUFFIX or type(payload) is not bytes
        or (expected_sha256 is not None and kind != "report")
    ):
        raise ValueError("validation run record selection is invalid")
    run_root = _opened_validation_run(root, run_id, allocation_id, configuration_home)
    target = run_root / "reports" / f"{suite_name}{_RUN_RECORD_SUFFIX[kind]}"
    if Path(os.path.abspath(selected_path)) != target:
        raise ValueError("suite record path differs from the selected Core run")
    _publish_validation_run_bytes(
        target, payload, expected_sha256=expected_sha256,
        label="suite record",
    )
    return target


def publish_validation_run_manifest(
    root: Path, run_id: str, allocation_id: str, payload: bytes, *,
    selected_path: Path, expected_sha256: str | None = None,
    configuration_home: Path | None = None,
) -> Path:
    """Publish the V1 run manifest once, then compare every revision."""

    run_root = _opened_validation_run(root, run_id, allocation_id, configuration_home)
    target = run_root / "run.json"
    if Path(os.path.abspath(selected_path)) != target:
        raise ValueError("run manifest path differs from the selected Core run")
    _publish_validation_run_bytes(
        target, payload, expected_sha256=expected_sha256,
        label="run manifest",
    )
    return target


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


def _source_ci_collection_target(root: Path, suite_name: str, selected_path: Path):
    if suite_name not in SOURCE_CI_COLLECTION_FILES:
        raise ValueError("source-CI collection suite is unsupported")
    selected_root = Path(root).resolve(strict=True)
    target = selected_root / ".workbench/validation" / SOURCE_CI_COLLECTION_FILES[suite_name]
    supplied = Path(selected_path)
    if ".." in supplied.parts or Path(os.path.abspath(supplied)) != target:
        raise ValueError("source-CI collection differs from its historical path")
    return selected_root, target


def publish_standalone_collection(
    root: Path, suite_name: str, payload: bytes, *, selected_path: Path,
    configuration_home: Path,
) -> Path:
    """Publish one direct collection at its exact V1 path through Core."""

    if (type(suite_name) is not str or _SUITE_NAME.fullmatch(suite_name) is None
            or suite_name in {".", ".."} or type(payload) is not bytes
            or not 0 < len(payload) <= _MAX_DIRECT_COLLECTION
            or not isinstance(selected_path, Path) or ".." in selected_path.parts):
        raise ValueError("direct collection selection or payload is invalid")
    selected_root = Path(root).resolve(strict=True)
    target = Path(os.path.abspath(selected_path.expanduser()))
    if target in {
        selected_root / ".workbench/validation" / name
        for name in SOURCE_CI_COLLECTION_FILES.values()
    }:
        raise ValueError("source-CI collection requires its admitted Core route")
    _source_core()
    from workbench_core.storage.record_stores import CoreRecordStores

    if (not isinstance(configuration_home, Path) or not configuration_home.is_absolute()
            or ".." in configuration_home.parts):
        raise ValueError("direct collection needs an absolute Core configuration home")
    provider = CoreRecordStores(
        workspace=selected_root, configuration_home=configuration_home,
        owner_id="validation",
    )
    provider.publish_validation_collection_target(
        target, payload, byte_limit=_MAX_DIRECT_COLLECTION,
    )
    return selected_path


def _source_ci_collection_store(
    root: Path, suite_name: str, selected_path: Path,
    configuration_home: Path | None,
):
    """Bind one historical source-CI fixture inventory to its Core store."""

    selected_root, target = _source_ci_collection_target(root, suite_name, selected_path)
    _source_core()
    from workbench_core.storage.record_stores import CoreRecordStores
    from workbench_core.user_config_home import default_user_config_home

    selected_home = default_user_config_home() if configuration_home is None else Path(configuration_home)
    if not selected_home.is_absolute() or ".." in selected_home.parts:
        raise ValueError("Core validation configuration home must be an absolute stable path")
    store = CoreRecordStores(
        workspace=selected_root, configuration_home=selected_home,
        owner_id="validation",
    ).open("validation-ci-collections-v1", selected_root)
    if store.root != target.parent:
        raise ValueError("Core source-CI collection store changed")
    return store, target


def _reject_source_ci_collection_stages(target: Path) -> None:
    from workbench_core.host_filesystem import count_uncertain_record_stages

    if count_uncertain_record_stages(target.parent, targets=(target.name,)):
        raise OSError("interrupted source-CI collection stage requires review")


def publish_source_ci_collection(
    root: Path, suite_name: str, payload: bytes, *, selected_path: Path,
    configuration_home: Path | None = None,
) -> Path:
    """Create one exact V1 fixture inventory; preserve uncertain stages."""

    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_CI_COLLECTION:
        raise ValueError("source-CI collection bytes exceed their bound")
    _, target = _source_ci_collection_store(
        root, suite_name, selected_path, configuration_home,
    )
    from workbench_core.host_filesystem import (
        fsync_directory, publish_commit_witness_bytes,
        read_private_single_link_bytes,
    )

    _reject_source_ci_collection_stages(target)
    publish_commit_witness_bytes(target, payload, byte_limit=_MAX_CI_COLLECTION)
    # Persist removal of the visible preparation stage before admission.
    fsync_directory(target.parent)
    _reject_source_ci_collection_stages(target)
    if read_private_single_link_bytes(target, byte_limit=_MAX_CI_COLLECTION) != payload:
        raise OSError("source-CI collection changed after publication")
    return target


def read_source_ci_collection(
    root: Path, suite_name: str, *, selected_path: Path,
    configuration_home: Path | None = None,
) -> bytes:
    """Reopen the exact Core collection target before domain admission."""

    _, target = _source_ci_collection_target(root, suite_name, selected_path)
    # Observation of a missing historical result must not create a Core store
    # or diagnostic directory. Only an existing candidate is adopted for read.
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(f"source-CI collection is absent: {target}")
    _, target = _source_ci_collection_store(
        root, suite_name, selected_path, configuration_home,
    )
    from workbench_core.host_filesystem import read_bounded_single_link_bytes

    _reject_source_ci_collection_stages(target)
    # The old writer used a regular file under a sometimes-public directory.
    # Core can read that historical V1 without changing its file bytes/mode.
    payload = read_bounded_single_link_bytes(target, byte_limit=_MAX_CI_COLLECTION)
    _reject_source_ci_collection_stages(target)
    return payload


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
    "selected_core_configuration_home", "publish_standalone_collection",
    "publish_validation_timing", "open_validation_invocation", "allocate_ide_toolchain_stage",
    "reject_existing_ide_toolchain_stage",
    "review_ide_toolchain_stages_on_reuse",
    "promote_ide_toolchain_directory", "extract_ide_toolchain_archive",
    "verify_ide_toolchain_directory", "admit_ide_toolchain_directory",
]
