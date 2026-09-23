#!/usr/bin/env python3

"""Integrity checks for the current experimental field guides.

The guides are editable public documentation, not frozen evidence. These
tests protect navigation, live command paths, and authority boundaries
without pinning historical wording or receipt identities.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
GUIDE_ROOT = MODULE_ROOT / "guides" / "experimental" / "worldgen-cleanmix"

EXPECTED_GUIDES = {
    "first-playable-worldgen.md",
    "inspect-gtceu-worldgen.md",
    "iterate-mega-regions-hydrology.md",
    "localize-cleanmix-failure.md",
    "observe-exact-cleanroom-worldgen-run.md",
    "observe-world-studio-with-strata.md",
    "prototype-total-replacement.md",
    "read-atlas-listener-write-chain.md",
    "run-worldgen-iteration.md",
}

PRIVATE_OR_RETIRED_MARKERS = (
    "AGENTS.md",
    "program/",
    "DP01",
    "B00",
    "external-decorator",
    "test-independent-forge-decorator",
)


class ExperimentalGuideTests(unittest.TestCase):
    def test_directory_contains_only_the_current_bounded_guide_set(self) -> None:
        actual = {path.name for path in GUIDE_ROOT.glob("*.md")}
        self.assertEqual({"README.md", *EXPECTED_GUIDES}, actual)

    def test_index_links_every_guide_once(self) -> None:
        index = (GUIDE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Status: experimental working guides", index)
        self.assertIn("## Authority boundary", index)
        for name in EXPECTED_GUIDES:
            with self.subTest(guide=name):
                self.assertEqual(1, index.count(f"]({name})"))

    def test_each_guide_declares_status_and_authority_boundary(self) -> None:
        for name in EXPECTED_GUIDES:
            with self.subTest(guide=name):
                text = (GUIDE_ROOT / name).read_text(encoding="utf-8")
                self.assertIn("Status: experimental working guide", text)
                self.assertIn("## Authority boundary", text)

    def test_documented_python_entry_points_exist(self) -> None:
        for document in GUIDE_ROOT.glob("*.md"):
            text = document.read_text(encoding="utf-8")
            paths = re.findall(r"(?m)^\s*python3\s+([^\s\\]+\.py)\b", text)
            for relative_path in paths:
                with self.subTest(document=document.name, path=relative_path):
                    self.assertTrue((REPOSITORY_ROOT / relative_path).is_file())

    def test_relative_markdown_links_resolve(self) -> None:
        documents = [MODULE_ROOT / "README.md", *sorted(GUIDE_ROOT.glob("*.md"))]
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text):
                if "://" in target:
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target).resolve().is_file())

    def test_guides_do_not_reference_private_or_retired_surfaces(self) -> None:
        for document in GUIDE_ROOT.glob("*.md"):
            text = document.read_text(encoding="utf-8")
            for marker in PRIVATE_OR_RETIRED_MARKERS:
                with self.subTest(document=document.name, marker=marker):
                    self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
