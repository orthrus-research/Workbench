"""Own the selected platform input policy. Library and loader semantics belong to Java."""

from hashlib import sha256
import json
import platform
from . import profile

PROFILE_API_VERSION = 1


def target_policy():
    raw = profile().resource("runtime-toolchain").read_bytes()
    return {"profile": "cleanroom", "sha256": sha256(raw).hexdigest(), "policy": json.loads(raw)}


def jvm_policy():
    """Own exact Axiom JVM bytes, without claiming installed-pack qualification."""
    system, machine = platform.system(), platform.machine().lower()
    if system not in ('Linux', 'Windows') or machine not in ('amd64', 'x86_64'):
        raise ValueError('Axiom requires a selected Linux x64 or Windows x64 JVM policy')
    resource = 'axiom-jvm-windows-x64' if system == 'Windows' else 'axiom-jvm'
    raw = profile().resource(resource).read_bytes()
    return {"profile": "cleanroom", "sha256": sha256(raw).hexdigest(), "policy": json.loads(raw)}


def preparation_inputs():
    """Select original SERVER inputs for Core preparation, without native execution."""
    selected = profile()
    root_raw = selected.resource("axiom-native-root").read_bytes()
    library_raw = selected.resource("axiom-native-libraries").read_bytes()
    root, library = json.loads(root_raw), json.loads(library_raw)
    if (root.get("schema") != "axiom.native-root-class-space-policy.v1"
            or root.get("profile") != "cleanroom" or root.get("side") != "SERVER"
            or root.get("inputStage") != "raw-original-artifacts"
            or library.get("schema") != "axiom.native-identity-runtime.v1"
            or library.get("profile") != "cleanroom"
            or root.get("libraryPolicy") != "native-identity-runtime.json"
            or root.get("libraryPolicySha256") != sha256(library_raw).hexdigest()
            or root.get("cleanroomRevision") != library.get("cleanroomRevision")):
        raise ValueError("Native SERVER preparation policy binding differs")
    fields = ("path", "url", "sha256", "size")
    if ([row.get("role") for row in root["inputs"]] != ["cleanroom-universal", "minecraft-server"]
            or any(root["inputs"][0][key] != library["inputs"][0][key] for key in fields)):
        raise ValueError("Native SERVER preparation root artifacts differ")
    # Library inputs include a client image for an older scope. Root inputs own
    # this SERVER selection; prepared images are not acquisition dependencies.
    artifacts = [{key: row[key] for key in fields} for row in root["inputs"] + library["libraries"]]
    if len({row["path"] for row in artifacts}) != len(artifacts):
        raise ValueError("Native SERVER preparation artifact paths are duplicated")
    return {
        "schema": "axiom.native-input-preparation.v1",
        "profile": "cleanroom",
        "side": "server",
        "inputStage": "raw-original-artifacts",
        "policySha256": {
            "native-root-class-space.json": sha256(root_raw).hexdigest(),
            "native-identity-runtime.json": sha256(library_raw).hexdigest(),
        },
        "artifacts": artifacts,
    }


def assembly_policy():
    """Expose the same verified original SERVER selection and classpath order."""
    requirements = preparation_inputs()
    selected = profile()
    resources = {"native-root-class-space.json": selected.resource("axiom-native-root").read_bytes(),
                 "native-identity-runtime.json": selected.resource("axiom-native-libraries").read_bytes()}
    if any(sha256(resources[name]).hexdigest() != digest
           for name, digest in requirements["policySha256"].items()):
        raise ValueError("Platform assembly policy changed during selection")
    return {"profile": "cleanroom", "root": json.loads(resources["native-root-class-space.json"]),
            "libraries": json.loads(resources["native-identity-runtime.json"]),
            "sourceInputs": {"profiles/platforms/cleanroom/" + name: sha256(raw).hexdigest()
                             for name, raw in resources.items()}}
