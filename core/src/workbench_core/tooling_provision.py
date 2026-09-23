"""Core-owned, hash-locked Prism and Packwiz provisioning.

Packwiz builds from the source and vendored dependencies shipped with Core.
Prism and the Go compiler are acquired from exact official release assets or
from caller-supplied copies of those same bytes. No URL is user-configurable.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
from threading import Event
from typing import Any, Mapping, Sequence
from uuid import uuid4

from workbench_api.state_paths import default_runtime_state_root
from workbench_api.processes import ProcessError

from . import check_storage as storage
from . import tool_process
from .artifact_store import ArtifactStoreError, fetch_verified_artifact, sha256_file
from .runtime_java import _extract_tar, _extract_zip, _tree_identity


FORMAT = "workbench-managed-developer-tools-v1"
SOURCE_COMMIT = "ef87d964f8cbd52b3b13ea42453ef322290e2b9e"
SOURCE_TREE = "771b4ce3087a41905db1fdb61c5ce88dc27dc97f"
SOURCE_BUNDLE = "packwiz-source-v1.bundle"
SOURCE_SHA256 = "2966661cc11e041ab1916ee834d59763640c4fb75e709b4f86ebf0f6f4e55375"
SOURCE_SIZE = 2551418
SOURCE_PARTS = (
    (SOURCE_BUNDLE + ".part1.b64", "d1f2a466f9a81a4f8b9520eb8e01087829d1d835bcfcb85921ae34b13e0202c6", 1700949),
    (SOURCE_BUNDLE + ".part2.b64", "1627f0040c03d19201750d092d9619d012f05cf6b18012053be59ff273a9a157", 1700949),
)
GO_VERSION = "go1.25.14"
PRISM_VERSION = "11.1.0"

ASSETS: dict[str, dict[str, dict[str, Any]]] = {
    "linux-x64": {
        "prism": {
            "filename": "PrismLauncher-Linux-Qt6-Portable-11.1.0.tar.gz",
            "url": "https://github.com/PrismLauncher/PrismLauncher/releases/download/11.1.0/PrismLauncher-Linux-Qt6-Portable-11.1.0.tar.gz",
            "sha256": "8218067587a9bb355e9ed8817091ef9bf44bcf27c2cdec59a7525621f61a3392",
            "size": 63950612,
            "executable": "PrismLauncher",
            "archive": "tar.gz",
        },
        "go": {
            "filename": "go1.25.14.linux-amd64.tar.gz",
            "url": "https://go.dev/dl/go1.25.14.linux-amd64.tar.gz",
            "sha256": "a21ae5633a269bcd7e90cf767e48225633795e99d831742cbf3397064fee7712",
            "size": 59909419,
            "executable": "go/bin/go",
            "archive": "tar.gz",
        },
        "packwiz": {
            "executable": "packwiz",
            "sha256": "c2682b78127a4cc3f19b537b7de2787ccfcf58199300899a6a35d8f2603a1b82",
            "size": 12374164,
        },
    },
    "windows-x64": {
        "prism": {
            "filename": "PrismLauncher-Windows-MinGW-w64-Portable-11.1.0.zip",
            "url": "https://github.com/PrismLauncher/PrismLauncher/releases/download/11.1.0/PrismLauncher-Windows-MinGW-w64-Portable-11.1.0.zip",
            "sha256": "2bf5e879ea1c3f6a1aaaa43539667ce296308abf3e6a984d5cc4c48bfe3c431c",
            "size": 43926838,
            "executable": "prismlauncher.exe",
            "archive": "zip",
        },
        "go": {
            "filename": "go1.25.14.windows-amd64.zip",
            "url": "https://go.dev/dl/go1.25.14.windows-amd64.zip",
            "sha256": "119044a92b3987c341cd6aebb256676dd4780d292f7b4e72a3e9976677841697",
            "size": 67591780,
            "executable": "go/bin/go.exe",
            "archive": "zip",
        },
        "packwiz": {
            "executable": "packwiz.exe",
            "sha256": "20b355c06c164f2d2d52c014c802aeca162bc046652c32db4c0af759e596976e",
            "size": 12853760,
        },
    },
}


class ToolingProvisionError(ValueError):
    """The selected tool, archive, build, or retained bytes are invalid."""


def host_key(*, system: str | None = None, machine: str | None = None) -> str:
    observed_system = (system or platform.system()).casefold()
    observed_machine = (machine or platform.machine()).casefold()
    if observed_system == "linux" and observed_machine in {"x86_64", "amd64"}:
        return "linux-x64"
    if observed_system == "windows" and observed_machine in {"amd64", "x86_64"}:
        return "windows-x64"
    raise ToolingProvisionError(
        "managed Prism and Packwiz currently support Linux x64 and Windows x64"
    )


def _managed_root(state_root: Path | str) -> Path:
    return Path(state_root).expanduser().absolute() / ".workbench" / "managed-tools"


def _receipt_path(state_root: Path | str, key: str, name: str) -> Path:
    return _managed_root(state_root) / key / name / "receipt.json"


def _bundle(destination: Path) -> Path:
    """Reassemble the exact archive from bounded, public-text package resources."""

    try:
        with destination.open("xb") as target:
            for name, digest, size in SOURCE_PARTS:
                candidate = Path(str(files("workbench_core").joinpath("data", name)))
                if not candidate.is_file() or candidate.is_symlink():
                    raise ToolingProvisionError("packaged Packwiz source part is missing")
                encoded = candidate.read_bytes()
                if (sha256(encoded).hexdigest(), len(encoded)) != (digest, size):
                    raise ToolingProvisionError("packaged Packwiz source part changed")
                try:
                    target.write(base64.b64decode(encoded.removesuffix(b"\n"), validate=True))
                except binascii.Error as exc:
                    raise ToolingProvisionError("packaged Packwiz source part is invalid") from exc
        if sha256_file(destination) != (SOURCE_SHA256, SOURCE_SIZE):
            raise ToolingProvisionError("packaged Packwiz source bundle changed")
        return destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _read_receipt(path: Path) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ToolingProvisionError("managed tool receipt is not an ordinary file")
    value = storage.read_json(path, byte_limit=4 * 1024 * 1024)
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ToolingProvisionError("managed tool receipt has an unknown format")
    return value


def inspect_tools(state_root: Path | str, *, key: str | None = None) -> dict[str, Any]:
    """Check retained bytes without downloading, executing or changing state."""

    selected = host_key() if key is None else key
    if selected not in ASSETS:
        raise ToolingProvisionError("unsupported managed tool host")
    policy = ASSETS[selected]
    rows: dict[str, dict[str, Any]] = {}
    for name in ("prism", "packwiz"):
        destination = _receipt_path(state_root, selected, name).parent
        try:
            receipt = _read_receipt(destination / "receipt.json")
            if receipt is None:
                if destination.exists() or destination.is_symlink():
                    raise ToolingProvisionError("managed tool has no receipt")
                rows[name] = {"state": "missing", "executable": None}
                continue
            expected = policy[name]
            if receipt.get("host") != selected or receipt.get("tool") != name:
                raise ToolingProvisionError(f"managed {name} receipt identity changed")
            if receipt.get("policy_sha256") != _policy_digest(expected):
                raise ToolingProvisionError(f"managed {name} policy changed")
            executable = destination / "content" / expected["executable"]
            if not executable.is_file() or executable.is_symlink():
                raise ToolingProvisionError(f"managed {name} executable is missing")
            if name == "packwiz":
                if (
                    receipt.get("source_sha256") != SOURCE_SHA256
                    or receipt.get("source_commit") != SOURCE_COMMIT
                    or receipt.get("source_tree") != SOURCE_TREE
                    or receipt.get("go_version") != GO_VERSION
                ):
                    raise ToolingProvisionError("managed Packwiz source policy changed")
                if sha256_file(executable) != (expected["sha256"], expected["size"]):
                    raise ToolingProvisionError("managed Packwiz bytes changed")
            else:
                observed = _tree_identity(destination / "content")
                if observed != receipt.get("tree"):
                    raise ToolingProvisionError("managed Prism tree changed")
            rows[name] = {"state": "ready", "executable": str(executable)}
        except (OSError, ValueError) as exc:
            rows[name] = {"state": "invalid", "executable": None, "detail": str(exc)}
    return {
        "format": FORMAT,
        "host": selected,
        "state": "initialized" if all(row["state"] == "ready" for row in rows.values()) else "attention",
        "tools": rows,
    }


def _asset_path(
    asset: Mapping[str, Any], *, seed_dir: Path | None, state_root: Path, label: str
) -> Path:
    url = asset["url"]
    if seed_dir is not None:
        candidate = seed_dir / asset["filename"]
        if candidate.is_symlink() or not candidate.is_file():
            raise ToolingProvisionError(
                f"{label} seed is missing; select the exact {asset['filename']}"
            )
        url = candidate.resolve().as_uri()
    cache_state = _managed_root(state_root)
    def fetch() -> Path:
        return fetch_verified_artifact(
            url=url, expected_sha256=asset["sha256"],
            expected_size=asset["size"], state_root=cache_state,
            label=label, timeout_seconds=120,
        )[0]
    try:
        with storage.execution_lock(cache_state):
            try:
                return fetch()
            except ArtifactStoreError as exc:
                if not str(exc).startswith("cached "):
                    raise
                cached = cache_state / "artifacts/sha256" / asset["sha256"]
                if not cached.exists() and not cached.is_symlink():
                    raise
                quarantine = cache_state / "artifacts/quarantine"
                if quarantine.is_symlink() or (quarantine.exists() and not quarantine.is_dir()):
                    raise ToolingProvisionError("tool archive quarantine is not an ordinary directory")
                quarantine.mkdir(mode=0o700, exist_ok=True)
                storage.ordinary(quarantine, directory=True)
                cached.rename(quarantine / f"{asset['sha256']}-{uuid4().hex}")
                return fetch()
    except ValueError as exc:
        raise ToolingProvisionError(
            f"{label} is unavailable or mismatched; place the exact official "
            f"{asset['filename']} in the seed directory and retry: {exc}"
        ) from exc


def _extract(archive: Path, destination: Path, kind: str) -> None:
    if kind == "tar.gz":
        _extract_tar(archive, destination)
    else:
        _extract_zip(archive, destination, label="developer tool")


def _policy_digest(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quarantine(destination: Path) -> None:
    if not destination.exists() and not destination.is_symlink():
        return
    quarantine = destination.parent / "quarantine"
    if quarantine.is_symlink() or (quarantine.exists() and not quarantine.is_dir()):
        raise ToolingProvisionError("managed tool quarantine is not an ordinary directory")
    quarantine.mkdir(mode=0o700, exist_ok=True)
    storage.ordinary(quarantine, directory=True)
    destination.rename(quarantine / f"{destination.name}-{uuid4().hex}")


def _prepare_prism(state_root: Path, key: str, seed_dir: Path | None) -> None:
    policy = ASSETS[key]["prism"]
    archive = _asset_path(policy, seed_dir=seed_dir, state_root=state_root, label="Prism Launcher")
    parent = _managed_root(state_root) / key
    destination = parent / "prism"
    with storage.execution_lock(parent):
        if inspect_tools(state_root, key=key)["tools"]["prism"]["state"] == "ready":
            return
        _quarantine(destination)
        staging = parent / (".prism-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        try:
            _extract(archive, staging / "content", policy["archive"])
            executable = staging / "content" / policy["executable"]
            if not executable.is_file() or executable.is_symlink():
                raise ToolingProvisionError("official Prism archive lacks its executable")
            manifest = _tree_identity(staging / "content")
            storage.write_json(staging / "receipt.json", {
                "format": FORMAT, "tool": "prism", "host": key,
                "version": PRISM_VERSION, "policy_sha256": _policy_digest(policy),
                "archive_sha256": policy["sha256"], "tree": manifest,
            }, byte_limit=4 * 1024 * 1024)
            staging.rename(destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def _run(command: list[str], *, cwd: Path | None = None,
         environment: Mapping[str, str] | None = None, timeout: int = 300) -> str:
    try:
        result = tool_process.execute(
            command, cwd=Path.cwd() if cwd is None else cwd, stdin=b"",
            environment=os.environ if environment is None else environment,
            cancelled=Event(), timeout_seconds=timeout,
            output_limit=4 * 1024 * 1024,
        )
    except (OSError, ProcessError) as exc:
        raise ToolingProvisionError(f"developer tool process failed: {command[0]}") from exc
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if result.exit_code != 0:
        raise ToolingProvisionError(
            f"developer tool process exited {result.exit_code}: {output[-4096:]}"
        )
    return output.strip()


def _go_executable(
    state_root: Path, key: str, seed_dir: Path | None,
    override: Path | None, staging: Path,
) -> Path:
    if override is not None:
        candidate = override.expanduser().absolute()
        if candidate.is_symlink() or not candidate.is_file():
            raise ToolingProvisionError("selected Go executable must be an ordinary file")
    else:
        asset = ASSETS[key]["go"]
        archive = _asset_path(asset, seed_dir=seed_dir, state_root=state_root, label="Go compiler")
        _extract(archive, staging / "go-toolchain", asset["archive"])
        candidate = staging / "go-toolchain" / asset["executable"]
    if not candidate.is_file() or candidate.is_symlink():
        raise ToolingProvisionError("selected Go compiler is missing")
    observed = _run([str(candidate), "version"], timeout=15)
    if not observed.startswith(f"go version {GO_VERSION} "):
        raise ToolingProvisionError(f"Packwiz requires exact {GO_VERSION}; found {observed}")
    return candidate


def _prepare_packwiz(
    state_root: Path, key: str, seed_dir: Path | None, go_override: Path | None
) -> None:
    parent = _managed_root(state_root) / key
    destination = parent / "packwiz"
    policy = ASSETS[key]["packwiz"]
    with storage.execution_lock(parent):
        if inspect_tools(state_root, key=key)["tools"]["packwiz"]["state"] == "ready":
            return
        _quarantine(destination)
        staging = parent / (".packwiz-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        try:
            source_bundle = _bundle(staging / SOURCE_BUNDLE)
            compiler = _go_executable(state_root, key, seed_dir, go_override, staging)
            source = staging / "source"
            _extract_tar(source_bundle, source)
            source_bundle.unlink()
            if sha256_file(source / "go.mod")[0] != "594d699ba3863ed70b1339bdb535afaf63eee0066ae3f59ea89e56543c79263d":
                raise ToolingProvisionError("Packwiz source manifest changed")
            if sha256_file(source / "go.sum")[0] != "dbac35acf64a4258999359d40d57a95ad46f76a8dec25ef104bd125040bce52e":
                raise ToolingProvisionError("Packwiz dependency lock changed")
            content = staging / "content"
            content.mkdir()
            executable = content / policy["executable"]
            environment = {name: value for name, value in os.environ.items()
                           if not name.startswith("GO") and name not in {"GOPATH", "GOCACHE", "CGO_ENABLED"}}
            environment.update({
                "GOTOOLCHAIN": "local", "GOWORK": "off", "GOENV": "off",
                "GOPROXY": "off", "GOSUMDB": "off", "GOVCS": "off",
                "GOFLAGS": "-mod=vendor", "CGO_ENABLED": "0",
                "GOOS": "windows" if key == "windows-x64" else "linux",
                "GOARCH": "amd64", "GOAMD64": "v1",
                "GOCACHE": str(parent / "go-cache"),
                "GOPATH": str(parent / "go-path"),
            })
            _run([str(compiler), "build", "-trimpath", "-buildvcs=false",
                  "-ldflags=-s -w -buildid=", "-o", str(executable), "."],
                 cwd=source, environment=environment)
            if sha256_file(executable) != (policy["sha256"], policy["size"]):
                raise ToolingProvisionError("Packwiz build does not match the locked output")
            if key == "linux-x64":
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            storage.write_json(staging / "receipt.json", {
                "format": FORMAT, "tool": "packwiz", "host": key,
                "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
                "source_sha256": SOURCE_SHA256, "go_version": GO_VERSION,
                "policy_sha256": _policy_digest(policy),
                "executable_sha256": policy["sha256"],
            })
            shutil.rmtree(staging / "source")
            shutil.rmtree(staging / "go-toolchain", ignore_errors=True)
            staging.rename(destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def prepare_tools(
    state_root: Path | str, *, key: str | None = None,
    seed_dir: Path | str | None = None, go_executable: Path | str | None = None,
) -> dict[str, Any]:
    """Install or reuse both tools in Core state; return verified paths."""

    selected = host_key() if key is None else key
    if selected not in ASSETS:
        raise ToolingProvisionError("unsupported managed tool host")
    root = storage.initialize(Path(state_root).expanduser().absolute())
    parent = _managed_root(root) / selected
    parent.mkdir(parents=True, exist_ok=True)
    seed = None if seed_dir is None else Path(seed_dir).expanduser().absolute()
    if seed is not None and (seed.is_symlink() or not seed.is_dir()):
        raise ToolingProvisionError("seed directory must be an ordinary directory")
    before = inspect_tools(root, key=selected)
    if before["tools"]["prism"]["state"] != "ready":
        _prepare_prism(root, selected, seed)
    if before["tools"]["packwiz"]["state"] != "ready":
        _prepare_packwiz(
            root, selected, seed,
            None if go_executable is None else Path(go_executable),
        )
    return inspect_tools(root, key=selected)


def _plan(state_root: Path, key: str, seed_dir: Path | None,
          go_executable: Path | None) -> dict[str, Any]:
    check = inspect_tools(state_root, key=key)
    needed = [name for name in ("prism", "packwiz")
              if check["tools"][name]["state"] != "ready"]
    seed_hashes: dict[str, str] = {}
    if seed_dir is not None:
        if seed_dir.is_symlink() or not seed_dir.is_dir():
            raise ToolingProvisionError("seed directory must be an ordinary directory")
        for name in ("prism", "go"):
            if name == "prism" and "prism" not in needed:
                continue
            if name == "go" and ("packwiz" not in needed or go_executable is not None):
                continue
            asset = ASSETS[key][name]
            path = seed_dir / asset["filename"]
            if path.is_symlink() or not path.is_file():
                raise ToolingProvisionError(f"exact {asset['filename']} seed is missing")
            digest, size = sha256_file(path)
            if (digest, size) != (asset["sha256"], asset["size"]):
                raise ToolingProvisionError(f"{name} seed does not match the pinned bytes")
            seed_hashes[name] = digest
    go_identity = None
    if go_executable is not None and "packwiz" in needed:
        selected_go = go_executable.expanduser().absolute()
        if selected_go.is_symlink() or not selected_go.is_file():
            raise ToolingProvisionError("selected Go executable must be an ordinary file")
        version = _run([str(selected_go), "version"], timeout=15)
        if not version.startswith(f"go version {GO_VERSION} "):
            raise ToolingProvisionError(f"Packwiz requires exact {GO_VERSION}; found {version}")
        digest, size = sha256_file(selected_go)
        go_identity = {"sha256": digest, "size": size, "version": version}
    selection = {
        "state_root": str(state_root), "host": key,
        "seed_dir": None if seed_dir is None else str(seed_dir),
        "seed_hashes": seed_hashes,
        "go_executable": None if go_executable is None else str(go_executable),
        "go_identity": go_identity,
        "policies": {name: _policy_digest(ASSETS[key][name]) for name in ASSETS[key]},
        "source_sha256": SOURCE_SHA256,
    }
    identity = {"selection": selection, "current_tools": check["tools"]}
    return {
        "format": FORMAT + "-plan", "state": check["state"],
        "plan_id": "workbench-tooling-plan:sha256:" + sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "selection": selection,
        "actions": needed,
        "check": check,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workbench tooling")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--check", action="store_true")
    operation.add_argument("--plan", action="store_true")
    operation.add_argument("--apply", metavar="PLAN_ID")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--seed-dir", type=Path,
                        help="directory containing exact official Prism/Go archives")
    parser.add_argument("--go-executable", type=Path,
                        help=f"existing exact {GO_VERSION} compiler")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        root = (args.state_root or default_runtime_state_root()).expanduser().absolute()
        key = host_key()
        if args.check:
            result = inspect_tools(root, key=key)
        else:
            plan = _plan(root, key, args.seed_dir, args.go_executable)
            if args.plan:
                result = plan
            else:
                if args.apply != plan["plan_id"]:
                    raise ToolingProvisionError("tooling plan changed; rerun --plan")
                result = prepare_tools(root, key=key, seed_dir=args.seed_dir,
                                       go_executable=args.go_executable)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.plan:
            print(f"Tooling plan: {result['plan_id']}")
            print("Install: " + (", ".join(result["actions"]) or "nothing"))
        else:
            print(f"Prism and Packwiz tooling: {result['state']}")
            for name, row in result["tools"].items():
                print(f"- {name}: {row['state']}" +
                      (f" ({row['executable']})" if row["executable"] else ""))
        return 0 if args.plan or result.get("state") == "initialized" else 1
    except (OSError, ValueError) as exc:
        print(f"Workbench tooling failed: {exc}", file=sys.stderr)
        return 2


__all__ = ["ASSETS", "ToolingProvisionError", "host_key", "inspect_tools", "prepare_tools", "main"]
