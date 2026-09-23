"""Build and materialize explicit, dependency-free Java check attachments.

Profiles supply source and admission policy; Core owns compilation, bytes and
launch custody. Attachments never change an immutable runtime image.
"""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import subprocess
import zipfile

from .check_storage import ordinary, safe_path, tree_manifest, seal, CheckStorageError

DIRECTORY = "workbench-check-attachment"
JAR = DIRECTORY + "/observer.jar"
CONFIG = DIRECTORY + "/observer.properties"
BRIDGE = DIRECTORY + "/bridge.jar"


def build(directory, executable, specification):
    if specification is None:
        return None
    if set(specification) != {"sources", "main_class", "configuration", "policy", "bootstrap_classes"}:
        raise CheckStorageError("invalid check attachment specification")
    sources = specification["sources"]
    main = specification["main_class"]
    bootstrap = specification["bootstrap_classes"]
    class_name = r"[a-zA-Z_$][a-zA-Z0-9_$]*(?:\.[a-zA-Z_$][a-zA-Z0-9_$]*)+"
    if (not isinstance(sources, dict) or not 1 <= len(sources) <= 16
            or any(not isinstance(name, str) or not isinstance(raw, bytes) for name, raw in sources.items())
            or sum(len(raw) for raw in sources.values()) > 256 * 1024
            or not isinstance(main, str) or re.fullmatch(class_name, main) is None
            or not isinstance(bootstrap, list) or not 1 <= len(bootstrap) <= 16
            or any(not isinstance(name, str) or re.fullmatch(class_name, name) is None for name in bootstrap)
            or len(set(bootstrap)) != len(bootstrap) or main in bootstrap
            or not isinstance(specification["configuration"], bytes)
            or len(specification["configuration"]) > 1024 * 1024):
        raise CheckStorageError("check attachment exceeds its build bounds")
    directory.mkdir(mode=0o700)
    source = directory / "source"
    classes = directory / "classes"
    source.mkdir(); classes.mkdir()
    for name, raw in sorted(sources.items()):
        path = safe_path(name)
        if not name.endswith(".java") or not isinstance(raw, bytes):
            raise CheckStorageError("attachment build accepts only Java source bytes")
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    compiler = ordinary(Path(executable).with_name("javac"))
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    with (directory / "compiler.log").open("wb") as log:
        bridge_names = {name.rsplit(".", 1)[-1] + ".java" for name in specification["bootstrap_classes"]}
        for release, names in (("17", [name for name in sources if Path(name).name in bridge_names]),
                               ("25", [name for name in sources if Path(name).name not in bridge_names])):
            if not names:
                raise CheckStorageError("observer requires separate bootstrap and agent sources")
            result = subprocess.run(
                [str(compiler), "-J-Xmx256m", "-proc:none", "-encoding", "UTF-8", "--release", release,
                 "-g", "-classpath", str(classes), "-d", str(classes),
                 *(str(source / name) for name in sorted(names))],
                cwd=directory, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=60,
            )
            if result.returncode:
                raise CheckStorageError("check observer compilation failed; inspect its retained compiler.log")
    rows = tree_manifest(classes)
    if not rows or len(rows) > 128 or sum(row["size"] for row in rows) > 2 * 1024**2:
        raise CheckStorageError("check observer class output exceeds bounds")
    if main.replace(".", "/") + ".class" not in {row["path"] for row in rows}:
        raise CheckStorageError("check observer lacks its declared entry point")
    output = directory / "payload" / DIRECTORY
    output.mkdir(parents=True)
    prefixes = [name.replace(".", "/") for name in specification["bootstrap_classes"]]
    if not prefixes or len(prefixes) > 16 or main in specification["bootstrap_classes"]:
        raise CheckStorageError("invalid bootstrap bridge class declaration")
    jars = {"observer.jar": {}, "bridge.jar": {}}
    for row in rows:
        bridge = any(row["path"] == prefix + ".class" or row["path"].startswith(prefix + "$") for prefix in prefixes)
        jars["bridge.jar" if bridge else "observer.jar"][row["path"]] = (classes / row["path"]).read_bytes()
    jars["observer.jar"]["META-INF/MANIFEST.MF"] = ("Manifest-Version: 1.0\r\nPremain-Class: " + main + "\r\nBoot-Class-Path: bridge.jar\r\n\r\n").encode()
    for filename, entries in jars.items():
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in sorted(entries.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                archive.writestr(info, raw)
        (output / filename).write_bytes(buffer.getvalue())
    (output / "observer.properties").write_bytes(specification["configuration"])
    return seal("check-attachment", {
        "format": "workbench-check-attachment-v1", "kind": "java-startup-observer",
        "main_class": main, "policy": specification["policy"],
        "source_files": tree_manifest(source), "compiler_sha256": sha256(compiler.read_bytes()).hexdigest(),
        "files": tree_manifest(directory / "payload"),
    })


def validate(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"format", "kind", "main_class", "policy", "source_files", "compiler_sha256", "files", "id"}
            or value["format"] != "workbench-check-attachment-v1" or value["kind"] != "java-startup-observer"
            or seal("check-attachment", {key: row for key, row in value.items() if key != "id"}) != value
            or {row["path"] for row in value["files"]} != {JAR, BRIDGE, CONFIG}):
        raise CheckStorageError("invalid sealed check attachment")
    return value


def materialize(runtime, retained, value):
    if value is None:
        return
    validate(value)
    if tree_manifest(retained / "payload") != value["files"]:
        raise CheckStorageError("retained attachment bytes changed")
    target = runtime / DIRECTORY
    if target.exists() or target.is_symlink():
        raise CheckStorageError("reserved check attachment directory already exists")
    target.mkdir(mode=0o700)
    for row in value["files"]:
        raw = ordinary(retained / "payload" / row["path"]).read_bytes()
        if sha256(raw).hexdigest() != row["sha256"] or len(raw) != row["size"]:
            raise CheckStorageError("attachment changed during materialization")
        target_file = runtime / row["path"]
        target_file.write_bytes(raw)
        target_file.chmod(row["mode"])


def arguments(runtime, value):
    if value is None:
        return []
    validate(value)
    rows = [{**row, "path": row["path"].split("/", 1)[1]} for row in value["files"]]
    if tree_manifest(runtime / DIRECTORY) != rows:
        raise CheckStorageError("check attachment changed before launch")
    return ["-javaagent:" + JAR + "=" + CONFIG]


def comparison_identity(value):
    """Nonce/candidate configuration differs per run; observer implementation does not."""
    if value is None:
        return None
    return {key: value[key] for key in ("kind", "main_class", "policy", "source_files", "compiler_sha256")} | {
        "artifact_sha256": {row["path"]: row["sha256"] for row in value["files"] if row["path"] != CONFIG},
    }
