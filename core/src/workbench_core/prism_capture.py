"""Prism preparation wrapper: capture a sanitized handoff, never execute Java.

Prism resolves its components/assets/natives before invoking this wrapper. A
prepared handoff is not a game run or a check result. Input and errors must never
leak launcher account values into Workbench evidence.
"""

import os
import select
import sys
import time
from hashlib import sha256
from pathlib import Path

# The wrapper is also a direct-file process entry point, like process_gate.py.
# Installed environments use their installed API; contributor source uses only
# the adjacent native API/Core projects, never a guessed pack checkout.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "api/pyproject.toml").is_file() and (
        source_root / "core/pyproject.toml"
    ).is_file():
        sys.path.insert(0, str(source_root / "api/src"))

from workbench_api.prism import MAX_PROTOCOL_BYTES, parse_launch_script

from workbench_core.check_storage import ordinary, read_json, seal, write_json


def normalize_handoff(argv, raw, request):
    """Return only known launch fields, with account fields irreversibly removed."""
    policy = request["requirements"]
    fields = parse_launch_script(raw)
    if (
        fields["mainClass"] != policy["main_class"]
        or fields.get("launcherVersion") != policy["prism_version"]
        or fields.get("traits")
    ):
        raise ValueError("Prism handoff differs from the admitted platform")
    if (
        len(argv) < 5
        or argv[0] != request["java"]
        or argv[-1] != "org.prismlauncher.EntryPoint"
        or argv.count("-cp") != 1
    ):
        raise ValueError("Prism handoff has an unsupported Java command")
    if (
        any(
            not isinstance(arg, str)
            or len(arg.encode()) > 65536
            or any(ord(c) < 32 for c in arg)
            for arg in argv
        )
        or sum(len(x.encode()) for x in argv) > 1024 * 1024
    ):
        raise ValueError("Prism handoff argument array exceeds its bound")
    cp_index = argv.index("-cp")
    if cp_index != len(argv) - 3:
        raise ValueError("Prism handoff has an unsupported classpath position")
    jvm = argv[1:cp_index]
    native = [
        x.removeprefix("-Djava.library.path=")
        for x in jvm
        if x.startswith("-Djava.library.path=")
    ]
    expected_native = Path(request["instance"]) / "natives"
    if native != [str(expected_native)]:
        raise ValueError("Prism native directory differs from the prepared instance")
    expected_jvm = [
        *policy["jvm_arguments"],
        "-Xms512m",
        "-Xmx4096m",
        "-Djava.library.path=" + str(expected_native),
    ]
    if jvm != expected_jvm:
        raise ValueError("Prism handoff has unapproved JVM arguments")
    classpath = argv[cp_index + 1].split(":")
    if not classpath or len(classpath) > 512 or len(set(classpath)) != len(classpath):
        raise ValueError("Prism handoff classpath is empty, duplicate or over-bound")
    libraries = Path(request["prism_data"]) / "libraries"
    for index, value in enumerate(classpath):
        path = ordinary(Path(value))
        if index == 0:
            if (
                path.name != "NewLaunch.jar"
                or sha256(path.read_bytes()).hexdigest()
                != policy["prism_entrypoint_sha256"]
            ):
                raise ValueError("Prism handoff lacks its standard entrypoint artifact")
        elif not path.is_relative_to(libraries):
            raise ValueError("Prism handoff library escapes the prepared cache")
    parameters = fields.get("param", [])
    if len(parameters) % 2 or len(parameters) > 128:
        raise ValueError("Prism handoff has unsupported game arguments")
    permitted = {
        "--username": None,
        "--uuid": None,
        "--accessToken": None,
        "--userType": None,
        "--userProperties": None,
        "--version": policy["minecraft_version"],
        "--gameDir": str(Path(request["instance"]) / ".minecraft"),
        "--assetsDir": str(Path(request["prism_data"]) / "assets"),
        "--assetIndex": policy["asset_index"],
        "--versionType": "release",
    }
    sanitized, seen, tweakers = [], set(), []
    replacements = {
        "--username": "Workbench",
        "--uuid": "00000000000000000000000000000000",
        "--accessToken": "0",
        "--userType": "legacy",
        "--userProperties": "{}",
    }
    for key, value in zip(parameters[::2], parameters[1::2]):
        if key == "--tweakClass":
            tweakers.append(value)
        elif (
            key not in permitted
            or key in seen
            or permitted[key] is not None
            and value != permitted[key]
        ):
            raise ValueError("Prism handoff has unapproved game parameters")
        seen.add(key)
        sanitized.extend([key, replacements.get(key, value)])
    if (
        tweakers != policy["tweakers"]
        or not {
            "--username",
            "--uuid",
            "--accessToken",
            "--userType",
            "--version",
            "--gameDir",
            "--assetsDir",
            "--assetIndex",
        }
        <= seen
    ):
        raise ValueError("Prism handoff lacks required platform parameters")
    return {
        "format": "workbench-prism-handoff-v1",
        "request_id": request["id"],
        "java": request["java"],
        "jvm_arguments": policy["jvm_arguments"] + ["-Xms512m", "-Xmx4096m"],
        "classpath": classpath,
        "natives": str(expected_native),
        "assets": str(Path(request["prism_data"]) / "assets"),
        "protocol": {
            "mainClass": policy["main_class"],
            "launcher": "standard",
            "launcherBrand": "PrismLauncher",
            "launcherVersion": policy["prism_version"],
            "instanceName": "Workbench saved check",
            "windowTitle": "Workbench saved check",
            "windowParams": "854x480",
            "param": sanitized,
        },
        "game_executed": False,
        "credentials_retained": False,
    }


def capture(request_path, argv):
    request = read_json(request_path)
    if (
        seal("prism-capture-request", {k: v for k, v in request.items() if k != "id"})
        != request
    ):
        raise ValueError("Prism capture request changed")
    if Path.cwd() != Path(request["instance"]) / ".minecraft":
        raise ValueError("Prism capture has another working directory")
    deadline = time.monotonic() + 30
    raw = b""
    while time.monotonic() < deadline:
        if (request_path.parent / "cancel.json").exists():
            raise ValueError("Prism capture cancelled")
        if not select.select([0], [], [], 0.25)[0]:
            continue
        chunk = os.read(0, 8192)
        if not chunk:
            raise ValueError("Prism capture ended before its launch marker")
        raw += chunk
        if len(raw) > MAX_PROTOCOL_BYTES:
            raise ValueError("Prism capture exceeds its bound")
        if b"\nlaunch\n" in raw:
            handoff = normalize_handoff(argv, raw, request)
            write_json(
                request_path.parent / "handoff.json", seal("prism-handoff", handoff)
            )
            return
    raise ValueError("Prism capture timed out")


if __name__ == "__main__":
    try:
        capture(Path(sys.argv[1]), sys.argv[2:])
    except Exception:  # noqa: BLE001 - account-bearing input must never enter a traceback
        print(
            "Workbench rejected the Prism preparation handoff; no game was executed.",
            file=sys.stderr,
        )
        raise SystemExit(1)
