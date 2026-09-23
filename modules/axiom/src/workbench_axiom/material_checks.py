"""Saved material-program transport and native evidence, not a material interpreter.

These helpers do not write files, start processes, or infer recipe validity.
The selected source root is a complete program, never an extraction of declarations.
"""

from hashlib import sha256
from io import BytesIO
import json
import re
from pathlib import PurePosixPath
from zipfile import ZipFile, ZipInfo, ZIP_STORED, BadZipFile

from workbench_api.profile_extensions import require_profile_extension, profile_extension_identity
from .cli import _json, installation


def portable(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or path.as_posix() != value
            or any(part in {".", "..", ".git"} for part in path.parts)
            or any(c in value for c in "\\:") or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("select a portable checkout-relative material program path")
    return path


PROGRAM_DIRECTORIES = ("groovy", "config")
SOURCE_ACK_SCHEMA = "axiom.native-source-inventory-ack.v1"
SOURCE_INVENTORY_ENCODING = "sorted-path-recursively-key-sorted-json-utf8-v1"
SOURCE_INVENTORY_SCOPE = "all-submitted-groovy-and-config-files"


def _inventory_digest(rows):
    return sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def source_acknowledgement(program):
    """Expected custody identity from complete retained bytes, never a native result."""
    rows = program["files"]
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError("retained source inventory is malformed")
    paths = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}
                or not isinstance(row["path"], str) or type(row["size"]) is not int or row["size"] < 0
                or not isinstance(row["sha256"], str) or re.fullmatch("[0-9a-f]{64}", row["sha256"]) is None):
            raise ValueError("retained source inventory is malformed")
        portable(row["path"])
        if not row["path"].startswith(("groovy/", "config/")):
            raise ValueError("retained source inventory is outside saved Groovy/configuration")
        paths.append(row["path"])
    if paths != sorted(set(paths)) or _inventory_digest(rows) != program["sha256"]:
        raise ValueError("retained source inventory digest or order differs")
    return {"schema": SOURCE_ACK_SCHEMA, "sha256": program["sha256"], "fileCount": len(rows),
            "inventoryEncoding": SOURCE_INVENTORY_ENCODING, "scope": SOURCE_INVENTORY_SCOPE}


def archive_inventory(path):
    """Read every submitted ZIP entry before invoking native code; never infer from its response."""
    try:
        return _archive_inventory(path)
    except (BadZipFile, RuntimeError, NotImplementedError) as failure:
        raise ValueError("cannot verify complete material archive: " + str(failure)) from failure


def _archive_inventory(path):
    rows, seen, total, groovy_count, groovy_bytes = [], set(), 0, 0, 0
    with ZipFile(path) as archive:
        for entry in archive.infolist():
            name = entry.filename
            portable(name)
            if entry.is_dir() or name in seen or len(seen) >= 4096 or not name.startswith(("groovy/", "config/")):
                raise ValueError("material archive requires unique saved Groovy/configuration files")
            seen.add(name)
            bound = 4 * 1024**2 if name.startswith("config/") else 1024**2
            with archive.open(entry) as stream:
                raw = stream.read(bound + 1)
            total += len(raw)
            if name.startswith("groovy/"):
                groovy_count += 1
                groovy_bytes += len(raw)
            if len(raw) > bound or total > 24 * 1024**2 or groovy_count > 512 or groovy_bytes > 8 * 1024**2:
                raise ValueError("complete material program exceeds native input bounds")
            rows.append({"path": name, "size": len(raw), "sha256": sha256(raw).hexdigest()})
    rows.sort(key=lambda row: row["path"])
    return {"sha256": _inventory_digest(rows), "files": rows}


def program_snapshot(sources, root):
    """Archive complete saved Groovy and configuration under the selected root.

    Unsupported files/loaders are retained for native rejection, never omitted to
    make a partial program pass. ZIP metadata is deterministic; source bytes and
    run-config loader order are unchanged.
    """
    prefix = "" if root == "." else str(portable(root)) + "/"
    selected = {path[len(prefix):]: raw for path, raw in sources.items()
                if any(path.startswith(prefix + directory + "/") for directory in PROGRAM_DIRECTORIES)}
    if "groovy/runConfig.json" not in selected or not any(
            path.startswith("groovy/") and path.endswith(".groovy") for path in selected):
        raise ValueError("complete material program requires groovy/runConfig.json and Groovy source")
    groovy = [raw for path, raw in selected.items() if path.startswith("groovy/")]
    # The pack's configuration includes native resources, not just small .cfg
    # files. Preserve them unchanged without raising Groovy's individual bound.
    if (len(groovy) > 512 or sum(map(len, groovy)) > 8 * 1024**2
            or len(selected) > 4096 or any(len(raw) > (1024**2 if path.startswith("groovy/") else 4 * 1024**2)
                                    for path, raw in selected.items())
            or sum(map(len, selected.values())) > 24 * 1024**2):
        raise ValueError("complete material program exceeds native input bounds")
    stream, rows, paths = BytesIO(), [], {}
    with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
        for path, raw in sorted(selected.items()):
            portable(path)
            row = {"path": path, "size": len(raw), "sha256": sha256(raw).hexdigest()}
            rows.append(row)
            paths[path] = prefix + path
            info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    raw = stream.getvalue()
    # Java's sourceProgram inventory is sorted, with recursively sorted JSON keys.
    digest = _inventory_digest(rows)
    return raw, {"sha256": digest, "files": rows, "paths": paths,
                 "archiveSha256": sha256(raw).hexdigest(), "archiveSize": len(raw)}


def intent(raw):
    if len(raw) > 1024**2:
        raise ValueError("material expectations exceed one MiB")
    value = _json(raw)
    if not isinstance(value, dict) or set(value) - {"observeMaterials", "expectations"}:
        raise ValueError("saved material intent contains only observeMaterials and/or expectations")
    return value  # Java owns expectation vocabulary, types and evaluation.


def contexts(profile):
    owner = require_profile_extension("workbench.axiom_targets", profile)
    value = owner.material_contexts()
    if value.get("profile") != profile:
        raise ValueError("material contexts belong to another profile")
    return {**value, "owner": profile_extension_identity("workbench.axiom_targets", profile)}


def inputs_identity(engine_home, runtime_home, profile, context_id, platform):
    """Bind selected installed identities; execution still verifies native bytes."""
    catalog = contexts(profile)
    selected = [row for row in catalog["policy"]["contexts"] if row["id"] == context_id]
    if len(selected) != 1 or selected[0]["platformProfile"] != platform:
        raise ValueError("material context differs from selected pack/platform")
    admission = require_profile_extension("workbench.axiom_targets", profile).material_admission(context_id)
    manifest, _, _, digest = installation(engine_home)
    with (runtime_home / "runtime.json").open("rb") as stream:
        raw = stream.read(4 * 1024**2 + 1)
    if len(raw) > 4 * 1024**2:
        raise ValueError("native runtime manifest exceeds bound")
    runtime = _json(raw)
    if (runtime.get("schema") != "axiom.material-runtime.v1" or runtime.get("context") != selected[0]
            or runtime.get("contextPolicySha256") != catalog["sha256"]
            or runtime.get("admissionPolicySha256") != admission["sha256"]):
        raise ValueError("native runtime differs from installed material context policy")
    return {"engineManifestSha256": digest, "engineVersion": manifest["version"],
            "runtimeManifestSha256": sha256(raw).hexdigest(), "context": selected[0],
            "contextPolicySha256": catalog["sha256"], "admissionPolicySha256": admission["sha256"],
            "profileOwner": catalog["owner"]}


def _native_mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError("native " + name + " must be a JSON object")
    return value


def verify_native_sources(response, program, baseline=None):
    """Require native inventory evidence to refer to the exact submitted bytes."""
    response = _native_mapping(response, "response")
    body = _native_mapping(response.get("result", {}), "result")
    for name in ("sourceScope", "sourceAdmission", "execution", "bootstrap", "initialization",
                 "sourceComparison", "nativeSourceComparison", "comparison", "effectComparison"):
        if name in body:
            _native_mapping(body[name], "result." + name)
    scope = body.get("sourceScope", {})
    if "configuration" in scope:
        _native_mapping(scope["configuration"], "result.sourceScope.configuration")
    for owner, field in (("sourceAdmission", "findings"), ("execution", "diagnostics")):
        value = body.get(owner, {})
        if field in value and (not isinstance(value[field], list)
                               or any(not isinstance(row, dict) for row in value[field])):
            raise ValueError("native result." + owner + "." + field + " must contain JSON objects")
    if baseline is not None:
        if "baseline" not in body or "candidate" not in body:
            # A supervisor failure may legitimately have no paired observation.
            if (any(key in body for key in ("baseline", "candidate", "sourceProgram", "execution", "sourceAdmission", "sourceScope", "nativeOutcome"))
                    or response.get("status") not in {"request-error", "execution-error", "incomplete"}):
                raise ValueError("native material comparison is missing its observations")
            return
        verify_native_sources(body["baseline"], baseline)
        verify_native_sources(body["candidate"], program)
        for key, source in (("baseline", baseline), ("candidate", program)):
            expected = source_acknowledgement(source)
            effect = body.get("effectComparison", {})
            if key + "SourceProgram" in effect and effect[key + "SourceProgram"] != expected:
                raise ValueError("native effect comparison source inventory differs")
            for name in ("comparison", "sourceComparison", "nativeSourceComparison"):
                comparison = body.get(name, {})
                if key + "SourceSha256" in comparison and comparison[key + "SourceSha256"] != expected["sha256"]:
                    raise ValueError("native comparison source inventory differs")
        if body.get("sourceComparison", {}).get("status") in {"changed", "unchanged"}:
            if body["sourceComparison"] != saved_source_comparison(program, baseline):
                raise ValueError("saved source comparison differs from retained source inventory")
        return
    observed = body.get("sourceProgram")
    if "sourceProgram" in body:
        _native_mapping(observed, "source inventory acknowledgement")
    if observed is not None:
        expected = source_acknowledgement(program)
        if not isinstance(observed, dict) or observed.get("schema") != SOURCE_ACK_SCHEMA:
            raise ValueError("unsupported native source inventory acknowledgement schema")
        if observed != expected or type(observed.get("fileCount")) is not int:
            raise ValueError("native result source inventory acknowledgement differs from retained program")
        configuration = body.get("sourceScope", {}).get("configuration")
        if configuration is not None:
            files = [row for row in program["files"] if row["path"].startswith("config/")]
            reference = {"owner": "core-retained-material-program", "sourceProgramSha256": program["sha256"], "filesPointer": "/files"}
            if (configuration.get("inventoryReference") != reference or "inventoryPointer" in configuration
                    or type(configuration.get("fileCount")) is not int or configuration["fileCount"] != len(files)
                    or configuration.get("sha256") != _inventory_digest(files)):
                raise ValueError("native configuration source inventory reference differs from retained program")
    if observed is None and (response.get("status") not in {"request-error", "execution-error", "incomplete", "unsupported", "requires-context"}
                             or any(key in body for key in ("sourceScope", "sourceAdmission", "execution", "nativeOutcome"))):
        raise ValueError("native source outcome is missing source custody")


def saved_source_comparison(program, baseline):
    """Saved file changes are owned by Core and never assert native effect causation."""
    source_acknowledgement(program)
    source_acknowledgement(baseline)
    before = {row["path"]: row for row in baseline["files"]}
    after = {row["path"]: row for row in program["files"]}
    added, removed = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
    modified = sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
    return {"status": "changed" if added or removed or modified else "unchanged", "added": added,
            "removed": removed, "modified": modified, "owner": "core-verified-saved-inputs",
            "baselineSourceSha256": baseline["sha256"], "candidateSourceSha256": program["sha256"],
            "meaning": "saved-file-difference-not-native-effect-or-recipe-validity"}


def project_source_comparison(response, program, baseline):
    verify_native_sources(response, program, baseline)
    body = response.get("result", {})
    if baseline is not None and all(body.get(side, {}).get("result", {}).get("sourceProgram") is not None
                                    for side in ("baseline", "candidate")):
        # Keep the native source binding descriptor verbatim beside the Core projection.
        if "sourceComparison" in body:
            body["nativeSourceComparison"] = body["sourceComparison"]
        body["sourceComparison"] = saved_source_comparison(program, baseline)


def findings(response):
    """Project native diagnostics with exact result pointers, preserving precision.

    No basename matching, message parsing, error reclassification, or fabricated
    expression ranges. Shell may anchor a reported line to its retained bytes.
    """
    result = []

    def visit(envelope, side, pointer):
        body = envelope.get("result", {})
        for index, finding in enumerate(body.get("sourceAdmission", {}).get("findings", [])):
            result.append({"side": side, "pointer": pointer + f"/result/sourceAdmission/findings/{index}",
                           "channel": "source-admission", "message": finding["message"],
                           "code": finding["code"], "nativeLocation": {key: finding[key] for key in ("path", "line", "column") if key in finding}})
        for index, diagnostic in enumerate(body.get("execution", {}).get("diagnostics", [])):
            channel = diagnostic.get("channel")
            if channel is None:
                # EarlyDiagnostics captures original Log4j events before the
                # source-aware Groovy observer exists. Its trace is evidence,
                # never a source-location map.
                if (isinstance(diagnostic.get("logger"), str)
                        and isinstance(diagnostic.get("trace"), str)
                        and diagnostic.get("locationStatus") == "unlocated"
                        and not diagnostic.get("locations")):
                    channel = "log4j"
                else:
                    raise ValueError("native diagnostic has no recognized channel")
            base = {"side": side, "pointer": pointer + f"/result/execution/diagnostics/{index}",
                    "channel": channel, "message": diagnostic["message"],
                    "severity": diagnostic["severity"]}
            locations = diagnostic.get("locations", [])
            if locations:
                for location_index, location in enumerate(locations):
                    result.append({**base, "nativeLocation": location,
                                   "sourceRelationships": _source_relationships(diagnostic, location, base["pointer"]),
                                   "locationPointer": base["pointer"] + f"/locations/{location_index}"})
            else:
                result.append({**base, "nativeLocation": None})

    body = response.get("result", {})
    if "candidate" in body and "baseline" in body:
        visit(body["baseline"], "baseline", "/result/baseline")
        visit(body["candidate"], "candidate", "/result/candidate")
    else:
        visit(response, "candidate", "")
    return result


def _source_relationships(diagnostic, location, pointer):
    """Exact native frame membership, not inferred source causation or blame."""
    relationships = []
    exceptions = diagnostic.get("causality", {}).get("exceptions", [])
    for index, exception in enumerate(exceptions):
        for frame, native_location in enumerate(exception["locations"]):
            if native_location == location:
                relationships.append({"kind": "exception-frame", "exceptionIndex": index,
                    "exceptionPointer": pointer + f"/causality/exceptions/{index}",
                    "type": exception["type"], "message": exception["message"],
                    "locationPointer": pointer + f"/causality/exceptions/{index}/locations/{frame}"})
    for index, finding in enumerate(diagnostic.get("compilerFindings", [])):
        if finding.get("location") == location and "exceptionIndex" in finding:
            exception_index = finding["exceptionIndex"]
            relationships.append({"kind": "compiler-finding", "exceptionIndex": exception_index,
                "exceptionPointer": pointer + f"/causality/exceptions/{exception_index}",
                "locationPointer": pointer + f"/compilerFindings/{index}/location"})
    for index, native_location in enumerate(diagnostic.get("observationLocations", [])):
        if native_location == location:
            relationships.append({"kind": "observation-site",
                "locationPointer": pointer + f"/observationLocations/{index}"})
    return relationships
