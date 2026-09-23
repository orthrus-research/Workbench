from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
import sys

sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence.runtime_surface import (  # noqa: E402
    FORMAT_VERSION,
    RuntimeSurfaceError,
    scan_runtime_surface,
    validate_runtime_surface,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(base: Path) -> Path:
    project = base / "example"
    _write(project / "settings.gradle", "rootProject.name = 'example'\n")
    _write(project / "build.gradle", "plugins { id 'java' }\n")
    _write(
        project / "src/main/resources/mcmod.info",
        json.dumps(
            [{"modid": "example", "name": "Example", "version": "1.2.3"}]
        )
        + "\n",
    )
    _write(
        project / "src/main/java/example/ExampleMod.java",
        """package example;
import net.minecraftforge.fml.common.Mod;
@Mod(modid = "example")
public final class ExampleMod {
    public static final String ID = "example:machine";
    public void registerMachine(int tier) {
        setRegistryName("example:machine");
    }
}
""",
    )
    _write(
        project / "src/main/java/example/mixin/TargetMixin.java",
        """package example.mixin;
@Mixin(targets = "other.mod.Target")
public class TargetMixin {}
""",
    )
    _write(
        project / "src/main/resources/mixins.example.json",
        json.dumps(
            {
                "package": "example.mixin",
                "mixins": ["TargetMixin"],
                "plugin": "example.mixin.Plugin",
            }
        )
        + "\n",
    )
    _write(
        project / "src/main/resources/assets/example/recipes/machine.json",
        json.dumps({"type": "example:assembly", "result": "example:machine"})
        + "\n",
    )
    _write(
        project / "src/main/resources/data/example/loot_tables/machine.json",
        "{}\n",
    )
    _write(project / "config/example.cfg", "[general]\nenabled=true\n")
    _write(
        project / "scripts/example.groovy",
        "def addMachine(name) { return 'example:machine' }\n"
        "machineTier = 4\n",
    )
    return project


class RuntimeSurfaceTests(unittest.TestCase):
    def test_hashes_exact_crlf_bytes_on_every_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _fixture(Path(temporary))
            script = project / "scripts/example.groovy"
            payload = b"machineTier = 4\r\nreturn 'example:machine'\r\n"
            script.write_bytes(payload)

            report = scan_runtime_surface(project)

        row = next(
            item
            for item in report["files"]
            if item["path"] == "scripts/example.groovy"
        )
        self.assertEqual(len(payload), row["bytes"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), row["sha256"])

    def test_discovers_nested_gradle_project_sources_from_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "pack"
            _write(root / "settings.gradle", "include ':mods:child'\n")
            _write(root / "mods/child/build.gradle", "plugins { id 'java' }\n")
            _write(
                root / "mods/child/src/main/resources/mcmod.info",
                json.dumps(
                    [{"modid": "child", "name": "Child", "version": "4.5.6"}]
                )
                + "\n",
            )
            _write(
                root / "mods/child/src/main/java/child/NestedMod.java",
                "package child;\npublic class NestedMod {}\n",
            )
            report = scan_runtime_surface(root)

        self.assertIn(
            "mods/child/src/main/java/child/NestedMod.java",
            {row["path"] for row in report["files"]},
        )
        declaration = next(
            row
            for row in report["declarations"]
            if row["kind"] == "class" and row["name"] == "child.NestedMod"
        )
        self.assertEqual(
            [
                {
                    "basis": "module mod descriptor under mods/child",
                    "mod_id": "child",
                    "state": "declared",
                    "version": "4.5.6",
                }
            ],
            declaration["owner_candidates"],
        )

    def test_scans_exact_static_runtime_surface_without_runtime_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _fixture(Path(temporary))
            first = scan_runtime_surface(project)
            second = scan_runtime_surface(project)

        self.assertEqual(first, second)
        self.assertEqual(FORMAT_VERSION, first["format"])
        validate_runtime_surface(first)
        self.assertTrue(first["coverage"]["complete"])
        declarations = first["declarations"]
        by_kind: dict[str, list[dict[str, object]]] = {}
        for row in declarations:
            by_kind.setdefault(row["kind"], []).append(row)
        self.assertTrue(
            any(row["name"] == "example.ExampleMod" for row in by_kind["class"])
        )
        self.assertTrue(
            any(
                row["name"] == "example.ExampleMod#registerMachine"
                for row in by_kind["method"]
            )
        )
        self.assertTrue(
            any(row["name"] == "example:machine" for row in by_kind["registry-name"])
        )
        self.assertTrue(
            any(row["name"] == "example:machine" for row in by_kind["recipe"])
        )
        self.assertTrue(
            any(row["name"] == "example:machine" for row in by_kind["loot-table"])
        )
        self.assertTrue(
            any(row["name"] == "example.mixin.TargetMixin" for row in by_kind["mixin"])
        )
        self.assertTrue(
            any(
                any(
                    identity["kind"] == "groovy-key"
                    and identity["value"] == "machineTier"
                    for identity in row["identities"]
                )
                for row in by_kind["groovy-key"]
            )
        )
        self.assertTrue(
            any(
                any(
                    identity["kind"] == "config-key"
                    and identity["value"] == "enabled"
                    for identity in row["identities"]
                )
                for row in by_kind["config-key"]
            )
        )
        self.assertTrue(
            all(
                row["state"] in {"declared", "static-possible"}
                for row in declarations
            )
        )

    def test_limits_and_skipped_symlinks_remain_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _fixture(Path(temporary))
            outside = Path(temporary) / "secret.java"
            outside.write_text("class Secret {}\n", encoding="utf-8")
            (project / "src/main/java/example/Linked.java").symlink_to(outside)
            report = scan_runtime_surface(project, max_files=2)

        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(2, report["coverage"]["scanned_files"])
        self.assertNotIn(
            "src/main/java/example/Linked.java",
            {row["path"] for row in report["files"]},
        )
        self.assertIn(
            {"path": "*", "reason": "file-count-limit"},
            report["coverage"]["skipped"],
        )

    def test_validator_rejects_stale_identity_and_unknown_file_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = scan_runtime_surface(_fixture(Path(temporary)))
        stale = deepcopy(report)
        stale["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeSurfaceError, "identity"):
            validate_runtime_surface(stale)
        dangling = deepcopy(report)
        dangling["declarations"][0]["declaration"]["path"] = "missing.java"
        with self.assertRaisesRegex(RuntimeSurfaceError, "unknown file"):
            validate_runtime_surface(dangling)


if __name__ == "__main__":
    unittest.main()
