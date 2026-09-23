#!/usr/bin/env python3
"""Compare the Java composition reader to Python TOML on the entire pinned pack.

This is independent metadata/identity evidence, not game or recipe execution.
Every declaration is checked; no sample of the eight initial dependency refs.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import tomllib
import zipfile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-home", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--workbench-python", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    before = sha256(args.target.read_bytes()).hexdigest()
    cases = 0
    with tempfile.TemporaryDirectory(prefix="axiom-composition-") as temporary:
        root = Path(temporary)
        engine = root / "engine"
        shutil.copytree(args.engine_home, engine)
        target = root / "target.zip"
        shutil.copy2(args.target, target)
        java = args.java_home.resolve(strict=True) / "bin/java"
        environment = {"LANG": "C.UTF-8", "HOME": str(root), "WORKBENCH_STATE_ROOT": str(root / "state")}

        def invoke(request=None, workbench=False):
            nonlocal cases
            if workbench:
                command = [str(args.workbench_python.absolute()), "-I", "-m", "workbench_core", "axiom", "target",
                           "--engine-home", str(engine), "--java", str(java), "--target", str(target), "--profile", "supersymmetry"]
                request_path = root / "request.json"
                request_path.write_text(json.dumps(request))
                command += ["--request", str(request_path)]
            else:
                command = [str(java), "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main", "target", "--target", str(target)]
            result = subprocess.run(command, input=b"" if request is None else json.dumps(request).encode(),
                                    capture_output=True, cwd=root, env=environment, timeout=45)
            if result.returncode:
                raise AssertionError(f"Composition failed ({result.returncode}): {result.stdout[-3000:]} {result.stderr[-1000:]}")
            value = json.loads(result.stdout)
            if value["status"] != "accepted" or value["result"]["wholePackParity"]:
                raise AssertionError("Composition inspection and recipe qualification were conflated")
            cases += 1
            return value["result"]

        base = invoke()  # Java first verifies the source package without trusting Python parsing.
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            pack = next(row for row in manifest["repositories"] if row["id"] == "supersymmetry")
            files = {row["path"]: archive.read("blobs/" + row["sha256"]) for row in pack["files"]}
            pack_manifest = tomllib.loads(files["pack.toml"].decode())
            declared = {path: tomllib.loads(raw.decode()) for path, raw in files.items()
                        if path.startswith("mods/") and path.endswith(".pw.toml")}
            mixins = {(repo["id"], row["path"], row["sha256"]) for repo in manifest["repositories"] for row in repo["files"]
                      if row["path"].startswith("src/main/resources/") and PurePosixPath(row["path"]).name.startswith(("mixin.", "mixins."))
                      and row["path"].endswith(".json")}
        composition = base["composition"]
        if composition["parsedDescriptors"] != len(declared) or not composition["declarationInventoryComplete"]:
            raise AssertionError("Not all declared artifact metadata was parsed")
        if composition["pack"] != pack_manifest or composition["index"]["observedHash"] != sha256(files["index.toml"]).hexdigest():
            raise AssertionError("Pack/index identity differs between Java and Python")
        if composition["indexVerified"] or composition["index"]["size"] != 1:
            raise AssertionError("The known pinned empty/mismatched index was accidentally qualified")
        observed_mixins = {(row["repository"], row["path"], row["sha256"]) for row in composition["transformationResources"]}
        if observed_mixins != mixins or composition["activeTransformationsResolved"]:
            raise AssertionError("Mixin declaration inventory differs or claims activation")
        rows = {row["path"]: row for row in composition["artifacts"]}
        if set(rows) != set(declared):
            raise AssertionError("Java artifact declarations differ from the independent TOML inventory")
        for path, metadata in declared.items():
            row = rows[path]
            expected = {"name": metadata["name"], "outputPath": str(PurePosixPath(path).parent / metadata["filename"]),
                        "sha256": sha256(files[path]).hexdigest(), "side": metadata.get("side") or "both",
                        "optional": metadata.get("option", {}).get("optional", False),
                        "defaultEnabled": metadata.get("option", {}).get("default", False),
                        "downloadHash": {"algorithm": metadata["download"]["hash-format"], "value": metadata["download"]["hash"].lower()}}
            if any(row.get(key) != value for key, value in expected.items()):
                raise AssertionError("Parsed metadata differs: " + path)
            curse = metadata["update"]["curseforge"]
            if row["download"] != {"mode": "metadata:curseforge", "projectId": curse["project-id"], "fileId": curse["file-id"]}:
                raise AssertionError("Artifact locator differs: " + path)
            if row["artifactVerified"] or row["activation"] != "unresolved" or row["indexMembership"] != "not-indexed":
                raise AssertionError("Unindexed declaration became an installed artifact: " + path)
        optional = {path for path, metadata in declared.items() if metadata.get("option", {}).get("optional", False)}
        selections = [("client", {}), ("server", {}), ("client", {path: True for path in optional}),
                      ("client", {path: False for path in optional})]
        selection_counts = []
        for side, options in selections:
            request = {"schema": "axiom.target-request.v1", "targetId": base["targetId"], "overlays": [],
                       "sourcePaths": ["groovy/postInit/chemistry/organic_chemistry/Coolants.groovy"],
                       "composition": {"side": side, "options": options}}
            result = invoke(request)
            selected = set()
            for path, metadata in declared.items():
                option = metadata.get("option", {})
                enabled = not option.get("optional", False) or options.get(path, option.get("default", False))
                if enabled and (metadata.get("side") or "both") in {"both", side}:
                    selected.add(path)
            observed = {row["path"] for row in result["composition"]["artifacts"] if row["selection"] == "included-declaration"}
            if observed != selected or result["composition"]["selectedDeclarations"] != len(selected):
                raise AssertionError("Java/Python side/option selection differs")
            if result["candidateId"] != base["candidateId"] or result["composition"]["compositionId"] == composition["compositionId"]:
                raise AssertionError("Source identity and selected composition were conflated")
            selection_counts.append({"side": side, "overrides": options, "declarations": len(selected)})
        if args.workbench_python:
            integrated = invoke(request, workbench=True)
            if integrated["composition"] != result["composition"]:
                raise AssertionError("Installed profile bridge differs from standalone Java")
        if before != sha256(target.read_bytes()).hexdigest() or before != sha256(args.target.read_bytes()).hexdigest():
            raise AssertionError("Inspection changed source archive")
        report = {"cases": cases, "descriptorComparisons": len(declared), "mixinResources": len(mixins),
                  "configurationFiles": composition["configurationFiles"], "selectionComparisons": selection_counts,
                  "targetId": base["targetId"], "compositionId": composition["compositionId"],
                  "outsideCheckout": True, "workbenchBridge": bool(args.workbench_python),
                  "indexVerified": False, "installedCompositionQualified": False, "minecraftLaunched": False}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x") as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.write("\n")
        print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
