#!/usr/bin/env python3
"""Build Axiom's independently versioned JVM artifact; never publish it."""

import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "api/src", ROOT / "modules/axiom/src"):
    sys.path.insert(0, str(source))


def verify_archive(path):
    """The distributable folder must retain its own third-party notices."""
    prefix = path.stem + "/"
    from workbench_axiom.cli import ENGINE_NOTICES
    required = {prefix + name for name in ENGINE_NOTICES}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not required.issubset(names):
            raise ValueError("Engine archive is missing internal license/notice files")
        if len(names) != len(set(names)) or any(not name.startswith(prefix) or ".." in Path(name).parts for name in names):
            raise ValueError("Engine archive has members outside its distributable folder")
        # GPL addon implementation is independently supplied, never an engine
        # resource/source or bundled class. Check nested JARs as well as paths.
        from axiom_material_ore_sources import EXTERNAL_NAMES
        def addon_member(name):
            return Path(name).stem.split("$")[0] in EXTERNAL_NAMES
        for name in names:
            if addon_member(name): raise ValueError("Engine archive bundles separately supplied addon code")
            if name.endswith(".jar"):
                with zipfile.ZipFile(io.BytesIO(archive.read(name))) as jar:
                    if any(addon_member(member) for member in jar.namelist()):
                        raise ValueError("Engine archive bundles separately supplied addon code")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gradle", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--provision", action="store_true", help="Use the existing hash-locked Workbench build toolchains")
    parser.add_argument("--output", type=Path, default=ROOT / ".workbench/build/axiom")
    parser.add_argument("--registry-root", type=Path, help="Existing profile-pinned utility JARs for offline native registry tests")
    args = parser.parse_args(argv)
    if args.provision:
        if args.gradle or args.java_home:
            parser.error("--provision cannot be combined with explicit toolchains")
        from axiom_runtime import provisioned_selection
        java_home, gradle_home = provisioned_selection()
        gradle = gradle_home / ("bin/gradle.bat" if os.name == "nt" else "bin/gradle")
    else:
        if not args.gradle or not args.java_home:
            parser.error("supply --gradle and --java-home, or --provision")
        java_home, gradle = args.java_home.resolve(strict=True), args.gradle.resolve(strict=True)
    from axiom_runtime import verify_runtime
    verify_runtime(java_home, compiler=True)
    from axiom_registry_runtime import provision as provision_registries, verify as verify_registries
    if args.registry_root:
        registry_root = verify_registries(args.registry_root)
    elif args.provision:
        registry_root = provision_registries(ROOT / ".workbench/axiom/registry-runtime")
    else:
        parser.error("supply --registry-root for offline registry tests, or --provision")
    environment = dict(os.environ)
    for key in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "GRADLE_OPTS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(key, None)
    environment.update(JAVA_HOME=str(java_home), GRADLE_USER_HOME=str(ROOT / ".workbench/axiom/gradle-home"),
                       AXIOM_REGISTRY_TEST_ROOT=str(registry_root))
    subprocess.run([str(gradle), "-p", str(ROOT / "modules/axiom/jvm"), "--no-daemon", "--console=plain",
                    "--dependency-verification=strict", "clean", "test", "installDist", "distZip", "sourcesJar"],
                   cwd=ROOT, env=environment, check=True)
    subprocess.run([sys.executable, str(ROOT / "tools/axiom_smoke.py"), "--engine-home",
                    str(ROOT / "modules/axiom/jvm/build/install/workbench-axiom-engine"), "--java-home", str(java_home)],
                   cwd=ROOT, check=True)
    from component_versions import load_authority
    component = load_authority()[1]["workbench-axiom-engine"]
    filename = component["artifacts"][0]["filename_template"].format(version=component["version"])
    artifact = ROOT / "modules/axiom/jvm/build/distributions" / filename
    verify_archive(artifact)
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / filename
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing candidate: " + str(destination))
    shutil.copy2(artifact, destination)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
