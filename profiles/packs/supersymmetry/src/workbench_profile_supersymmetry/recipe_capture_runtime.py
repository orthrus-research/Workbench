"""Profile-owned preparation for the finite original-Forge recipe observer.

The caller owns storage, Java inspection, cancellation and game execution.
Compilation uses the bound process API; this module never binds a Core host.
Launch planning validates declarations and performs no filesystem writes.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from threading import Event
from typing import Any
from urllib.parse import unquote, urlsplit
import zipfile

from workbench_api.processes import capture_process
from workbench_api.host_filesystem import secure_private_path
from workbench_api.resources import repository_root

from . import recipe_capture_inputs as inputs
from .recipe_capture_inputs import build_capture_input, build_source_binding


PROFILE_API_VERSION = 1
SOURCE_ROOTS = ("groovy", "config", "resources", "scripts")
OBSERVER_PATH = "mods/workbench-forge-recipe-observer-0.1.0.jar"
PREPARATION = {"policy": "forge-loli-original-capability-materialization-v1",
               "phase": "before-two-effective-samples"}
_SERVER_PROPERTIES = {
    "max-tick-time": "-1", "server-ip": "127.0.0.1", "server-port": "0",
    "enable-query": "false", "enable-rcon": "false", "level-name": "workbench-capture",
}
_OBSERVER = "atlas/probes/forge-recipe-observer/src/main/java/dev/workbench/crucible/forgerecipes/"
_SHARED = "atlas/probes/ultimate-runtime-graph-producer/src/main/java/dev/workbench/crucible/runtimegraph/"
_SOURCE_PATHS = tuple(sorted([
    *(_OBSERVER + name + ".java" for name in (
        "ForgeBlockStateValue", "ForgeCapabilityPreparation", "ForgeCapturePublisher",
        "ForgeNbtCheck", "ForgeOrdinaryItemMatching", "ForgeRecipeObserverMod",
        "ForgeRecipeSnapshot", "ForgeReflection")),
    _SHARED + "CanonicalJson.java", _SHARED + "Hashing.java",
]))
_HEX = re.compile(r"[0-9a-f]{64}")


class RecipeCaptureRuntimeError(ValueError):
    """The selected inputs cannot prepare this declared observer model."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _windows_access_path(value: str, *, force_extended: bool = False) -> str:
    """Use extended paths only at the filesystem boundary, never in records."""
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        raise RecipeCaptureRuntimeError("filesystem inputs require ordinary paths, not device namespaces")
    path = PureWindowsPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RecipeCaptureRuntimeError("filesystem access requires an absolute normalized path")
    if len(value) < 240 and not force_extended:
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def _native_path(path: Path, *, force_extended: bool = False) -> Path:
    # This profile owns these reads/writes. Core remains behind the process and
    # custody APIs; an OS spelling does not change the public path identity.
    return Path(_windows_access_path(os.path.abspath(path), force_extended=force_extended)) if os.name == "nt" else path


def _resolved_path(path: Path) -> Path:
    resolved = _native_path(path).resolve()
    if os.name == "nt":
        value = str(resolved)
        if value.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + value[8:])
        if value.startswith("\\\\?\\"):
            return Path(value[4:])
    return resolved


def _write_private(path: Path, value: Any) -> None:
    _native_path(path).write_bytes(_canonical(value) + b"\n")
    secure_private_path(path, directory=False)


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise RecipeCaptureRuntimeError(f"invalid {label} SHA-256")
    return value


def _identity(path: Path) -> dict[str, Any]:
    native = _native_path(path)
    if native.is_symlink() or not native.is_file():
        raise RecipeCaptureRuntimeError(f"expected an ordinary input file: {path}")
    with native.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.absolute()), "size": native.stat().st_size, "sha256": digest}


def _file_record(value: Any, label: str, *, relative: bool = False) -> dict[str, Any]:
    if type(value) is not dict or not {"path", "size", "sha256"} <= set(value):
        raise RecipeCaptureRuntimeError(f"invalid {label} file record")
    if type(value["size"]) is not int or value["size"] < 0:
        raise RecipeCaptureRuntimeError(f"invalid {label} file size")
    _sha(value["sha256"], label)
    if relative:
        inputs._path(value["path"])
    else:
        _absolute(value["path"], label)
    return value


def _absolute(value: Any, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value) or any(
            ord(char) < 32 or ord(char) == 127 for char in str(value)):
        raise RecipeCaptureRuntimeError(f"invalid {label} path")
    path = Path(value)
    if os.name == "nt" and str(path).startswith(("\\\\?\\", "\\\\.\\")):
        raise RecipeCaptureRuntimeError(f"{label} requires an ordinary path, not a device namespace")
    if not path.is_absolute() or ".." in path.parts:
        raise RecipeCaptureRuntimeError(f"{label} requires an absolute normalized path")
    return path


def _runtime_rows(files: Any) -> dict[str, dict[str, Any]]:
    if type(files) not in (list, tuple) or not files:
        raise RecipeCaptureRuntimeError("selected runtime requires its complete file inventory")
    rows = {}
    for row in files:
        _file_record(row, "runtime", relative=True)
        if "mode" in row and row["mode"] not in (0o644, 0o755, 0o100644, 0o100755):
            raise RecipeCaptureRuntimeError("runtime file has an unsupported ordinary file mode")
        if row["path"] in rows:
            raise RecipeCaptureRuntimeError("duplicate runtime file")
        rows[row["path"]] = row
    inputs._portable_tree(list(rows))
    return rows


def descriptor() -> dict[str, Any]:
    """Declare this profile's supported selection and replacement policy."""
    return {"id": "supersymmetry.forge-recipe-capture", "api_version": 1,
            "platform": deepcopy(inputs.QUALIFIED_PLATFORM),
            "runtime_artifacts": deepcopy(inputs.QUALIFIED_ARTIFACTS),
            "physical_side": "dedicated_server", "source_roots": list(SOURCE_ROOTS),
            "required_source_files": ["groovy/runConfig.json"],
            "runtime_exclusions": [".git", "logs", "crash-reports", "world",
                                   "workbench-capture", "cache/groovy", "eula.txt", OBSERVER_PATH],
            "server_property_overrides": deepcopy(_SERVER_PROPERTIES),
            "observer_path": OBSERVER_PATH, "observation_preparation": deepcopy(PREPARATION),
            "checkpoint": "post-start-end-tick", "sample_count": 2,
            "limitations": ["Only the pinned original Forge/Java 8 artifact model is supported.",
                            "Preparation does not prove initialization or capture admission.",
                            "The observer covers finite GT recipes and matching, not all game acquisition or uses."]}


def _property_unescape(value: str) -> str:
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        index += 1
        if char != "\\":
            result.append(char)
            continue
        if index == len(value):
            break
        escaped = value[index]
        index += 1
        if escaped == "u":
            digits = value[index:index + 4]
            if len(digits) != 4 or re.fullmatch(r"[0-9a-fA-F]{4}", digits) is None:
                raise RecipeCaptureRuntimeError("server.properties contains an invalid Java Unicode escape")
            result.append(chr(int(digits, 16)))
            index += 4
        else:
            result.append({"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(escaped, escaped))
    return "".join(result)


def _property_key(logical: str) -> str:
    # Validate Unicode escapes in values as java.util.Properties.load also does.
    _property_unescape(logical)
    escaped = False
    end = len(logical)
    for index, char in enumerate(logical):
        if not escaped and char in " \t\f=:":
            end = index
            break
        escaped = not escaped if char == "\\" else False
    return _property_unescape(logical[:end])


def prepare_server_properties(raw: bytes) -> bytes:
    """Return explicit capture settings while preserving unrelated property bytes.

    Java's ISO-8859-1 property syntax, escaped keys, continuations and duplicate
    managed keys are respected. Comments and unrelated logical records survive
    byte-for-byte. The caller retains the original and writes this new output.
    """
    if type(raw) is not bytes:
        raise RecipeCaptureRuntimeError("server.properties input must be bytes")
    physical = [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", raw.decode("latin-1")) if line]
    retained = []
    record = ""
    logical = ""
    continuing = False
    for original in physical:
        content = original.rstrip("\r\n").lstrip(" \t\f")
        if not continuing and (not content or content.startswith(("#", "!"))):
            retained.append(original)
            continue
        record += original
        logical += content
        trailing = len(logical) - len(logical.rstrip("\\"))
        continuing = bool(trailing % 2)
        if continuing:
            logical = logical[:-1]
            continue
        if _property_key(logical) not in _SERVER_PROPERTIES:
            retained.append(record)
        record, logical = "", ""
    if record and _property_key(logical) not in _SERVER_PROPERTIES:
        # An unterminated continuation must not consume the first appended key.
        if continuing:
            record += "\n" if record.endswith(("\r", "\n")) else "\n\n"
        retained.append(record)
    content = "".join(retained)
    if content and not content.endswith(("\r", "\n")):
        content += "\n"
    content += "".join(f"{key}={value}\n" for key, value in sorted(_SERVER_PROPERTIES.items()))
    return content.encode("latin-1")


def select_runtime_artifacts(runtime_files: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve original binaries by bytes within their actual loader locations."""
    rows = _runtime_rows(runtime_files)
    selected = {}
    for role, wanted in inputs.QUALIFIED_ARTIFACTS.items():
        root_jar = role in {"forge_sha256", "minecraft_sha256"}
        matches = []
        for name, row in rows.items():
            path = PurePosixPath(name)
            eligible = (len(path.parts) == 1 if root_jar else
                        len(path.parts) == 2 and path.parts[0] == "mods" or
                        len(path.parts) == 3 and path.parts[:2] == ("mods", "1.12.2"))
            if eligible and path.suffix.lower() == ".jar" and row["sha256"] == wanted:
                matches.append(name)
        if len(matches) != 1:
            raise RecipeCaptureRuntimeError(
                f"selected runtime needs exactly one {role} original artifact; found {len(matches)}")
        selected[role] = matches[0]
    return selected


def _verify_rows(root: Path, rows: dict[str, dict[str, Any]], names: list[str]) -> list[Path]:
    result = []
    for name in names:
        if name not in rows:
            raise RecipeCaptureRuntimeError(f"Forge launch classpath is absent from runtime inventory: {name}")
        path = root / name
        if _resolved_path(path) != path.absolute():
            raise RecipeCaptureRuntimeError(f"runtime classpath traverses a symbolic link: {name}")
        actual = _identity(path)
        if any(actual[key] != rows[name][key] for key in ("size", "sha256")):
            raise RecipeCaptureRuntimeError(f"selected runtime bytes changed: {name}")
        result.append(path)
    return result


def _manifest_main(raw: bytes) -> dict[str, str]:
    """Read main attributes with JAR continuation semantics, before entry hashes."""
    result: dict[str, str] = {}
    key = None
    for line in raw.decode("utf-8").splitlines():
        if not line:
            break
        if line.startswith(" "):
            if key is None:
                raise RecipeCaptureRuntimeError("invalid Forge manifest continuation")
            result[key] += line[1:]
        else:
            if ": " not in line:
                raise RecipeCaptureRuntimeError("invalid Forge manifest attribute")
            key, value = line.split(": ", 1)
            key = key.lower()
            if key in result:
                raise RecipeCaptureRuntimeError("duplicate Forge manifest attribute")
            result[key] = value
    return result


def observer_classpath(runtime_root: Path, runtime_files: list[dict[str, Any]],
                       artifact_paths: dict[str, str]) -> list[Path]:
    """Read and verify original Forge's own launch dependencies for compilation."""
    root = _absolute(runtime_root, "runtime")
    rows = _runtime_rows(runtime_files)
    if artifact_paths != select_runtime_artifacts(runtime_files):
        raise RecipeCaptureRuntimeError("artifact roles differ from the selected original runtime")
    forge = artifact_paths["forge_sha256"]
    forge_path = _verify_rows(root, rows, [forge])[0]
    with zipfile.ZipFile(_native_path(forge_path)) as jar:
        info = jar.getinfo("META-INF/MANIFEST.MF")
        if info.file_size > 4 * 1024**2:
            raise RecipeCaptureRuntimeError("Forge manifest exceeds the supported size")
        manifest = _manifest_main(jar.read(info))
    if manifest.get("main-class") != "net.minecraftforge.fml.relauncher.ServerLaunchWrapper":
        raise RecipeCaptureRuntimeError("Forge artifact does not declare the supported server entry point")
    names = [forge]
    for entry in manifest.get("class-path", "").split():
        parsed = urlsplit(entry)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise RecipeCaptureRuntimeError("Forge classpath is not a local relative file")
        name = inputs._path(unquote(parsed.path))
        if name in names:
            raise RecipeCaptureRuntimeError("duplicate Forge launch classpath entry")
        names.append(name)
    if artifact_paths["minecraft_sha256"] not in names:
        raise RecipeCaptureRuntimeError("Forge launch classpath does not contain the selected original Minecraft server")
    return _verify_rows(root, rows, names)


def _profile_root() -> Path:
    return repository_root(__file__) / "profiles/packs/supersymmetry"


def observer_source_manifest() -> dict[str, Any]:
    """Identify exactly the Java sources shipped by this profile installation."""
    root = _profile_root()
    files = []
    for name in _SOURCE_PATHS:
        row = _identity(root / name)
        files.append({"path": name, "size": row["size"], "sha256": row["sha256"]})
    body = {"format": "workbench-forge-observer-sources-v1", "files": files}
    return {**body, "id": "forge-observer-sources:sha256:" + _hash(body)}


def _java_tool(home: Path, name: str, *, windows: bool) -> Path:
    return home / "bin" / (name + ".exe" if windows else name)


def _materialized_source_mode(name: str, declared_mode: int, *, windows: bool | None = None) -> int:
    """Compare physical host modes without changing the retained Git declaration.

    Windows stat derives the executable bit from the filename, while POSIX can
    preserve the declaration. This mirrors the Core copy contract through value
    semantics; the profile does not import the Core storage implementation.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return 0o755 if PurePosixPath(name).suffix.casefold() in {".exe", ".com", ".bat", ".cmd"} else 0o644
    return declared_mode & 0o777


def _compiler_classpath(classpath: list[Path], output: Path, *, windows: bool) -> list[str]:
    try:
        result = [os.path.relpath(path.absolute(), output) for path in classpath]
    except ValueError as exc:
        raise RecipeCaptureRuntimeError("compiler dependencies must be staged on the build output volume") from exc
    if windows and any(not name.isascii() for name in result):
        raise RecipeCaptureRuntimeError("Java 8 compiler dependencies require ASCII relative paths in the Core-prepared environment")
    return result


def _verify_staged_sources(output: Path, rows: list[dict[str, Any]]) -> None:
    expected = {row["path"] for row in rows}
    observed = {"sources/" + path.name for path in _native_path(output / "sources").iterdir()}
    if observed != expected:
        raise RecipeCaptureRuntimeError("staged observer source inventory changed")
    for row in rows:
        actual = _identity(output / row["path"])
        if any(actual[key] != row[key] for key in ("size", "sha256")):
            raise RecipeCaptureRuntimeError(f"staged observer source bytes changed: {row['path']}")


def build_observer(java_home: Path, classpath: list[Path], output: Path, *,
                   cancelled: Event) -> dict[str, Any]:
    """Build a new retained Java 8 observer using the caller's bound process host."""
    java_home = _absolute(java_home, "build JDK")
    output = _absolute(output, "observer build output")
    if cancelled.is_set():
        raise RecipeCaptureRuntimeError("observer build was cancelled before preparation")
    if not classpath or len({_resolved_path(path) for path in classpath}) != len(classpath):
        raise RecipeCaptureRuntimeError("observer compilation requires a distinct explicit classpath")
    sources = observer_source_manifest()
    root = _profile_root()
    source_paths = [root / row["path"] for row in sources["files"]]
    release_path = java_home / "release"
    release = _native_path(release_path).read_text(encoding="utf-8")
    if not re.search(r'^JAVA_VERSION="1\.8\.', release, re.MULTILINE):
        raise RecipeCaptureRuntimeError("this observer requires an explicit Java 8 build JDK")
    compiler = _java_tool(java_home, "javac", windows=os.name == "nt")
    records = [_identity(path) for path in [*source_paths, *classpath, compiler, release_path]]
    staged_sources = [{**row, "source_path": row["path"],
                       "path": "sources/" + PurePosixPath(row["path"]).name}
                      for row in sources["files"]]
    if len({row["path"] for row in staged_sources}) != len(staged_sources):
        raise RecipeCaptureRuntimeError("observer source basenames collide")
    classpath_arguments = _compiler_classpath(classpath, output, windows=os.name == "nt")
    argv = [str(compiler), "-encoding", "UTF-8", "-source", "8", "-target", "8",
            "-classpath", os.pathsep.join(classpath_arguments), "-d", "classes",
            *(row["path"] for row in staged_sources)]
    request = {"format": "workbench-forge-observer-build-request-v1", "inputs": records,
               "sources": sources, "source_count": len(source_paths),
               "staged_sources": staged_sources, "argv": argv}
    request["binding"] = _hash(request)
    _native_path(output).mkdir(mode=0o700)
    secure_private_path(output, directory=True)
    classes = output / "classes"
    _native_path(classes).mkdir(mode=0o700)
    secure_private_path(classes, directory=True)
    _native_path(output / "sources").mkdir(mode=0o700)
    secure_private_path(output / "sources", directory=True)
    _write_private(output / "request.json", request)
    for source, row in zip(source_paths, staged_sources):
        if cancelled.is_set():
            raise RecipeCaptureRuntimeError("observer build was cancelled during source staging")
        target = output / row["path"]
        with _native_path(source).open("rb") as original, _native_path(target).open("xb") as staged:
            staged.write(original.read())
        secure_private_path(target, directory=False)
    _verify_staged_sources(output, staged_sources)
    result = capture_process(
        argv,
        directory=output / "compiler", binding=request["binding"], cwd=output, stdin=b"",
        environment={}, cancelled=cancelled, timeout_seconds=None, output_limit=None,
    )
    receipt = {"format": "workbench-forge-observer-build-v1", "request": request,
               "compiler": result.reference, "exit_code": result.exit_code,
               "state": "failed" if result.exit_code else "compiled-pending-verification"}
    _write_private(output / "result.json", receipt)
    if result.exit_code:
        raise RecipeCaptureRuntimeError(f"observer compiler failed; see {result.stderr.path}")
    for row in records:
        if _identity(Path(row["path"])) != row:
            raise RecipeCaptureRuntimeError(f"observer build input changed during compilation: {row['path']}")
    _verify_staged_sources(output, staged_sources)
    if cancelled.is_set():
        raise RecipeCaptureRuntimeError("observer build was cancelled after compilation")
    entries = {"META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\r\n\r\n"}
    # A short traversal root can have arbitrarily deeper class descendants.
    native_classes = _native_path(classes, force_extended=True)
    for path in sorted(native_classes.rglob("*.class")):
        if path.is_symlink() or not path.is_file():
            raise RecipeCaptureRuntimeError("compiler output is not an ordinary class file")
        raw = path.read_bytes()
        if raw[:4] != b"\xca\xfe\xba\xbe" or len(raw) < 8 or int.from_bytes(raw[6:8], "big") != 52:
            raise RecipeCaptureRuntimeError("observer class is not Java 8 bytecode")
        entries[path.relative_to(native_classes).as_posix()] = raw
    if "dev/workbench/crucible/forgerecipes/ForgeRecipeObserverMod.class" not in entries:
        raise RecipeCaptureRuntimeError("compiler did not produce the observer entry point")
    artifact = output / PurePosixPath(OBSERVER_PATH).name
    with zipfile.ZipFile(_native_path(artifact), "x", compression=zipfile.ZIP_DEFLATED) as jar:
        for name, raw in sorted(entries.items()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            jar.writestr(member, raw)
    secure_private_path(artifact, directory=False)
    receipt["artifact"] = _identity(artifact)
    receipt["class_count"] = len(entries) - 1
    receipt["state"] = "compiled-not-runtime-qualified"
    _write_private(output / "result.json", receipt)
    return receipt


def _observer_receipt(receipt: Any) -> dict[str, Any]:
    if (type(receipt) is not dict or receipt.get("format") != "workbench-forge-observer-build-v1"
            or receipt.get("state") != "compiled-not-runtime-qualified"
            or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0):
        raise RecipeCaptureRuntimeError("observer requires a successful retained build receipt")
    artifact = _file_record(receipt.get("artifact"), "observer artifact")
    request = receipt.get("request")
    if (type(request) is not dict or request.get("format") != "workbench-forge-observer-build-request-v1"
            or request.get("binding") != _hash({key: value for key, value in request.items() if key != "binding"})):
        raise RecipeCaptureRuntimeError("observer build request binding differs")
    sources = request.get("sources")
    if (type(sources) is not dict or sources.get("format") != "workbench-forge-observer-sources-v1"
            or sources.get("id") != "forge-observer-sources:sha256:" + _hash(
                {key: value for key, value in sources.items() if key != "id"})
            or type(sources.get("files")) is not list
            or any(type(row) is not dict for row in sources["files"])
            or [row.get("path") for row in sources["files"]] != list(_SOURCE_PATHS)):
        raise RecipeCaptureRuntimeError("observer source identity differs from its source manifest")
    for row in sources["files"]:
        _file_record(row, "observer source", relative=True)
    if "staged_sources" in request:
        expected = [{**row, "source_path": row["path"],
                     "path": "sources/" + PurePosixPath(row["path"]).name}
                    for row in sources["files"]]
        if request["staged_sources"] != expected:
            raise RecipeCaptureRuntimeError("observer staged source mapping differs from its packaged inputs")
    if request.get("source_count") != len(_SOURCE_PATHS):
        raise RecipeCaptureRuntimeError("observer source count differs")
    compiler = receipt.get("compiler")
    if (type(compiler) is not dict or compiler.get("format") != "workbench-process-capture-v1"
            or compiler.get("binding") != request["binding"]):
        raise RecipeCaptureRuntimeError("observer compiler capture binding differs")
    return artifact


def plan_capture_launch(capture_input: dict[str, Any], *, runtime_files: list[dict[str, Any]],
                        artifact_paths: dict[str, str], observer_path: str,
                        observer_build: dict[str, Any], java: dict[str, Any],
                        runtime_root: Path, input_manifest_path: Path,
                        input_manifest_sha256: str, output: Path,
                        heap_mib: int = 16384) -> dict[str, Any]:
    """Construct a launch from complete declarations; Core verifies actual bytes.

    ``runtime_files`` describes the prepared runtime after complete replacement
    of every declared source root and observer installation. The supplied Java
    record must describe the separately inspected and retained game executable.
    This function does not launch, copy, accept the EULA or admit a capture.
    """
    inputs.validate_capture_input(capture_input)
    if capture_input.get("observation_preparation") != PREPARATION:
        raise RecipeCaptureRuntimeError("Forge observation requires the declared original capability preparation policy")
    for field in ("capture_id", "launch_id"):
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", capture_input[field]) is None:
            raise RecipeCaptureRuntimeError(f"{field} is not supported by the Forge observer")
    runtime = _absolute(runtime_root, "runtime")
    manifest_path = _absolute(input_manifest_path, "input manifest")
    destination = _absolute(output, "capture output")
    _sha(input_manifest_sha256, "input manifest")
    if destination == runtime or destination.is_relative_to(runtime):
        raise RecipeCaptureRuntimeError("capture output must be outside the runtime inventory")
    if type(heap_mib) is not int or heap_mib < 1:
        raise RecipeCaptureRuntimeError("capture heap must be an explicit positive MiB value")
    _file_record(java, "game Java")
    if type(java.get("major")) is not int or java["major"] != 8:
        raise RecipeCaptureRuntimeError("the selected recipe model requires game Java 8")
    rows = _runtime_rows(runtime_files)
    if any(name == "cache/groovy" or name.startswith("cache/groovy/") for name in rows):
        raise RecipeCaptureRuntimeError("prepared runtime contains a previous compiled Groovy script cache")
    if artifact_paths != select_runtime_artifacts(runtime_files):
        raise RecipeCaptureRuntimeError("launch artifact paths differ from the selected runtime")
    if observer_path != OBSERVER_PATH or observer_path not in rows:
        raise RecipeCaptureRuntimeError("the built observer must be installed at the profile's declared mod path")
    artifact = _observer_receipt(observer_build)
    if any(rows[observer_path][key] != artifact[key] for key in ("size", "sha256")):
        raise RecipeCaptureRuntimeError("installed observer differs from its retained build")
    candidate = capture_input["pack_source"]["candidate"]
    mapped = {row["path"]: row for row in candidate["files"]
              if row["path"].split("/", 1)[0] in SOURCE_ROOTS}
    if "groovy/runConfig.json" not in mapped:
        raise RecipeCaptureRuntimeError("saved source must include groovy/runConfig.json")
    observed = {name: row for name, row in rows.items() if name.split("/", 1)[0] in SOURCE_ROOTS}
    if set(mapped) != set(observed):
        raise RecipeCaptureRuntimeError("prepared source roots differ from saved candidate; replace complete roots before launch")
    for name, row in mapped.items():
        if any(observed[name][key] != row[key] for key in ("size", "sha256")):
            raise RecipeCaptureRuntimeError(f"prepared saved source bytes differ: {name}")
        if observed[name].get("mode", 0) & 0o777 != _materialized_source_mode(name, row["mode"]):
            raise RecipeCaptureRuntimeError(f"prepared saved source mode differs: {name}")
    properties = {key: capture_input[key] for key in
                  ("capture_id", "launch_id", "candidate_lock_sha256", "adapter_profile_sha256")}
    properties.update(enabled="true", input_manifest_path=str(manifest_path),
                      input_manifest_sha256=input_manifest_sha256, output=str(destination),
                      minecraft_path=str(runtime / artifact_paths["minecraft_sha256"]))
    argv = [str(java["path"]), f"-Xms{min(2048, heap_mib)}M", f"-Xmx{heap_mib}M",
            "-Djava.awt.headless=true",
            *(f"-Dworkbench.runtimeGraph.{key}={value}" for key, value in sorted(properties.items())),
            "-jar", str(runtime / artifact_paths["forge_sha256"]), "nogui"]
    body = {"format": "workbench-supersymmetry-recipe-capture-launch-v1",
            "argv": argv, "cwd": str(runtime), "output": str(destination),
            "java": deepcopy(java), "input_manifest_sha256": input_manifest_sha256,
            "observer_sha256": artifact["sha256"],
            "observer_sources": observer_build["request"]["sources"]["id"],
            "source_roots": list(SOURCE_ROOTS), "mapped_source_files": sorted(mapped),
            "unmapped_source_files": sorted(set(row["path"] for row in candidate["files"]) - set(mapped)),
            "observation_preparation": deepcopy(PREPARATION), "heap_mib": heap_mib,
            "server_property_overrides": deepcopy(_SERVER_PROPERTIES),
            "native_executed": False, "eula_acceptance": "required-before-execution"}
    return {**body, "id": "forge-recipe-capture-launch:sha256:" + _hash(body)}
