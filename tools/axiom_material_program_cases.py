"""Exact-base, whole-program source witnesses; never compile or execute Groovy.

These fixtures are inputs to native execution qualification, not a linter and
not an oracle for registry/property/dispatch semantics.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "modules/axiom/tests/fixtures/material-program"
PRODUCER = "groovy/material/DeveloperMaterials.groovy"
EDITS = "groovy/classes/MaterialEdits.groovy"
LISTENERS = "groovy/preInit/Materials.groovy"


def identity(files):
    rows = [{"path": name, "sha256": sha256(raw).hexdigest(), "size": len(raw)}
            for name, raw in sorted(files.items())]
    # Sort only the identity inventory. The native loader owns execution order.
    digest = sha256(json.dumps(rows, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"sha256": digest, "files": rows}


def source_acknowledgement(files):
    """Expected native acknowledgement of every exact submitted archive member."""
    inventory = identity(files)
    return {"schema": "axiom.native-source-inventory-ack.v1", "sha256": inventory["sha256"],
            "fileCount": len(inventory["files"]),
            "inventoryEncoding": "sorted-path-recursively-key-sorted-json-utf8-v1",
            "scope": "all-submitted-groovy-and-config-files"}


def matches_source_acknowledgement(observed, files):
    return (isinstance(observed, dict) and type(observed.get("fileCount")) is int
            and observed == source_acknowledgement(files))


@dataclass(frozen=True)
class Edit:
    path: str
    before: str
    after: str

    def apply(self, files):
        result = dict(files)
        old = result[self.path].decode("utf-8")
        if old.count(self.before) != 1:
            raise ValueError("acceptance edit needs one exact source anchor: " + self.path)
        result[self.path] = old.replace(self.before, self.after, 1).encode("utf-8")
        return result


def cases(root=FIXTURE):
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted((root / "groovy").rglob("*")) if p.is_file()}
    if set(files) != {PRODUCER, EDITS, LISTENERS, "groovy/runConfig.json"}:
        raise ValueError("complete acceptance program inventory changed")
    witnesses = [
        ("complete-program", ["AMPF-A01", "AMPF-A02", "AMPF-A06", "AMPF-A07"], None,
         {"aggregate": "accepted", "expectations": json.loads((root / "expectations.json").read_bytes())}),
        ("logged-components-error", ["AMPF-A04"],
         Edit(PRODUCER, ".components(Lithium, Aluminium, Silicon * 4, Oxygen * 10)",
              ".components(Lithium, 1, Aluminium, Silicon * 4, Oxygen * 10)"),
         {"aggregate": "source-error", "nativeLogContains": "Tried to use old method for material components", "nativeContinuation": True}),
        ("property-setter-error", ["AMPF-A03", "AMPF-A16"],
         Edit(EDITS, "Aluminosilicate.setMaterialRGB(0x99ccbb)",
              "Aluminosilicate.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(0)"),
         {"aggregate": "source-error", "nativeCause": "java.lang.IllegalArgumentException", "nativeMessage": "Harvest Level must be greater than zero!"}),
        ("late-registration", ["AMPF-A05"],
         Edit(LISTENERS, "MaterialEdits.apply()", "MaterialEdits.apply()\n    DeveloperMaterials.named(31003, 'developer_too_late').dust().build()"),
         {"aggregate": "source-error", "nativeLogContains": "Materials cannot be registered in the PostMaterialEvent", "absentRegistryIdentity": "supersymmetry:developer_too_late"}),
        ("unknown-addon", ["AMPF-A09"],
         # SusyFluidStorageKeys is now a real pinned input, so it can no longer
         # witness missing linkage. This deliberately absent fixture type can.
         Edit(PRODUCER, "import gregtech.api.unification.material.Material", "import unprovided.addon.MaterialRules\nimport gregtech.api.unification.material.Material"),
         {"aggregate": "incomplete", "coverageGap": "unqualified addon dependency"}),
        ("caught-unqualified-operation", ["AMPF-A10"],
         Edit(EDITS, "Titanate.addFlags(NO_SMELTING)", "Titanate.addFlags(NO_SMELTING)\n        try { Titanate.getLocalizedName() } catch (Throwable ignored) { }"),
         {"aggregate": "incomplete", "coverageGap": "unqualified localization", "stickyCoverage": True}),
        ("native-skip", ["AMPF-A22"],
         Edit(PRODUCER, "package material", "// NO_RUN\npackage material"),
         {"mustNotClaim": "complete-program-validity", "skipVisibility": True}),
        ("conflicting-package", ["AMPF-A04", "AMPF-A18"],
         Edit(PRODUCER, "package material", "package research.orthrus.axiom"),
         {"mustNotAccept": True, "nativeLogOrAdmissionFailure": "package mismatch or trusted-package shadowing"}),
        ("timeout", ["AMPF-A19"],
         Edit(PRODUCER, "log.infoMC('Registering the developer material program')", "while (true) { }"),
         {"aggregate": "incomplete", "termination": "bounded", "cleanup": "required"}),
    ]
    result = []
    for name, requirements, edit, expected in witnesses:
        candidate = edit.apply(files) if edit else dict(files)
        result.append({"name": name, "requirements": requirements, "files": candidate,
                       "baseIdentity": identity(files), "candidateIdentity": identity(candidate),
                       "expected": expected, "executionQualified": False})
    return result
