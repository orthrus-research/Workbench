"""Private, context-owned setup for repeatable material-check invocations.

Core retains paths and opaque owner bindings. The caller must reobserve those
bindings through the installed domain/profile owner on every use; this store
neither discovers a pack installation nor qualifies native initialization.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from . import check_storage as storage
from .artifact_store import fetch_verified_artifact
from .host_filesystem import fsync_directory


FORMAT = "workbench-material-check-setup-v1"
STATUS = "workbench-material-check-setup-status-v1"
PREPARATION = "workbench-material-check-input-preparation-v1"
PATH_KEYS = {"engine_home", "runtime_home", "java"}


def selected_java(*, environment=None):
    """Reuse Core's saved Java path; the caller still verifies profile bytes."""
    from .runtime_java import _java_executable, host_platform
    from .setup_cli import default_setup_record_path, load_setup_record

    record = load_setup_record(default_setup_record_path(environment=environment))
    selection = {} if record is None else record["selection"]
    selected = selection.get("java_home") or selection.get("managed_java_home")
    if selected is None:
        raise ValueError("Core setup has no selected Java; run workbench setup with a runtime profile or supply --java")
    home = Path(selected)
    if (not selected or any(c in selected for c in "\0\r\n") or not home.is_absolute()
            or str(Path(os.path.abspath(home))) != selected):
        raise ValueError("Core setup Java home must be an explicit absolute path; run workbench setup again or supply --java")
    return _java_executable(home, host_platform())


def _location(root, selection_id, context_id):
    for value in (selection_id, context_id):
        if not isinstance(value, str) or not value or len(value) > 512 or any(c in value for c in "\0\r\n"):
            raise ValueError("material setup requires exact selection and context identities")
    key = sha256(storage.canonical([selection_id, context_id])).hexdigest()
    return Path(root) / ".workbench/material-check-setups" / (key + ".json")


def _paths(paths):
    if not isinstance(paths, dict) or set(paths) != PATH_KEYS:
        raise ValueError("material setup requires engine, runtime and Java paths together")
    result = {}
    for name, value in paths.items():
        if not isinstance(value, str) or not value or any(c in value for c in "\0\r\n"):
            raise ValueError("material setup paths must be explicit absolute paths")
        path = Path(value)
        if not path.is_absolute() or str(Path(os.path.abspath(path))) != value:
            raise ValueError("material setup paths must be explicit absolute paths")
        result[name] = value
    return result


def _preparation_requirements(requirements):
    if (not isinstance(requirements, dict) or set(requirements) != {
            "schema", "profile", "side", "inputStage", "policySha256", "artifacts"}
            or requirements["schema"] != "axiom.native-input-preparation.v1"
            or requirements["side"] != "server" or requirements["inputStage"] != "raw-original-artifacts"
            or not isinstance(requirements["profile"], str) or not requirements["profile"]):
        raise ValueError("native input preparation requires the selected raw SERVER policy")
    policies = requirements["policySha256"]
    if not isinstance(policies, dict) or not policies:
        raise ValueError("native input preparation requires exact source policy identities")
    for name, digest in policies.items():
        storage.safe_path(name)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("native input preparation policy digest is invalid")
    rows = requirements["artifacts"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("native input preparation requires declared artifacts")
    paths = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "url", "sha256", "size"}:
            raise ValueError("native input artifact fields differ from the selected policy")
        storage.safe_path(row["path"])
        if row["path"] in paths:
            raise ValueError("native input preparation repeats an artifact path")
        paths.add(row["path"])
        if (not isinstance(row["url"], str) or not row["url"] or type(row["size"]) is not int or row["size"] <= 0
                or not isinstance(row["sha256"], str) or len(row["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in row["sha256"])):
            raise ValueError("native input artifact requires an exact URL, size and SHA-256")
    return json.loads(storage.canonical(requirements))


def _prepared_files(directory, expected):
    observed = [{key: row[key] for key in ("path", "size", "sha256")}
                for row in storage.tree_manifest(directory)]
    if observed != expected:
        raise ValueError("prepared native input files differ from selected requirements")


def prepare_inputs(root, selection_id, context_id, requirements, verify, *, cancelled=lambda: False):
    """Acquire profile-owned raw inputs through Core; engine/runtime assembly remains pending."""
    def check_cancelled():
        if cancelled():
            raise ValueError("native input preparation cancelled; setup remains incomplete")

    check_cancelled()
    root = Path(root).absolute()
    location = _location(root, selection_id, context_id)
    requirements = _preparation_requirements(requirements)
    inputs = verify(requirements, context_id)
    if not isinstance(inputs, dict):
        raise ValueError("native input preparation requires current owner bindings")
    inputs = json.loads(storage.canonical(inputs))
    binding = {"selection_id": selection_id, "context_id": context_id, "requirements": requirements, "inputs": inputs}
    identity = sha256(storage.canonical(binding)).hexdigest()
    parent = location.parent / "inputs"
    destination = parent / identity
    files = sorted([{key: row[key] for key in ("path", "size", "sha256")}
                    for row in requirements["artifacts"]], key=lambda row: row["path"])
    record = storage.seal("material-check-input-preparation", {
        "format": PREPARATION, "state": "inputs-prepared", "setup_state": "incomplete", **binding,
        "paths": {"artifact_root": str(destination / "files")}, "files": files,
        "receipt_uri": (destination / "preparation.json").as_uri(),
        "pending": ["engine", "native-runtime", "intended-jvm-qualification"],
    })
    check_cancelled()
    storage.initialize(root)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with storage.execution_lock(parent):
        if destination.exists() or destination.is_symlink():
            retained = storage.read_json(destination / "preparation.json")
            if retained != record:
                raise ValueError("retained native input preparation identity changed")
            _prepared_files(destination / "files", files)
            if verify(requirements, context_id) != inputs:
                raise ValueError("native input preparation owner bindings changed")
            check_cancelled()
            return retained
        staging = parent / (".prepare-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        artifact_root = staging / "files"
        artifact_root.mkdir(mode=0o700)
        storage.write_json(staging / "request.json", binding)
        try:
            for row in requirements["artifacts"]:
                check_cancelled()
                cached, _ = fetch_verified_artifact(
                    url=row["url"], expected_sha256=row["sha256"], expected_size=row["size"],
                    state_root=root / ".workbench", label="native input " + row["path"],
                )
                # The shared acquisition service bounds in-flight I/O; cancellation
                # is checked at each acquisition/copy boundary and before publication.
                check_cancelled()
                target = artifact_root / row["path"]
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(storage.ordinary(cached), target)
                target.chmod(0o600)
                check_cancelled()
            _prepared_files(artifact_root, files)
            if verify(requirements, context_id) != inputs:
                raise ValueError("native input preparation owner bindings changed")
            check_cancelled()
            storage.write_json(staging / "preparation.json", record)
            check_cancelled()
            staging.rename(destination)
            fsync_directory(parent)
        except (OSError, ValueError) as exc:
            if staging.exists():
                storage.write_json(staging / "failure.json", {
                    "state": "incomplete", "type": type(exc).__name__, "message": str(exc),
                })
            raise
    return record


def _load(root, selection_id, context_id):
    path = _location(root, selection_id, context_id)
    if not path.exists() and not path.is_symlink():
        return None
    value = storage.read_json(path)
    if not isinstance(value, dict) or set(value) != {
        "format", "state", "selection_id", "context_id", "paths", "program_root", "inputs", "id"
    }:
        raise ValueError("material setup record is malformed")
    if (storage.seal("material-check-setup", {key: row for key, row in value.items() if key != "id"}) != value
            or value["format"] != FORMAT or value["state"] != "configured-not-run"
            or value["selection_id"] != selection_id or value["context_id"] != context_id):
        raise ValueError("material setup identity or selected context changed")
    _paths(value["paths"])
    if value["program_root"] != ".":
        storage.safe_path(value["program_root"])
    if not isinstance(value["inputs"], dict):
        raise ValueError("material setup owner bindings are malformed")
    return value


def configure(root, selection_id, context_id, paths, program_root, verify):
    """Explicitly bind current inputs; never infer paths or execute native code."""
    paths = _paths(paths)
    if program_root != ".":
        storage.safe_path(program_root)
    inputs = verify(paths, context_id)
    if not isinstance(inputs, dict):
        raise ValueError("material setup requires current owner bindings")
    record = storage.seal("material-check-setup", {
        "format": FORMAT, "state": "configured-not-run", "selection_id": selection_id,
        "context_id": context_id, "paths": paths, "program_root": program_root, "inputs": inputs,
    })
    storage.initialize(root)
    path = _location(root, selection_id, context_id)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    storage.ordinary(path.parent, directory=True)
    with storage.execution_lock(path.parent):
        if path.exists() or path.is_symlink():
            storage.ordinary(path)
        temporary = path.parent / (".setup-" + uuid4().hex)
        storage.write_json(temporary, record)
        try:
            os.replace(temporary, path)
            fsync_directory(path.parent)
        finally:
            if temporary.exists():
                temporary.unlink()
    return record


def resolve(root, selection_id, context_id, verify):
    """Revalidate actual inputs; stored setup is a selection, not a freshness cache."""
    record = _load(root, selection_id, context_id)
    if record is None:
        raise ValueError("material check setup is missing; run checks materials setup for this context")
    if verify(record["paths"], context_id) != record["inputs"]:
        raise ValueError("material check setup is stale; validate and configure the selected inputs again")
    return record


def status(root, selection_id, context_id, verify):
    """Return non-executing setup readiness, including actionable stale inputs."""
    result = {"format": STATUS, "state": "missing", "selection_id": selection_id,
              "context_id": context_id, "setup_id": None, "paths": None, "program_root": None,
              "failure": None, "readiness_scope": "saved-input-and-profile-bindings-only"}
    try:
        record = _load(root, selection_id, context_id)
        if record is None:
            return result
        result.update(setup_id=record["id"], paths=record["paths"], program_root=record["program_root"])
        if verify(record["paths"], context_id) != record["inputs"]:
            raise ValueError("material check setup is stale; validate and configure the selected inputs again")
        result["state"] = "ready"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(state="stale", failure={"type": type(exc).__name__, "message": str(exc),
                      "meaning": "setup-unavailable-not-source-invalidity"})
    return result


def prepare_engine(root, archive, verify, *, cancelled=lambda: False):
    """Retain a selected engine ZIP through Core acquisition and extraction.

    The module verifies its distribution contract. Core retains all files and
    original notices, acquisition identity and failures. This does not configure
    a runnable setup or qualify the selected engine for a pack context.
    """
    from .runtime_java import _extract_zip

    def check_cancelled():
        if cancelled():
            raise ValueError("engine preparation cancelled; setup remains incomplete")

    check_cancelled()
    root, archive = Path(root).absolute(), Path(archive).absolute()
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("select an existing Axiom engine distribution ZIP")
    with archive.open("rb") as stream:
        digest = sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    selected = {"sha256": digest.hexdigest(), "size": size}
    parent = root / ".workbench/material-check-setups/engines"
    destination = parent / selected["sha256"]
    storage.initialize(root)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with storage.execution_lock(parent):
        check_cancelled()
        if destination.exists():
            retained = storage.read_json(destination / "engine.json", byte_limit=None)
            if retained.get("archive") != selected or retained.get("state") != "prepared-not-run":
                raise ValueError("retained engine preparation identity changed")
            _prepared_files(destination / "content", retained["files"])
            home = destination / "content" / retained["directory"]
            if verify(home) != retained["inputs"]:
                raise ValueError("retained engine differs from the installed module contract")
            check_cancelled()
            return home
        staging = parent / (".engine-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        storage.write_json(staging / "request.json", {"archive": selected, "source": archive.as_uri()})
        try:
            cached, _ = fetch_verified_artifact(url=archive.as_uri(), expected_sha256=selected["sha256"],
                expected_size=size, state_root=root / ".workbench", label="Axiom engine distribution")
            check_cancelled()
            _extract_zip(cached, staging / "content", label="Axiom engine")
            check_cancelled()
            children = list((staging / "content").iterdir())
            if len(children) != 1 or not children[0].is_dir():
                raise ValueError("engine distribution requires one component directory")
            home = children[0]
            inputs = verify(home)
            rows = [{key: row[key] for key in ("path", "size", "sha256")}
                    for row in storage.tree_manifest(staging / "content")]
            check_cancelled()
            storage.write_json(staging / "engine.json", {"state": "prepared-not-run", "archive": selected,
                "directory": home.name, "inputs": inputs, "files": rows}, byte_limit=None)
            staging.rename(destination)
            fsync_directory(parent)
        except Exception as exc:
            if staging.exists():
                storage.write_json(staging / "failure.json", {"state": "incomplete", "type": type(exc).__name__,
                                   "message": str(exc)}, byte_limit=None)
            raise
    return destination / "content" / home.name


def prepare_runtime(root, selection_id, context_id, preparation, observe, build, *, cancelled=lambda: False):
    """Retain an owner-built native runtime beside its verified prepared inputs.

    Domain code observes engine/JVM/resource identity and performs assembly through
    Core's process port. Core owns staging, failure retention, reuse and publication.
    A separate configure call binds a successfully verified runnable setup.
    """
    def check_cancelled():
        if cancelled():
            raise ValueError("native runtime preparation cancelled; setup remains incomplete")

    check_cancelled()
    root = Path(root).absolute()
    location = _location(root, selection_id, context_id)
    if (preparation.get("selection_id") != selection_id or preparation.get("context_id") != context_id
            or preparation.get("state") != "inputs-prepared"):
        raise ValueError("native runtime preparation requires this selection's verified inputs")
    artifact_root = Path(preparation["paths"]["artifact_root"])
    _prepared_files(artifact_root, preparation["files"])
    inputs = observe()
    binding = {"preparation_id": preparation["id"], "inputs": inputs}
    identity = sha256(storage.canonical(binding)).hexdigest()
    parent = location.parent / "runtimes"
    destination = parent / identity
    storage.initialize(root)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with storage.execution_lock(parent):
        if destination.exists():
            retained = storage.read_json(destination / "assembly.json", byte_limit=None)
            if retained.get("binding") != binding or retained.get("state") != "assembled-not-run":
                raise ValueError("retained native assembly identity changed")
            _prepared_files(destination / "runtime", retained["files"])
            _prepared_files(artifact_root, preparation["files"])
            if observe() != inputs:
                raise ValueError("native assembly owner inputs changed")
            check_cancelled()
            return destination / "runtime"
        staging = parent / (".assemble-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        work = staging / "work"
        work.mkdir(mode=0o700)
        storage.write_json(staging / "request.json", binding, byte_limit=None)
        try:
            check_cancelled()
            build(artifact_root, staging / "runtime", work)
            check_cancelled()
            _prepared_files(artifact_root, preparation["files"])
            if observe() != inputs:
                raise ValueError("native assembly owner inputs changed")
            rows = [{key: row[key] for key in ("path", "size", "sha256")}
                    for row in storage.tree_manifest(staging / "runtime")]
            storage.write_json(staging / "assembly.json", {"state": "assembled-not-run", "binding": binding,
                               "files": rows}, byte_limit=None)
            check_cancelled()
            staging.rename(destination)
            fsync_directory(parent)
        except Exception as exc:
            if staging.exists():
                storage.write_json(staging / "failure.json", {"state": "incomplete", "type": type(exc).__name__,
                                   "message": str(exc)}, byte_limit=None)
            raise
    return destination / "runtime"
