#!/usr/bin/env python3
"""Outside-checkout platform custody, binary/default witnesses and installed integration checks."""

import argparse
from hashlib import sha1, sha256
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

from axiom_artifact_smoke import file_hash


def javap_defaults(output):
    """Read only the scalar/enum/enum-array annotation defaults used by these witnesses."""
    def value(text):
        if text.startswith("[") and text.endswith("]"):
            return [] if text == "[]" else [value(part) for part in text[1:-1].split(",")]
        enum = re.fullmatch(r"(L[\w/$]+;)\.([\w$]+)", text)
        if enum:
            return {"enumType": enum[1], "value": enum[2]}
        try:
            return json.loads(text)
        except ValueError as failure:
            raise AssertionError("Unsupported javap default representation") from failure
    result = []
    for block in re.finditer(r"^  public abstract [^\n]*? (\w+)\(\);\n(.*?)(?=^  \S|^}|\Z)", output, re.M | re.S):
        descriptor = re.search(r"^    descriptor: (.+)$", block[2], re.M)
        flags = re.search(r"^    flags: \(0x([0-9a-fA-F]+)\)", block[2], re.M)
        default = re.search(r"^    AnnotationDefault:\n      default_value: [^\n]+\n        ([^\n]+)$", block[2], re.M)
        if not descriptor or not flags or "AnnotationDefault:" in block[2] and not default:
            raise AssertionError("Malformed javap annotation member")
        member = {"name": block[1], "descriptor": descriptor[1], "access": int(flags[1], 16), "defaultPresent": default is not None}
        if default:
            member["default"] = value(default[1])
        result.append(member)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine-home", "java-home", "platform"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("workbench-python", "target", "artifacts", "request", "report"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    if any((args.target, args.artifacts, args.request)) and not all((args.target, args.artifacts, args.request)):
        parser.error("combined inspection requires --target, --artifacts and --request together")
    started, cases = time.monotonic(), 0
    original = file_hash(args.platform)
    with tempfile.TemporaryDirectory(prefix="axiom-platform-smoke-") as temporary:
        root = Path(temporary)
        engine, platform = root / "engine", root / "platform.zip"
        shutil.copytree(args.engine_home, engine); shutil.copy2(args.platform, platform)
        java = args.java_home.resolve(strict=True) / "bin"
        environment = {"LANG": "C.UTF-8", "HOME": str(root), "WORKBENCH_STATE_ROOT": str(root / "state")}
        if args.target:
            shutil.copy2(args.target, root / "source.zip"); shutil.copy2(args.artifacts, root / "mods.zip")

        def invoke(request=None, expected=0, workbench=False, combined=False):
            nonlocal cases
            operation = "target" if combined else "platform"
            extra = ["--platform", str(platform)]
            if combined:
                extra = ["--target", str(root / "source.zip"), "--artifacts", str(root / "mods.zip"), *extra]
            if workbench:
                command = [str(args.workbench_python.absolute()), "-I", "-m", "workbench_core", "axiom", operation,
                           "--engine-home", str(engine), "--java", str(java / "java"), "--profile", "supersymmetry" if combined else "cleanroom", *extra]
                if combined:
                    command += ["--platform-profile", "cleanroom"]
                if request is not None:
                    (root / "request.json").write_text(json.dumps(request))
                    command += ["--request", str(root / "request.json")]
            else:
                command = [str(java / "java"), "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main", operation, *extra]
            process = subprocess.run(command, input=b"" if request is None else json.dumps(request).encode(), capture_output=True,
                                     cwd=root, env=environment, timeout=45)
            if process.returncode != expected:
                raise AssertionError(f"Platform inspection exit {process.returncode}: {process.stdout[-3000:]} {process.stderr[-2000:]}")
            value = json.loads(process.stdout)
            if value["result"]["wholePackParity"] or value["result"]["installedCompositionQualified"]:
                raise AssertionError("Platform inspection overclaimed parity")
            cases += 1
            return value["result"]

        result = invoke()
        if result["missingArtifacts"] or result["classesLoaded"] or not result["providedArtifactBytesVerified"]:
            raise AssertionError("Smoke requires the full declared platform byte selection without loading classes")
        plan = result["metadata"]
        if len(plan["declarations"]) != 160 or len(plan["classpath"]) != 114 or len(plan["nativeExtractions"]) != 1:
            raise AssertionError("Unexpected pinned Cleanroom 0.6.12-alpha Linux client inventory")
        rows = {row["path"]: row for row in result["artifacts"]}
        declarations = {row["path"]: row for row in plan["libraries"]}
        classes = 0
        with zipfile.ZipFile(platform) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if sha256(archive.read("policy.json")).hexdigest() != result["policySha256"] or sha256(archive.read("bootstrap.zip")).hexdigest() != result["bootstrapSha256"]:
                raise AssertionError("Independent bootstrap/policy hash differs")
            for record in manifest["artifacts"]:
                raw = archive.read("blobs/" + record["sha256"])
                if sha256(raw).hexdigest() != record["sha256"] or sha1(raw).hexdigest() != declarations[record["path"]]["sha1"] or len(raw) != record["size"]:
                    raise AssertionError("Independent platform byte/hash verification differs")
                if rows[record["path"]]["role"] == "native-extraction":
                    if record["path"] in plan["classpath"] or "classFiles" in rows[record["path"]]["binary"]:
                        raise AssertionError("Native extraction became a Java classpath provider")
                    continue
                with zipfile.ZipFile(io.BytesIO(raw)) as jar:
                    expected = sum(entry.filename.endswith(".class") for entry in jar.infolist())
                    if expected != rows[record["path"]]["binary"]["classFiles"]:
                        raise AssertionError("JDK/Python ZIP class counts differ: " + record["path"])
                    classes += expected
        main = next(row["path"] for row in plan["libraries"] if row["role"] == "main-jar")
        if plan["classpath"][-1] != main:
            raise AssertionError("Main JAR was not last in the metadata-derived classpath")
        cleanroom = next(row["path"] for row in plan["libraries"] if row["coordinate"] == "com.cleanroommc:cleanroom:0.6.12-alpha")
        query = {"schema": "axiom.platform-request.v1", "platformId": result["platformId"], "libraryPaths": [cleanroom]}
        detail = invoke(query)
        if detail["platformId"] != result["platformId"]:
            raise AssertionError("Detail filter changed platform identity")
        binary = next(row["binary"] for row in detail["artifacts"] if row["path"] == cleanroom)
        types = {row["class"]: row for row in binary["annotationTypeDeclarations"]}
        subscribe = types["net/minecraftforge/fml/common/eventhandler/SubscribeEvent"]
        subscriber = types["net/minecraftforge/fml/common/Mod$EventBusSubscriber"]
        defaults = {row["name"]: row.get("default") for row in subscribe["members"]}
        sides = {row["name"]: row.get("default") for row in subscriber["members"]}
        if defaults != {"priority": {"enumType": "Lnet/minecraftforge/fml/common/eventhandler/EventPriority;", "value": "NORMAL"}, "receiveCanceled": False}:
            raise AssertionError("Unexpected selected platform SubscribeEvent defaults")
        if sides != {"modid": "", "value": [{"enumType": "Lnet/minecraftforge/fml/relauncher/Side;", "value": "CLIENT"},
                                              {"enumType": "Lnet/minecraftforge/fml/relauncher/Side;", "value": "SERVER"}]}:
            raise AssertionError("Unexpected selected platform EventBusSubscriber defaults")
        with zipfile.ZipFile(platform) as archive:
            witness = root / "cleanroom-witness.jar"
            witness.write_bytes(archive.read("blobs/" + rows[cleanroom]["sha256"]))
        witnesses = []
        for name in (subscribe["class"], subscriber["class"]):
            output = subprocess.run([str(java / "javap"), "-p", "-v", "-classpath", str(witness), name.replace("/", ".")],
                                    cwd=root, env=environment, capture_output=True, timeout=20, check=True).stdout.decode()
            if javap_defaults(output) != types[name]["members"]:
                raise AssertionError("Independent javap defaults witness differs: " + name)
            witnesses.append({"class": name, "sha256": types[name]["sha256"], "members": types[name]["members"]})
        invoke(dict(query, platformId="stale"), expected=2)
        combined_id = None
        if args.target:
            request = json.loads(args.request.read_bytes())
            joined = invoke(request, combined=True)
            combined_id = joined["inputCompositionId"]
            summary = joined["platformInspection"]
            for field in ("platformId", "policySha256", "bootstrapSha256", "missingArtifacts", "providedArtifacts", "artifactBytes", "uniqueClassNames", "duplicateClassCount"):
                if summary[field] != result[field]:
                    raise AssertionError("Joining target and mod inputs changed platform identity/coverage: " + field)
            if summary["metadata"]["classpath"] != plan["classpath"] or summary["metadata"]["nativeExtractions"] != plan["nativeExtractions"]:
                raise AssertionError("Combined summary changed classpath/native selection")
            invoke(dict(request, platform=dict(query, platformId="stale")), combined=True, expected=2)
            wrong = json.loads(json.dumps(request)); wrong["composition"]["side"] = "server"
            invoke(wrong, combined=True, expected=2)
            if args.workbench_python and invoke(request, workbench=True, combined=True) != joined:
                raise AssertionError("Installed combined result differs from standalone Java")
        if args.workbench_python and invoke(workbench=True) != result:
            raise AssertionError("Installed platform result differs from standalone Java")
        if file_hash(args.platform) != original or file_hash(platform) != original:
            raise AssertionError("Platform evaluation modified input bytes")
        if args.target and (file_hash(args.target) != file_hash(root / "source.zip") or file_hash(args.artifacts) != file_hash(root / "mods.zip")):
            raise AssertionError("Combined evaluation modified source/mod bytes")
        report = {"cases": cases, "platformId": result["platformId"], "platformZipSha256": original, "inputCompositionId": combined_id,
                  "declarations": len(plan["declarations"]), "classpathFiles": len(plan["classpath"]), "nativeExtractionFiles": len(plan["nativeExtractions"]),
                  "artifacts": result["providedArtifacts"], "classFiles": classes, "annotationDefaultWitnesses": witnesses,
                  "installedOutsideCheckout": True, "workbenchBridge": bool(args.workbench_python), "minecraftLaunched": False,
                  "installedCompositionQualified": False, "wholePackParity": False, "seconds": round(time.monotonic() - started, 3)}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x") as output:
                json.dump(report, output, indent=2, sort_keys=True); output.write("\n")
        print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
