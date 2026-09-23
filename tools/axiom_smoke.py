#!/usr/bin/env python3
"""Run Axiom from a disposable installed copy outside the checkout, without Minecraft."""

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api/src"))
from workbench_api.filesystem_paths import native_path
POLICY = ROOT / "profiles/platforms/cleanroom" / (
    "jvm-runtime-windows-x64.json" if os.name == "nt" else "jvm-runtime.json"
)


def context():
    ores = ["dyeCyan", "dustTinyBorax", "dustSodiumHydroxide", "dustTinyMercaptobenzothiazole"]
    value = {"scope": "explicit-context", "items": [], "ores": [], "oreLookupOrder": {},
             "fluids": ["ethylene_glycol", "coolant", "advanced_coolant", "polydimethylsiloxane"],
             "circuit": {"id": "fixture:circuit", "meta": 0}}
    for name in ["circuit", *ores]:
        identifier = "fixture:" + name.lower()
        value["items"].append({"id": identifier, "meta": 0, "maxStack": 64, "capabilities": "none"})
        value["oreLookupOrder"][identifier + ":0"] = [] if name == "circuit" else [name]
        if name != "circuit":
            value["ores"].append({"name": name, "members": [{"id": identifier, "meta": 0}]})
    return value


def machine(tier):
    return {"machine": "gregtech:mixer." + tier, "entry": "idle-search",
            "inputItems": [{"id": "fixture:" + name, "meta": 0, "count": 1}
                           for name in ("dyecyan", "dusttinyborax", "dustsodiumhydroxide")] + [None] * 3,
            "inputFluids": ([{"id": "ethylene_glycol", "amount": 10000}, None, None] if tier == "mv" else
                            [{"id": "ethylene_glycol", "amount": 8000}, {"id": "ethylene_glycol", "amount": 2000}, None]),
            "outputItems": [None], "outputFluids": [None, None], "ghostCircuit": -1,
            "energy": 8000 if tier == "mv" else 2000, "overclockTier": 2 if tier == "mv" else 1,
            "voidItems": False, "voidFluids": False, "allowInputFromOutputSideItems": False,
            "allowInputFromOutputSideFluids": False, "cache": None, "outputsFull": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-home", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--workbench-python", type=Path, help="Also exercise an already installed Core/API/Axiom wheel closure")
    parser.add_argument("--sandbox-backend", choices=("bubblewrap", "docker", "gvisor"), default="bubblewrap")
    args = parser.parse_args(argv)
    from axiom_sandbox import selected_worker
    with selected_worker(args.sandbox_backend) as sandbox_arguments:
        return _run(args, sandbox_arguments)


def _run(args, sandbox_arguments):
    java = args.java_home.resolve(strict=True) / "bin" / (
        "java.exe" if os.name == "nt" else "java"
    )
    vm_arguments = ["-Xshare:off"] if os.name == "nt" else []
    source = (ROOT / "modules/axiom/tests/fixtures/Coolants.groovy").read_text()
    cases = 0
    with tempfile.TemporaryDirectory(prefix="axiom-installed-smoke-") as temporary:
        root = Path(temporary)
        engine = root / "engine"
        shutil.copytree(native_path(args.engine_home), engine)
        forbidden = ("BytecodeExecution", "ClassExecution", "ClassCode", "ClassNamespaces", "ClassInitialization",
                     "ClassLoading", "ClassPathResources", "ClassPackages", "ClassAssertions", "EventRuntime",
                     "EventTransforms", "FoundationTransformers", "JarSignatures", "JdkDefinitions", "JdkClassLoading")
        for archive_path in list((engine / "lib").glob("*.jar")) + list((engine / "sources").glob("*.jar")):
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    if name.startswith("research/orthrus/axiom/") and name.rsplit("/", 1)[-1].split("$")[0].split(".")[0] in forbidden:
                        raise AssertionError("Retired JVM implementation remains in distribution: " + name)
        policy = POLICY.read_bytes()
        bindings = []
        for archive_path in (engine / "lib").glob("*.jar"):
            with zipfile.ZipFile(archive_path) as archive:
                policy_resource = "axiom/" + POLICY.name
                if policy_resource in archive.namelist():
                    bindings.append(archive.read(policy_resource))
        if bindings != [policy]:
            raise AssertionError("Installed runtime policy differs from its profile owner")
        for jar, embedded, retained in (
            ("groovy-4.0.30.jar", "META-INF/LICENSE", "groovy-and-tomlj/LICENSE"),
            ("groovy-4.0.30.jar", "META-INF/NOTICE", "groovy-and-tomlj/NOTICE"),
            ("checker-qual-3.21.2.jar", "META-INF/LICENSE.txt", "checker-qual/LICENSE.txt"),
        ):
            with zipfile.ZipFile(engine / "lib" / jar) as archive:
                if (engine / "third-party" / retained).read_bytes() != archive.read(embedded):
                    raise AssertionError("Distribution omitted or altered a parser license/notice")
        if (engine / "third-party/antlr4-runtime.txt").read_bytes() != (ROOT / "modules/axiom/sources/licenses/antlr4-runtime.txt").read_bytes():
            raise AssertionError("Distribution omitted or altered ANTLR's license")
        command = [str(java), *vm_arguments, *sandbox_arguments, "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main"]
        environment = {"LANG": "C.UTF-8", "WORKBENCH_STATE_ROOT": str(root / "state"), "HOME": str(root)}
        rejected = subprocess.run([str(java), "-Djava.security.properties=unadmitted", *command[1:], "coverage"],
                                  capture_output=True, cwd=root, env=environment, timeout=45)
        rejected_body = json.loads(rejected.stdout)
        if (rejected.returncode != 4 or rejected_body.get("completion") != "not-evaluated"
                or rejected_body.get("result", {}).get("rule") != "runtime.mismatch"):
            raise AssertionError("Unadmitted JVM configuration was not rejected before evaluation")
        cases += 1

        def invoke(operation, request, expected=0, *, workbench=False):
            nonlocal cases
            raw = json.dumps(request).encode() if request is not None else b""
            if workbench:
                executable = args.workbench_python.absolute()
                invocation = [str(executable), "-I", "-m", "workbench_core", "axiom", operation,
                              "--engine-home", str(engine), "--java", str(java)]
                if operation != "coverage":
                    request_path = root / "request.json"
                    request_path.write_bytes(raw)
                    invocation += ["--request", str(request_path)]
                    invocation += ["--sandbox-backend", args.sandbox_backend]
            else:
                invocation = [*command, operation]
            result = subprocess.run(invocation, input=raw, capture_output=True, cwd=root, env=environment, timeout=45)
            if result.returncode != expected:
                raise AssertionError(f"{operation}: exit {result.returncode}, expected {expected}\n{result.stdout.decode()}\n{result.stderr.decode()}")
            value = json.loads(result.stdout)
            if value["schema"] != "axiom.result.v1" or value["result"]["wholePackParity"] is not False:
                raise AssertionError("invalid or overclaimed Axiom result")
            if operation == "coverage":
                execution = value["result"]["execution"]
                runtime = value["result"]["runtime"]
                if (execution["javaSemantics"] != "profile-pinned-native-jvm"
                        or execution["customJvmImplemented"] is not False
                        or execution["upstreamRecipeEnvironmentExecuted"] is not False
                        or runtime["observed"]["runtimeVersion"] != "25.0.4+7-LTS"
                        or runtime["verification"] != "process-preflight"):
                    raise AssertionError("missing or overclaimed native runtime boundary")
            cases += 1
            return value

        coverage = invoke("coverage", None)
        if os.name == "nt":
            # The native engine's source runner requires Linux bubblewrap.
            # Windows qualification here is deliberately limited to installed
            # runtime preflight and read-only coverage.
            if args.workbench_python:
                invoke("coverage", None, workbench=True)
            print(json.dumps({"cases": cases, "installedOutsideCheckout": True,
                              "workbenchBridge": bool(args.workbench_python), "minecraftLaunched": False,
                              "sourceEvaluationQualified": False, "wholePackParity": False}, sort_keys=True))
            return 0
        request = {"schema": "axiom.request.v1", "targetId": coverage["result"]["targetId"], "registry": context(),
                   "files": [{"path": "groovy/postInit/chemistry/organic_chemistry/Coolants.groovy", "text": source}]}
        checked = invoke("check", request)
        if len(checked["result"]["definitions"]) != 3:
            raise AssertionError("Coolants must construct exactly three effective definitions in this selected program")
        query = dict(request, queryKind="select-and-start", machine=machine("mv"), loads=[])
        started = invoke("query", query)
        after = started["result"]["machine"]["after"]
        if after["progress"] != 1 or after["outputFluids"] != [None, None] or after["energy"] != 8000:
            raise AssertionError("start transition incorrectly delivered products or drew energy")
        query["machine"]["cache"] = after["cache"]
        cached = invoke("query", query)
        if cached["result"]["machine"]["selection"] != "previous-recipe":
            raise AssertionError("same installed code/program must accept its own cache identity")
        query["machine"] = machine("lv")
        invoke("query", query, 1)
        query["machine"]["outputFluids"] = [{"id": "coolant", "amount": 1000}] * 2
        invoke("query", query)
        attack = copy.deepcopy(request)
        attack["files"][0]["text"] = "new File('/tmp/axiom-must-not-write').text = 'escape'"
        invoke("check", attack, 3)
        stale = copy.deepcopy(query)
        stale["machine"]["cache"] = after["cache"]
        stale["files"][0]["text"] += "\n// edited source"
        invoke("query", stale, 2)
        if args.workbench_python:
            invoke("coverage", None, workbench=True)
            invoke("check", request, workbench=True)
            invoke("query", query, workbench=True)
    print(json.dumps({"cases": cases, "installedOutsideCheckout": True, "workbenchBridge": bool(args.workbench_python),
                      "minecraftLaunched": False, "registry": "synthetic-explicit-context", "wholePackParity": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
