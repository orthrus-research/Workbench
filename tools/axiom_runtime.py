#!/usr/bin/env python3
"""Verify profile-owned Axiom runtimes; request build tools through Core."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from urllib.request import url2pathname

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "profiles/platforms/cleanroom" / ("jvm-runtime-windows-x64.json" if os.name == 'nt' else "jvm-runtime.json")


def checked_path(root, row):
    relative = Path(row["path"])
    if (not row["path"] or relative.is_absolute() or ".." in relative.parts
            or relative.as_posix() != row["path"] or "\\" in row["path"]):
        raise ValueError("unsafe conformance input path")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("indirect conformance input path")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("conformance input escaped root")
    if not path.is_file() or ("size" in row and path.stat().st_size != row["size"]):
        raise ValueError("conformance input size changed: " + row["path"])
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != row["sha256"]:
        raise ValueError("conformance input changed: " + row["path"])
    return path


def verify_runtime(home, *, compiler=False):
    policy = json.loads(POLICY.read_bytes())
    if policy.get("schema") != "axiom.jvm-runtime.v1" or policy.get("profile") != "cleanroom":
        raise ValueError("unknown Axiom runtime policy")
    home = home.resolve(strict=True)
    entries = policy["runtimeFiles"] + (policy["compilerFiles"] if compiler else [])
    if len({row["path"] for row in entries}) != len(entries):
        raise ValueError("duplicate runtime entry")
    for row in entries:
        checked_path(home, row)
    return policy


def provisioned_java_selection():
    """Ask Core to retain the profile-selected JDK without provisioning Gradle."""
    for source in (ROOT / 'api/src', ROOT / 'core/src'):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
    from workbench_core.development import enable_source_checkout
    from workbench_core.runtime_java import ensure_java_runtime
    policy = json.loads(POLICY.read_bytes())
    enable_source_checkout(ROOT)
    result = ensure_java_runtime(ROOT, state_root=ROOT / '.workbench/axiom/build-toolchains/java',
                                 candidates=(), config_path=ROOT / 'workbench.toml')
    receipt = result['receipt']
    if receipt['asset']['sha256'] != policy['archive']['archive_sha256']:
        raise ValueError('Core Java acquisition differs from Cleanroom JVM selection')
    java = Path(url2pathname(urlparse(receipt['target']['java_home_uri']).path))
    verify_runtime(java, compiler=True)
    return java


def provisioned_selection():
    """Ask Core to retain the selected JDK and exact Gradle ZIP."""
    java = provisioned_java_selection()
    from workbench_core.build_toolchain_setup import prepare_zip_toolchain
    from validation.provision_ide_toolchains import load_lock
    lock = load_lock()['gradle']
    gradle = prepare_zip_toolchain(ROOT / '.workbench/axiom/build-toolchains/gradle',
                                   lock, executable='bin/gradle.bat' if os.name == 'nt' else 'bin/gradle')
    return java, gradle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--provision-java", action="store_true", help="select and retain the profile-locked JDK through Core")
    parser.add_argument("--github-env-file", type=Path, help="append WORKBENCH_TEST_JAVA to a CI environment file")
    parser.add_argument("--compiler", action="store_true")
    args = parser.parse_args(argv)
    if args.provision_java == (args.java_home is not None):
        parser.error("supply exactly one of --java-home or --provision-java")
    if args.github_env_file is not None and not args.provision_java:
        parser.error("--github-env-file requires --provision-java")
    if args.provision_java:
        java_home = provisioned_java_selection()
        if args.github_env_file is not None:
            with args.github_env_file.open("a", encoding="utf-8") as output:
                output.write(f"WORKBENCH_TEST_JAVA={java_home / 'bin/java'}\n")
        compiler = True
    else:
        java_home = args.java_home
        compiler = args.compiler
    value = verify_runtime(java_home, compiler=compiler)
    print(json.dumps({"verified": True, "runtimeVersion": value["runtimeVersion"],
                      "policySha256": sha256(POLICY.read_bytes()).hexdigest(), "compiler": compiler}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
