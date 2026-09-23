"""Strict current-format coverage for the public source lock."""

from __future__ import annotations

import copy
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
ATLAS_SOURCE = ROOT / "modules/atlas/src"
if str(ATLAS_SOURCE) not in sys.path:
    sys.path.insert(0, str(ATLAS_SOURCE))
PROFILE_SOURCE = ROOT / "profiles/packs/supersymmetry/src"
if str(PROFILE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROFILE_SOURCE))

from workbench_atlas import source_lock  # noqa: E402
from workbench_api.profiles import Profile  # noqa: E402
from workbench_profile_supersymmetry import profile  # noqa: E402


class SourceLockTests(unittest.TestCase):
    def test_supersymmetry_declares_canonical_source_lock_resource(self) -> None:
        self.assertNotEqual(
            source_lock.SOURCE_LOCK_PATH, profile().resource("source-lock")
        )
        self.assertEqual(
            source_lock.SOURCE_LOCK_PATH.read_bytes(),
            profile().resource("source-lock").read_bytes(),
        )

    def test_selected_profile_resource_relocates_lock_without_changing_identity(self) -> None:
        original = source_lock.load_source_lock()
        with tempfile.TemporaryDirectory() as temporary:
            relocated = Path(temporary) / "selected-pack" / "source-lock.json"
            relocated.parent.mkdir()
            relocated.write_bytes(source_lock.SOURCE_LOCK_PATH.read_bytes())
            owner = Profile(
                "supersymmetry", "pack", relocated.parent,
                {"source-lock": relocated.name},
            )
            with patch.object(
                source_lock,
                "profiles",
                return_value=(owner,),
            ) as admitted:
                self.assertEqual(
                    original,
                    source_lock.load_source_lock(pack_profile="supersymmetry"),
                )
                admitted.assert_called_once_with()

    def test_unavailable_selected_profile_does_not_fall_back_to_legacy_lock(self) -> None:
        with patch.object(source_lock, "profiles", return_value=()):
            with self.assertRaisesRegex(
                source_lock.SourceLockError, "no available source-lock resource"
            ):
                source_lock.load_source_lock(pack_profile="supersymmetry")

    def test_platform_cannot_claim_a_pack_source_lock(self) -> None:
        owner = Profile(
            "platform", "platform", source_lock.SOURCE_LOCK_PATH.parent,
            {"source-lock": source_lock.SOURCE_LOCK_PATH.name},
        )
        with patch.object(source_lock, "profiles", return_value=(owner,)):
            with self.assertRaisesRegex(source_lock.SourceLockError, "pack profile"):
                source_lock.load_source_lock(pack_profile="platform")

    def test_explicit_path_and_profile_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(source_lock.SourceLockError, "either"):
            source_lock.load_source_lock(
                source_lock.SOURCE_LOCK_PATH, pack_profile="supersymmetry"
            )
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            source_lock.parser().parse_args(
                ["--source-lock", "lock.json", "--pack-profile", "supersymmetry"]
            )

    def test_public_lock_is_the_only_supported_canonical_v3_shape(self) -> None:
        document = source_lock.load_source_lock()
        self.assertEqual(source_lock.FORMAT, document["format"])
        self.assertEqual(3, document["schema_version"])
        self.assertEqual(source_lock.LOCK_ID, document["lock_id"])
        self.assertEqual(source_lock.PACK_SOURCE_ID, document["pack"]["source_id"])
        self.assertEqual(
            list(source_lock.SOURCE_IDS),
            [row["source_id"] for row in document["sources"]],
        )

    def test_predecessor_shape_is_rejected(self) -> None:
        predecessor = {
            "schema_version": 2,
            "lock_id": "obsolete-source-lock",
            "snapshot_id": source_lock.PACK_SNAPSHOT_ID,
            "pack_commit": "9d3aa7ae0294bf27f0b8acbb893d61da23a06972",
            "cache_layout": ".deconstruction/sources/{source_id}/{revision}",
            "sources": [],
        }
        with self.assertRaisesRegex(source_lock.SourceLockError, "fields differ"):
            source_lock.validate_source_lock(predecessor)

    def test_sources_must_remain_complete_and_sorted(self) -> None:
        document = copy.deepcopy(source_lock.load_source_lock())
        document["sources"][0], document["sources"][1] = (
            document["sources"][1],
            document["sources"][0],
        )
        with self.assertRaisesRegex(source_lock.SourceLockError, "six current sources"):
            source_lock.validate_source_lock(document)

    def test_loader_rejects_noncanonical_encoding(self) -> None:
        document = source_lock.load_source_lock()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source-lock.json"
            path.write_text(json.dumps(document, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(
                source_lock.SourceLockError, "canonical JSON encoding"
            ):
                source_lock.load_source_lock(path)


if __name__ == "__main__":
    unittest.main()
