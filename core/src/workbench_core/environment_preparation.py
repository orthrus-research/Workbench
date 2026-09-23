"""Core-owned, no-game Packwiz/Prism preparation with durable process custody.

Callers supply captured source and packaged declarative requirements. Core owns
all writes, downloads, process supervision, image publication and recovery. No
profile implementation or Shell import belongs in this module.
"""

import json
import os
import re
import signal
import socket
import sys
import threading
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from workbench_api.checks import observation
from workbench_api.prism import encode_launch_script

from . import check_storage as storage
from . import environment_packwiz as packwiz
from .artifact_store import fetch_verified_artifact
from .check_execution import CheckProgress, recover_execution
from .runner import supervise_process
from .runtime_java import _extract_zip
from .sessions import RetainedSession


def attempt_path(root, identity):
    if re.fullmatch(r"environment-[0-9a-f]{32}", identity or "") is None:
        raise ValueError("select one exact environment preparation attempt")
    return root / ".workbench/environment-attempts" / identity


def read_request(root, identity):
    attempt = attempt_path(root, identity)
    value = storage.read_json(attempt / "request.json")
    if (
        storage.seal(
            "environment-request", {k: v for k, v in value.items() if k != "id"}
        )
        != value
        or value["attempt_id"] != identity
    ):
        raise ValueError("environment preparation request changed")
    return attempt, value


def tool(path, *, elf=False):
    path = storage.ordinary(Path(path))
    if path.stat().st_size > 512 * 1024**2:
        raise ValueError("selected executable exceeds its byte bound")
    with path.open("rb") as stream:
        if elf and stream.read(4) != b"\x7fELF":
            raise ValueError("select a native Linux tool executable")
    if not os.access(path, os.X_OK):
        raise ValueError("selected tool is not executable")
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


def plan(
    root,
    *,
    source,
    source_rows,
    candidate_id,
    binding,
    requirements,
    dependencies,
    tools,
    accounts,
    seeds,
    context,
    provider,
    excluded_roots,
):
    if sys.platform != "linux":
        raise ValueError("environment preparation currently requires Linux executables")
    packwiz.validate_downloads(dependencies)
    selected = {name: tool(path, elf=name != "prism") for name, path in tools.items()}
    if set(selected) != {"prism", "java", "packwiz"}:
        raise ValueError("select Prism, Packwiz and the platform's native Java")
    java = Path(selected["java"]["path"])
    release = storage.ordinary(java.parent.parent / "release").read_text()
    metadata = dict(re.findall(r'^([A-Z_]+)="(.*)"$', release, re.MULTILINE))
    if (
        java.name != "java"
        or metadata.get("JAVA_RUNTIME_VERSION", "").removesuffix("-LTS")
        != requirements["java"]["runtime_version"]
        or metadata.get("IMPLEMENTOR") != requirements["java"]["vendor"]
    ):
        raise ValueError("selected JDK differs from the packaged platform requirement")
    accounts = storage.ordinary(Path(accounts))
    if accounts.stat().st_size > 4 * 1024**2:
        raise ValueError("selected Prism account store exceeds its bound")
    seeds = [str(storage.ordinary(Path(path), directory=True)) for path in seeds]
    if len(seeds) > 8:
        raise ValueError("select at most eight hash-verified artifact seed roots")
    storage.initialize(root)
    parent = root / ".workbench/environment-attempts"
    parent.mkdir(mode=0o700, exist_ok=True)
    storage.ordinary(parent, directory=True)
    identity = "environment-" + uuid4().hex
    attempt = attempt_path(root, identity)
    attempt.mkdir(mode=0o700)
    rows = [
        {**row, "mode": 0o755 if row["mode"] & 0o100 else 0o644} for row in source_rows
    ]
    (attempt / "source").mkdir(mode=0o700)
    for row in rows:
        raw = source[row["path"]]
        if len(raw) != row["size"] or sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("captured candidate bytes differ from their manifest")
        target = attempt / "source" / storage.safe_path(row["path"])
        _write(target, raw)
        target.chmod(row["mode"])
    request = storage.seal(
        "environment-request",
        {
            "format": "workbench-environment-request-v1",
            "state": "planned-not-run",
            "attempt_id": identity,
            "workspace_uri": context["selection"]["pack_uri"],
            "selection_id": context["selection_id"],
            "candidate_id": candidate_id,
            "context": context,
            "provider": provider,
            "source_files": rows,
            "binding": binding,
            "requirements": requirements,
            "dependencies": dependencies,
            "tools": selected,
            "accounts_path": str(accounts),
            "seeds": seeds,
            "excluded_roots": excluded_roots,
            "effects": [
                "Refresh only a private copy of the saved Packwiz source; apply client-side declared optional defaults",
                "Download hash-locked dependencies and resolve Prism libraries, assets and natives",
                "Copy only the selected account store into a fresh private Prism root; Prism may refresh that private copy online",
                "Capture the launcher handoff without executing Minecraft; publish an offline-account immutable image",
                "Retain process lifecycle evidence; discard child output at the account boundary; remove copied account files after verified closure",
                "Move disposable preparation files to recoverable private trash; source and the original launcher remain unchanged",
            ],
            "authority": {
                "runtime_launched": False,
                "source_mutated": False,
                "qualification_granted": False,
            },
        },
    )
    storage.write_json(attempt / "request.json", request)
    return request


def _command(
    root,
    attempt,
    work,
    label,
    argv,
    *,
    cwd,
    environment,
    cancelled,
    timeout=600,
    capture=True,
):
    phase = attempt / label
    phase.mkdir(mode=0o700)
    renderer = CheckProgress(
        attempt=phase,
        request_id=storage.read_json(attempt / "request.json")["id"],
        runtime=work,
        logs=(),
        optional_logs=(),
        observe=lambda _: observation(
            "running", "environment-" + label, "Preparing environment: " + label
        ),
        timeout=timeout,
        cancelled=cancelled,
    )
    session = RetainedSession(
        root=root,
        command_id="workbench.environment." + label,
        argv=argv,
        cwd=cwd,
        intent="execute",
    )
    storage.write_json(phase / "console.json", {"session_id": session.session_id})
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(
                sig, lambda *_: setattr(renderer, "signalled", True)
            )
    try:
        result = supervise_process(
            argv,
            cwd=cwd,
            root=root,
            session=session,
            renderer=renderer,
            source="environment-" + label,
            environment=environment,
            capture_output=capture,
            require_group_closure=True,
        )
        record = {
            "state": "closed",
            "stop_reason": renderer.stop_reason or "process-exited",
            "console": asdict(result),
            "cwd": str(cwd),
            "observations": renderer.observations,
        }
        storage.write_json(phase / "execution.json", record)
        if renderer.stop_reason or result.effective_exit_code != 0:
            raise ValueError("environment phase failed or stopped: " + label)
        return record
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)


def _config(path, values):
    # QSettings INI values used here are paths, booleans and a bounded wrapper
    # command. Escape backslashes/quotes instead of treating paths as a shell.
    def encode(value):
        value = str(value)
        if any(c in value for c in "\0\r\n"):
            raise ValueError(
                "Prism configuration contains an unsupported control character"
            )
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    raw = (
        "[General]\nConfigVersion=1.3\n"
        + "".join(k + "=" + encode(v) + "\n" for k, v in values.items())
    ).encode()
    if path.exists():
        storage.ordinary(path)
        path.unlink()  # Only the fresh bootstrap instance's replaced configuration.
    _write(path, raw)


def _prism(work, attempt, request, bootstrap):
    data = work / "prism-data"
    data.mkdir(mode=0o700)
    instance = data / "instances/workbench-preparation"
    _extract_zip(bootstrap, instance)
    (instance / ".minecraft").mkdir(mode=0o700, exist_ok=True)
    account_path = storage.ordinary(Path(request["accounts_path"]))
    with account_path.open("rb") as stream:
        account = stream.read(4 * 1024**2 + 1)
    if len(account) > 4 * 1024**2:
        raise ValueError("selected Prism account store exceeds its bound")
    try:
        parsed = json.loads(account)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("accounts"), list):
            raise ValueError()  # noqa: TRY004 - invalid JSON value, not a Python API type
    except (ValueError, UnicodeError):
        raise ValueError("selected Prism account store is invalid") from None
    _write(data / "accounts.json", account)
    del account, parsed
    java = request["tools"]["java"]["path"]
    capture_request = storage.seal(
        "prism-capture-request",
        {
            "requirements": request["requirements"],
            "java": java,
            "instance": str(instance),
            "prism_data": str(data),
            "preparation_id": request["id"],
        },
    )
    storage.write_json(work / "capture-request.json", capture_request)
    values = {
        "Language": "en_US",
        "LastHostname": socket.gethostname(),
        "JavaPath": java,
        "AutomaticJavaDownload": "false",
        "AutomaticJavaSwitch": "false",
        "UserAskedAboutAutomaticJavaDownload": "true",
        "PastebinURL": "",
        "ApplicationTheme": "system",
        "IconTheme": "pe_colored",
        "CheckUpdates": "false",
        "MinMemAlloc": "512",
        "MaxMemAlloc": "4096",
        "PreLaunchCommand": "",
        "PostExitCommand": "",
        "WrapperCommand": "",
    }
    _config(data / "prismlauncher.cfg", values)
    # Prism tokenizes custom commands itself. Quoted tokens preserve spaces;
    # shell expansion is neither requested nor allowed.
    wrapper = " ".join(
        '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'
        for x in (
            sys.executable,
            Path(__file__).with_name("prism_capture.py"),
            work / "capture-request.json",
        )
    )
    values.update(
        {
            "InstanceType": "OneSix",
            "name": "Workbench preparation",
            "OverrideJavaLocation": "true",
            "OverrideJavaArgs": "true",
            "JvmArgs": "",
            "IgnoreJavaCompatibility": "true",
            "OverrideCommands": "true",
            "WrapperCommand": wrapper,
            "OverrideMemory": "true",
            "OverrideMiscellaneous": "true",
            "QuitAfterGameStop": "true",
            "CloseAfterLaunch": "false",
            "OverridePerformance": "true",
            "EnableFeralGamemode": "false",
            "EnableMangoHud": "false",
            "UseDiscreteGpu": "false",
            "UseZink": "false",
            "OverrideNativeWorkarounds": "true",
            "UseNativeOpenAL": "false",
            "UseNativeGLFW": "false",
            "OverrideEnv": "true",
            "Env": "",
            "OverrideLegacySettings": "true",
            "OnlineFixes": "false",
            "ManagedPack": "false",
        }
    )
    _config(instance / "instance.cfg", values)
    return data, instance, capture_request


def _copy_file(source, target):
    source = storage.ordinary(source)
    if source.stat().st_size > 2 * 1024**3:
        raise ValueError("launcher artifact exceeds its bound")
    with source.open("rb") as stream:
        raw = stream.read(2 * 1024**3 + 1)
    _write(target, raw)


def _assemble(work, request, handoff):
    payload = work / "payload"
    for name in ("libraries", "natives", "assets", "workbench-launch.txt"):
        if (payload / name).exists() or (payload / name).is_symlink():
            raise ValueError(
                "Packwiz payload collides with a reserved launcher resource"
            )
    classpath = []
    library_root = Path(handoff["assets"]).parent / "libraries"
    for position, name in enumerate(handoff["classpath"]):
        source = storage.ordinary(Path(name))
        # FML inspects Maven coordinates in library paths. Content manifests
        # provide integrity without replacing that meaningful directory shape.
        relative = (
            "libraries/workbench-prism/NewLaunch.jar"
            if position == 0
            else "libraries/" + source.relative_to(library_root).as_posix()
        )
        storage.safe_path(relative)
        _copy_file(source, payload / relative)
        classpath.append(relative)
    for name in ("natives", "assets"):
        source = Path(handoff[name])
        storage.copy_manifest(source, payload / name, storage.tree_manifest(source))
    protocol = handoff["protocol"]
    parameters = protocol["param"][:]
    parameters[parameters.index("--gameDir") + 1] = "."
    parameters[parameters.index("--assetsDir") + 1] = "assets"
    _write(
        payload / "workbench-launch.txt",
        encode_launch_script({**protocol, "param": parameters}),
    )
    return [
        *handoff["jvm_arguments"],
        "-Djava.library.path=natives",
        "-cp",
        ":".join(classpath),
        "org.prismlauncher.EntryPoint",
    ]


def _remove_accounts(work):
    # Copies are ephemeral secrets, not recoverable evidence. Never target the
    # user's selected source store or a broad directory.
    for name in ("accounts.json", "accounts.json.bak"):
        path = work / "prism-data" / name
        if path.exists() or path.is_symlink():
            storage.ordinary(path).unlink()


def recover(root, identity, consent):
    attempt, request = read_request(root, identity)
    if consent != request["id"]:
        raise ValueError("recovery requires the exact environment request")
    with storage.execution_lock(attempt):
        if (attempt / "recovery.json").exists():
            return storage.read_json(attempt / "recovery.json")
        work = root / ".workbench/tmp" / identity
        for label, cwd in (
            ("refresh", work / "stage"),
            ("install", work / "payload"),
            ("prism", work),
        ):
            phase = attempt / label
            if phase.exists() and not (phase / "execution.json").exists():
                recover_execution(root, phase, cwd)
        if work.exists():
            _remove_accounts(work)
            cleanup = storage.cleanup_projection(root, work)
        else:
            cleanup = None
        result = storage.seal(
            "environment-recovery",
            {
                "format": "workbench-environment-recovery-v1",
                "request_id": consent,
                "state": "recovered-incomplete",
                "cleanup": cleanup,
            },
        )
        storage.write_json(attempt / "recovery.json", result)
        return result


def execute(root, identity, consent, *, validate_image, cancelled=lambda: False):
    attempt, request = read_request(root, identity)
    if consent != request["id"]:
        raise ValueError(
            "preparation requires confirmation of the exact environment request"
        )
    with storage.execution_lock(attempt):
        if any(
            (attempt / name).exists()
            for name in ("started.json", "result.json", "recovery.json")
        ):
            raise ValueError(
                "environment attempt already started; reopen or plan again"
            )
        if storage.tree_manifest(attempt / "source") != request["source_files"]:
            raise ValueError("retained environment candidate changed")
        for name, selected in request["tools"].items():
            if tool(selected["path"], elf=name != "prism") != selected:
                raise ValueError("selected preparation tool changed")
        stopped = lambda: cancelled() or (attempt / "cancel.json").exists()
        if stopped():
            raise ValueError("environment preparation was cancelled before execution")
        storage.write_json(attempt / "started.json", {"request_id": request["id"]})
        work = root / ".workbench/tmp" / identity
        work.mkdir(mode=0o700)
        image, error, cleanup = None, None, {"state": "retained"}
        phase = "image-reuse"
        try:
            # Reuse only an image whose bytes and current profile admission are
            # reproduced. Source-only changes never run either upstream tool.
            for summary in storage.image_summaries(root):
                if summary["binding"] == request["binding"]:
                    candidate = storage.load_image(root, summary["id"])
                    try:
                        validate_image(
                            storage.image_path(root, candidate["id"]) / "payload",
                            storage.image_executable(root, candidate),
                            candidate["arguments"],
                        )
                    except ValueError:
                        # A once-valid image may no longer satisfy current
                        # packaged policy. Preserve it; prepare a new image.
                        continue
                    image = candidate
                    break
            if image is None:
                phase = "source-refresh"
                stage = work / "stage"
                storage.copy_manifest(
                    attempt / "source", stage, request["source_files"]
                )
                environment = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LANG": "C.UTF-8",
                    "HOME": str(work),
                    "TMPDIR": str(work),
                    "NO_COLOR": "1",
                }
                argv = [
                    request["tools"]["packwiz"]["path"],
                    "--cache",
                    str(work / "packwiz-cache"),
                    "--config",
                    str(work / "packwiz-config.toml"),
                    "--yes",
                    "refresh",
                ]
                _command(
                    root,
                    attempt,
                    work,
                    "refresh",
                    argv,
                    cwd=stage,
                    environment=environment,
                    cancelled=stopped,
                )
                phase = "index-verification"
                refreshed = packwiz.refreshed_index(stage, request["dependencies"])
                payload = work / "payload"
                seeded = packwiz.initialize_payload(
                    payload, request["dependencies"], request["seeds"]
                )
                storage.write_json(attempt / "seeded.json", {"paths": seeded})
                artifacts = {}
                phase = "locked-downloads"
                for name in ("bootstrap", "installer"):
                    if stopped():
                        raise ValueError("environment preparation cancelled")
                    lock = request["requirements"][name]
                    artifacts[name], _ = fetch_verified_artifact(
                        url=lock["url"],
                        expected_sha256=lock["sha256"],
                        expected_size=lock["size"],
                        state_root=root / ".workbench",
                        label=name,
                    )
                sentinel = work / "empty-launcher"
                sentinel.mkdir(mode=0o700)
                argv = [
                    request["tools"]["java"]["path"],
                    "-cp",
                    str(artifacts["installer"]),
                    "link.infra.packwiz.installer.Main",
                    "--no-gui",
                    "--side",
                    "client",
                    "--pack-folder",
                    str(payload),
                    "--multimc-folder",
                    str(sentinel),
                    (stage / "pack.toml").as_uri(),
                ]
                phase = "pack-install"
                _command(
                    root,
                    attempt,
                    work,
                    "install",
                    argv,
                    cwd=payload,
                    environment=environment,
                    cancelled=stopped,
                    timeout=1800,
                    capture=False,
                )
                phase = "payload-verification"
                packwiz.verify_payload(
                    payload, stage, request["dependencies"], refreshed
                )
                if list(sentinel.iterdir()):
                    raise ValueError("Packwiz unexpectedly wrote launcher metadata")
                if stopped():
                    raise ValueError("environment preparation cancelled")
                phase = "launcher-bootstrap"
                data, instance, capture_request = _prism(
                    work, attempt, request, artifacts["bootstrap"]
                )
                argv = [
                    request["tools"]["prism"]["path"],
                    "--dir",
                    str(data),
                    "--launch",
                    instance.name,
                ]
                phase = "launcher-resolution"
                _command(
                    root,
                    attempt,
                    work,
                    "prism",
                    argv,
                    cwd=work,
                    environment={
                        **environment,
                        "HOME": str(data),
                        "QT_QPA_PLATFORM": "offscreen",
                    },
                    cancelled=stopped,
                    capture=False,
                )
                phase = "handoff-verification"
                handoff = storage.read_json(work / "handoff.json")
                if (
                    storage.seal(
                        "prism-handoff", {k: v for k, v in handoff.items() if k != "id"}
                    )
                    != handoff
                    or handoff["request_id"] != capture_request["id"]
                ):
                    raise ValueError("Prism did not produce the exact no-game handoff")
                _remove_accounts(work)
                phase = "image-assembly"
                arguments = _assemble(work, request, handoff)
                java = Path(request["tools"]["java"]["path"])
                phase = "image-admission"
                validate_image(payload, java, arguments)
                if stopped():
                    raise ValueError(
                        "environment preparation cancelled before publication"
                    )
                phase = "image-publication"
                image = storage.import_image(
                    root,
                    payload,
                    java,
                    arguments,
                    request["binding"],
                    excluded_roots=request["excluded_roots"],
                    toolchain_root=java.parent.parent,
                    launch_input="workbench-launch.txt",
                    _managed_source=True,
                    cancelled=stopped,
                )
        except Exception as exc:  # noqa: BLE001 - never leak launcher/account exceptions
            # Never serialize raw launcher exceptions, argv or account fields.
            error = {
                "type": type(exc).__name__,
                "phase": phase,
                "message": "Preparation failed; inspect phase states or recover this attempt. No game was executed.",
            }
        finally:
            unclosed = any(
                (attempt / label / "console.json").exists()
                and not (attempt / label / "execution.json").exists()
                for label in ("refresh", "install", "prism")
            )
            if not unclosed:
                try:
                    _remove_accounts(work)
                    receipt = storage.cleanup_projection(root, work)
                    storage.write_json(attempt / "cleanup.json", receipt)
                    cleanup = {
                        "state": "trashed",
                        "receipt_uri": (attempt / "cleanup.json").as_uri(),
                        "copied_accounts_removed": True,
                    }
                except Exception:  # noqa: BLE001 - retain uncertain cleanup for recovery
                    cleanup = {
                        "state": "blocked",
                        "reason": "private preparation cleanup needs recovery",
                    }
            else:
                cleanup = {
                    "state": "blocked",
                    "reason": "process closure unverified; private preparation retained",
                }
        result = storage.seal(
            "environment-result",
            {
                "format": "workbench-environment-result-v1",
                "state": "ready" if image else "cancelled" if stopped() else "failed",
                "attempt_id": identity,
                "request_id": request["id"],
                "workspace_uri": request["workspace_uri"],
                "selection_id": request["selection_id"],
                "candidate_id": request["candidate_id"],
                "binding": request["binding"],
                "image_id": image["id"] if image else None,
                "error": error,
                "cleanup": cleanup,
                "phases": {
                    name: storage.read_json(attempt / name / "execution.json")[
                        "stop_reason"
                    ]
                    if (attempt / name / "execution.json").exists()
                    else "closure-unverified"
                    if (attempt / name / "console.json").exists()
                    else "not-started"
                    for name in ("refresh", "install", "prism")
                },
                "authority": {
                    "runtime_launched": False,
                    "source_mutated": False,
                    "qualification_granted": False,
                },
            },
        )
        storage.write_json(attempt / "result.json", result)
        return result
