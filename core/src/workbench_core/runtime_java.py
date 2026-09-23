"""Discover or provision the exact profile-selected Java runtime."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile, ZipInfo

from workbench_core.artifact_store import (
    ArtifactStoreError,
    DOWNLOAD_CHUNK_BYTES,
    fetch_verified_artifact,
    sha256_file,
)
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    ResolvedBindings,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_api.state_paths import default_suite_state_root
from .filesystem_paths import native_path


RECEIPT_PATH = Path("receipts/java-runtime-v2.json")
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_UNICODE_CDS_TRANSFORM_ID = (
    "workbench-java-portability:windows-unicode-default-cds-removal-v1"
)
GIT_RELEASE_RE = re.compile(
    r"^jdk-(?P<version>(?P<feature>[1-9][0-9]*)(?:\.0\.\d+)?\+\d+)$"
)


class JavaRuntimeError(ValueError):
    """Raised when the selected Java runtime cannot be made usable."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _required_string(
    record: Mapping[str, Any],
    field: str,
    label: str,
) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise JavaRuntimeError(f"{label} lacks {field}")
    return value


def load_java_runtime_policy(
    suite_root: Path | str,
    *,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the selected profile's exact supported provider-adapter policy."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise JavaRuntimeError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise JavaRuntimeError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    profile = active_configuration.platform_document.values
    java = profile.get("java")
    if not isinstance(java, Mapping):
        raise JavaRuntimeError("selected platform profile lacks java")
    runtime_identity = _required_string(
        java,
        "runtime",
        "selected Java policy",
    )
    provision = java.get("runtime_provision")
    if not isinstance(provision, Mapping):
        raise JavaRuntimeError(
            "selected Java policy lacks runtime_provision"
        )

    policy: dict[str, Any] = {
        "profile_id": _required_string(
            profile,
            "profile_id",
            "selected platform profile",
        ),
        "runtime_identity": runtime_identity,
        "distribution": _required_string(
            provision,
            "distribution",
            "Java runtime provision policy",
        ),
        "feature_version": provision.get("feature_version"),
        "release_name": _required_string(
            provision,
            "release_name",
            "Java runtime provision policy",
        ),
        "release_type": _required_string(
            provision,
            "release_type",
            "Java runtime provision policy",
        ),
        "image_type": _required_string(
            provision,
            "image_type",
            "Java runtime provision policy",
        ),
        "jvm_impl": _required_string(
            provision,
            "jvm_impl",
            "Java runtime provision policy",
        ),
        "heap_size": _required_string(
            provision,
            "heap_size",
            "Java runtime provision policy",
        ),
        "project": _required_string(
            provision,
            "project",
            "Java runtime provision policy",
        ),
        "vendor": _required_string(
            provision,
            "vendor",
            "Java runtime provision policy",
        ),
        "java_vendor": _required_string(
            provision,
            "java_vendor",
            "Java runtime provision policy",
        ),
        "api_base_url": _required_string(
            provision,
            "api_base_url",
            "Java runtime provision policy",
        ).rstrip("/"),
    }
    feature_version = policy["feature_version"]
    if type(feature_version) is not int or feature_version <= 0:
        raise JavaRuntimeError(
            "Java runtime provision policy lacks a positive feature_version"
        )
    release_match = GIT_RELEASE_RE.fullmatch(policy["release_name"])
    if (
        release_match is None
        or int(release_match.group("feature")) != feature_version
    ):
        raise JavaRuntimeError(
            "Temurin release_name must be an exact tag for feature_version"
        )
    adapter_policy = {
        "distribution": "eclipse-temurin",
        "release_type": "ga",
        "image_type": "jdk",
        "jvm_impl": "hotspot",
        "heap_size": "normal",
        "project": "jdk",
        "vendor": "eclipse",
        "java_vendor": "Eclipse Adoptium",
        "api_base_url": "https://api.adoptium.net/v3",
    }
    if any(policy[field] != value for field, value in adapter_policy.items()):
        raise JavaRuntimeError(
            "unsupported Java runtime provider-adapter policy"
        )
    expected_identity = (
        "eclipse-temurin-" + release_match.group("version")
    )
    if runtime_identity != expected_identity:
        raise JavaRuntimeError(
            "selected Java runtime identity and Temurin release differ"
        )
    policy["policy_sha256"] = sha256(
        _canonical_bytes(policy)
    ).hexdigest()
    return policy


def host_platform() -> dict[str, str]:
    """Map the current host to Adoptium operating-system and architecture IDs."""

    system = platform.system().lower()
    os_map = {
        "linux": "linux",
        "windows": "windows",
        "darwin": "mac",
    }
    if system not in os_map:
        raise JavaRuntimeError(
            f"Temurin provisioning does not support host OS: {system}"
        )
    machine = platform.machine().lower()
    arch_map = {
        "amd64": "x64",
        "x86_64": "x64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "ppc64le": "ppc64le",
        "s390x": "s390x",
        "riscv64": "riscv64",
    }
    if machine not in arch_map:
        raise JavaRuntimeError(
            f"Temurin provisioning does not support host architecture: "
            f"{machine}"
        )
    return {
        "os": os_map[system],
        "architecture": arch_map[machine],
        "system": platform.system(),
        "machine": platform.machine(),
    }


def _target_root(
    state_root: Path,
    policy: Mapping[str, Any],
    host: Mapping[str, str],
) -> Path:
    version = str(policy["release_name"]).removeprefix("jdk-")
    return (
        state_root
        / "jdks"
        / "eclipse-temurin"
        / version
        / f"{host['os']}-{host['architecture']}"
        / str(policy["policy_sha256"])[:16]
    )


def _java_executable(java_home: Path, host: Mapping[str, str]) -> Path:
    name = "java.exe" if host["os"] == "windows" else "java"
    return java_home / "bin" / name


def _is_junction(path: Path) -> bool:
    predicate = getattr(os.path, "isjunction", None)
    return bool(predicate is not None and predicate(path))


def _windows_short_path(path: Path) -> Path | None:
    """Return an exact ASCII DOS alias, when Windows provides one.

    Temurin's Windows launcher can fail to locate ``java.dll`` when its path
    contains characters outside the active ANSI code page. An 8.3 alias may
    get the launcher past that stage, but the class-loading probe must still
    verify the actual JDK before Core admits it.
    """

    if os.name != "nt":
        return None
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short_path = kernel32.GetShortPathNameW
    get_short_path.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    get_short_path.restype = ctypes.c_uint32
    required = get_short_path(str(path), None, 0)
    if not required:
        return None
    buffer = ctypes.create_unicode_buffer(required)
    written = get_short_path(str(path), buffer, len(buffer))
    if not written or written >= len(buffer):
        return None
    candidate = Path(buffer.value)
    if not str(candidate).isascii():
        return None
    return candidate


def java_execution_path(path: Path) -> Path:
    """Select an existing ordinary path Java can use without changing its bytes.

    Windows Java launchers may not understand Unicode or legacy-length paths.
    An existing ASCII DOS alias is usable only when it identifies the same stable
    file or directory. Paths of 240 characters or more also request a shorter
    alias; this does not qualify arbitrary lengths for later Java arguments.
    Keep the returned lexical path in execution records;
    resolving it would discard the alias. Callers still own input boundaries
    and content binding. This function never creates aliases or junctions.
    """
    from .check_storage import CheckStorageError, ordinary

    selected = Path(path).expanduser().absolute()

    def observe(candidate):
        try:
            metadata = candidate.stat()
            directory = stat.S_ISDIR(metadata.st_mode)
            admitted = ordinary(candidate, directory=directory)
            return admitted.stat()
        except (OSError, CheckStorageError) as exc:
            raise JavaRuntimeError(
                f"Java execution path must be an existing ordinary file or directory: {candidate}"
            ) from exc

    def token(metadata):
        return (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
        )

    before = observe(selected)
    execution = selected
    if os.name == "nt" and (not str(selected).isascii() or len(str(selected)) >= 240):
        execution = _windows_short_path(selected)
        if (execution is None or not execution.is_absolute()
                or not str(execution).isascii() or len(str(execution)) >= 240):
            raise JavaRuntimeError(
                "Java cannot use the selected Windows path because a sufficiently short "
                "ASCII 8.3 alias is unavailable. Select a shorter ASCII JDK or --state-root directory."
            )
    current = observe(execution)
    try:
        same_file = os.path.samefile(selected, execution)
    except OSError as exc:
        raise JavaRuntimeError("Java execution path changed during alias verification") from exc
    after = observe(selected)
    if not same_file or token(before) != token(current) or token(before) != token(after):
        raise JavaRuntimeError("Java execution alias does not identify the same unchanged selected input")
    return execution


def _windows_runtime_short_paths(
    target_root: Path,
    java_home_relative: Path,
    executable_relative: Path,
) -> tuple[Path, Path] | None:
    """Predict stable ASCII execution paths from the target parent's alias."""

    short_parent = _windows_short_path(target_root.parent)
    suffixes = (
        (target_root.name, "extracted", *java_home_relative.parts),
        (target_root.name, "extracted", *executable_relative.parts),
    )
    if short_parent is None or any(
        not part.isascii()
        for suffix in suffixes
        for part in suffix
    ):
        return None
    java_home = short_parent.joinpath(*suffixes[0])
    java = short_parent.joinpath(*suffixes[1])
    if not str(java_home).isascii() or not str(java).isascii():
        return None
    return java_home, java


def _plan_java_execution(
    *,
    host: Mapping[str, str],
    target_root: Path,
    java_home_relative: Path,
    executable_relative: Path,
) -> dict[str, Any]:
    canonical_home = target_root / "extracted" / java_home_relative
    canonical_java = target_root / "extracted" / executable_relative
    if host["os"] != "windows" or (
        str(canonical_home).isascii() and str(canonical_java).isascii()
    ):
        return {
            "kind": "canonical-path",
            "java_home": canonical_home,
            "java": canonical_java,
        }
    short_paths = _windows_runtime_short_paths(
        target_root,
        java_home_relative,
        executable_relative,
    )
    if short_paths is not None:
        short_home, short_java = short_paths
        return {
            "kind": "windows-short-path",
            "java_home": short_home,
            "java": short_java,
        }
    raise JavaRuntimeError(
        "Windows Java cannot execute from this Unicode state path because "
        "the volume did not provide an ASCII DOS short path. Choose an ASCII "
        "state location in Workbench setup."
    )


def plan_managed_java_execution(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    *,
    state_root: Path | str,
) -> dict[str, Any]:
    """Describe Java's exact lexical execution side effect without writing."""

    if host["os"] != "windows":
        return {
            "kind": "canonical-path",
            "portability_transform_id": None,
        }
    state = Path(state_root).expanduser().resolve()
    if str(state).isascii():
        return {
            "kind": "canonical-path",
            "portability_transform_id": None,
        }
    anchor = state
    missing_parts: list[str] = []
    while not anchor.exists() and anchor != anchor.parent:
        missing_parts.append(anchor.name)
        anchor = anchor.parent
    if any(not part.isascii() for part in missing_parts):
        raise JavaRuntimeError(
            "Windows cannot verify an ASCII 8.3 DOS short-name alias "
            "read-only for the nonexistent Unicode portion of --state-root; "
            "pass an ASCII --state-root"
        )
    # When the Unicode portion already exists, GetShortPathNameW can prove
    # whether the volume supplied a usable ASCII 8.3 alias without writing.
    if not str(anchor).isascii():
        short_anchor = _windows_short_path(anchor)
        if short_anchor is None or not str(short_anchor).isascii():
            raise JavaRuntimeError(
                "Windows 8.3 ASCII DOS short-name alias is unavailable for "
                "the selected Unicode state path; pass an ASCII --state-root"
            )
    return {
        "kind": "windows-short-path",
        "portability_transform_id": WINDOWS_UNICODE_CDS_TRANSFORM_ID,
    }


def probe_java(
    executable: Path | str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, str]:
    """Execute Java once and return the identity-bearing runtime properties."""

    # Preserve receipt-bound DOS short paths and junctions lexically. Resolving
    # them before CreateProcess reintroduces the Unicode path that the alias is
    # specifically meant to avoid.
    java = Path(executable).expanduser().absolute()
    if not java.is_file():
        raise JavaRuntimeError(f"Java executable is missing: {java}")
    try:
        completed = subprocess.run(
            [str(java), "-XshowSettings:properties", "-version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            env={
                key: value
                for key, value in os.environ.items()
                if key.casefold()
                not in {
                    "java_tool_options",
                    "_java_options",
                    "jdk_java_options",
                }
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JavaRuntimeError(
            f"cannot execute Java candidate: {java}"
        ) from exc
    output = completed.stdout + "\n" + completed.stderr
    if completed.returncode != 0:
        detail = " ".join(completed.stderr.strip().split())[:1000]
        raise JavaRuntimeError(
            f"Java candidate exited with {completed.returncode}: {java}"
            + (f" ({detail})" if detail else "")
        )
    if re.search(r"\[warning\]\[cds(?:,[^\]]*)?\]", output, re.IGNORECASE):
        raise JavaRuntimeError(
            "Java candidate reported an unusable class-data-sharing archive: "
            f"{java}"
        )
    properties: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$", line)
        if match:
            properties[match.group(1)] = match.group(2)
    required = (
        "java.version",
        "java.runtime.version",
        "java.vendor",
        "java.home",
        "java.vm.name",
        "java.vm.version",
        "os.arch",
    )
    missing = [
        name
        for name in required
        if not properties.get(name)
    ]
    if missing:
        raise JavaRuntimeError(
            "Java candidate lacks identity properties: "
            + ", ".join(missing)
        )
    if os.name == "nt":
        # A Windows JDK can report its version through an ASCII 8.3 alias yet
        # fail as soon as Java loads a main class: native DLL lookup expands
        # the real Unicode home and loses characters outside the active code
        # page. Test the selected JDK's own compiler main before admitting it.
        try:
            class_probe = subprocess.run(
                [str(java), "-Xshare:off", "-m", "jdk.compiler/com.sun.tools.javac.Main", "-version"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key.casefold() not in {
                        "java_tool_options", "_java_options", "jdk_java_options"
                    }
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JavaRuntimeError(
                f"Java candidate cannot load its compiler main class: {java}"
            ) from exc
        if class_probe.returncode != 0:
            detail = " ".join(class_probe.stderr.strip().split())[:1000]
            raise JavaRuntimeError(
                f"Java candidate cannot load its compiler main class: {java}"
                + (f" ({detail})" if detail else "")
            )
    return {
        "java_version": properties["java.version"],
        "runtime_version": properties["java.runtime.version"],
        "vendor": properties["java.vendor"],
        "vendor_version": properties.get("java.vendor.version", ""),
        "java_home": properties["java.home"],
        "vm_name": properties["java.vm.name"],
        "vm_version": properties["java.vm.version"],
        "os_arch": properties["os.arch"],
    }


def _probe_mismatch(
    probe: Mapping[str, str],
    policy: Mapping[str, Any],
    host: Mapping[str, str],
) -> str | None:
    version = str(policy["release_name"]).removeprefix("jdk-")
    runtime_version = probe.get("runtime_version", "")
    if runtime_version != version and not runtime_version.startswith(version + "-"):
        return (
            f"requires runtime {version}, found "
            f"{probe.get('runtime_version')}"
        )
    vendor = probe.get("vendor", "").casefold()
    vendor_version = probe.get("vendor_version", "").casefold()
    if (
        str(policy["java_vendor"]).casefold() not in vendor
        and "temurin" not in vendor_version
    ):
        return (
            f"requires Eclipse Temurin, found "
            f"{probe.get('vendor')}"
        )
    java_arch = probe.get("os_arch", "").casefold()
    arch_aliases = {
        "x64": {"amd64", "x86_64", "x64"},
        "aarch64": {"aarch64", "arm64"},
        "ppc64le": {"ppc64le"},
        "s390x": {"s390x"},
        "riscv64": {"riscv64"},
    }
    if java_arch not in arch_aliases.get(host["architecture"], set()):
        return (
            f"requires architecture {host['architecture']}, found "
            f"{probe.get('os_arch')}"
        )
    return None


def _normalize_managed_probe(
    probe: Mapping[str, str],
    *,
    host: Mapping[str, str],
    java_home: Path,
    extracted_root: Path,
) -> dict[str, str]:
    reported_home_text = probe["java_home"]
    if (
        host["os"] == "windows"
        and os.name != "nt"
        and (
            re.match(r"^[A-Za-z]:[\\/]", reported_home_text)
            or reported_home_text.startswith("\\\\")
        )
    ):
        try:
            converted = subprocess.run(
                ["wslpath", "-u", reported_home_text],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JavaRuntimeError(
                "cannot map the Windows Java home into the current host"
            ) from exc
        if converted.returncode or not converted.stdout.strip():
            raise JavaRuntimeError(
                "cannot map the Windows Java home into the current host"
            )
        reported_home = Path(converted.stdout.strip()).resolve()
    else:
        reported_home = Path(reported_home_text).resolve()
    expected_home = java_home.resolve()
    if reported_home != expected_home:
        raise JavaRuntimeError(
            "Java reported a home outside the selected archive layout"
        )
    normalized = dict(probe)
    normalized["java_home"] = (
        "@runtime/"
        + expected_home.relative_to(extracted_root.resolve()).as_posix()
    )
    return normalized


def discover_java_runtime(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    *,
    environment: Mapping[str, str] | None = None,
    candidates: Sequence[tuple[str, Path]] | None = None,
) -> dict[str, Any]:
    """Inspect explicit environment and PATH candidates without writing state."""

    selected_environment = os.environ if environment is None else environment
    discovered_candidates: list[tuple[str, Path]] = []
    if candidates is None:
        for variable in (
            "WORKBENCH_JAVA_HOME",
            "JAVA_HOME",
            "JDK_HOME",
        ):
            value = selected_environment.get(variable)
            if value:
                discovered_candidates.append((
                    variable,
                    _java_executable(Path(value), host),
                ))
        path_java = shutil.which(
            "java.exe" if host["os"] == "windows" else "java",
            path=selected_environment.get("PATH", ""),
        )
        if path_java:
            discovered_candidates.append(("PATH", Path(path_java)))
    else:
        discovered_candidates.extend(candidates)

    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    selected: dict[str, Any] | None = None
    for origin, candidate in discovered_candidates:
        java = candidate.expanduser().absolute()
        if java in seen:
            continue
        seen.add(java)
        row: dict[str, Any] = {
            "origin": origin,
            "java_uri": java.as_uri(),
        }
        try:
            probe = probe_java(java)
            mismatch = _probe_mismatch(probe, policy, host)
            row["probe"] = probe
            row["state"] = (
                "compatible" if mismatch is None else "incompatible"
            )
            if mismatch is not None:
                row["reason"] = mismatch
            elif selected is None:
                selected = dict(row)
        except JavaRuntimeError as exc:
            row["state"] = "broken"
            row["reason"] = str(exc)
        rows.append(row)
    return {
        "state": "found" if selected is not None else "missing",
        "selected": selected,
        "candidates": rows,
    }


def _bounded_json_response(url: str, timeout_seconds: float) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Workbench-Temurin-Provisioner/0.1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_METADATA_BYTES + 1)
    except (HTTPError, URLError, OSError) as exc:
        raise JavaRuntimeError(
            f"cannot query Adoptium release metadata: {exc}"
        ) from exc
    if len(payload) > MAX_METADATA_BYTES:
        raise JavaRuntimeError(
            "Adoptium release metadata exceeds the safety limit"
        )
    try:
        return json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise JavaRuntimeError(
            "Adoptium release metadata is not valid JSON"
        ) from exc


def resolve_temurin_asset(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Resolve the exact configured release to one host archive."""

    base = (
        f"{policy['api_base_url']}/assets/feature_releases/"
        f"{policy['feature_version']}/{policy['release_type']}"
    )
    for page in range(10):
        query = urlencode({
            "architecture": host["architecture"],
            "heap_size": policy["heap_size"],
            "image_type": policy["image_type"],
            "jvm_impl": policy["jvm_impl"],
            "os": host["os"],
            "page": page,
            "page_size": 20,
            "project": policy["project"],
            "sort_method": "DATE",
            "sort_order": "DESC",
            "vendor": policy["vendor"],
        })
        query_url = f"{base}?{query}"
        releases = _bounded_json_response(query_url, timeout_seconds)
        if not isinstance(releases, list):
            raise JavaRuntimeError(
                "Adoptium release metadata must be an array"
            )
        if not releases:
            break
        matches = [
            release
            for release in releases
            if isinstance(release, dict)
            and release.get("release_name") == policy["release_name"]
        ]
        if not matches:
            continue
        if len(matches) != 1:
            raise JavaRuntimeError(
                "Adoptium returned duplicate configured releases"
            )
        release = matches[0]
        binaries = release.get("binaries")
        if not isinstance(binaries, list):
            raise JavaRuntimeError(
                "Adoptium release lacks host binaries"
            )
        matching_binaries = [
            binary
            for binary in binaries
            if isinstance(binary, dict)
            and binary.get("architecture") == host["architecture"]
            and binary.get("os") == host["os"]
            and binary.get("image_type") == policy["image_type"]
            and binary.get("jvm_impl") == policy["jvm_impl"]
            and binary.get("heap_size") == policy["heap_size"]
        ]
        if len(matching_binaries) != 1:
            raise JavaRuntimeError(
                "Adoptium release does not contain one matching host binary"
            )
        binary = matching_binaries[0]
        package = binary.get("package")
        version_data = release.get("version_data")
        if not isinstance(package, dict) or not isinstance(
            version_data,
            dict,
        ):
            raise JavaRuntimeError(
                "Adoptium release lacks package or version metadata"
            )
        package_url = _required_string(
            package,
            "link",
            "Adoptium package",
        )
        checksum = _required_string(
            package,
            "checksum",
            "Adoptium package",
        )
        package_name = _required_string(
            package,
            "name",
            "Adoptium package",
        )
        package_size = package.get("size")
        if urlparse(package_url).scheme != "https":
            raise JavaRuntimeError(
                "Adoptium package must use HTTPS"
            )
        if SHA256_RE.fullmatch(checksum) is None:
            raise JavaRuntimeError(
                "Adoptium package lacks an exact SHA-256"
            )
        if type(package_size) is not int or package_size <= 0:
            raise JavaRuntimeError(
                "Adoptium package lacks an exact size"
            )
        expected_suffix = (
            ".zip" if host["os"] == "windows" else ".tar.gz"
        )
        if not package_name.endswith(expected_suffix):
            raise JavaRuntimeError(
                f"Adoptium host package must be {expected_suffix}"
            )
        if (
            release.get("release_type") != policy["release_type"]
            or release.get("vendor") != policy["vendor"]
            or version_data.get("major") != policy["feature_version"]
        ):
            raise JavaRuntimeError(
                "Adoptium release metadata violates the selected policy"
            )
        return {
            "api_query_url": query_url,
            "release_name": release["release_name"],
            "release_link": _required_string(
                release,
                "release_link",
                "Adoptium release",
            ),
            "release_timestamp": _required_string(
                release,
                "timestamp",
                "Adoptium release",
            ),
            "semver": _required_string(
                version_data,
                "semver",
                "Adoptium version",
            ),
            "openjdk_version": _required_string(
                version_data,
                "openjdk_version",
                "Adoptium version",
            ),
            "scm_ref": _required_string(
                binary,
                "scm_ref",
                "Adoptium binary",
            ),
            "package_name": package_name,
            "url": package_url,
            "sha256": checksum,
            "size": package_size,
        }
    raise JavaRuntimeError(
        "configured Temurin release is unavailable for this host"
    )


def _safe_archive_path(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise JavaRuntimeError(f"unsafe Java archive path: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or ":" in path.parts[0]
    ):
        raise JavaRuntimeError(f"unsafe Java archive path: {name!r}")
    return path


def _extract_tar(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, mode="r:gz") as source:
            members = source.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise JavaRuntimeError(
                    "Temurin archive has an invalid member count"
                )
            expanded = sum(
                member.size
                for member in members
                if member.isfile()
            )
            if expanded > MAX_EXPANDED_BYTES:
                raise JavaRuntimeError(
                    "Temurin archive expands beyond the safety limit"
                )
            seen: set[str] = set()
            for member in members:
                normalized = _safe_archive_path(
                    member.name
                ).as_posix().rstrip("/")
                key = normalized.casefold()
                if key in seen:
                    raise JavaRuntimeError(
                        f"duplicate Java archive path: {normalized}"
                    )
                seen.add(key)
                if member.isdev() or member.isfifo():
                    raise JavaRuntimeError(
                        f"unsupported Java archive member: {normalized}"
                    )
            destination.mkdir(parents=True)
            source.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise JavaRuntimeError(
            "Temurin package is not a safe tar.gz archive"
        ) from exc


def _zip_member_path(info: ZipInfo) -> PurePosixPath:
    path = _safe_archive_path(info.filename)
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise JavaRuntimeError(
            f"Java ZIP member is a symbolic link: {info.filename}"
        )
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise JavaRuntimeError(
            f"Java ZIP member has unsupported type: {info.filename}"
        )
    if info.flag_bits & 0x1:
        raise JavaRuntimeError(
            f"Java ZIP member is encrypted: {info.filename}"
        )
    return path


def _extract_zip(archive: Path, destination: Path, *, label: str = "Temurin") -> None:
    try:
        with ZipFile(archive) as source:
            infos = source.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
                raise JavaRuntimeError(
                    f"{label} ZIP has an invalid member count"
                )
            if sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
                raise JavaRuntimeError(
                    f"{label} ZIP expands beyond the safety limit"
                )
            seen: set[str] = set()
            validated: list[tuple[ZipInfo, PurePosixPath]] = []
            for info in infos:
                member = _zip_member_path(info)
                normalized = member.as_posix().rstrip("/")
                key = normalized.casefold()
                if key in seen:
                    raise JavaRuntimeError(
                        f"duplicate Java ZIP path: {normalized}"
                    )
                seen.add(key)
                validated.append((info, member))
            native_path(destination).mkdir(parents=True)
            for info, member in validated:
                path = destination.joinpath(*member.parts)
                if info.is_dir():
                    native_path(path).mkdir(parents=True, exist_ok=True)
                    continue
                native_path(path.parent).mkdir(parents=True, exist_ok=True)
                with source.open(info) as input_stream:
                    with native_path(path).open("xb") as output_stream:
                        shutil.copyfileobj(
                            input_stream,
                            output_stream,
                            DOWNLOAD_CHUNK_BYTES,
                        )
                mode = stat.S_IMODE(info.external_attr >> 16)
                if mode:
                    native_path(path).chmod(mode)
    except (BadZipFile, OSError) as exc:
        raise JavaRuntimeError(
            f"{label} package is not a safe ZIP archive"
        ) from exc


def _extract_archive(
    archive: Path,
    package_name: str,
    destination: Path,
) -> None:
    if package_name.endswith(".tar.gz"):
        _extract_tar(archive, destination)
    elif package_name.endswith(".zip"):
        _extract_zip(archive, destination)
    else:
        raise JavaRuntimeError(
            "unsupported Temurin package archive type"
        )


def _find_java_home(
    extracted_root: Path,
    host: Mapping[str, str],
) -> tuple[Path, Path]:
    executable_name = (
        "java.exe" if host["os"] == "windows" else "java"
    )
    candidates = [
        path
        for path in extracted_root.rglob(executable_name)
        if path.parent.name == "bin" and path.is_file()
    ]
    if len(candidates) != 1:
        raise JavaRuntimeError(
            "Temurin archive must contain one Java executable"
        )
    executable = candidates[0]
    resolved_executable = executable.resolve()
    if not resolved_executable.is_relative_to(extracted_root.resolve()):
        raise JavaRuntimeError(
            "Temurin Java executable escapes the extracted runtime"
        )
    java_home = executable.parent.parent
    return java_home, executable


def _tree_identity(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise JavaRuntimeError(
            "managed Java tree is not a regular directory"
        )
    root_resolved = root.resolve()
    entries: list[dict[str, Any]] = []
    file_count = 0
    directory_count = 0
    symlink_count = 0
    total_bytes = 0
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root_resolved):
                raise JavaRuntimeError(
                    f"managed Java symlink escapes its tree: {relative}"
                )
            entries.append({
                "kind": "symlink",
                "mode": mode,
                "path": relative,
                "target": os.readlink(path),
            })
            symlink_count += 1
            continue
        if path.is_dir():
            entries.append({
                "kind": "directory",
                "mode": mode,
                "path": relative,
            })
            directory_count += 1
            continue
        if not path.is_file():
            raise JavaRuntimeError(
                f"managed Java tree contains a special file: {relative}"
            )
        digest, size = sha256_file(path)
        entries.append({
            "kind": "file",
            "mode": mode,
            "path": relative,
            "sha256": digest,
            "size": size,
        })
        file_count += 1
        total_bytes += size
    return {
        "tree_sha256": "sha256:" + sha256(
            _canonical_bytes(entries)
        ).hexdigest(),
        "file_count": file_count,
        "directory_count": directory_count,
        "symlink_count": symlink_count,
        "total_bytes": total_bytes,
    }


def _asset_identity(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: asset.get(field)
        for field in (
            "release_name",
            "semver",
            "openjdk_version",
            "scm_ref",
            "package_name",
            "url",
            "sha256",
            "size",
        )
    }


def _runtime_identity_v2(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    asset: Mapping[str, Any],
    tree: Mapping[str, Any],
    portability: Mapping[str, Any],
) -> str:
    identity = {
        "receipt_format": "workbench-java-runtime-receipt-v2",
        "policy": dict(policy),
        "host": dict(host),
        "asset": _asset_identity(asset),
        "tree_sha256": tree["tree_sha256"],
        "portability": dict(portability),
    }
    return "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()


def _prepare_managed_java_portability(
    *,
    extracted_root: Path,
    java_home: Path,
    host: Mapping[str, str],
    execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply deterministic runtime-image transforms needed for execution."""

    if host.get("os") != "windows" or execution.get("kind") != "windows-short-path":
        return {
            "cds": {
                "state": "vendor-default",
                "transform_id": None,
                "removed_archives": [],
            }
        }
    server = java_home / "bin" / "server"
    archives = sorted(server.glob("classes*.jsa"), key=lambda path: path.name)
    removed: list[dict[str, Any]] = []
    for archive in archives:
        if archive.is_symlink() or not archive.is_file():
            raise JavaRuntimeError(
                "managed Java CDS archive is not one regular file: "
                f"{archive}"
            )
        try:
            relative = archive.relative_to(extracted_root).as_posix()
        except ValueError as exc:
            raise JavaRuntimeError(
                "managed Java CDS archive escapes the extracted tree"
            ) from exc
        digest, size = sha256_file(archive)
        removed.append({
            "path": relative,
            "sha256": digest,
            "size": size,
        })
    for archive in archives:
        archive.unlink()
    return {
        "cds": {
            "state": "disabled-for-windows-unicode-custody",
            "transform_id": WINDOWS_UNICODE_CDS_TRANSFORM_ID,
            "removed_archives": removed,
        }
    }


def _validate_managed_java_portability(
    portability: Mapping[str, Any],
    *,
    extracted_root: Path,
    java_home: Path,
    execution_kind: str,
) -> dict[str, Any]:
    """Validate the receipt-bound portability transform on reuse."""

    if set(portability) != {"cds"} or not isinstance(
        portability.get("cds"), Mapping
    ):
        raise JavaRuntimeError("managed Java portability record is invalid")
    cds = portability["cds"]
    if set(cds) != {"state", "transform_id", "removed_archives"} or not isinstance(
        cds.get("removed_archives"), list
    ):
        raise JavaRuntimeError("managed Java CDS portability record is invalid")
    expected_state = (
        "disabled-for-windows-unicode-custody"
        if execution_kind == "windows-short-path"
        else "vendor-default"
    )
    if cds.get("state") != expected_state:
        raise JavaRuntimeError(
            "managed Java CDS portability state differs from execution"
        )
    expected_transform_id = (
        WINDOWS_UNICODE_CDS_TRANSFORM_ID
        if execution_kind == "windows-short-path"
        else None
    )
    if cds.get("transform_id") != expected_transform_id:
        raise JavaRuntimeError(
            "managed Java CDS portability transform differs from execution"
        )
    expected_prefix = (
        java_home.relative_to(extracted_root).as_posix() + "/bin/server/"
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in cds["removed_archives"]:
        if not isinstance(row, Mapping) or set(row) != {
            "path",
            "sha256",
            "size",
        }:
            raise JavaRuntimeError(
                "managed Java removed CDS archive record is invalid"
            )
        relative = row.get("path")
        digest = row.get("sha256")
        size = row.get("size")
        member = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            member is None
            or relative in seen
            or member.parent
            != PurePosixPath(expected_prefix.removesuffix("/"))
            or re.fullmatch(r"classes[^/]*\.jsa", member.name) is None
            or member.is_absolute()
            or ".." in member.parts
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or type(size) is not int
            or size < 0
        ):
            raise JavaRuntimeError(
                "managed Java removed CDS archive identity is invalid"
            )
        candidate = extracted_root.joinpath(*member.parts)
        if candidate.exists() or candidate.is_symlink():
            raise JavaRuntimeError(
                "managed Java removed CDS archive has reappeared"
            )
        seen.add(relative)
        rows.append({
            "path": relative,
            "sha256": digest,
            "size": size,
        })
    if rows != sorted(rows, key=lambda row: row["path"]):
        raise JavaRuntimeError(
            "managed Java removed CDS archives are not canonical"
        )
    if expected_state == "vendor-default" and rows:
        raise JavaRuntimeError(
            "managed Java canonical execution unexpectedly removed CDS archives"
        )
    if expected_state != "vendor-default" and any(
        path.exists() or path.is_symlink()
        for path in (java_home / "bin" / "server").glob("classes*.jsa")
    ):
        raise JavaRuntimeError(
            "managed Java CDS archives remain under Unicode custody"
        )
    return {
        "cds": {
            "state": expected_state,
            "transform_id": expected_transform_id,
            "removed_archives": rows,
        }
    }


def _receipt_v2(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    asset: Mapping[str, Any],
    *,
    cache_path: Path,
    target_root: Path,
    java_home_relative: Path,
    executable_relative: Path,
    execution: Mapping[str, Any],
    portability: Mapping[str, Any],
    probe: Mapping[str, str],
    tree: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_id = _runtime_identity_v2(
        policy,
        host,
        asset,
        tree,
        portability,
    )
    execution_record = {
        "kind": execution["kind"],
        "java_home_uri": Path(execution["java_home"]).as_uri(),
        "java_uri": Path(execution["java"]).as_uri(),
    }
    canonical_home = target_root / "extracted" / java_home_relative
    canonical_java = target_root / "extracted" / executable_relative
    return {
        "format": "workbench-java-runtime-receipt-v2",
        "schema_version": 2,
        "runtime_id": runtime_id,
        "operation_class": "local-mutation",
        "state": "ready",
        "policy": dict(policy),
        "host": dict(host),
        "asset": {
            **dict(asset),
            "cache_uri": cache_path.as_uri(),
        },
        "target": {
            "root_uri": target_root.as_uri(),
            "custody_java_home_uri": canonical_home.as_uri(),
            "custody_java_uri": canonical_java.as_uri(),
            "java_home_uri": execution_record["java_home_uri"],
            "java_uri": execution_record["java_uri"],
            "receipt_uri": (target_root / RECEIPT_PATH).as_uri(),
        },
        "execution": execution_record,
        "portability": dict(portability),
        "probe": dict(probe),
        "tree": dict(tree),
        "limitations": [
            "Workbench does not modify system JAVA_HOME or PATH.",
            "Workbench does not change Prism or MultiMC Java settings.",
            "This receipt verifies Java; it does not claim Cleanroom launched.",
        ],
    }


def _load_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise JavaRuntimeError(
            "managed Java receipt cannot be a symbolic link"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JavaRuntimeError(
            "managed Java receipt is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise JavaRuntimeError(
            "managed Java receipt must be an object"
        )
    return value


def _current_managed_receipt_path(target_root: Path) -> Path:
    if (
        not target_root.is_dir()
        or target_root.is_symlink()
        or _is_junction(target_root)
    ):
        raise JavaRuntimeError(
            "managed Java target is not a regular directory"
        )
    receipt_path = target_root / RECEIPT_PATH
    if not receipt_path.exists() and not receipt_path.is_symlink():
        raise JavaRuntimeError(
            "managed Java target lacks the current V2 runtime receipt; "
            "remove the stale target before provisioning again: "
            f"{target_root}"
        )
    return receipt_path


def _reuse_managed_runtime_v2(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    *,
    target_root: Path,
) -> dict[str, Any]:
    receipt_path = _current_managed_receipt_path(target_root)
    receipt = _load_receipt(receipt_path)
    asset = receipt.get("asset")
    target = receipt.get("target")
    execution = receipt.get("execution")
    portability = receipt.get("portability")
    if (
        receipt.get("format") != "workbench-java-runtime-receipt-v2"
        or receipt.get("schema_version") != 2
        or receipt.get("operation_class") != "local-mutation"
        or receipt.get("state") != "ready"
        or receipt.get("policy") != dict(policy)
        or receipt.get("host") != dict(host)
        or not isinstance(asset, dict)
        or not isinstance(target, dict)
        or not isinstance(execution, dict)
        or not isinstance(portability, dict)
    ):
        raise JavaRuntimeError(
            "managed Java target belongs to a different runtime"
        )
    canonical_home_uri = target.get("custody_java_home_uri")
    canonical_java_uri = target.get("custody_java_uri")
    if not isinstance(canonical_home_uri, str) or not isinstance(
        canonical_java_uri,
        str,
    ):
        raise JavaRuntimeError("managed Java receipt lacks custody URIs")
    canonical_home = _file_uri_path(
        canonical_home_uri,
        "managed Java custody home",
    )
    canonical_java = _file_uri_path(
        canonical_java_uri,
        "managed Java custody executable",
    )
    extracted = target_root / "extracted"
    try:
        home_relative = canonical_home.relative_to(extracted)
        executable_relative = canonical_java.relative_to(extracted)
    except ValueError as exc:
        raise JavaRuntimeError(
            "managed Java custody paths escape the extracted tree"
        ) from exc
    expected_target_base = {
        "root_uri": target_root.as_uri(),
        "custody_java_home_uri": canonical_home.as_uri(),
        "custody_java_uri": canonical_java.as_uri(),
        "receipt_uri": receipt_path.as_uri(),
    }
    if any(target.get(field) != value for field, value in expected_target_base.items()):
        raise JavaRuntimeError("managed Java custody URIs have been modified")
    tree = _tree_identity(extracted)
    if receipt.get("tree") != tree:
        raise JavaRuntimeError(
            "managed Java tree has drifted from its receipt"
        )
    kind = execution.get("kind")
    if kind == "canonical-path":
        expected_execution = {
            "kind": kind,
            "java_home_uri": canonical_home.as_uri(),
            "java_uri": canonical_java.as_uri(),
        }
    elif kind == "windows-short-path":
        short_paths = _windows_runtime_short_paths(
            target_root,
            home_relative,
            executable_relative,
        )
        if short_paths is None:
            raise JavaRuntimeError(
                "managed Java ASCII short-path alias is no longer available"
            )
        short_home, short_java = short_paths
        expected_execution = {
            "kind": kind,
            "java_home_uri": short_home.as_uri(),
            "java_uri": short_java.as_uri(),
        }
    else:
        raise JavaRuntimeError("managed Java execution kind is invalid")
    if execution != expected_execution:
        raise JavaRuntimeError(
            "managed Java execution aliases have been modified"
        )
    validated_portability = _validate_managed_java_portability(
        portability,
        extracted_root=extracted,
        java_home=canonical_home,
        execution_kind=kind,
    )
    runtime_id = _runtime_identity_v2(
        policy,
        host,
        asset,
        tree,
        validated_portability,
    )
    if receipt.get("runtime_id") != runtime_id:
        raise JavaRuntimeError(
            "managed Java runtime identity has been modified"
        )
    expected_target = {
        **expected_target_base,
        "java_home_uri": expected_execution["java_home_uri"],
        "java_uri": expected_execution["java_uri"],
    }
    if target != expected_target:
        raise JavaRuntimeError(
            "managed Java target execution URIs have been modified"
        )
    execution_home = _file_uri_path(
        expected_execution["java_home_uri"],
        "managed Java execution home",
    )
    executable = _file_uri_path(
        expected_execution["java_uri"],
        "managed Java execution executable",
    )
    try:
        if (
            not os.path.samefile(execution_home, canonical_home)
            or not os.path.samefile(executable, canonical_java)
        ):
            raise JavaRuntimeError(
                "managed Java execution alias escapes its custody tree"
            )
    except OSError as exc:
        raise JavaRuntimeError(
            "managed Java execution alias cannot be resolved"
        ) from exc
    probe = _normalize_managed_probe(
        probe_java(executable),
        host=host,
        java_home=canonical_home,
        extracted_root=extracted,
    )
    mismatch = _probe_mismatch(probe, policy, host)
    if mismatch is not None:
        raise JavaRuntimeError(
            f"managed Java no longer satisfies its policy: {mismatch}"
        )
    if receipt.get("probe") != probe:
        raise JavaRuntimeError(
            "managed Java probe differs from its receipt"
        )
    top_level = sorted(path.name for path in target_root.iterdir())
    receipt_children = sorted(
        path.name for path in receipt_path.parent.iterdir()
    )
    if top_level != ["extracted", "receipts"] or receipt_children != [
        RECEIPT_PATH.name
    ]:
        raise JavaRuntimeError(
            "managed Java target contains unrecorded top-level state"
        )
    return {
        "format": "workbench-java-runtime-result-v2",
        "schema_version": 2,
        "outcome": "reused",
        "source": "managed",
        "receipt": receipt,
    }


def _reuse_managed_runtime(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    *,
    target_root: Path,
) -> dict[str, Any]:
    return _reuse_managed_runtime_v2(
        policy,
        host,
        target_root=target_root,
    )


def _file_uri_path(uri: str, label: str) -> Path:
    parsed = urlparse(uri)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.query
        or parsed.fragment
    ):
        raise JavaRuntimeError(f"{label} must be a local file URI")
    from urllib.request import url2pathname

    path_text = url2pathname(parsed.path)
    if (
        os.name == "nt"
        and len(path_text) >= 3
        and path_text[0] == "/"
        and path_text[2] == ":"
    ):
        path_text = path_text[1:]
    return Path(path_text)


def materialize_temurin_runtime(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    asset: Mapping[str, Any],
    *,
    state_root: Path | str,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Materialize one exact Temurin archive beneath Workbench state."""

    state = Path(state_root).expanduser().resolve()
    target_root = _target_root(state, policy, host)
    if target_root.exists() or target_root.is_symlink():
        return _reuse_managed_runtime(
            policy,
            host,
            target_root=target_root,
        )
    if asset.get("release_name") != policy.get("release_name"):
        raise JavaRuntimeError(
            "Temurin asset release differs from the selected policy"
        )
    plan_managed_java_execution(policy, host, state_root=state)
    url = _required_string(asset, "url", "Temurin asset")
    checksum = _required_string(asset, "sha256", "Temurin asset")
    package_name = _required_string(
        asset,
        "package_name",
        "Temurin asset",
    )
    size = asset.get("size")
    try:
        cache_path, artifact_outcome = fetch_verified_artifact(
            url=url,
            expected_sha256=checksum,
            expected_size=size,
            state_root=state,
            label="Temurin archive",
            timeout_seconds=timeout_seconds,
            user_agent="Workbench-Temurin-Provisioner/0.1",
        )
    except ArtifactStoreError as exc:
        raise JavaRuntimeError(str(exc)) from exc

    target_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{target_root.name}.java-",
        dir=target_root.parent,
    ))
    staging_execution: dict[str, Any] | None = None
    final_execution: dict[str, Any] | None = None
    published_identity: tuple[int, int] | None = None
    try:
        extracted = staging / "extracted"
        _extract_archive(cache_path, package_name, extracted)
        java_home, executable = _find_java_home(extracted, host)
        java_home_relative = java_home.relative_to(extracted)
        executable_relative = executable.relative_to(extracted)
        staging_execution = _plan_java_execution(
            host=host,
            target_root=staging,
            java_home_relative=java_home_relative,
            executable_relative=executable_relative,
        )
        final_execution = _plan_java_execution(
            host=host,
            target_root=target_root,
            java_home_relative=java_home_relative,
            executable_relative=executable_relative,
        )
        portability = _prepare_managed_java_portability(
            extracted_root=extracted,
            java_home=java_home,
            host=host,
            execution=final_execution,
        )
        tree = _tree_identity(extracted)
        raw_probe = probe_java(staging_execution["java"])
        mismatch = _probe_mismatch(raw_probe, policy, host)
        if mismatch is not None:
            raise JavaRuntimeError(
                f"provisioned Java violates its policy: {mismatch}"
            )
        probe = _normalize_managed_probe(
            raw_probe,
            host=host,
            java_home=java_home,
            extracted_root=extracted,
        )
        receipt = _receipt_v2(
            policy,
            host,
            asset,
            cache_path=cache_path,
            target_root=target_root,
            java_home_relative=java_home_relative,
            executable_relative=executable_relative,
            execution=final_execution,
            portability=portability,
            probe=probe,
            tree=tree,
        )
        receipt_path = staging / RECEIPT_PATH
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(
                receipt,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            staging.rename(target_root)
        except OSError as exc:
            raise JavaRuntimeError(
                "cannot publish the managed Java runtime atomically"
            ) from exc
        target_info = target_root.stat()
        published_identity = (target_info.st_dev, target_info.st_ino)
        final_home = target_root / "extracted" / java_home_relative
        final_java = target_root / "extracted" / executable_relative
        try:
            if (
                not os.path.samefile(final_execution["java_home"], final_home)
                or not os.path.samefile(final_execution["java"], final_java)
            ):
                raise JavaRuntimeError(
                    "published Java execution alias escapes Workbench custody"
                )
        except OSError as exc:
            raise JavaRuntimeError(
                "published Java execution alias cannot be resolved"
            ) from exc
        final_probe = _normalize_managed_probe(
            probe_java(final_execution["java"]),
            host=host,
            java_home=final_home,
            extracted_root=target_root / "extracted",
        )
        if final_probe != probe:
            raise JavaRuntimeError(
                "published Java probe differs from the staged runtime"
            )
    except BaseException:
        if published_identity is not None and target_root.is_dir():
            try:
                current = target_root.stat()
                if (current.st_dev, current.st_ino) == published_identity:
                    shutil.rmtree(target_root)
            except OSError:
                pass
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        "format": "workbench-java-runtime-result-v2",
        "schema_version": 2,
        "outcome": "provisioned",
        "source": "managed",
        "artifact_outcome": artifact_outcome,
        "receipt": receipt,
    }


def inspect_managed_java_runtime(
    policy: Mapping[str, Any],
    host: Mapping[str, str],
    *,
    state_root: Path | str,
) -> dict[str, Any] | None:
    """Verify an already-managed runtime without resolving or downloading assets.

    Setup uses this read-only boundary so a valid content-addressed JDK is
    represented as reuse in the reviewed plan instead of as a download.
    """

    state = Path(state_root).expanduser().resolve()
    target = _target_root(state, policy, host)
    if not target.exists() and not target.is_symlink():
        return None
    return _reuse_managed_runtime(
        policy,
        host,
        target_root=target,
    )


def ensure_java_runtime(
    suite_root: Path | str,
    *,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
    state_root: Path | str | None = None,
    candidates: Sequence[tuple[str, Path]] | None = None,
    timeout_seconds: float = 60.0,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
    resolved_bindings: ResolvedBindings | None = None,
) -> dict[str, Any]:
    """Discover or provision exact Java for one executable host.

    Explicit ``candidates`` take precedence. Otherwise the only external Java
    candidate comes from the manifest's ``java_candidate_home`` binding. The
    broad ambient ``JAVA_HOME``/``JDK_HOME``/``PATH`` discovery helper remains
    available as a low-level diagnostic API but is not used by this managed
    operation.
    """

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise JavaRuntimeError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise JavaRuntimeError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    selected_state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    policy = load_java_runtime_policy(
        suite,
        configuration=active_configuration,
    )
    selected_host = host_platform() if host is None else dict(host)
    required_host_fields = {
        "os",
        "architecture",
        "system",
        "machine",
    }
    if (
        set(selected_host) != required_host_fields
        or selected_host["os"] not in {"linux", "windows", "mac"}
        or selected_host["architecture"]
        not in {"x64", "aarch64", "ppc64le", "s390x", "riscv64"}
        or any(
            not isinstance(selected_host[field], str)
            or not selected_host[field]
            for field in required_host_fields
        )
    ):
        raise JavaRuntimeError("Java host identity is invalid")
    selected_candidates = candidates
    candidates_from_configuration = selected_candidates is None
    if selected_candidates is None:
        operation_bindings = resolved_bindings
        if operation_bindings is None:
            operation_bindings = active_configuration.resolve_bindings(
                environment,
                names=("java_candidate_home",),
            )
        try:
            active_configuration.require_binding_snapshot(
                operation_bindings,
                names=("java_candidate_home",),
            )
        except WorkbenchConfigurationError as exc:
            raise JavaRuntimeError(
                f"resolved bindings are invalid: {exc}"
            ) from exc
        try:
            java_home = operation_bindings.get("java_candidate_home")
            binding = operation_bindings.binding("java_candidate_home")
        except KeyError as exc:
            raise JavaRuntimeError(
                "resolved bindings omit java_candidate_home"
            ) from exc
        selected_candidates = (
            ()
            if java_home is None
            else ((
                (
                    f"config:{binding.environment_variable}"
                    if binding.environment_variable is not None
                    else "config:java_candidate_home"
                ),
                _java_executable(Path(java_home), selected_host),
            ),)
        )

    target = _target_root(selected_state, policy, selected_host)
    if (
        candidates_from_configuration
        and len(selected_candidates) == 1
        and (target.exists() or target.is_symlink())
    ):
        receipt_path = _current_managed_receipt_path(target)
        retained = _load_receipt(receipt_path)
        retained_target = retained.get("target")
        managed_java_uri = (
            retained_target.get("java_uri")
            if isinstance(retained_target, dict)
            else None
        )
        managed_java = (
            _file_uri_path(
                managed_java_uri,
                "managed Java executable",
            ).expanduser().absolute()
            if isinstance(managed_java_uri, str)
            else None
        )
        configured_java = selected_candidates[0][1].expanduser().absolute()
        if managed_java is not None and os.path.normcase(
            str(managed_java)
        ) == os.path.normcase(
            str(configured_java),
        ):
            return _reuse_managed_runtime(
                policy,
                selected_host,
                target_root=target,
            )

    discovery = discover_java_runtime(
        policy,
        selected_host,
        candidates=selected_candidates,
    )
    selected = discovery["selected"]
    if isinstance(selected, dict):
        return {
            "format": "workbench-java-runtime-result-v1",
            "schema_version": 1,
            "outcome": "discovered",
            "source": "external",
            "policy": policy,
            "host": selected_host,
            "runtime": selected,
            "discovery": discovery,
        }

    if target.exists() or target.is_symlink():
        return _reuse_managed_runtime(
            policy,
            selected_host,
            target_root=target,
        )

    asset = resolve_temurin_asset(
        policy,
        selected_host,
        timeout_seconds=timeout_seconds,
    )
    result = materialize_temurin_runtime(
        policy,
        selected_host,
        asset,
        state_root=selected_state,
        timeout_seconds=timeout_seconds,
    )
    result["discovery"] = discovery
    return result
