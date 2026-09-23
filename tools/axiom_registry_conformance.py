#!/usr/bin/env python3
"""Execute locked GT registry/queue source against the retained native extraction."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import axiom_registry_runtime as runtime_inputs
import axiom_registry_sources as extraction
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import LOCK, MATERIAL_ROOT, engine_inputs, source

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "modules/axiom/tests/oracles/RegistryConformance.java"


def retained_inputs(originals, root=MATERIAL_ROOT):
    retained = {name: (root / (name + ".java")).read_text() for name in (*extraction.PATHS, "FluidRegistration")}
    for name, path in extraction.PATHS.items():
        if retained[name] != extraction.extract(name, originals[path]):
            raise ValueError("retained registry source differs from extraction: " + name)
    if retained["FluidRegistration"] != extraction.fluid_storage(originals[extraction.FLUID_PATH]):
        raise ValueError("retained fluid queue differs from extraction")
    return retained


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gtceu", "java-home", "engine-home", "registry-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report is not None and args.report.exists():
        raise ValueError("report already exists")
    raw_lock, driver = LOCK.read_bytes(), DRIVER.read_bytes()
    lock = json.loads(raw_lock)
    recipe = Path(extraction.__file__).read_bytes()
    registry_policy = runtime_inputs.POLICY.read_bytes()
    root = runtime_inputs.verify(args.registry_root).resolve(strict=True)
    java = args.java_home.resolve(strict=True)
    policy = verify_runtime(java, compiler=True)
    home = args.engine_home.resolve(strict=True)
    manifest_bytes, manifest, jars = engine_inputs(home, raw_lock)
    bindings = []
    for jar in jars:
        with zipfile.ZipFile(jar) as archive:
            if "axiom/registry-runtime.json" in archive.namelist():
                bindings.append(archive.read("axiom/registry-runtime.json"))
    if bindings != [registry_policy]:
        raise ValueError("installed registry policy differs from profile")
    originals = {path: source(args.gtceu, path, lock) for path in (*extraction.PATHS.values(), extraction.FLUID_PATH)}
    retained = retained_inputs(originals)
    # Shared carriers and native binding are part of the receipt, not a second semantic oracle.
    shared = {name: (MATERIAL_ROOT / (name + ".java")).read_bytes() for name in
              ("MaterialState", "MaterialPhase", "NativeNamedRegistry", "RegistryRuntime")}
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="axiom-registry-conformance-") as temporary:
        directory = Path(temporary)
        for name, path in extraction.PATHS.items():
            (directory / ("Original" + name + ".java")).write_text(extraction.extract(name, originals[path], "Original"))
        (directory / "OriginalFluidRegistration.java").write_text(extraction.fluid_storage(originals[extraction.FLUID_PATH], "Original"))
        (directory / "RegistryConformance.java").write_bytes(driver)
        classpath = os.pathsep.join(map(str, jars))
        subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", classpath,
                        "-d", str(directory), *map(str, directory.glob("*.java"))],
                       cwd=directory, env=environment, check=True, timeout=60)
        run = subprocess.run([str(java / "bin/java"), "-Xmx256m", "-cp", str(directory) + os.pathsep + classpath,
                              "research.orthrus.axiom.RegistryConformance", str(root)],
                             cwd=directory, env=environment, capture_output=True, text=True, timeout=120)
        if run.returncode:
            raise AssertionError("native registry comparison failed:\n" + run.stdout[-4000:] + run.stderr[-6000:])
        result = json.loads(run.stdout)
        if result != {"execution": "native-jvm", "registryOperations": 24000, "fluidQueueOperations": 48000,
                      "wholePackParity": False, "fluidBuildersExecuted": False}:
            raise ValueError("incomplete registry comparison")
    if (engine_inputs(home, raw_lock)[0] != manifest_bytes or LOCK.read_bytes() != raw_lock
            or DRIVER.read_bytes() != driver or Path(extraction.__file__).read_bytes() != recipe
            or runtime_inputs.POLICY.read_bytes() != registry_policy or retained_inputs(originals) != retained
            or any((MATERIAL_ROOT / (name + ".java")).read_bytes() != raw for name, raw in shared.items())):
        raise ValueError("registry comparison binding changed")
    runtime_inputs.verify(root)
    for row in policy["runtimeFiles"] + policy["compilerFiles"]:
        checked_path(java, row)
    for path, original in originals.items():
        if source(args.gtceu, path, lock) != original:
            raise ValueError("comparison source changed")
    if args.report is not None:
        report = {"schema": "axiom.registry-conformance.v1", "status": "passed", "result": result,
                  "sourceLockSha256": sha256(raw_lock).hexdigest(), "driverSha256": sha256(driver).hexdigest(),
                  "extractionRecipeSha256": sha256(recipe).hexdigest(), "registryPolicySha256": sha256(registry_policy).hexdigest(),
                  "engineJars": manifest["jars"], "runtimeInputs": policy["runtimeFiles"] + policy["compilerFiles"],
                  "sourceInputs": {path: sha256(text.encode()).hexdigest() for path, text in originals.items()},
                  "retainedSources": {name: sha256(text.encode()).hexdigest() for name, text in retained.items()},
                  "sharedSources": {name: sha256(raw).hexdigest() for name, raw in shared.items()},
                  "substitutions": ["Package/visibility/annotations; MaterialState and MaterialPhase carriers shared",
                                    "Evaluation-scoped active mod, manager owner, network IDs and diagnostics",
                                    "Original pinned Minecraft registry utilities via shared native binding; no remapping or game startup",
                                    "Fluid key/builder/default factory/diagnostic ports shared; Fastutil remains the selected native implementation"],
                  "notQualified": ["original material producers/events", "FluidBuilder or Forge fluid registration",
                                   "installed transforms/source-artifact equivalence", "whole-pack validity"]}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
