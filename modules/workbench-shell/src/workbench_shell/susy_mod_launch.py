"""Launch one retained SUSY constituent candidate and prove its loaded JAR.

This consumes the distinct staged-client receipt produced by ``workbench dev
stage``.  It never relabels candidate bytes as a canonical Packwiz
materialization and never mutates the retained stage.  A run-generated Java
agent asks Forge Loader for the exact source JAR behind every expected mod ID,
hashes that file inside the game JVM, and publishes a nonce-bound observation.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1, sha256
import base64
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import time
from typing import Any, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

from workbench_core.runtime_java import JavaRuntimeError, ensure_java_runtime, host_platform
from .runtime_compatibility import (
    RuntimeCompatibilityError,
    apply_compatibility_patches,
)
from .runtime_launch import (
    CLIENT_LOADED_RE,
    RuntimeLaunchError,
    _canonical_bytes,
    _capture_file,
    _initialize_launcher_root,
    _launcher_path,
    _latest_crash,
    _probe_launcher,
    _project_instance,
    _selected_java,
    _timestamp,
    _utc_now,
    _write_receipt,
)
from .runtime_materialize import (
    packwiz_materialization_version,
    verify_packwiz_materialization_receipt_identity,
)
from .susy_mod_dev import (
    RESULT_FORMAT,
    SusyModDevError,
    _digest,
    _file_uri_path,
    _runtime_tree,
)
from workbench_profile_supersymmetry.console import COMPATIBILITY_EXPERIMENTS, RECCOMPLEX_EXPERIMENT_BY_PACK_VERSION
from workbench_api.state_paths import default_suite_state_root


LAUNCH_RESULT_FORMAT = "workbench-susy-mod-launch-result-v1"
LAUNCH_RECEIPT_FORMAT = "workbench-susy-mod-launch-receipt-v1"
STAGE_FORMAT = "workbench-susy-mod-client-stage-v1"
RUN_ID_RE = re.compile(
    r"^susy-mod-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MOD_ID_RE = re.compile(r"^[a-z0-9_.-]+$")
OFFLINE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
MAX_RECORD_BYTES = 16 * 1024 * 1024
DIAGNOSTIC_GUIDANCE_BY_PACK_VERSION = {
    "0.1.16.11": Path(
        "profiles/packs/supersymmetry/diagnostics/"
        "susy-reccomplex-named-variable-v1.json"
    ),
    "0.1.16.12": Path(
        "profiles/packs/supersymmetry/diagnostics/"
        "susy-reccomplex-named-variable-0112-v1.json"
    ),
}


class SusyModLaunchError(RuntimeError):
    """A retained SUSY candidate cannot be launched or proven safely."""


def _compatibility_experiment_scopes(
    suite: Path,
    selected_experiments: Sequence[str],
    *,
    pack_version: Any,
    runtime_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resolve experiment applicability from the pack profile and exact bytes."""

    if not selected_experiments:
        return {}
    profile_path = suite / "profiles/packs/supersymmetry/profile.yaml"
    if not profile_path.is_file() or profile_path.is_symlink():
        raise SusyModLaunchError(
            "Supersymmetry compatibility profile is unavailable"
        )
    try:
        profile_raw = profile_path.read_bytes()
        profile = yaml.safe_load(profile_raw)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SusyModLaunchError(
            "Supersymmetry compatibility profile is unreadable"
        ) from exc
    rows = profile.get("compatibility_experiments") if isinstance(profile, dict) else None
    if not isinstance(rows, list):
        raise SusyModLaunchError(
            "Supersymmetry compatibility profile lacks its experiment registry"
        )
    if not isinstance(pack_version, str) or not pack_version:
        raise SusyModLaunchError(
            "retained Supersymmetry pack version is unavailable"
        )
    resolved: dict[str, dict[str, Any]] = {}
    for experiment_id in selected_experiments:
        spec_relative = COMPATIBILITY_EXPERIMENTS[experiment_id]
        spec_path = suite / spec_relative
        spec = _read_json(spec_path, f"{experiment_id} compatibility experiment")
        patch_id = spec.get("patch_id")
        expected_profile_spec = spec_relative.relative_to(
            "profiles/packs/supersymmetry"
        ).as_posix()
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("patch_id") == patch_id
            and row.get("spec") == expected_profile_spec
        ]
        authority_path = profile_path.resolve()
        authority_raw = profile_raw
        authority_kind = "pack-profile"
        authority_state = (
            matches[0].get("maturity") if len(matches) == 1 else None
        )
        applies_to = matches[0].get("applies_to") if len(matches) == 1 else None
        if len(matches) != 1:
            applies_to = spec.get("applicability")
            authority = spec.get("authority")
            relative_authority = (
                authority.get("path") if isinstance(authority, dict) else None
            )
            overlay_id = (
                authority.get("overlay_id") if isinstance(authority, dict) else None
            )
            if not isinstance(relative_authority, str) or not isinstance(
                overlay_id, str
            ):
                raise SusyModLaunchError(
                    f"{experiment_id} is not selected by a Supersymmetry authority"
                )
            authority_path = (spec_path.parent / relative_authority).resolve()
            profile_root = (
                suite / "profiles/packs/supersymmetry"
            ).resolve()
            if (
                not authority_path.is_relative_to(profile_root)
                or not authority_path.is_file()
                or authority_path.is_symlink()
            ):
                raise SusyModLaunchError(
                    f"{experiment_id} overlay authority path is unsafe"
                )
            try:
                authority_raw = authority_path.read_bytes()
                authority_record = json.loads(authority_raw.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise SusyModLaunchError(
                    f"{experiment_id} overlay authority is unreadable"
                ) from exc
            if (
                not isinstance(authority_record, dict)
                or authority_record.get("format")
                != "workbench-cleanroom-compatibility-overlays-v1"
                or authority_record.get("schema_version") != 1
            ):
                raise SusyModLaunchError(
                    f"{experiment_id} overlay authority has the wrong identity"
                )
            overlays = (
                authority_record.get("overlays")
                if isinstance(authority_record, dict)
                else None
            )
            overlay_matches = [
                row
                for row in overlays or []
                if isinstance(row, dict) and row.get("overlay_id") == overlay_id
            ]
            target = spec.get("target")
            overlay = overlay_matches[0] if len(overlay_matches) == 1 else None
            overlay_target = (
                overlay.get("target") if isinstance(overlay, dict) else None
            )
            replacement = (
                overlay_target.get("replacement")
                if isinstance(overlay_target, dict)
                else None
            )
            input_artifact = (
                overlay.get("input_artifact")
                if isinstance(overlay, dict)
                else None
            )
            if (
                not isinstance(target, dict)
                or not isinstance(input_artifact, dict)
                or not isinstance(overlay_target, dict)
                or not isinstance(replacement, dict)
                or overlay.get("profile_state") != "provisional"
                or input_artifact.get("sha256") != target.get("sha256")
                or overlay_target.get("entry") != target.get("entry")
                or overlay_target.get("input_entry_sha256")
                != target.get("entry_sha256")
                or replacement.get("from_constant_pool_utf8")
                != target.get("find_utf8")
                or replacement.get("to_constant_pool_utf8")
                != target.get("replace_utf8")
            ):
                raise SusyModLaunchError(
                    f"{experiment_id} does not match its provisional overlay authority"
                )
            authority_kind = "provisional-overlay-manifest"
            authority_state = "provisional"
        elif authority_state != "experimental":
            raise SusyModLaunchError(
                f"{experiment_id} is not selected as an experimental profile input"
            )
        if not isinstance(applies_to, dict) or any(
            not isinstance(applies_to.get(key), str) or not applies_to[key]
            for key in ("pack_version", "susycore", "recurrent_complex")
        ):
            raise SusyModLaunchError(
                f"{experiment_id} lacks an exact Supersymmetry applicability scope"
            )
        if pack_version != applies_to["pack_version"]:
            raise SusyModLaunchError(
                f"{experiment_id} applies to Supersymmetry "
                f"{applies_to['pack_version']}, not retained {pack_version}"
            )
        expected_paths = (
            f"mods/supersymmetry-v{applies_to['susycore']}.jar",
            f"mods/RecurrentComplex-{applies_to['recurrent_complex']}.jar",
        )
        missing = [path for path in expected_paths if path not in runtime_records]
        if missing:
            raise SusyModLaunchError(
                f"{experiment_id} does not match the retained runtime: "
                + ", ".join(missing)
            )
        target = spec.get("target")
        target_path = (
            str(target.get("path")).removeprefix(".minecraft/")
            if isinstance(target, dict)
            else ""
        )
        target_record = runtime_records.get(target_path)
        if (
            target_path not in expected_paths
            or not isinstance(target_record, Mapping)
            or target_record.get("sha256") != target.get("sha256")
        ):
            raise SusyModLaunchError(
                f"{experiment_id} target bytes are outside its declared scope"
            )
        resolved[experiment_id] = {
            "authority_uri": authority_path.as_uri(),
            "authority_sha256": sha256(authority_raw).hexdigest(),
            "authority_kind": authority_kind,
            "authority_state": authority_state,
            "patch_id": patch_id,
            "pack_version": pack_version,
            "susycore": applies_to["susycore"],
            "recurrent_complex": applies_to["recurrent_complex"],
            "runtime_paths": list(expected_paths),
        }
    return resolved


AGENT_SOURCE = r'''package dev.cleanroommc.workbench;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;
import java.lang.instrument.Instrumentation;
import java.lang.management.ManagementFactory;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.List;
import java.util.Map;

public final class CandidateLoadedAgent {
    private CandidateLoadedAgent() {}

    public static void premain(final String arguments, Instrumentation instrumentation) {
        final Thread probe = new Thread(new Runnable() {
            @Override public void run() {
                observe(arguments, instrumentation);
            }
        }, "workbench-candidate-loaded-probe");
        probe.setDaemon(true);
        probe.start();
    }

    private static void observe(String arguments, Instrumentation instrumentation) {
        try {
            String[] fields = arguments.split(":", 4);
            if (fields.length != 4 || fields[0].isEmpty()) return;
            final String nonce = fields[0];
            final Path output = Paths.get(new String(
                Base64.getUrlDecoder().decode(fields[1]), StandardCharsets.UTF_8));
            final String[] expected = new String(
                Base64.getUrlDecoder().decode(fields[2]), StandardCharsets.UTF_8).split(",");
            final Path readyLog = Paths.get(new String(
                Base64.getUrlDecoder().decode(fields[3]), StandardCharsets.UTF_8));
            final long deadline = System.nanoTime() + 900000000000L;
            boolean ready = false;
            while (System.nanoTime() < deadline) {
                try {
                    if (!ready) {
                        ready = hasLoaderCheckpoint(readyLog);
                        if (!ready) {
                            Thread.sleep(250L);
                            continue;
                        }
                    }
                    Class<?> loaderClass = null;
                    for (Class<?> loaded : instrumentation.getAllLoadedClasses()) {
                        ClassLoader defining = loaded.getClassLoader();
                        if (loaded.getName().equals("net.minecraftforge.fml.common.Loader")
                            && defining != null
                            && defining.getClass().getName().equals(
                                "net.minecraft.launchwrapper.LaunchClassLoader")) {
                            loaderClass = loaded;
                            break;
                        }
                    }
                    if (loaderClass == null) {
                        Thread.sleep(250L);
                        continue;
                    }
                    java.lang.reflect.Field instanceField =
                        loaderClass.getDeclaredField("instance");
                    instanceField.setAccessible(true);
                    Object loader = instanceField.get(null);
                    if (loader == null) {
                        Thread.sleep(250L);
                        continue;
                    }
                    Method indexedMethod = loaderClass.getMethod("getIndexedModList");
                    Object raw = indexedMethod.invoke(loader);
                    if (!(raw instanceof Map)) {
                        Thread.sleep(250L);
                        continue;
                    }
                    Map<?, ?> indexed = (Map<?, ?>) raw;
                    List<String> rows = new ArrayList<String>();
                    boolean complete = true;
                    for (String modId : expected) {
                        Object container = indexed.get(modId);
                        if (container == null) {
                            complete = false;
                            break;
                        }
                        Class<?> containerClass = container.getClass();
                        File source = (File) containerClass.getMethod("getSource").invoke(container);
                        String observedId = String.valueOf(
                            containerClass.getMethod("getModId").invoke(container));
                        String version = String.valueOf(
                            containerClass.getMethod("getVersion").invoke(container));
                        File canonical = source.getCanonicalFile();
                        rows.add("{\"mod_id\":\"" + escape(observedId)
                            + "\",\"version\":\"" + escape(version)
                            + "\",\"source_path\":\"" + escape(canonical.getPath())
                            + "\",\"sha256\":\"" + hex(canonical)
                            + "\",\"size\":" + canonical.length() + "}");
                    }
                    if (!complete) {
                        Thread.sleep(250L);
                        continue;
                    }
                    Collections.sort(rows);
                    StringBuilder body = new StringBuilder();
                    body.append("{\"format\":\"workbench-forge-loaded-source-probe-v1\"");
                    body.append(",\"nonce\":\"").append(escape(nonce)).append("\"");
                    body.append(",\"process\":\"").append(escape(
                        ManagementFactory.getRuntimeMXBean().getName())).append("\"");
                    body.append(",\"pid\":").append(processId());
                    body.append(",\"mods\":[");
                    for (int index = 0; index < rows.size(); index++) {
                        if (index > 0) body.append(',');
                        body.append(rows.get(index));
                    }
                    body.append("]}\n");
                    Files.createDirectories(output.getParent());
                    Path temporary = output.resolveSibling(output.getFileName().toString() + ".tmp");
                    FileOutputStream stream = new FileOutputStream(temporary.toFile(), false);
                    try {
                        stream.write(body.toString().getBytes(StandardCharsets.UTF_8));
                        stream.getFD().sync();
                    } finally {
                        stream.close();
                    }
                    try {
                        Files.move(temporary, output, StandardCopyOption.ATOMIC_MOVE,
                            StandardCopyOption.REPLACE_EXISTING);
                    } catch (AtomicMoveNotSupportedException exception) {
                        Files.move(temporary, output, StandardCopyOption.REPLACE_EXISTING);
                    }
                    return;
                } catch (NoSuchMethodException exception) {
                    Thread.sleep(250L);
                }
            }
        } catch (Throwable ignored) {
            // Workbench treats an absent or malformed receipt as failed proof.
        }
    }

    private static String hex(File source) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        BufferedInputStream stream = new BufferedInputStream(new FileInputStream(source));
        try {
            byte[] buffer = new byte[1048576];
            int count;
            while ((count = stream.read(buffer)) >= 0) {
                if (count > 0) digest.update(buffer, 0, count);
            }
        } finally {
            stream.close();
        }
        StringBuilder value = new StringBuilder();
        for (byte item : digest.digest()) value.append(String.format("%02x", item & 0xff));
        return value.toString();
    }

    private static long processId() {
        String runtime = ManagementFactory.getRuntimeMXBean().getName();
        int separator = runtime.indexOf('@');
        try {
            return Long.parseLong(separator < 0 ? runtime : runtime.substring(0, separator));
        } catch (NumberFormatException exception) {
            return -1L;
        }
    }

    private static boolean hasLoaderCheckpoint(Path log) {
        File file = log.toFile();
        if (!file.isFile()) return false;
        try {
            RandomAccessFile stream = new RandomAccessFile(file, "r");
            try {
                long length = stream.length();
                int count = (int)Math.min(length, 65536L);
                byte[] tail = new byte[count];
                stream.seek(length - count);
                stream.readFully(tail);
                return new String(tail, StandardCharsets.UTF_8).contains(
                    "Forge Mod Loader has successfully loaded");
            } finally {
                stream.close();
            }
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static String escape(String value) {
        StringBuilder escaped = new StringBuilder();
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character == '\\' || character == '"') escaped.append('\\').append(character);
            else if (character == '\n') escaped.append("\\n");
            else if (character == '\r') escaped.append("\\r");
            else if (character == '\t') escaped.append("\\t");
            else if (character < 32) escaped.append('?');
            else escaped.append(character);
        }
        return escaped.toString();
    }
}
'''


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SusyModLaunchError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise SusyModLaunchError(f"{label} must be a regular file: {path}")
    if info.st_size > MAX_RECORD_BYTES:
        raise SusyModLaunchError(f"{label} exceeds the record limit: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SusyModLaunchError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SusyModLaunchError(f"{label} must be a JSON object: {path}")
    return value


def _file_digest(path: Path, algorithm: str) -> tuple[str, int]:
    digest = sha1() if algorithm == "sha1" else sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _base_identity(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".minecraft":
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise SusyModLaunchError(f"instance base contains a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            entries.append({"kind": "directory", "path": relative.as_posix()})
            continue
        if not stat.S_ISREG(info.st_mode):
            raise SusyModLaunchError(f"instance base contains a special file: {relative}")
        digest, size = _file_digest(path, "sha256")
        entries.append({
            "kind": "file",
            "path": relative.as_posix(),
            "sha256": digest,
            "size": size,
            "mode": stat.S_IMODE(info.st_mode),
        })
    return {
        "tree_sha256": "sha256:" + sha256(_canonical_bytes(entries)).hexdigest(),
        "entry_count": len(entries),
    }


def _validate_retained_stage(
    suite_root: Path,
    run_id: str,
) -> tuple[Path, dict[str, Any], dict[str, Any], Path, Path]:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise SusyModLaunchError("launch requires an exact retained SUSY run ID")
    runs_root = (default_suite_state_root(suite_root) / "dev-runs").resolve()
    lexical_run_root = runs_root / run_id
    if lexical_run_root.is_symlink():
        raise SusyModLaunchError(f"retained SUSY run is unsafe: {run_id}")
    run_root = lexical_run_root.resolve()
    if run_root.parent != runs_root or not run_root.is_dir():
        raise SusyModLaunchError(f"retained SUSY run is missing: {run_id}")
    result = _read_json(run_root / "result.json", "SUSY mod build result")
    stage = _read_json(
        run_root / "runtime/client-stage-v1.json",
        "SUSY client stage receipt",
    )
    if (
        result.get("format") != RESULT_FORMAT
        or result.get("schema_version") != 1
        or result.get("run_id") != run_id
        or result.get("outcome") != "passed"
        or result.get("failed_stage") is not None
        or result.get("runtime", {}).get("client") != stage
    ):
        raise SusyModLaunchError("retained result is not a passing client stage")
    result_without_id = {key: value for key, value in result.items() if key != "result_id"}
    if result.get("result_id") != "workbench-susy-mod-dev-result:" + _digest(result_without_id):
        raise SusyModLaunchError("retained SUSY result identity has drifted")
    if (
        stage.get("format") != STAGE_FORMAT
        or stage.get("schema_version") != 1
        or stage.get("state") != "staged"
        or stage.get("run_id") != run_id
        or stage.get("plan_id") != result.get("plan_id")
        or stage.get("canonical_materialization_mutated") is not False
    ):
        raise SusyModLaunchError("retained SUSY client-stage identity is invalid")
    stage_without_id = {key: value for key, value in stage.items() if key != "stage_id"}
    if stage.get("stage_id") != "workbench-susy-mod-client-stage:" + _digest(stage_without_id):
        raise SusyModLaunchError("retained SUSY client-stage digest has drifted")

    target = stage.get("target")
    source = stage.get("source")
    replacement = stage.get("replacement")
    if not all(isinstance(item, dict) for item in (target, source, replacement)):
        raise SusyModLaunchError("retained SUSY client-stage record is incomplete")
    assert isinstance(target, dict)
    assert isinstance(source, dict)
    assert isinstance(replacement, dict)
    staged_lexical = _file_uri_path(target.get("instance_uri"), "staged client")
    runtime_root = run_root / "runtime"
    if runtime_root.is_symlink() or staged_lexical.is_symlink():
        raise SusyModLaunchError("staged client must not be a symbolic link")
    staged_instance = staged_lexical.resolve()
    expected_staged = (run_root / "runtime/client").resolve()
    if (
        staged_instance != expected_staged
        or not staged_instance.is_relative_to(run_root)
        or not staged_instance.is_dir()
        or target.get("launch_ready") is not True
        or target.get("launched") is not False
    ):
        raise SusyModLaunchError("retained staged client target is not launch-ready")
    canonical_lexical = _file_uri_path(source.get("instance_uri"), "canonical client")
    if canonical_lexical.is_symlink():
        raise SusyModLaunchError("canonical client must not be a symbolic link")
    canonical_instance = canonical_lexical.resolve()
    if not canonical_instance.is_dir():
        raise SusyModLaunchError("canonical client materialization is unavailable")

    staged_summary, staged_records = _runtime_tree(staged_instance / ".minecraft")
    source_summary, source_records = _runtime_tree(canonical_instance / ".minecraft")
    if staged_summary != target.get("payload") or source_summary != source.get("payload"):
        raise SusyModLaunchError("staged or canonical client payload has drifted")
    if _base_identity(staged_instance) != _base_identity(canonical_instance):
        raise SusyModLaunchError("staged client base differs from its canonical source")

    relative = replacement.get("path")
    candidate = replacement.get("candidate")
    baseline = replacement.get("baseline")
    if (
        not isinstance(relative, str)
        or not relative.startswith("mods/")
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not isinstance(candidate, dict)
        or not isinstance(baseline, dict)
        or target.get("changed_paths") != [relative]
    ):
        raise SusyModLaunchError("staged candidate replacement identity is invalid")
    changed = sorted(
        path
        for path in set(source_records) | set(staged_records)
        if source_records.get(path) != staged_records.get(path)
    )
    if changed != [relative]:
        raise SusyModLaunchError("staged client changes more than the candidate path")
    candidate_lexical = staged_instance / ".minecraft" / relative
    baseline_lexical = canonical_instance / ".minecraft" / relative
    if candidate_lexical.is_symlink() or baseline_lexical.is_symlink():
        raise SusyModLaunchError("candidate or baseline JAR must not be a symbolic link")
    candidate_path = candidate_lexical.resolve()
    baseline_path = baseline_lexical.resolve()
    if (
        candidate_path.parent != (staged_instance / ".minecraft" / "mods").resolve()
        or baseline_path.parent != (canonical_instance / ".minecraft" / "mods").resolve()
        or not candidate_path.is_file()
        or not baseline_path.is_file()
    ):
        raise SusyModLaunchError("candidate or baseline JAR path is unsafe")
    if staged_records.get(relative) != {
        "sha256": candidate.get("sha256"),
        "size": candidate.get("size"),
        "mode": staged_records.get(relative, {}).get("mode"),
    }:
        raise SusyModLaunchError("staged candidate JAR identity has drifted")
    expected_mod_ids = candidate.get("mod_ids")
    if (
        not isinstance(expected_mod_ids, list)
        or not expected_mod_ids
        or len(set(expected_mod_ids)) != len(expected_mod_ids)
        or any(not isinstance(item, str) or MOD_ID_RE.fullmatch(item) is None for item in expected_mod_ids)
    ):
        raise SusyModLaunchError("staged candidate lacks exact mod IDs")
    algorithm = baseline.get("hash_format")
    if algorithm not in {"sha1", "sha256"}:
        raise SusyModLaunchError("baseline hash format is unsupported")
    baseline_hash, baseline_size = _file_digest(baseline_path, algorithm)
    baseline_sha256, _ = _file_digest(baseline_path, "sha256")
    if (
        baseline_hash != baseline.get("hash")
        or baseline_sha256 != baseline.get("sha256")
        or baseline_size != baseline.get("size")
    ):
        raise SusyModLaunchError("canonical baseline JAR identity has drifted")

    receipt_lexical = _file_uri_path(source.get("receipt_uri"), "materialization receipt")
    if receipt_lexical.is_symlink():
        raise SusyModLaunchError("materialization receipt must not be a symbolic link")
    receipt_path = receipt_lexical.resolve()
    materialization = _read_json(receipt_path, "canonical materialization receipt")
    materialization_target = materialization.get("target")
    launcher_record = materialization.get("launcher")
    materialization_version = packwiz_materialization_version(materialization)
    if (
        materialization_version != 2
        or not verify_packwiz_materialization_receipt_identity(materialization)
        or materialization.get("state") != "materialized"
        or materialization.get("readiness") != "pack-payload-installed"
        or materialization.get("materialization_id") != stage.get("materialization_id")
        or not isinstance(materialization_target, dict)
        or not isinstance(launcher_record, dict)
        or _file_uri_path(
            materialization_target.get("instance_root_uri"),
            "materialization target",
        ).resolve() != canonical_instance
        or _file_uri_path(
            materialization_target.get("receipt_uri"),
            "materialization receipt target",
        ).resolve() != receipt_path
    ):
        raise SusyModLaunchError("canonical materialization receipt does not bind the stage")
    materialization_payload = materialization.get("payload")
    if not isinstance(materialization_payload, dict) or {
        field: materialization_payload.get(field)
        for field in ("tree_sha256", "file_count", "total_bytes")
    } != source.get("payload"):
        raise SusyModLaunchError("canonical materialization payload does not bind the stage")
    payload_root = _file_uri_path(
        materialization_payload.get("root_uri"),
        "materialization payload root",
    )
    if payload_root.is_symlink() or payload_root.resolve() != (
        canonical_instance / ".minecraft"
    ).resolve():
        raise SusyModLaunchError("canonical materialization payload root has drifted")
    manifest_sha256, _ = _file_digest(canonical_instance / "mmc-pack.json", "sha256")
    if manifest_sha256 != launcher_record.get("manifest_sha256_after"):
        raise SusyModLaunchError("canonical launcher manifest has drifted")

    artifacts = result.get("artifact_set")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise SusyModLaunchError("retained stage lacks one selected build artifact")
    artifact = artifacts[0]
    if (
        not isinstance(artifact, dict)
        or artifact.get("sha256") != candidate.get("sha256")
        or artifact.get("size") != candidate.get("size")
        or sorted(artifact.get("mod_ids", [])) != sorted(expected_mod_ids)
    ):
        raise SusyModLaunchError("build artifact and staged candidate identities differ")
    return run_root, result, stage, staged_instance, candidate_path


def _default_launcher_executable(launcher: str) -> Path:
    names = ["prismlauncher", "prismlauncher.exe"] if launcher == "prism" else ["multimc", "multimc.exe"]
    candidates: list[Path] = []
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found).resolve())
    if launcher == "prism":
        candidates.extend(
            path.resolve()
            for path in (Path("/mnt") / "c" / "Users").glob(
                "*/AppData/Local/Programs/PrismLauncher/prismlauncher.exe"
            )
            if path.is_file() and not path.is_symlink()
        )
    unique = sorted(set(candidates), key=str)
    if len(unique) != 1:
        raise SusyModLaunchError(
            f"cannot choose one {launcher} launcher executable; pass --launcher-executable"
        )
    return unique[0]


def _default_launcher_root(executable: Path, launcher: str) -> Path:
    if executable.suffix.casefold() == ".exe":
        parts = executable.parts
        try:
            user_index = parts.index("Users") + 1
            user_root = Path(*parts[: user_index + 1])
        except (ValueError, IndexError) as exc:
            raise SusyModLaunchError("cannot derive Windows launcher root") from exc
        name = "PrismLauncher" if launcher == "prism" else "MultiMC"
        candidate = user_root / "AppData/Roaming" / name
    else:
        name = "PrismLauncher" if launcher == "prism" else "MultiMC"
        candidate = Path.home() / ".local/share" / name
    if not candidate.is_dir() or candidate.is_symlink():
        raise SusyModLaunchError(
            f"derived launcher root is unavailable; pass --launcher-root: {candidate}"
        )
    return candidate.resolve()


def _safe_record_text(value: bytes, limit: int = 64 * 1024) -> str:
    return value[:limit].decode("utf-8", errors="replace")


def _compile_probe_agent(
    build_root: Path,
    *,
    projected_instance: Path,
    java_executable: Path,
    launcher_host: Mapping[str, str],
    nonce: str,
    expected_mod_ids: Sequence[str],
) -> tuple[dict[str, Any], Path, str, Path]:
    """Compile a disposable Java-8 agent with the selected runtime's javac."""

    root = build_root
    if root.exists() or root.is_symlink():
        raise SusyModLaunchError("launcher projection already contains a load probe")
    source = root / "src/dev/cleanroommc/workbench/CandidateLoadedAgent.java"
    classes = root / "classes"
    output = (
        projected_instance
        / ".workbench/candidate-loaded-probe/loaded-source-v1.json"
    )
    source.parent.mkdir(parents=True)
    classes.mkdir()
    source.write_text(AGENT_SOURCE, encoding="utf-8")

    compiler_name = "javac.exe" if launcher_host["os"] == "windows" else "javac"
    compiler = java_executable.with_name(compiler_name)
    if not compiler.is_file() or compiler.is_symlink():
        raise SusyModLaunchError(
            "selected launcher Java has no sibling javac; a full managed JDK is required"
        )
    compiler_digest, compiler_size = _file_digest(compiler, "sha256")
    try:
        compiler_version = subprocess.run(
            [str(compiler), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SusyModLaunchError("candidate load probe compiler probe failed") from exc
    if compiler_version.returncode or not compiler_version.stdout.strip():
        raise SusyModLaunchError("candidate load probe compiler probe failed")
    command = [
        str(compiler),
        "--release",
        "8",
        "-g:none",
        "-encoding",
        "UTF-8",
        "-d",
        _launcher_path(classes, launcher_host),
        _launcher_path(source, launcher_host),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SusyModLaunchError("candidate load probe compilation failed") from exc
    if completed.returncode:
        detail = _safe_record_text(completed.stdout).strip()
        raise SusyModLaunchError(
            "candidate load probe compilation failed"
            + (f": {detail}" if detail else "")
        )
    class_files = sorted(
        classes.rglob("CandidateLoadedAgent*.class"),
        key=lambda item: item.relative_to(classes).as_posix(),
    )
    expected_classes = {
        "dev/cleanroommc/workbench/CandidateLoadedAgent.class",
        "dev/cleanroommc/workbench/CandidateLoadedAgent$1.class",
    }
    if (
        {path.relative_to(classes).as_posix() for path in class_files}
        != expected_classes
        or any(path.is_symlink() for path in class_files)
        or any(path.read_bytes()[:8] != b"\xca\xfe\xba\xbe\x00\x00\x004" for path in class_files)
    ):
        raise SusyModLaunchError("candidate load probe compiler output is incomplete")
    jar = root / "workbench-candidate-loaded-agent.jar"
    manifest = (
        "Manifest-Version: 1.0\r\n"
        "Premain-Class: dev.cleanroommc.workbench.CandidateLoadedAgent\r\n"
        "Can-Redefine-Classes: false\r\n\r\n"
    ).encode("ascii")
    try:
        with ZipFile(jar, "x", compression=ZIP_DEFLATED) as archive:
            archive.writestr("META-INF/MANIFEST.MF", manifest)
            for class_file in class_files:
                archive.write(class_file, class_file.relative_to(classes).as_posix())
    except OSError as exc:
        raise SusyModLaunchError("cannot package the candidate load probe") from exc
    jar_digest, jar_size = _file_digest(jar, "sha256")
    source_digest, source_size = _file_digest(source, "sha256")
    output_argument = base64.urlsafe_b64encode(
        _launcher_path(output, launcher_host).encode("utf-8")
    ).decode("ascii")
    mods_argument = base64.urlsafe_b64encode(
        ",".join(sorted(expected_mod_ids)).encode("utf-8")
    ).decode("ascii")
    log_argument = base64.urlsafe_b64encode(
        _launcher_path(
            projected_instance / ".minecraft/logs/latest.log", launcher_host
        ).encode("utf-8")
    ).decode("ascii")
    agent_argument = f"{nonce}:{output_argument}:{mods_argument}:{log_argument}"
    projected_jar = (
        projected_instance
        / ".workbench/candidate-loaded-probe/"
        "workbench-candidate-loaded-agent.jar"
    )
    agent_path = _launcher_path(projected_jar, launcher_host)
    if '"' in agent_path or "=" in agent_path:
        raise SusyModLaunchError("candidate load probe path contains an unsafe separator")
    javaagent = (
        f'-javaagent:"{agent_path}"={agent_argument}'
        if any(character.isspace() for character in agent_path)
        else f"-javaagent:{agent_path}={agent_argument}"
    )
    return {
        "format": "workbench-forge-loaded-source-probe-build-v1",
        "compiler_uri": compiler.as_uri(),
        "compiler_sha256": compiler_digest,
        "compiler_size": compiler_size,
        "compiler_version": _safe_record_text(compiler_version.stdout).strip(),
        "source_sha256": source_digest,
        "source_size": source_size,
        "jar_uri": jar.as_uri(),
        "projected_jar_uri": projected_jar.as_uri(),
        "jar_sha256": jar_digest,
        "jar_size": jar_size,
        "class_count": len(class_files),
        "target_release": 8,
        "output_uri": output.as_uri(),
    }, output, javaagent, jar


def _config_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SusyModLaunchError("projected instance.cfg is unreadable") from exc
    values = [line.split("=", 1)[1] for line in lines if line.startswith(f"{key}=")]
    if len(values) > 1:
        raise SusyModLaunchError(f"projected instance.cfg duplicates {key}")
    return values[0] if values else None


def _probe_configuration(source_instance: Path, javaagent: str) -> dict[str, str]:
    config = source_instance / "instance.cfg"
    existing = _config_value(config, "JvmArgs")
    if existing and existing not in {"@Invalid()", "@Variant(\\0\\0\\0\\0)"}:
        raise SusyModLaunchError(
            "staged instance already has JVM arguments; V1 will not rewrite them"
        )
    return {"JvmArgs": javaagent, "OverrideJavaArgs": "true"}


def _normalize_loaded_path(value: str, host: Mapping[str, str]) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    return normalized.casefold() if host["os"] == "windows" else normalized


def _validate_loaded_source_proof(
    path: Path,
    *,
    nonce: str,
    expected_mod_ids: Sequence[str],
    expected_source_path: Path,
    expected_sha256: str,
    expected_size: int,
    launcher_host: Mapping[str, str],
) -> dict[str, Any]:
    proof = _read_json(path, "Forge loaded-source proof")
    required = {"format", "nonce", "process", "pid", "mods"}
    if set(proof) != required or proof.get("format") != (
        "workbench-forge-loaded-source-probe-v1"
    ) or proof.get("nonce") != nonce:
        raise SusyModLaunchError("Forge loaded-source proof identity is invalid")
    pid = proof.get("pid")
    process = proof.get("process")
    rows = proof.get("mods")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or not isinstance(process, str)
        or not process
        or process.split("@", 1)[0] != str(pid)
        or not isinstance(rows, list)
        or len(rows) != len(expected_mod_ids)
    ):
        raise SusyModLaunchError("Forge loaded-source proof is incomplete")
    expected_path = _normalize_loaded_path(
        _launcher_path(expected_source_path, launcher_host), launcher_host
    )
    seen: set[str] = set()
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "mod_id", "version", "source_path", "sha256", "size"
        }:
            raise SusyModLaunchError("Forge loaded-source proof row is malformed")
        mod_id = row.get("mod_id")
        if (
            not isinstance(mod_id, str)
            or mod_id in seen
            or mod_id not in expected_mod_ids
            or not isinstance(row.get("version"), str)
            or not row["version"]
            or not isinstance(row.get("source_path"), str)
            or _normalize_loaded_path(row["source_path"], launcher_host) != expected_path
            or row.get("sha256") != expected_sha256
            or row.get("size") != expected_size
        ):
            raise SusyModLaunchError("Forge loaded-source proof does not match the candidate")
        seen.add(mod_id)
        normalized_rows.append(dict(row))
    if seen != set(expected_mod_ids):
        raise SusyModLaunchError("Forge loaded-source proof omits an expected mod ID")
    proof_digest, proof_size = _file_digest(path, "sha256")
    return {
        "format": proof["format"],
        "nonce": nonce,
        "process": process,
        "pid": pid,
        "mods": sorted(normalized_rows, key=lambda row: row["mod_id"]),
        "record_uri": path.as_uri(),
        "record_sha256": proof_digest,
        "record_size": proof_size,
    }


_WINDOWS_QUERY = r'''
$needle = $env:WORKBENCH_SUSY_INSTANCE_ID
$rows = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      ($_.Name -eq "java.exe" -or $_.Name -eq "javaw.exe") -and
      $_.CommandLine -and $_.CommandLine.IndexOf(
        $needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } |
    ForEach-Object {
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        creation_date = [string]$_.CreationDate
        executable_path = [string]$_.ExecutablePath
        image = [string]$_.Name
      }
    }
)
ConvertTo-Json -Compress -InputObject $rows
'''.strip()

_WINDOWS_LAUNCHER_QUERY = r'''
$expected = $env:WORKBENCH_SUSY_LAUNCHER_EXECUTABLE
$rows = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.ExecutablePath -and $_.ExecutablePath -ieq $expected
    } |
    ForEach-Object {
      $process = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        creation_date = [string]$_.CreationDate
        executable_path = [string]$_.ExecutablePath
        responding = [bool]($process -and $process.Responding)
      }
    }
)
ConvertTo-Json -Compress -InputObject $rows
'''.strip()

_WINDOWS_INSTANCE_LAUNCHER_QUERY = r'''
$expected = $env:WORKBENCH_SUSY_LAUNCHER_EXECUTABLE
$instance = $env:WORKBENCH_SUSY_LAUNCHER_INSTANCE_ID
$rows = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.ExecutablePath -and $_.ExecutablePath -ieq $expected -and
      $_.CommandLine -and $_.CommandLine.IndexOf(
        $instance, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } |
    ForEach-Object {
      $process = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        creation_date = [string]$_.CreationDate
        executable_path = [string]$_.ExecutablePath
        responding = [bool]($process -and $process.Responding)
      }
    }
)
ConvertTo-Json -Compress -InputObject $rows
'''.strip()

_WINDOWS_ACT = r'''
$expected = @(ConvertFrom-Json -InputObject $env:WORKBENCH_SUSY_PROCESS_IDENTITIES)
$force = $env:WORKBENCH_SUSY_FORCE -eq "true"
$acted = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $candidate = $_
      @($expected | Where-Object {
        [int]$_.pid -eq [int]$candidate.ProcessId -and
        [string]$_.creation_date -eq [string]$candidate.CreationDate -and
        [string]$_.executable_path -ieq [string]$candidate.ExecutablePath
      }).Count -eq 1
    } |
    ForEach-Object {
      if ($force) {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      } else {
        $process = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -ne 0) {
          [void]$process.CloseMainWindow()
        }
      }
      [int]$_.ProcessId
    }
)
ConvertTo-Json -Compress -InputObject $acted
'''.strip()


def _powershell(script: str, environment: Mapping[str, str]) -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        raise SusyModLaunchError("PowerShell is required for Windows process custody")
    selected_environment = os.environ.copy()
    selected_environment.update(environment)
    if os.name != "nt" and executable.casefold().endswith(".exe"):
        existing = [
            item for item in selected_environment.get("WSLENV", "").split(":")
            if item
        ]
        existing_names = {item.split("/", 1)[0] for item in existing}
        existing.extend(
            name for name in environment if name not in existing_names
        )
        selected_environment["WSLENV"] = ":".join(existing)
    encoded_script = (
        "$utf8 = New-Object System.Text.UTF8Encoding($false); "
        "[Console]::OutputEncoding = $utf8; $OutputEncoding = $utf8;\n"
        + script
    )
    try:
        completed = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                encoded_script,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
            env=selected_environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SusyModLaunchError("Windows process custody command failed") from exc
    if len(completed.stdout) > 1024 * 1024 or len(completed.stderr) > 64 * 1024:
        raise SusyModLaunchError("Windows process custody output is excessive")
    try:
        stdout = completed.stdout.decode("utf-8")
        stderr = completed.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SusyModLaunchError("Windows process custody output is not UTF-8") from exc
    if completed.returncode:
        detail = stderr.strip()
        raise SusyModLaunchError(
            "Windows process custody command failed"
            + (f": {detail}" if detail else "")
        )
    return stdout.strip()


def _parse_windows_rows(raw: str) -> dict[int, dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise SusyModLaunchError("Windows process inventory is malformed") from exc
    if not isinstance(value, list) or len(value) > 64:
        raise SusyModLaunchError("Windows process inventory is excessive")
    rows: dict[int, dict[str, Any]] = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != {
            "pid", "creation_date", "executable_path", "image"
        }:
            raise SusyModLaunchError("Windows process inventory row is malformed")
        pid = row.get("pid")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or pid in rows
            or not isinstance(row.get("creation_date"), str)
            or not row["creation_date"]
            or not isinstance(row.get("executable_path"), str)
            or not row["executable_path"]
            or not isinstance(row.get("image"), str)
            or not row["image"]
        ):
            raise SusyModLaunchError("Windows process inventory identity is invalid")
        rows[pid] = dict(row)
    return rows


def _windows_owned_processes(instance_id: str) -> dict[int, dict[str, Any]]:
    return _parse_windows_rows(_powershell(
        _WINDOWS_QUERY,
        {"WORKBENCH_SUSY_INSTANCE_ID": instance_id},
    ))


def _parse_windows_launcher_rows(raw: str) -> dict[int, dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise SusyModLaunchError(
            "Windows launcher process inventory is malformed"
        ) from exc
    if not isinstance(value, list) or len(value) > 16:
        raise SusyModLaunchError(
            "Windows launcher process inventory is excessive"
        )
    rows: dict[int, dict[str, Any]] = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != {
            "pid", "creation_date", "executable_path", "responding"
        }:
            raise SusyModLaunchError(
                "Windows launcher process inventory row is malformed"
            )
        pid = row.get("pid")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or pid in rows
            or not isinstance(row.get("creation_date"), str)
            or not row["creation_date"]
            or not isinstance(row.get("executable_path"), str)
            or not row["executable_path"]
            or not isinstance(row.get("responding"), bool)
        ):
            raise SusyModLaunchError(
                "Windows launcher process inventory identity is invalid"
            )
        rows[pid] = dict(row)
    return rows


def _windows_launcher_processes(
    executable_path: str,
) -> dict[int, dict[str, Any]]:
    return _parse_windows_launcher_rows(_powershell(
        _WINDOWS_LAUNCHER_QUERY,
        {"WORKBENCH_SUSY_LAUNCHER_EXECUTABLE": executable_path},
    ))


def _windows_instance_launcher_processes(
    executable_path: str,
    instance_id: str,
) -> dict[int, dict[str, Any]]:
    return _parse_windows_launcher_rows(_powershell(
        _WINDOWS_INSTANCE_LAUNCHER_QUERY,
        {
            "WORKBENCH_SUSY_LAUNCHER_EXECUTABLE": executable_path,
            "WORKBENCH_SUSY_LAUNCHER_INSTANCE_ID": instance_id,
        },
    ))


def _windows_process_action(
    identities: Mapping[int, Mapping[str, Any]], *, force: bool
) -> None:
    if not identities:
        return
    payload = [identities[pid] for pid in sorted(identities)]
    _powershell(_WINDOWS_ACT, {
        "WORKBENCH_SUSY_PROCESS_IDENTITIES": json.dumps(
            payload, separators=(",", ":")
        ),
        "WORKBENCH_SUSY_FORCE": "true" if force else "false",
    })


def _same_windows_process_identity(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any],
) -> bool:
    return isinstance(left, Mapping) and all(
        left.get(key) == right.get(key)
        for key in ("pid", "creation_date", "executable_path")
    )


def _wait_windows_empty(instance_id: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _windows_owned_processes(instance_id):
            return True
        time.sleep(0.25)
    return not _windows_owned_processes(instance_id)


def _wait_windows_instance_launcher_empty(
    executable_path: str,
    instance_id: str,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _windows_instance_launcher_processes(executable_path, instance_id):
            return True
        time.sleep(0.25)
    return not _windows_instance_launcher_processes(executable_path, instance_id)


def _process_group_exists(pgid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_process_group(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    if os.name != "posix":  # pragma: no cover - native Windows
        if process.poll() is None:
            process.terminate()
        return {"pgid": None, "forced": False, "running": process.poll() is None}
    pgid = process.pid
    forced = False
    # Reap an already-exited group leader before probing the PGID.  A zombie
    # leader keeps killpg(..., 0) observable and otherwise turns a clean stop
    # into a needless 15-second TERM/KILL escalation.
    process.poll()
    if _process_group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 10.0
        while _process_group_exists(pgid) and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.1)
    if _process_group_exists(pgid):
        forced = True
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5.0
        while _process_group_exists(pgid) and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.1)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    return {"pgid": pgid, "forced": forced, "running": _process_group_exists(pgid)}


def _cleanup_owned_processes(
    process: subprocess.Popen[bytes] | None,
    *,
    launcher_host: Mapping[str, str],
    instance_id: str,
    observed_windows: Mapping[int, Mapping[str, Any]],
    launcher_executable: str | None = None,
    launcher_instance_id: str | None = None,
) -> dict[str, Any]:
    windows_record: dict[str, Any] | None = None
    launcher_record: dict[str, Any] | None = None
    errors: list[str] = []
    if launcher_host["os"] == "windows":
        try:
            current = _windows_owned_processes(instance_id)
            expected = dict(observed_windows)
            expected.update(current)
            expected = {
                pid: row for pid, row in current.items()
                if expected.get(pid) == row
            }
            tracked = dict(expected)
            _windows_process_action(expected, force=False)
            graceful = _wait_windows_empty(instance_id, 10.0)
            if not graceful:
                current = _windows_owned_processes(instance_id)
                expected = {
                    pid: row for pid, row in current.items()
                    if expected.get(pid) == row
                }
                _windows_process_action(expected, force=True)
            empty = _wait_windows_empty(instance_id, 10.0)
            survivors = _windows_owned_processes(instance_id)
            windows_record = {
                "tracked": [tracked[pid] for pid in sorted(tracked)],
                "graceful": graceful,
                "forced": not graceful,
                "survivors": [survivors[pid] for pid in sorted(survivors)],
                "empty": empty and not survivors,
            }
        except SusyModLaunchError as exc:
            errors.append(str(exc))
            windows_record = {
                "tracked": [observed_windows[pid] for pid in sorted(observed_windows)],
                "empty": False,
                "state": "custody-query-failed",
            }
    group_record = None if process is None else _stop_process_group(process)
    if (
        launcher_host["os"] == "windows"
        and launcher_executable is not None
        and launcher_instance_id is not None
    ):
        try:
            tracked = _windows_instance_launcher_processes(
                launcher_executable,
                launcher_instance_id,
            )
            _windows_process_action(tracked, force=False)
            graceful = _wait_windows_instance_launcher_empty(
                launcher_executable,
                launcher_instance_id,
                10.0,
            )
            if not graceful:
                current = _windows_instance_launcher_processes(
                    launcher_executable,
                    launcher_instance_id,
                )
                expected = {
                    pid: row for pid, row in current.items()
                    if _same_windows_process_identity(tracked.get(pid), row)
                }
                _windows_process_action(expected, force=True)
            empty = _wait_windows_instance_launcher_empty(
                launcher_executable,
                launcher_instance_id,
                10.0,
            )
            survivors = _windows_instance_launcher_processes(
                launcher_executable,
                launcher_instance_id,
            )
            launcher_record = {
                "tracked": [tracked[pid] for pid in sorted(tracked)],
                "graceful": graceful,
                "forced": not graceful,
                "survivors": [survivors[pid] for pid in sorted(survivors)],
                "empty": empty and not survivors,
            }
        except SusyModLaunchError as exc:
            errors.append(str(exc))
            launcher_record = {
                "empty": False,
                "state": "custody-query-failed",
            }
    running = bool(
        (windows_record is not None and not windows_record.get("empty"))
        or (launcher_record is not None and not launcher_record.get("empty"))
        or (group_record is not None and group_record.get("running"))
    )
    return {
        "windows": windows_record,
        "launcher": launcher_record,
        "launcher_process_group": group_record,
        "owned_processes_running": running,
        "errors": errors,
    }


def _clear_projection_runtime_evidence(projection: Path) -> list[dict[str, Any]]:
    runtime = projection / ".minecraft"
    removed: list[dict[str, Any]] = []
    for name in ("logs", "crash-reports"):
        target = runtime / name
        if target.is_symlink():
            raise SusyModLaunchError(f"projected {name} is a symbolic link")
        if target.exists():
            if not target.is_dir():
                raise SusyModLaunchError(f"projected {name} is not a directory")
            summary, _records = _runtime_tree(target)
            removed.append({"path": name, "kind": "directory", "identity": summary})
            shutil.rmtree(target)
    for pattern in ("hs_err_pid*.log", "replay_pid*.log"):
        for target in sorted(runtime.glob(pattern)):
            if target.is_symlink() or not target.is_file():
                raise SusyModLaunchError("projected JVM error evidence path is unsafe")
            digest, size = _file_digest(target, "sha256")
            removed.append({
                "path": target.relative_to(runtime).as_posix(),
                "kind": "file",
                "sha256": digest,
                "size": size,
            })
            target.unlink()
    return removed


def _wait_for_probe(path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file() and not path.is_symlink():
            return
        time.sleep(0.1)
    raise SusyModLaunchError("Forge completed, but candidate source proof was not emitted")


def _loader_log_corroboration(
    debug_log: Path, candidate_name: str, expected_mod_ids: Sequence[str]
) -> dict[str, Any]:
    if not debug_log.is_file() or debug_log.is_symlink():
        return {"passed": False, "reason": "debug-log-absent"}
    try:
        size = debug_log.stat().st_size
        if size > 64 * 1024 * 1024:
            return {"passed": False, "reason": "debug-log-too-large", "size": size}
        text = debug_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"passed": False, "reason": "debug-log-unreadable"}
    lowered = text.casefold()
    filename = candidate_name.casefold()
    discovery = bool(re.search(
        rf"(?:examining file\s*:?[ \t]*|adding\s+){re.escape(filename)}",
        lowered,
    ))
    mod_ids = {
        mod_id: re.search(rf"(?<![a-z0-9_.-]){re.escape(mod_id.casefold())}(?![a-z0-9_.-])", lowered)
        is not None
        for mod_id in expected_mod_ids
    }
    return {
        "passed": discovery and all(mod_ids.values()),
        "candidate_filename": candidate_name,
        "discovery": discovery,
        "mod_ids": mod_ids,
    }


def _diagnose_crash(
    crash: Path | None,
    suite: Path,
    *,
    pack_version: str | None = None,
    runtime_records: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if crash is None or not crash.is_file() or crash.is_symlink():
        return None
    try:
        if crash.stat().st_size > MAX_RECORD_BYTES:
            return None
        text = crash.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    required = (
        "mixins.susy.reccomplex.json:StructureSpawnContextMixin",
        "StructureSpawnContext",
        "setBlock(I)I",
        "InjectionError",
    )
    if not all(marker in text for marker in required):
        return None
    resolved_pack_version = pack_version or "0.1.16.11"
    guidance_relative = DIAGNOSTIC_GUIDANCE_BY_PACK_VERSION.get(
        resolved_pack_version
    )
    experiment_id = RECCOMPLEX_EXPERIMENT_BY_PACK_VERSION.get(
        resolved_pack_version
    )
    if guidance_relative is None or experiment_id is None:
        return None
    guidance_path = suite / guidance_relative
    patch_path = suite / COMPATIBILITY_EXPERIMENTS[experiment_id]
    guidance = _read_json(guidance_path, "SUSY Recurrent Complex guidance")
    guidance_digest, guidance_size = _file_digest(guidance_path, "sha256")
    patch_digest, patch_size = _file_digest(patch_path, "sha256")
    available_experiment: dict[str, Any] | None = None
    applicability_error: str | None = None
    if runtime_records is None:
        applicable = None
    else:
        try:
            applicable = _compatibility_experiment_scopes(
                suite,
                (experiment_id,),
                pack_version=resolved_pack_version,
                runtime_records=runtime_records,
            )[experiment_id]
        except SusyModLaunchError as exc:
            applicable = None
            applicability_error = str(exc)
    if runtime_records is None or applicable is not None:
        available_experiment = {
            "id": experiment_id,
            "spec_uri": patch_path.resolve().as_uri(),
            "spec_sha256": patch_digest,
            "spec_size": patch_size,
            "scope": "disposable launcher projection only",
            "applicability": applicable,
        }
    return {
        "pattern_id": guidance.get("pattern_id"),
        "category": guidance.get("match", {}).get("category"),
        "interpretation": guidance.get("interpretation"),
        "developer_actions": guidance.get("developer_actions"),
        "guidance": {
            "uri": guidance_path.resolve().as_uri(),
            "sha256": guidance_digest,
            "size": guidance_size,
        },
        "available_experiment": available_experiment,
        "experiment_unavailable_reason": applicability_error,
    }


_TERMINAL_STARTUP_RE = re.compile(
    r"MixinTargetAlreadyLoadedException"
    r"|\[main/FATAL\] \[Foundation\]: Unable to launch"
    r"|net\.minecraftforge\.fml\.common\.MissingModsException:\s+Mod\b"
    r"|Exception in thread \"main\""
    r"|A fatal error has been detected by the Java Runtime",
    re.IGNORECASE,
)


def _read_log_append(
    path: Path, offset: int, tail: str
) -> tuple[int, str, str]:
    if not path.is_file() or path.is_symlink():
        return offset, tail, ""
    try:
        size = path.stat().st_size
        if size < offset:
            offset = 0
            tail = ""
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            stream.seek(offset)
            appended = stream.read(8 * 1024 * 1024 + 1)
            offset = stream.tell()
    except OSError:
        return offset, tail, ""
    if len(appended) > 8 * 1024 * 1024:
        appended = appended[-8 * 1024 * 1024:]
    return offset, (tail + appended)[-8 * 1024 * 1024:], appended


def _monitor_susy_launch(
    process: subprocess.Popen[bytes],
    projection: Path,
    *,
    custody_token: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Observe fresh logs and the exact nonce-bound Windows game JVM."""

    if (
        not math.isfinite(timeout_seconds)
        or not math.isfinite(poll_interval_seconds)
        or timeout_seconds <= 0
        or poll_interval_seconds <= 0
    ):
        raise SusyModLaunchError("launch timeout and poll interval must be positive")
    latest = projection / ".minecraft/logs/latest.log"
    debug = projection / ".minecraft/logs/debug.log"
    crash_root = projection / ".minecraft/crash-reports"
    runtime = projection / ".minecraft"
    deadline = time.monotonic() + timeout_seconds
    attach_deadline = min(deadline, time.monotonic() + 120.0)
    latest_offset = 0
    debug_offset = 0
    latest_tail = ""
    debug_tail = ""
    seen: dict[int, dict[str, Any]] = {}
    attached = False
    dispatched = False
    next_process_query = 0.0
    current: dict[int, dict[str, Any]] = {}
    while True:
        now = time.monotonic()
        if now >= next_process_query:
            current = _windows_owned_processes(custody_token)
            seen.update(current)
            attached = attached or bool(current)
            next_process_query = now + min(2.0, poll_interval_seconds)

        crash = _latest_crash(crash_root)
        if crash is not None:
            return {
                "outcome": "failed",
                "failure_kind": "minecraft-crash-report",
                "crash_report": crash,
                "launcher_returncode": process.poll(),
                "process_state": "running" if current else "exited",
            }, seen
        fatal_jvm = next((
            path for pattern in ("hs_err_pid*.log", "replay_pid*.log")
            for path in runtime.glob(pattern)
            if path.is_file() and not path.is_symlink()
        ), None)
        if fatal_jvm is not None:
            return {
                "outcome": "failed",
                "failure_kind": "jvm-fatal-error",
                "fatal_error_uri": fatal_jvm.as_uri(),
                "launcher_returncode": process.poll(),
                "process_state": "running" if current else "exited",
            }, seen

        latest_offset, latest_tail, _latest_appended = _read_log_append(
            latest, latest_offset, latest_tail
        )
        debug_offset, debug_tail, _debug_appended = _read_log_append(
            debug, debug_offset, debug_tail
        )
        terminal = _TERMINAL_STARTUP_RE.search(debug_tail)
        if terminal is not None:
            return {
                "outcome": "failed",
                "failure_kind": "fatal-startup-log",
                "marker": terminal.group(0),
                "launcher_returncode": process.poll(),
                "process_state": "running" if current else "exited",
            }, seen
        checkpoint = CLIENT_LOADED_RE.search(latest_tail)
        if checkpoint is not None and current:
            return {
                "outcome": "checkpoint-reached",
                "checkpoint": {
                    "id": "fml-client-loaded",
                    "marker": checkpoint.group(0),
                    "source": "minecraft-latest-log",
                },
                "launcher_returncode": process.poll(),
                "process_state": "running",
            }, seen
        returncode = process.poll()
        if returncode is not None:
            if returncode:
                return {
                    "outcome": "failed",
                    "failure_kind": "launcher-stopped-before-game",
                    "launcher_returncode": returncode,
                    "process_state": "exited",
                }, seen
            dispatched = True
        if attached and not current:
            return {
                "outcome": "failed",
                "failure_kind": "game-jvm-exited-before-checkpoint",
                "launcher_returncode": returncode,
                "process_state": "exited",
            }, seen
        if dispatched and not attached and now >= attach_deadline:
            return {
                "outcome": "failed",
                "failure_kind": "game-jvm-never-started",
                "launcher_returncode": returncode,
                "process_state": "not-observed",
            }, seen
        if now >= deadline:
            return {
                "outcome": "timed-out",
                "failure_kind": "checkpoint-timeout",
                "launcher_returncode": returncode,
                "process_state": "running" if current else "not-observed",
            }, seen
        time.sleep(poll_interval_seconds)


def _launch_argv(run_id: str) -> list[str]:
    return ["workbench", "dev", "launch", "--run", run_id]


def launch_susy_mod_client(
    suite_root: Path | str,
    run_id: str,
    *,
    launcher: str = "prism",
    launcher_executable: Path | str | None = None,
    launcher_root: Path | str | None = None,
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    compatibility_experiments: Sequence[str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Launch and prove one exact retained SUSY constituent candidate."""

    if launcher not in {"prism", "multimc"}:
        raise SusyModLaunchError(f"unsupported client launcher: {launcher}")
    selected_experiments = tuple(compatibility_experiments)
    if (
        len(set(selected_experiments)) != len(selected_experiments)
        or any(item not in COMPATIBILITY_EXPERIMENTS for item in selected_experiments)
    ):
        raise SusyModLaunchError(
            "runtime compatibility experiment is unknown or repeated"
        )
    if isinstance(memory_mib, bool) or not isinstance(memory_mib, int) or not (
        1024 <= memory_mib <= 131072
    ):
        raise SusyModLaunchError("launcher memory must be between 1024 and 131072 MiB")
    if (
        not math.isfinite(timeout_seconds)
        or not math.isfinite(poll_interval_seconds)
        or timeout_seconds <= 0
        or poll_interval_seconds <= 0
    ):
        raise SusyModLaunchError("launch timeout and poll interval must be positive")
    if launcher_profile is None:
        if OFFLINE_NAME_RE.fullmatch(offline_name) is None:
            raise SusyModLaunchError(
                "offline player name must be 3-16 letters, digits, or underscores"
            )
    elif (
        not isinstance(launcher_profile, str)
        or not launcher_profile.strip()
        or len(launcher_profile) > 128
        or any(ord(character) < 32 for character in launcher_profile)
    ):
        raise SusyModLaunchError(
            "launcher profile must be printable and at most 128 characters"
        )

    suite = Path(suite_root).resolve()
    run_root, result, stage, staged_instance, candidate_path = (
        _validate_retained_stage(suite, run_id)
    )
    stage_path = run_root / "runtime/client-stage-v1.json"
    result_path = run_root / "result.json"
    stage_digest_before, _ = _file_digest(stage_path, "sha256")
    result_digest_before, _ = _file_digest(result_path, "sha256")
    replacement = stage["replacement"]
    target = stage["target"]
    candidate = replacement["candidate"]
    expected_mod_ids = sorted(candidate["mod_ids"])
    compatibility_scopes: dict[str, dict[str, Any]] = {}
    if selected_experiments:
        try:
            _staged_summary, staged_records = _runtime_tree(
                staged_instance / ".minecraft"
            )
        except (OSError, ValueError, SusyModDevError) as exc:
            raise SusyModLaunchError(
                f"cannot measure compatibility experiment inputs: {exc}"
            ) from exc
        retained_pack = result.get("supersymmetry")
        compatibility_scopes = _compatibility_experiment_scopes(
            suite,
            selected_experiments,
            pack_version=(
                retained_pack.get("version")
                if isinstance(retained_pack, Mapping)
                else None
            ),
            runtime_records=staged_records,
        )

    selected_executable = (
        _default_launcher_executable(launcher)
        if launcher_executable is None
        else Path(launcher_executable).expanduser().resolve()
    )
    try:
        executable, launcher_identity, launcher_host = _probe_launcher(
            selected_executable, launcher
        )
    except RuntimeLaunchError as exc:
        raise SusyModLaunchError(str(exc)) from exc
    if launcher_host["os"] != "windows":
        raise SusyModLaunchError(
            "this first dev-launch slice requires a Windows Prism/MultiMC host "
            "so the exact nonce-bound game JVM can be revalidated and stopped"
        )
    root = (
        _default_launcher_root(executable, launcher)
        if launcher_root is None
        else Path(launcher_root).expanduser().resolve()
    )
    launcher_processes = _windows_launcher_processes(
        _launcher_path(executable, launcher_host)
    )
    if any(not row["responding"] for row in launcher_processes.values()):
        raise SusyModLaunchError(
            f"an existing {launcher} launcher process is not responding; "
            "close that launcher process and retry"
        )
    native_host = host_platform()
    cross_host = (
        launcher_host["os"] != native_host["os"]
        or launcher_host["architecture"] != native_host["architecture"]
    )
    if cross_host and launcher_processes:
        raise SusyModLaunchError(
            f"an existing {launcher} launcher process prevents reliable "
            "cross-host launch custody; close that launcher process and retry"
        )
    java_state = (
        Path(launcher_java_state).expanduser().resolve()
        if launcher_java_state is not None
        else (
            root / ".workbench"
            if cross_host
            else default_suite_state_root(suite)
        )
    )
    java_candidates = (
        None
        if launcher_java is None
        else [("launcher-java", Path(launcher_java).expanduser().resolve())]
    )
    try:
        java_result = ensure_java_runtime(
            suite,
            host=launcher_host,
            state_root=java_state,
            candidates=java_candidates,
        )
        java_executable, java_identity = _selected_java(java_result, launcher_host)
    except (JavaRuntimeError, RuntimeLaunchError) as exc:
        raise SusyModLaunchError(str(exc)) from exc
    compiler = java_executable.with_name("javac.exe")
    if not compiler.is_file() or compiler.is_symlink():
        raise SusyModLaunchError(
            "selected launcher Java is not a full JDK; javac.exe is required"
        )
    try:
        launcher_root_record = _initialize_launcher_root(root, launcher)
    except RuntimeLaunchError as exc:
        raise SusyModLaunchError(str(exc)) from exc

    started = _utc_now()
    started_monotonic = time.monotonic()
    deadline = started_monotonic + timeout_seconds
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    instance_id = (
        "workbench-susy-dev-"
        + candidate["sha256"][:12]
        + "-"
        + stamp
    )
    projection = root / "instances" / instance_id
    launches_root = run_root / "runtime/launches"
    if launches_root.is_symlink():
        raise SusyModLaunchError("retained launch evidence root is a symbolic link")
    launches_root.mkdir(parents=True, exist_ok=True)
    evidence_root = launches_root / instance_id
    if evidence_root.exists() or evidence_root.is_symlink():
        raise SusyModLaunchError(f"launch evidence already exists: {evidence_root}")
    evidence_root.mkdir()
    lock = run_root / "runtime/client-launch.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "pid": os.getpid(),
                "run_id": run_id,
                "started_at": _timestamp(started),
            }, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise SusyModLaunchError(
            f"another launch owns retained run {run_id}"
        ) from exc
    except OSError as exc:
        raise SusyModLaunchError("cannot acquire the retained launch lock") from exc

    nonce = secrets.token_hex(24)
    process: subprocess.Popen[bytes] | None = None
    projection_record: dict[str, Any] | None = None
    probe_build: dict[str, Any] | None = None
    compatibility_records: list[dict[str, Any]] = []
    proof: dict[str, Any] | None = None
    corroboration: dict[str, Any] | None = None
    observation: dict[str, Any] = {
        "outcome": "failed",
        "failure_kind": "launch-not-started",
        "launcher_returncode": None,
        "process_state": "not-started",
    }
    cleanup: dict[str, Any] = {
        "windows": None,
        "launcher_process_group": None,
        "owned_processes_running": False,
        "errors": [],
    }
    observed_windows: dict[int, dict[str, Any]] = {}
    stale_evidence: list[dict[str, Any]] = []
    launch_error: str | None = None
    failure_kind: str | None = None
    command: list[str] = []
    redacted_command: list[str] = []
    live_launcher_log = evidence_root / "launcher-command.live.log"
    projected_candidate = projection / ".minecraft" / replacement["path"]

    try:
        if _windows_owned_processes(nonce):
            raise SusyModLaunchError("candidate load nonce unexpectedly matches a live JVM")
        probe_build, probe_path, javaagent, probe_jar = _compile_probe_agent(
            evidence_root / "probe-build",
            projected_instance=projection,
            java_executable=java_executable,
            launcher_host=launcher_host,
            nonce=nonce,
            expected_mod_ids=expected_mod_ids,
        )
        probe_configuration = _probe_configuration(staged_instance, javaagent)

        def prepare_projection(staged: Path) -> None:
            nonlocal compatibility_records, stale_evidence
            if _probe_configuration(staged, javaagent) != probe_configuration:
                raise SusyModLaunchError(
                    "staged launcher JVM arguments changed during projection"
                )
            try:
                stale_evidence = _clear_projection_runtime_evidence(staged)
            except OSError as exc:
                raise SusyModLaunchError(
                    "cannot clear stale runtime evidence from the disposable projection"
                ) from exc
            if selected_experiments:
                try:
                    compatibility_records = apply_compatibility_patches(
                        staged,
                        [
                            suite / COMPATIBILITY_EXPERIMENTS[item]
                            for item in selected_experiments
                        ],
                    )
                    for experiment_id, record in zip(
                        selected_experiments, compatibility_records, strict=True
                    ):
                        record["experiment_id"] = experiment_id
                        record["applicability"] = compatibility_scopes[experiment_id]
                except RuntimeCompatibilityError as exc:
                    raise SusyModLaunchError(str(exc)) from exc

        try:
            projection_record = _project_instance(
                staged_instance,
                projection,
                expected_payload=target["payload"],
                java_path=_launcher_path(
                    java_executable,
                    launcher_host,
                    preserve_execution_alias=True,
                ),
                java_probe=java_identity["probe"],
                display_name=(
                    f"Workbench SUSY Dev {candidate['sha256'][:8]}"
                ),
                memory_mib=memory_mib,
                additional_files={
                    ".workbench/candidate-loaded-probe/"
                    "workbench-candidate-loaded-agent.jar": probe_jar,
                },
                additional_config=probe_configuration,
                prepare_projection=prepare_projection,
            )
        except RuntimeLaunchError as exc:
            raise SusyModLaunchError(str(exc)) from exc
        if selected_experiments:
            projection_record["compatibility_experiments"] = compatibility_records
        projection_record["probe_configuration"] = {
            "atomic_publish": True,
            "settings": probe_configuration,
        }
        projected_digest, projected_size = _file_digest(
            projected_candidate, "sha256"
        )
        if (
            projected_digest != candidate["sha256"]
            or projected_size != candidate["size"]
        ):
            raise SusyModLaunchError("projected candidate JAR differs before launch")

        command = [
            str(executable),
            "--dir",
            _launcher_path(root, launcher_host),
            "--launch",
            instance_id,
        ]
        redacted_command = list(command)
        if launcher_profile is None:
            command.extend(["--offline", offline_name])
            redacted_command.extend(["--offline", offline_name])
        else:
            command.extend(["--profile", launcher_profile])
            redacted_command.extend(["--profile", "<redacted-profile>"])
        with live_launcher_log.open("xb") as stream:
            stream.write(_canonical_bytes({
                "command": redacted_command,
                "cwd": str(root),
                "launcher_output": "discarded-account-boundary",
            }) + b"\n")
        popen_options: dict[str, Any] = {}
        if os.name == "posix":
            popen_options["start_new_session"] = True
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                **popen_options,
            )
        except OSError as exc:
            launch_error = str(exc)
            failure_kind = "launcher-exec-failed"
        if process is not None:
            observation, monitor_windows = _monitor_susy_launch(
                process,
                projection,
                custody_token=nonce,
                timeout_seconds=max(0.001, deadline - time.monotonic()),
                poll_interval_seconds=poll_interval_seconds,
            )
            observed_windows.update(monitor_windows)
            try:
                observed_windows.update(_windows_owned_processes(nonce))
            except SusyModLaunchError:
                observed_windows = {}
                raise
            if observation.get("outcome") == "checkpoint-reached":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SusyModLaunchError(
                        "launch budget expired before candidate source proof"
                    )
                _wait_for_probe(probe_path, min(30.0, remaining))
                proof = _validate_loaded_source_proof(
                    probe_path,
                    nonce=nonce,
                    expected_mod_ids=expected_mod_ids,
                    expected_source_path=projected_candidate,
                    expected_sha256=candidate["sha256"],
                    expected_size=candidate["size"],
                    launcher_host=launcher_host,
                )
                if proof["pid"] not in observed_windows:
                    raise SusyModLaunchError(
                        "loaded-source proof PID is not the nonce-bound game JVM"
                    )
                corroboration = _loader_log_corroboration(
                    projection / ".minecraft/logs/debug.log",
                    projected_candidate.name,
                    expected_mod_ids,
                )
                if not corroboration["passed"]:
                    raise SusyModLaunchError(
                        "Forge debug log does not corroborate candidate discovery"
                    )
            else:
                failure_kind = str(
                    observation.get("failure_kind", "loader-checkpoint-not-reached")
                )
                if failure_kind == "launcher-stopped-before-game":
                    current_launchers = _windows_launcher_processes(
                        _launcher_path(executable, launcher_host)
                    )
                    if any(
                        not row["responding"]
                        for row in current_launchers.values()
                    ):
                        failure_kind = "launcher-ui-unresponsive"
                        observation["failure_kind"] = failure_kind
                        launch_error = (
                            f"the {launcher} launcher stopped responding before "
                            "it dispatched the game; close that launcher process "
                            "and retry"
                        )
    except KeyboardInterrupt:
        failure_kind = "cancelled"
        launch_error = "launch cancelled by the developer"
    except (
        SusyModLaunchError,
        RuntimeLaunchError,
        RuntimeCompatibilityError,
        JavaRuntimeError,
        SusyModDevError,
    ) as exc:
        failure_kind = failure_kind or "candidate-load-not-proven"
        launch_error = str(exc)
    finally:
        if process is not None and launcher_host["os"] == "windows":
            try:
                current = _windows_owned_processes(nonce)
                for pid, row in current.items():
                    observed_windows.setdefault(pid, row)
            except SusyModLaunchError as exc:
                cleanup["errors"].append(str(exc))
        cleanup = _cleanup_owned_processes(
            process,
            launcher_host=launcher_host,
            instance_id=nonce,
            observed_windows=observed_windows,
            launcher_executable=_launcher_path(executable, launcher_host),
            launcher_instance_id=instance_id,
        )
        try:
            lock.unlink()
        except OSError:
            cleanup["errors"].append("retained launch lock could not be removed")

    post_candidate: dict[str, Any] | None = None
    if projected_candidate.is_file() and not projected_candidate.is_symlink():
        projected_digest, projected_size = _file_digest(projected_candidate, "sha256")
        post_candidate = {
            "uri": projected_candidate.as_uri(),
            "sha256": projected_digest,
            "size": projected_size,
            "matches_candidate": (
                projected_digest == candidate["sha256"]
                and projected_size == candidate["size"]
            ),
        }
        if not post_candidate["matches_candidate"]:
            failure_kind = failure_kind or "candidate-drift-after-launch"
    else:
        failure_kind = failure_kind or "candidate-missing-after-launch"
    source_unchanged = False
    try:
        _validate_retained_stage(suite, run_id)
        stage_digest_after, _ = _file_digest(stage_path, "sha256")
        result_digest_after, _ = _file_digest(result_path, "sha256")
        source_unchanged = (
            stage_digest_before == stage_digest_after
            and result_digest_before == result_digest_after
        )
    except SusyModLaunchError as exc:
        launch_error = launch_error or str(exc)
    if not source_unchanged:
        failure_kind = failure_kind or "retained-stage-drift-after-launch"
    if cleanup["owned_processes_running"] or cleanup["errors"]:
        failure_kind = failure_kind or "cleanup-incomplete"
    if proof is None:
        failure_kind = failure_kind or "candidate-load-not-proven"
    if corroboration is None or not corroboration.get("passed"):
        failure_kind = failure_kind or "loader-log-not-corroborated"
    passed = failure_kind is None
    observed_at = _utc_now()

    redactions = () if launcher_profile is None else (launcher_profile,)
    evidence = [
        _capture_file(
            projection / ".minecraft/logs/latest.log",
            evidence_root / "minecraft-latest.log",
            "minecraft-latest-log",
            redact_values=redactions,
        ),
        _capture_file(
            projection / ".minecraft/logs/debug.log",
            evidence_root / "minecraft-debug.log",
            "minecraft-debug-log",
            redact_values=redactions,
        ),
        _capture_file(
            live_launcher_log,
            evidence_root / "launcher-command.log",
            "launcher-command-log",
        ),
    ]
    crash = observation.get("crash_report")
    crash_path = crash if isinstance(crash, Path) else None
    if isinstance(crash, Path):
        evidence.append(_capture_file(
            crash,
            evidence_root / "minecraft-crash-report.txt",
            "minecraft-crash-report",
            redact_values=redactions,
        ))
        observation = {
            key: value for key, value in observation.items() if key != "crash_report"
        }
    retained_pack = result.get("supersymmetry")
    diagnosis = _diagnose_crash(
        crash_path,
        suite,
        pack_version=(
            retained_pack.get("version")
            if isinstance(retained_pack, Mapping)
            else None
        ),
        runtime_records=staged_records,
    )
    launch_identity = {
        "run_id": run_id,
        "stage_id": stage["stage_id"],
        "instance_id": instance_id,
        "candidate_sha256": candidate["sha256"],
        "started_at": _timestamp(started),
        "launcher_sha256": launcher_identity["sha256"],
        "java_runtime_id": java_identity["runtime_id"],
    }
    launch_id = "workbench-susy-mod-launch:sha256:" + sha256(
        _canonical_bytes(launch_identity)
    ).hexdigest()
    receipt_path = evidence_root / "susy-mod-launch-v1.json"
    receipt: dict[str, Any] = {
        "format": LAUNCH_RECEIPT_FORMAT,
        "schema_version": 1,
        "launch_id": launch_id,
        "operation_class": "local-mutation",
        "state": "observed",
        "outcome": "passed" if passed else "failed",
        "failure_kind": None if passed else failure_kind,
        "reason": None if passed else launch_error,
        "started_at": _timestamp(started),
        "observed_at": _timestamp(observed_at),
        "run_id": run_id,
        "plan_id": result["plan_id"],
        "stage_id": stage["stage_id"],
        "candidate": {
            "path": replacement["path"],
            "sha256": candidate["sha256"],
            "size": candidate["size"],
            "mod_ids": expected_mod_ids,
        },
        "launcher": {
            **launcher_identity,
            "host": launcher_host,
            "data_root": launcher_root_record,
            "preexisting_processes": [
                launcher_processes[pid]
                for pid in sorted(launcher_processes)
            ],
            "instance_id": instance_id,
            "projection_uri": projection.as_uri(),
            "command": redacted_command,
            "pid": None if process is None else process.pid,
        },
        "java": java_identity,
        "launch_policy": {
            "account_mode": "launcher-profile" if launcher_profile else "offline",
            "launcher_profile": "explicit-redacted" if launcher_profile else None,
            "offline_name": None if launcher_profile else offline_name,
            "memory_mib": memory_mib,
            "timeout_seconds": timeout_seconds,
            "checkpoint": "fml-client-loaded",
            "compatibility_experiments": list(selected_experiments),
            "launcher_output": "discarded-account-boundary",
        },
        "projection": projection_record,
        "fresh_evidence_boundary": {"removed_from_projection": stale_evidence},
        "compatibility_experiments": compatibility_records,
        "probe_build": probe_build,
        "loaded_source_proof": proof,
        "loader_log_corroboration": corroboration,
        "observation": observation,
        "diagnosis": diagnosis,
        "cleanup": cleanup,
        "post_launch_candidate": post_candidate,
        "retained_inputs_unchanged": source_unchanged,
        "evidence": evidence,
        "target": {
            "evidence_root_uri": evidence_root.as_uri(),
            "receipt_uri": receipt_path.as_uri(),
        },
        "limitations": [
            (
                "This run applies only the explicitly selected, receipt-bound "
                "compatibility experiments to its disposable projection."
                if selected_experiments
                else "This run uses the exact staged SUSY client with no compatibility overlays."
            ),
            "A passing result proves Forge loaded the exact candidate JAR; it does not assert gameplay correctness.",
            "V1 process custody is implemented for a Windows Prism/MultiMC game JVM.",
        ],
    }
    _write_receipt(receipt_path, receipt)
    if (
        not passed
        and isinstance(diagnosis, Mapping)
        and isinstance(diagnosis.get("available_experiment"), Mapping)
        and diagnosis["available_experiment"].get("id") not in selected_experiments
    ):
        experiment_id = str(diagnosis["available_experiment"]["id"])
        next_actions = [{
            "id": "try-susy-reccomplex-compatibility",
            "label": "Retry with the exact SUSY/Recurrent Complex experiment",
            "argv": _launch_argv(run_id) + [
                "--runtime-experiment", experiment_id
            ],
            "available": True,
        }]
    else:
        next_actions = [{
            "id": "retry-exact-launch" if not passed else "launch-again",
            "label": (
                "Retry exact staged client"
                if not passed else "Launch exact staged client again"
            ),
            "argv": _launch_argv(run_id) + [
                part
                for experiment in selected_experiments
                for part in ("--runtime-experiment", experiment)
            ],
            "available": True,
        }]
    return {
        "format": LAUNCH_RESULT_FORMAT,
        "schema_version": 1,
        "outcome": receipt["outcome"],
        "receipt": deepcopy(receipt),
        "next_actions": next_actions,
    }


def render_susy_mod_launch(
    result: Mapping[str, Any], *, json_output: bool = False
) -> str:
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise SusyModLaunchError("SUSY launch result lacks its receipt")
    candidate = receipt.get("candidate", {})
    proof = receipt.get("loaded_source_proof")
    cleanup = receipt.get("cleanup", {})
    cleanup_complete = (
        isinstance(cleanup, Mapping)
        and not cleanup.get("owned_processes_running")
        and not cleanup.get("errors")
    )
    lines = [
        f"SUSY mod launch: {result.get('outcome')}",
        f"Run: {receipt.get('run_id')}",
        f"Candidate: {candidate.get('path')} · {candidate.get('sha256')}",
        (
            "Loaded proof: exact Forge source JAR"
            if isinstance(proof, Mapping)
            else f"Loaded proof: unavailable ({receipt.get('failure_kind')})"
        ),
        (
            "Cleanup: complete"
            if cleanup_complete
            else "Cleanup: incomplete"
        ),
        f"Receipt: {receipt.get('target', {}).get('receipt_uri')}",
    ]
    reason = receipt.get("reason")
    if isinstance(reason, str) and reason:
        lines.append(f"Reason: {reason}")
    diagnosis = receipt.get("diagnosis")
    if isinstance(diagnosis, Mapping):
        interpretation = diagnosis.get("interpretation")
        if isinstance(interpretation, str) and interpretation:
            lines.append(f"Diagnosis: {interpretation}")
        experiment = diagnosis.get("available_experiment")
        if (
            isinstance(experiment, Mapping)
            and isinstance(experiment.get("id"), str)
        ):
            lines.append(f"Available experiment: {experiment['id']}")
    actions = result.get("next_actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], Mapping):
        argv = actions[0].get("argv")
        if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
            lines.append("Next: " + " ".join(argv))
    return "\n".join(lines) + "\n"
