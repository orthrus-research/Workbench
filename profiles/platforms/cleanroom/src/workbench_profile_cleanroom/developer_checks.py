"""Native client-image admission for the selected Cleanroom platform."""

from hashlib import sha256
import json
from pathlib import Path
import re

import yaml
from . import profile
from .environment import requirements as environment_requirements
from workbench_api.prism import parse_launch_script

PROFILE_API_VERSION = 1


def validate_check_attachment(specification, executable):
    """Admit a separate source-built observer, never arbitrary image JVM flags."""
    if (set(specification) != {"sources", "main_class", "configuration", "policy", "bootstrap_classes"}
            or specification["policy"].get("mechanism") != "java-instrumentation-classfile-25"
            or specification["policy"].get("retransform") is not False
            or specification["policy"].get("external_dependencies") != []
            or specification["policy"].get("platform") != "cleanroom-0.6.12-alpha"):
        raise ValueError("unsupported Cleanroom check attachment policy")
    current = provenance(executable)
    if current["version"] != "0.6.12-alpha" or not current["java"]["JAVA_RUNTIME_VERSION"].startswith("25."):
        raise ValueError("check observer requires the admitted Cleanroom/JDK 25 toolchain")


def provenance(executable):
    policy = yaml.safe_load(profile().resource("profile").read_bytes())
    release = Path(executable).parent.parent / "release"
    metadata = dict(re.findall(r'^([A-Z_]+)="(.*)"$', release.read_text(), re.M))
    return {
        "id": "cleanroom", "version": policy["cleanroom_version"],
        "minecraft": policy["minecraft_version"],
        "java": {key: metadata.get(key) for key in ("JAVA_RUNTIME_VERSION", "IMPLEMENTOR", "OS_ARCH")},
        "toolchain_lock_sha256": sha256(profile().resource("runtime-toolchain").read_bytes()).hexdigest(),
    }


def validate_client_image(runtime, executable, arguments):
    selected = profile()
    policy = yaml.safe_load(selected.resource("profile").read_bytes())
    toolchain = json.loads(selected.resource("runtime-toolchain").read_bytes())
    main_class = toolchain["native_service_expectations"]["launch_main_class"]
    if not isinstance(arguments, list) or not all(
        isinstance(arg, str) for arg in arguments
    ):
        raise ValueError("client arguments must be a JSON string array")
    game_arguments = arguments
    if arguments and arguments[-1] == "org.prismlauncher.EntryPoint":
        script_path = runtime / "workbench-launch.txt"
        if script_path.is_symlink() or not script_path.is_file():
            raise ValueError("Prism image requires its bounded prepared launch input")
        with script_path.open("rb") as stream:
            script = parse_launch_script(stream.read(128 * 1024 + 1))
        if (
            script["mainClass"] != main_class
            or "userName" in script
            or "sessionId" in script
        ):
            raise ValueError(
                "Prism image differs from its platform or retains account fields"
            )
        game_arguments = script.get("param", [])
        keys = game_arguments[::2]
        permitted = {
            "--username",
            "--uuid",
            "--accessToken",
            "--userType",
            "--userProperties",
            "--version",
            "--gameDir",
            "--assetsDir",
            "--assetIndex",
            "--versionType",
            "--tweakClass",
        }
        if (
            len(game_arguments) % 2
            or any(key not in permitted for key in keys)
            or any(keys.count(key) != 1 for key in set(keys) - {"--tweakClass"})
        ):
            raise ValueError(
                "Prism check image has ambiguous or unsupported game parameters"
            )
        parameters = dict(zip(game_arguments[::2], game_arguments[1::2]))
        if (
            len(game_arguments) % 2
            or parameters.get("--username") != "Workbench"
            or parameters.get("--uuid") != "00000000000000000000000000000000"
            or parameters.get("--userType") != "legacy"
            or parameters.get("--accessToken") != "0"
        ):
            raise ValueError(
                "Prism check images require fixed offline account placeholders"
            )
        preparation = environment_requirements()
        if (
            parameters.get("--version") != preparation["minecraft_version"]
            or parameters.get("--assetIndex") != preparation["asset_index"]
            or parameters.get("--assetsDir") != "assets"
            or parameters.get("--userProperties", "{}") != "{}"
            or script.get("launcherVersion") != preparation["prism_version"]
            or script.get("traits")
            or [
                value
                for key, value in zip(keys, game_arguments[1::2])
                if key == "--tweakClass"
            ]
            != preparation["tweakers"]
        ):
            raise ValueError(
                "Prism check image differs from the packaged launch policy"
            )
        expected_jvm = preparation["jvm_arguments"] + [
            "-Xms512m",
            "-Xmx4096m",
            "-Djava.library.path=natives",
            "-cp",
        ]
        if arguments[:-2] != expected_jvm:
            raise ValueError("Prism check image has unapproved JVM arguments")
        classpath = arguments[-2].split(":")
        if not classpath or len(classpath) != len(set(classpath)):
            raise ValueError("Prism image requires an exact unique classpath")
        if classpath[0] != "libraries/workbench-prism/NewLaunch.jar" or any(not value.startswith("libraries/") for value in classpath):
            raise ValueError("Prism image must preserve its Maven library layout")
        entrypoint = runtime / classpath[0]
        if (
            entrypoint.name != "NewLaunch.jar"
            or entrypoint.is_symlink()
            or sha256(entrypoint.read_bytes()).hexdigest()
            != preparation["prism_entrypoint_sha256"]
        ):
            raise ValueError("Prism entrypoint differs from the selected launcher lock")
    elif arguments.count(main_class) != 1:
        raise ValueError(f"select the current Cleanroom entry point: {main_class}")
    if Path(executable).name != "java":
        raise ValueError(
            f"select native Java and the current Cleanroom entry point: {main_class}"
        )
    if game_arguments.count("--gameDir") != 1 or game_arguments[
        game_arguments.index("--gameDir") + 1 : game_arguments.index("--gameDir") + 2
    ] != ["."]:
        raise ValueError("client image must explicitly launch with --gameDir .")
    if any(
        arg.startswith(("/", "@"))
        or "file:/" in arg
        or re.search(r"(?:^|[:=])[/~]|(?:^|[:=/])\.\.(?:/|$)", arg)
        for arg in [*arguments, *game_arguments]
    ):
        raise ValueError("client arguments must use image-relative resources")
    if (
        game_arguments.count("--accessToken") > 1
        or "--accessToken" in game_arguments
        and game_arguments[
            game_arguments.index("--accessToken") + 1 : game_arguments.index(
                "--accessToken"
            )
            + 2
        ]
        != ["0"]
    ):
        raise ValueError(
            "check images use offline token 0; do not retain account credentials"
        )
    if any(arg.startswith(("--accessToken=", "--gameDir=")) for arg in game_arguments):
        raise ValueError(
            "use one explicit --gameDir . and optional --accessToken 0 pair"
        )
    java_home = Path(executable).parent.parent
    release = java_home / "release"
    if not release.is_file() or release.is_symlink():
        raise ValueError(
            "native client checks require a complete JDK with release metadata"
        )
    metadata = dict(re.findall(r'^([A-Z_]+)="(.*)"$', release.read_text(), re.M))
    expected = policy["java"]["runtime_provision"]["release_name"].removeprefix("jdk-")
    if (
        metadata.get("JAVA_RUNTIME_VERSION", "").removesuffix("-LTS") != expected
        or metadata.get("IMPLEMENTOR")
        != policy["java"]["runtime_provision"]["java_vendor"]
    ):
        raise ValueError(
            "native Java installation differs from the selected platform toolchain"
        )
    for artifact in toolchain["artifacts"]:
        matches = list(runtime.rglob(artifact["filename"]))
        if len(matches) != 1 or matches[0].is_symlink() or not matches[0].is_file():
            raise ValueError(
                f"client image lacks one exact platform artifact: {artifact['filename']}"
            )
        if (
            matches[0].stat().st_size != artifact["size"]
            or sha256(matches[0].read_bytes()).hexdigest() != artifact["sha256"]
        ):
            raise ValueError(
                f"platform artifact differs from its native lock: {artifact['filename']}"
            )
