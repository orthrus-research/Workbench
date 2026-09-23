#!/usr/bin/env python3
"""Verify offline artifact inspection outside checkout, with independent ZIP/javap evidence."""

import argparse
from hashlib import file_digest, sha256
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile


def file_hash(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


EVENT_ANNOTATIONS = {
    "net.minecraftforge.fml.common.eventhandler.SubscribeEvent",
    "net.minecraftforge.fml.common.Mod$EventHandler",
    "net.minecraftforge.fml.relauncher.SideOnly",
}


def javap_event_methods(output):
    """Independent, fail-closed reader for the selected JDK annotation witnesses.

    This does not implement Forge discovery. It compares declarations only and
    deliberately does not synthesize omitted annotation defaults.
    """
    methods = []
    for block in re.finditer(r"^  (\S[^\n]*\([^\n]*\)[^\n]*;)\n(.*?)(?=^  \S|^}|\Z)", output, re.M | re.S):
        declaration, body = block.groups()
        annotations = []
        for section in re.finditer(r"^    Runtime(Visible|Invisible)Annotations:\n(.*?)(?=^    \S|\Z)", body, re.M | re.S):
            for annotation in re.finditer(r"^        ([\w.$]+)(?:\(\n(.*?)^        \))?[ \t]*$", section[2], re.M | re.S):
                name, fields = annotation.groups()
                if name not in EVENT_ANNOTATIONS:
                    continue
                values = {}
                for line in (fields or "").splitlines():
                    field = re.fullmatch(r"          ([\w$]+)=(.+)", line)
                    if not field or field[1] in values:
                        raise AssertionError("Unsupported or repeated javap annotation field")
                    key, value = field.groups()
                    enum = re.fullmatch(r"(L[\w/$]+;)\.([\w$]+)", value)
                    if enum:
                        values[key] = {"enumType": enum[1], "value": enum[2]}
                    elif value in {"true", "false"}:
                        values[key] = value == "true"
                    else:
                        try:
                            values[key] = json.loads(value)
                        except ValueError as failure:
                            raise AssertionError("Unsupported javap annotation value") from failure
                annotations.append({"type": "L" + name.replace(".", "/") + ";", "runtimeVisible": section[1] == "Visible", "values": values})
        if not any(row["type"] in {"Lnet/minecraftforge/fml/common/eventhandler/SubscribeEvent;", "Lnet/minecraftforge/fml/common/Mod$EventHandler;"} for row in annotations):
            continue
        descriptor = re.search(r"^    descriptor: (.+)$", body, re.M)
        flags = re.search(r"^    flags: \(0x([0-9a-fA-F]+)\)", body, re.M)
        if not descriptor or not flags:
            raise AssertionError("Event method descriptor or flags absent from javap")
        method = {"name": declaration.split("(", 1)[0].split()[-1], "descriptor": descriptor[1], "access": int(flags[1], 16), "annotations": annotations}
        signature = re.search(r"^    Signature: #\d+\s+// (.+)$", body, re.M)
        if signature:
            method["signature"] = signature[1]
        methods.append(method)
    return methods


def normalized_methods(methods):
    return sorted((dict(method, annotations=sorted(method["annotations"], key=lambda row: row["type"])) for method in methods),
                  key=lambda method: (method["name"], method["descriptor"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine-home", "java-home", "target", "request", "artifacts"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workbench-python", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_bytes())
    identities = {"target": file_hash(args.target), "artifacts": file_hash(args.artifacts)}
    started, cases = time.monotonic(), 0
    with tempfile.TemporaryDirectory(prefix="axiom-artifact-inspection-") as temporary:
        root = Path(temporary)
        engine, target, artifacts = root / "engine", root / "source.zip", root / "artifacts.zip"
        shutil.copytree(args.engine_home, engine)
        shutil.copy2(args.target, target); shutil.copy2(args.artifacts, artifacts)
        java = args.java_home.resolve(strict=True) / "bin"
        environment = {"LANG": "C.UTF-8", "HOME": str(root), "WORKBENCH_STATE_ROOT": str(root / "state")}

        def invoke(body, expected=0, workbench=False):
            nonlocal cases
            extra = ["--target", str(target), "--artifacts", str(artifacts)]
            if workbench:
                request_path = root / "request.json"
                request_path.write_text(json.dumps(body))
                command = [str(args.workbench_python.absolute()), "-I", "-m", "workbench_core", "axiom", "target", "--engine-home", str(engine),
                           "--java", str(java / "java"), "--profile", "supersymmetry", "--request", str(request_path), *extra]
            else:
                command = [str(java / "java"), "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main", "target", *extra]
            result = subprocess.run(command, input=json.dumps(body).encode(), capture_output=True, env=environment, cwd=root, timeout=45)
            if result.returncode != expected:
                raise AssertionError(f"Artifact inspection exit {result.returncode}: {result.stdout[-2000:]} {result.stderr[-1000:]}")
            value = json.loads(result.stdout)
            if value["result"]["wholePackParity"] or value["result"]["installedCompositionQualified"]:
                raise AssertionError("Artifact inspection overclaimed recipe qualification")
            cases += 1
            return value["result"]

        result = invoke(request)
        inspected = result["artifactInspection"]
        if not inspected["providedArtifactBytesVerified"] or inspected["classesLoaded"]:
            raise AssertionError("Artifact bytes were not verified independently of class initialization")
        rows = {row["metadataPath"]: row for row in inspected["artifacts"]}
        class_files, witnesses = 0, []
        with zipfile.ZipFile(artifacts) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if {row["metadataPath"] for row in manifest["artifacts"]} != set(rows):
                raise AssertionError("Inspector omitted provided artifact records")
            for record in manifest["artifacts"]:
                raw = archive.read("blobs/" + record["sha256"])
                if sha256(raw).hexdigest() != record["sha256"]:
                    raise AssertionError("Independent artifact digest differs")
                if not record["outputPath"].lower().endswith(".jar"):
                    continue
                with zipfile.ZipFile(io.BytesIO(raw)) as jar:
                    expected = sum(entry.filename.endswith(".class") for entry in jar.infolist())
                    if expected != rows[record["metadataPath"]]["binary"]["classFiles"]:
                        raise AssertionError("JDK stream/Python central-directory class counts differ")
                    class_files += expected
                if record["metadataPath"] in {"mods/gregtech-ce-unofficial.pw.toml", "mods/susycore.pw.toml", "mods/groovyscript.pw.toml"}:
                    witness = root / "witness.jar"
                    witness.write_bytes(raw)
                    declarations = rows[record["metadataPath"]]["binary"]["entryPointDeclarations"]
                    for declaration in declarations:
                        annotations = [a for a in declaration["annotations"] if a["type"] == "Lnet/minecraftforge/fml/common/Mod;"]
                        if not annotations:
                            continue
                        name = declaration["class"]
                        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$/]*", name):
                            raise AssertionError("Unsafe javap witness class name")
                        output = subprocess.run([str(java / "javap"), "-v", "-classpath", str(witness), name.replace("/", ".")],
                                                capture_output=True, env=environment, cwd=root, timeout=20, check=True).stdout.decode()
                        values = annotations[0]["values"]
                        for key in ("modid", "version", "dependencies"):
                            if key in values and f'{key}="{values[key]}"' not in output:
                                raise AssertionError("ASM/javap annotation value differs: " + key)
                        witnesses.append({"artifact": record["metadataPath"], "class": name, "annotationValues": values})
        if len(witnesses) != 3:
            raise AssertionError("Expected one binary Mod annotation witness for each pinned principal mod")
        wrong = json.loads(json.dumps(request)); wrong["composition"]["side"] = "server" if request["composition"]["side"] == "client" else "client"
        if invoke(wrong, 2).get("rule") != "request.shape":
            raise AssertionError("Stale side selection did not reject the old artifact bundle")
        # Each selected JAR now includes annotation-type definitions as well as
        # handler declarations. Keep requests bounded; test every original owner
        # without assuming three full binary inventories fit one protocol response.
        detail_rows = {}
        for witness in witnesses:
            detailed = json.loads(json.dumps(request)); detailed["artifactPaths"] = [witness["artifact"]]
            detail = invoke(detailed)["artifactInspection"]
            if detail["artifactBundleId"] != inspected["artifactBundleId"] or detail["uniqueClassNames"] != inspected["uniqueClassNames"]:
                raise AssertionError("Detail selection changed binary identity or coverage")
            detail_rows[witness["artifact"]] = next(row for row in detail["artifacts"] if row["metadataPath"] == witness["artifact"])
        # Lifecycle and material/item/recipe registration owners from the three
        # pinned principal mods. No mod class is loaded by javap or by Axiom.
        handler_witnesses = []
        owners = {
            "mods/gregtech-ce-unofficial.pw.toml": ["gregtech/GregTechMod", "gregtech/common/CommonProxy"],
            "mods/susycore.pw.toml": ["supersymmetry/Supersymmetry", "supersymmetry/common/CommonProxy"],
            "mods/groovyscript.pw.toml": ["com/cleanroommc/groovyscript/GroovyScript"],
        }
        with zipfile.ZipFile(artifacts) as archive:
            for metadata, classes in owners.items():
                record = detail_rows[metadata]
                witness = root / "event-witness.jar"
                witness.write_bytes(archive.read("blobs/" + record["sha256"]))
                declarations = {row["class"]: row for row in record["binary"]["eventHandlerDeclarations"]}
                for name in classes:
                    output = subprocess.run([str(java / "javap"), "-p", "-v", "-classpath", str(witness), name.replace("/", ".")],
                                            capture_output=True, env=environment, cwd=root, timeout=20, check=True).stdout.decode()
                    actual = javap_event_methods(output)
                    if not actual or name not in declarations or normalized_methods(actual) != normalized_methods(declarations[name]["methods"]):
                        raise AssertionError("ASM/javap event method declarations differ: " + name)
                    handler_witnesses.append({"artifact": metadata, "class": name, "methods": actual})
        if args.workbench_python:
            integrated = invoke(request, workbench=True)["artifactInspection"]
            if integrated != inspected:
                raise AssertionError("Installed Workbench bridge differs from independent artifact inspection")
        for key, path in (("target", target), ("artifacts", artifacts), ("target", args.target), ("artifacts", args.artifacts)):
            if file_hash(path) != identities[key]:
                raise AssertionError("Inspection changed original artifact/source bytes")
        report = {"cases": cases, "providedArtifacts": len(rows), "classFilesCompared": class_files, "uniqueClassNames": inspected["uniqueClassNames"],
                  "duplicateClassCount": inspected["duplicateClassCount"], "missingArtifacts": inspected["missingArtifacts"], "binaryWitnesses": witnesses,
                  "eventSubscriberClasses": sum(row["binary"].get("eventSubscriberClasses", 0) for row in rows.values()),
                  "eventHandlerMethods": sum(row["binary"].get("eventHandlerMethods", 0) for row in rows.values()),
                  "eventHandlerWitnesses": handler_witnesses,
                  "eventMethodsCompared": sum(len(row["methods"]) for row in handler_witnesses),
                  "artifactBundleId": inspected["artifactBundleId"], "compositionId": inspected["compositionId"], "seconds": round(time.monotonic() - started, 3),
                  "outsideCheckout": True, "workbenchBridge": bool(args.workbench_python), "classesLoaded": False, "minecraftLaunched": False,
                  "installedCompositionQualified": False}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x") as stream:
                json.dump(report, stream, indent=2, sort_keys=True); stream.write("\n")
        print(json.dumps({key: value for key, value in report.items() if key not in {"binaryWitnesses", "eventHandlerWitnesses"}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
