"""Declarative native preparation policy for the packaged Cleanroom candidate."""

import json
import yaml
from . import profile


def requirements():
    selected = profile()
    policy = yaml.safe_load(selected.resource("profile").read_bytes())
    toolchain = json.loads(selected.resource("runtime-toolchain").read_bytes())
    return {
        "format": "workbench-native-client-requirements-v1",
        "platform": "cleanroom",
        "side": "client",
        "prism_version": "11.1.0",
        "prism_entrypoint_sha256": "116258fc5e26fb4144a67a8feec8f1110e754ce681b9d20e6b74ce68ecd58b36",
        "bootstrap": policy["runtime_artifacts"]["cleanroom_client"],
        "installer": policy["runtime_artifacts"]["packwiz_installer"],
        "java": {
            "runtime_version": policy["java"]["runtime_provision"][
                "release_name"
            ].removeprefix("jdk-"),
            "vendor": policy["java"]["runtime_provision"]["java_vendor"],
        },
        "main_class": toolchain["native_service_expectations"]["launch_main_class"],
        "minecraft_version": policy["minecraft_version"],
        "asset_index": "1.12",
        "tweakers": ["net.minecraftforge.fml.common.launcher.FMLTweaker"],
        "jvm_arguments": ["-Duser.language=en", "-Dfile.encoding=UTF-8"],
        "artifacts": toolchain["artifacts"],
    }
