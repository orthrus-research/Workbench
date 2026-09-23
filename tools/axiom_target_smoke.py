#!/usr/bin/env python3
"""Exercise the real portable source target outside the checkout, without Git/game access."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-home", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--workbench-python", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sandbox-backend", choices=("bubblewrap", "docker", "gvisor"), default="bubblewrap")
    args = parser.parse_args(argv)
    from axiom_sandbox import selected_worker
    with selected_worker(args.sandbox_backend) as sandbox_arguments:
        return _run(args, sandbox_arguments)


def _run(args, sandbox_arguments):
    started = time.monotonic()
    cases = 0
    target_digest = sha256(args.target.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="axiom-source-target-smoke-") as temporary:
        root = Path(temporary)
        target = root / "source-target.zip"
        shutil.copy2(args.target, target)
        engine = root / "engine"
        shutil.copytree(args.engine_home, engine)
        java = args.java_home.resolve(strict=True) / "bin/java"
        environment = {"LANG": "C.UTF-8", "HOME": str(root), "WORKBENCH_STATE_ROOT": str(root / "state")}

        def invoke(request=None, expected=0, workbench=False):
            nonlocal cases
            if workbench:
                command = [str(args.workbench_python.absolute()), "-I", "-m", "workbench_core", "axiom", "target",
                           "--engine-home", str(engine), "--java", str(java), "--target", str(target), "--profile", "supersymmetry"]
                command += ["--sandbox-backend", args.sandbox_backend]
                if request is not None:
                    path = root / "request.json"
                    path.write_text(json.dumps(request))
                    command += ["--request", str(path)]
            else:
                command = [str(java), *sandbox_arguments, "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main", "target", "--target", str(target)]
            result = subprocess.run(command, input=b"" if request is None else json.dumps(request).encode(), cwd=root,
                                    capture_output=True, env=environment, timeout=45)
            if result.returncode != expected:
                raise AssertionError(f"target exit {result.returncode}, expected {expected}: {result.stdout[-4000:]} {result.stderr[-1000:]}")
            value = json.loads(result.stdout)
            if value["schema"] != "axiom.result.v1" or value["result"]["wholePackParity"] is not False:
                raise AssertionError("target inspection overclaimed recipe qualification")
            cases += 1
            return value["result"]

        base = invoke()
        plan = base["sourcePlan"]
        if not base["sourceTreeMembershipVerified"] or not base["policySelectionComplete"] or plan["definitionsExecuted"]:
            raise AssertionError("source membership and execution were conflated")
        if plan["scheduledFiles"] != 308 or plan["syntaxErrors"] != 0 or plan["inspectedFiles"] != 308:
            raise AssertionError("unexpected pinned Supersymmetry source inventory")
        source = "groovy/postInit/chemistry/organic_chemistry/Coolants.groovy"
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            pack = next(row for row in manifest["repositories"] if row["id"] == "supersymmetry")
            record = next(row for row in pack["files"] if row["path"] == source)
            text = archive.read("blobs/" + record["sha256"]).decode()
        request = {"schema": "axiom.target-request.v1", "targetId": base["targetId"], "overlays": [],
                   "sourcePaths": [source], "includeFiles": True}
        detail = invoke(request)
        if detail["sourcePlan"]["inspectedFiles"] != 1 or detail["candidateId"] != base["candidateId"]:
            raise AssertionError("inspection selection changed source identity")
        request["overlays"] = [{"repository": "supersymmetry", "path": source, "expectedSha256": record["sha256"], "text": text + "\n// candidate\n"}]
        edited = invoke(request)
        if edited["candidateId"] == base["candidateId"]:
            raise AssertionError("source edit retained candidate identity")
        request["overlays"][0]["expectedSha256"] = "0" * 64
        invoke(request, 2)
        request["overlays"][0]["expectedSha256"] = record["sha256"]
        request["overlays"][0]["text"] = None
        request.pop("sourcePaths")
        request["includeFiles"] = False
        deleted = invoke(request)
        if deleted["sourcePlan"]["scheduledFiles"] != 307:
            raise AssertionError("deleted source was still scheduled")
        if sha256(target.read_bytes()).hexdigest() != target_digest or sha256(args.target.read_bytes()).hexdigest() != target_digest:
            raise AssertionError("evaluation mutated original source package")
        if args.workbench_python:
            integrated = invoke(workbench=True)
            if integrated["candidateId"] != base["candidateId"]:
                raise AssertionError("installed Workbench disagrees with independent engine")
        report = {"cases": cases, "targetId": base["targetId"], "candidateId": base["candidateId"],
                  "repositoryFiles": base["repositoryFiles"], "scripts": plan["scheduledFiles"], "syntaxErrors": plan["syntaxErrors"],
                  "literalRegistryReferences": plan["literalRegistryReferenceCount"], "dynamicRegistryReferences": plan["dynamicRegistryReferences"],
                  "installedOutsideCheckout": True, "workbenchBridge": bool(args.workbench_python), "minecraftLaunched": False,
                  "wholePackParity": False, "seconds": round(time.monotonic() - started, 3)}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x") as output:
                json.dump(report, output, indent=2, sort_keys=True)
                output.write("\n")
        print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
