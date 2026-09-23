from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "validate_public_tree.py"
SPEC = importlib.util.spec_from_file_location("validate_public_tree", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicTreePolicyTests(unittest.TestCase):
    def test_agent_markdown_variants_are_rejected_in_nested_exports(self) -> None:
        for name in ("agents.md", "AGENTS.local.md", "CLAUDE.team.md",
                     "codex.private.md", "GEMINI.LOCAL.MD", "SKILL.md",
                     "skills/skill.md", "review.agent.md", ".SKILLS/a/README.md"):
            with self.subTest(name=name):
                self.assertIsNotNone(MODULE.private_reason("nested/" + name))
        self.assertIsNone(MODULE.private_reason("docs/guides/atlas-mvp.md"))
        self.assertIsNone(MODULE.private_reason("instrumentation/java-agent/README.md"))

    def test_gitignore_excludes_agent_guidance_and_generated_delivery_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            ignored = ["nested/AGENTS.local.md", "nested/agents.md", "nested/SKILL.md",
                       "tools/review.agent.md", ".skills/review/README.md",
                       "nested/CLAUDE.team.md", ".workbench/exports/scan.zip",
                       "build.whl", "runtime.tar.xz", ".envrc", ".env.private"]
            result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                                    cwd=root, input="\n".join(ignored) + "\n",
                                    text=True, capture_output=True, check=True)
            self.assertEqual(ignored, result.stdout.splitlines())
            public = ["docs/guides/atlas-mvp.md", "LICENSE", ".env.example",
                      "profiles/platforms/cleanroom/fixtures/cleanmix-handler-regression/src/main/java/dev/workbench/cleanmixhandler/target/Example.java"]
            result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                                    cwd=root, input="\n".join(public) + "\n",
                                    text=True, capture_output=True)
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertEqual("", result.stdout)

    def test_rejects_provider_adapters_and_coordination_roots(self) -> None:
        self.assertIsNotNone(MODULE.private_reason("AGENTS.md"))
        self.assertIsNotNone(
            MODULE.private_reason("modules/atlas/program/README.md")
        )
        self.assertIsNotNone(
            MODULE.private_reason(
                "profiles/packs/supersymmetry/program/projects/active/README.md"
            )
        )
        self.assertIsNotNone(
            MODULE.private_reason(".github/prompts/release.md")
        )
        self.assertIsNotNone(
            MODULE.private_reason("nested/component/AGENTS.md")
        )
        self.assertIsNotNone(
            MODULE.private_reason(".github/agents/release.agent.md")
        )
        self.assertIsNotNone(
            MODULE.private_reason("tools/.continue/config.json")
        )
        self.assertIsNotNone(MODULE.private_reason("JUNIE.md"))
        self.assertIsNotNone(MODULE.private_reason(".gemini/settings.json"))
        self.assertIsNotNone(
            MODULE.private_reason(".github/copilot/review-instructions.md")
        )

    def test_rejects_internal_task_ledger_chain(self) -> None:
        self.assertIsNotNone(
            MODULE.private_reason(
                "modules/workbench-shell/data/developer-product-v2-task-ledger-v2.json"
            )
        )
        self.assertIsNotNone(
            MODULE.private_reason(
                "packaging/portable/development-v4-dp01-ledger-source-snapshot-v1.json"
            )
        )
        self.assertIsNotNone(
            MODULE.private_reason(
                "modules/atlas/contracts/atlas-m4-retained-evidence-closure-handoff-v1.md"
            )
        )
        self.assertIsNotNone(
            MODULE.private_reason(
                "modules/atlas/schemas/atlas-m4-retained-evidence-closure-v1.schema.json"
            )
        )
        self.assertIsNotNone(
            MODULE.private_reason("packaging/release/suite/README.md")
        )

    def test_does_not_confuse_product_instrumentation_with_coordination(self) -> None:
        self.assertIsNone(
            MODULE.private_reason("tests/fixtures/java-agent/agent_id.json")
        )
        self.assertIsNone(
            MODULE.private_reason("modules/workbench-shell/src/agent_status.py")
        )
        self.assertIsNone(MODULE.private_reason(".github/workflows/validate.yml"))

    def test_rejects_internal_milestone_ids_in_public_markdown(self) -> None:
        for value in (
            "ATLAS-M8-G01",
            "BLUEPRINTS-M2-A01",
            "CRUCIBLE-M4-S01",
            "WORKBENCH-SHELL-RFC01-H01",
            "WORKBENCH-SHELL-FS01",
            "SUPERSYMMETRY-P1-Q01",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(
                    MODULE.private_markdown_reason(
                        "modules/example/README.md",
                        f"Internal status: {value}",
                    )
                )

    def test_allows_public_contract_ids(self) -> None:
        self.assertIsNone(
            MODULE.private_markdown_reason(
                "modules/blueprints/README.md",
                "Contract: BLUEPRINTS-EXECUTABLE-ENGINE-V1",
            )
        )

    def test_source_lock_documentation_has_no_coordination_exemption(self) -> None:
        self.assertIsNotNone(
            MODULE.private_markdown_reason(
                "profiles/packs/supersymmetry/source-locks/legacy/README.md",
                "Internal status: ATLAS-M8-G01",
            )
        )

    def test_filesystem_scan_prunes_generated_caches(self) -> None:
        self.assertIn("node_modules", MODULE.GENERATED_DIRECTORY_NAMES)
        self.assertIn(".vscode-test", MODULE.GENERATED_DIRECTORY_NAMES)
        self.assertNotIn("program", MODULE.GENERATED_DIRECTORY_NAMES)
        self.assertNotIn(".agents", MODULE.GENERATED_DIRECTORY_NAMES)

    def test_source_scan_uses_the_candidate_worktree_not_deleted_index_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "AGENTS.md").write_text("private\n", encoding="utf-8")
            (root / "README.md").write_text("public\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "AGENTS.md", "README.md"],
                check=True,
            )
            (root / "AGENTS.md").unlink()
            (root / "new.md").write_text("public\n", encoding="utf-8")

            self.assertEqual(
                ["README.md", "new.md"],
                MODULE._tracked_paths(root),
            )


if __name__ == "__main__":
    unittest.main()
