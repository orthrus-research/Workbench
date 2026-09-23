from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


SUITE = Path(__file__).resolve().parents[5]
MODULE_PATH = (
    SUITE
    / "profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/quest_for_process.py"
)
SPEC = importlib.util.spec_from_file_location("quest_for_process", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
quest_for_process = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quest_for_process)


class QuestForProcessTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        quests = root / "config/betterquesting/DefaultQuests/Quests/5"
        language = root / "config/betterquesting/resources/supersymmetry/lang"
        quests.mkdir(parents=True)
        language.mkdir(parents=True)

        def quest(quest_id: int, prerequisites: list[int]) -> bytes:
            joined = ",\r\n".join(f"    {value}" for value in prerequisites)
            prerequisite_block = f"\r\n{joined}\r\n  " if prerequisites else ""
            types = ",\r\n".join("    1" for _ in prerequisites)
            types_block = f"\r\n{types}\r\n  " if prerequisites else ""
            return (
                "{\r\n"
                f'  "preRequisiteTypes:7": [{types_block}],\r\n'
                f'  "preRequisites:11": [{prerequisite_block}],\r\n'
                '  "properties:10": {\r\n'
                '    "betterquesting:10": {\r\n'
                f'      "desc:8": "susy.quest.db.{quest_id}.desc",\r\n'
                f'      "name:8": "susy.quest.db.{quest_id}.title"\r\n'
                "    }\r\n"
                "  },\r\n"
                f'  "questID:3": {quest_id},\r\n'
                '  "tasks:9": {}\r\n'
                "}"
            ).encode()

        (quests / "0.json").write_bytes(
            b'{\r\n  "properties:10": {\r\n'
            b'    "betterquesting:10": {\r\n'
            b'      "desc:8": "susy.quest.db.0.desc",\r\n'
            b'      "name:8": "susy.quest.db.0.title"\r\n'
            b"    }\r\n  },\r\n"
            b'  "questID:3": 0,\r\n  "tasks:9": {}\r\n}'
        )
        (quests / "100.json").write_bytes(quest(100, [200]))
        (quests / "200.json").write_bytes(quest(200, []))
        (quests / "300.json").write_bytes(quest(300, []))
        (language / "en_us.lang").write_bytes(
            b"susy.quest.db.100.title=Old title\r\n"
            b"susy.quest.db.100.desc=Old description\r\n"
            b"susy.quest.db.200.title=Second\r\n"
            b"susy.quest.db.200.desc=Second description\r\n"
            b"susy.quest.db.300.title=Third\r\n"
            b"susy.quest.db.300.desc=Third description\r\n"
        )
        return root

    def test_discovers_localized_quest_owners_with_bounded_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))

            result = quest_for_process.discover_quest_for_process_options(
                workspace,
                query="old TITLE",
                limit=2,
            )

            self.assertEqual(
                "workbench-supersymmetry-quest-for-process-options-v1",
                result["format"],
            )
            self.assertEqual(4, result["total_count"])
            self.assertEqual(1, result["matched_count"])
            self.assertEqual(1, result["returned_count"])
            self.assertFalse(result["truncated"])
            self.assertEqual(
                {
                    "current_prerequisites": [
                        {"quest_id": 200, "requirement_type": "IMPLICIT"}
                    ],
                    "localization_state": "resolved",
                    "localized_title": "Old title",
                    "path": (
                        "config/betterquesting/DefaultQuests/Quests/5/100.json"
                    ),
                    "quest_id": 100,
                    "title_key": "susy.quest.db.100.title",
                },
                result["options"][0],
            )
            self.assertFalse(result["authority_boundary"]["mutation_authorized"])

            missing = quest_for_process.discover_quest_for_process_options(
                workspace,
                query="0",
            )
            self.assertIsNone(missing["options"][0]["localized_title"])
            self.assertEqual(
                "missing", missing["options"][0]["localization_state"]
            )

    def test_quest_owner_discovery_limits_results_and_rejects_bad_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            result = quest_for_process.discover_quest_for_process_options(
                workspace,
                limit=2,
            )
            self.assertEqual(4, result["matched_count"])
            self.assertEqual(2, result["returned_count"])
            self.assertTrue(result["truncated"])

            for kwargs in ({"limit": 0}, {"query": "\n"}):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(quest_for_process.QuestForProcessError):
                        quest_for_process.discover_quest_for_process_options(
                            workspace,
                            **kwargs,
                        )

    def test_renders_existing_prerequisite_and_localization_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            before = (workspace / "config/betterquesting/DefaultQuests/Quests/5/100.json").read_bytes()
            result = quest_for_process.render_quest_for_process_update(
                workspace,
                quest_id=100,
                add_prerequisite_id=300,
                requirement_type="IMPLICIT",
                title="Gas Atomizer",
                description="Requires the upstream process.",
            )

            self.assertEqual("source-ready-runtime-unverified", result["state"])
            self.assertEqual(
                ["quest-definition", "quest-localization"],
                [row["role"] for row in result["operations"]],
            )
            for operation in result["operations"]:
                self.assertNotIn(b"\n", operation["content"].replace(b"\r\n", b""))
            self.assertIn(b"    300\r\n", result["operations"][0]["content"])
            self.assertIn(
                b"susy.quest.db.100.title=Gas Atomizer\r\n",
                result["operations"][1]["content"],
            )
            self.assertEqual(
                before,
                (workspace / "config/betterquesting/DefaultQuests/Quests/5/100.json").read_bytes(),
            )
            self.assertEqual(4, result["evidence"]["prerequisite_graph"]["file_count"])

    def test_rejects_dangling_duplicate_self_and_new_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            for prerequisite, message in (
                (999, "does not exist"),
                (200, "already exists"),
                (100, "cannot require itself"),
            ):
                with self.subTest(prerequisite=prerequisite):
                    with self.assertRaisesRegex(
                        quest_for_process.QuestForProcessError, message
                    ):
                        quest_for_process.render_quest_for_process_update(
                            workspace,
                            quest_id=100,
                            add_prerequisite_id=prerequisite,
                        )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError, "would create a quest cycle"
            ):
                quest_for_process.render_quest_for_process_update(
                    workspace,
                    quest_id=200,
                    add_prerequisite_id=100,
                )

    def test_rejects_mixed_line_endings_missing_keys_and_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            language = workspace / "config/betterquesting/resources/supersymmetry/lang/en_us.lang"
            language.write_bytes(language.read_bytes() + b"mixed=bad\n")
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError, "mixes LF and CRLF"
            ):
                quest_for_process.render_quest_for_process_update(
                    workspace, quest_id=100, title="New"
                )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            language = workspace / "config/betterquesting/resources/supersymmetry/lang/en_us.lang"
            language.write_bytes(
                language.read_bytes().replace(
                    b"susy.quest.db.100.title=Old title\r\n", b""
                )
            )
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError, "absent or duplicated"
            ):
                quest_for_process.render_quest_for_process_update(
                    workspace, quest_id=100, description="New"
                )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError, "requires a prerequisite"
            ):
                quest_for_process.render_quest_for_process_update(
                    workspace, quest_id=100
                )

    def test_supports_missing_requirement_arrays_and_requires_exact_typed_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            quest = workspace / "config/betterquesting/DefaultQuests/Quests/5/200.json"
            quest.write_bytes(
                quest.read_bytes().replace(
                    b'  "preRequisiteTypes:7": [],\r\n',
                    b"",
                )
            )
            result = quest_for_process.render_quest_for_process_update(
                workspace,
                quest_id=200,
                add_prerequisite_id=300,
            )
            self.assertIn(
                b'  "preRequisiteTypes:7": [\r\n    1\r\n  ],\r\n',
                result["operations"][0]["content"],
            )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            quest = workspace / "config/betterquesting/DefaultQuests/Quests/5/200.json"
            quest.write_bytes(
                quest.read_bytes()
                .replace(b'  "preRequisiteTypes:7": [],\r\n', b"")
                .replace(b'  "preRequisites:11": [],\r\n', b"")
            )
            result = quest_for_process.render_quest_for_process_update(
                workspace,
                quest_id=200,
                add_prerequisite_id=300,
            )
            self.assertTrue(
                result["operations"][0]["content"].startswith(
                    b'{\r\n  "preRequisiteTypes:7": ['
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            quest = workspace / "config/betterquesting/DefaultQuests/Quests/5/300.json"
            quest.write_bytes(quest.read_bytes().replace(b'"questID:3"', b'"questID:8"'))
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError,
                "exactly one questID:3",
            ):
                quest_for_process.render_quest_for_process_update(
                    workspace,
                    quest_id=100,
                    title="New title",
                )

    def test_offline_validator_rejects_non_blueprint_bytes_and_discloses_graph_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            result = quest_for_process.render_quest_for_process_update(
                workspace,
                quest_id=100,
                add_prerequisite_id=300,
            )
            self.assertFalse(
                result["evidence"]["source_checks"]["new_edge_creates_cycle"]
            )
            self.assertTrue(
                any("pre-existing quest graph health" in row for row in result["limitations"])
            )
            quest_path = result["evidence"]["quest_path"]
            before = {
                quest_path: (workspace / quest_path).read_bytes(),
                quest_for_process.QUEST_LANGUAGE.as_posix(): (
                    workspace / quest_for_process.QUEST_LANGUAGE
                ).read_bytes(),
            }
            forged = deepcopy(result)
            arbitrary = b'{\r\n  "questID:3": 100\r\n}'
            forged["operations"][0]["content"] = arbitrary
            forged["operations"][0]["content_sha256"] = hashlib.sha256(
                arbitrary
            ).hexdigest()
            forged["operations"][0]["content_size"] = len(arbitrary)
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError,
                "do not implement",
            ):
                quest_for_process.validate_quest_for_process_render(
                    forged,
                    before_bytes=before,
                )

    def test_rejects_symlinked_workspace_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = self._workspace(root / "external")
            workspace = root / "workspace"
            workspace.mkdir()
            try:
                os.symlink(external / "config", workspace / "config")
            except OSError as exc:  # pragma: no cover - platform capability
                self.skipTest(f"symbolic links unavailable: {exc}")
            with self.assertRaisesRegex(
                quest_for_process.QuestForProcessError,
                "symbolic link",
            ):
                quest_for_process.render_quest_for_process_update(
                    workspace,
                    quest_id=100,
                    title="New title",
                )


if __name__ == "__main__":
    unittest.main()
