"""Source authority imports and explicit locks do not need profile resources."""

import hashlib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from workbench_atlas import infrastructure_source_authorities as authorities


ROOT = Path(__file__).resolve().parents[3]


class InfrastructureSourceAuthorityBoundaryTests(unittest.TestCase):
    def test_selected_profile_lock_is_admitted_through_resource_api(self):
        from workbench_atlas import source_lock
        from workbench_api.profiles import Profile

        owner = Profile(
            "supersymmetry", "pack", source_lock.SOURCE_LOCK_PATH.parent,
            {"source-lock": source_lock.SOURCE_LOCK_PATH.name},
        )

        with patch.object(
            source_lock,
            "profiles",
            return_value=(owner,),
        ), redirect_stdout(StringIO()):
            self.assertEqual(
                0, authorities.main(["--pack-profile", "supersymmetry"])
            )
        with patch.object(source_lock, "profiles", return_value=()), \
                redirect_stderr(StringIO()) as errors:
            self.assertEqual(
                1, authorities.main(["--pack-profile", "supersymmetry"])
            )
            self.assertIn("no available source-lock resource", errors.getvalue())
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(
                1,
                authorities.main(
                    ["--pack-profile", "supersymmetry", "--verify-files"]
                ),
            )
            self.assertIn("requires --source-root", errors.getvalue())

    def test_import_and_argument_parsing_do_not_load_default_profile_lock(self):
        program = r'''
from pathlib import Path
import sys
from unittest.mock import patch
root = Path(sys.argv[1])
sys.path[:0] = [str(root / "api/src"), str(root / "modules/atlas/src")]
from workbench_atlas import source_lock
with patch.object(source_lock, "load_source_lock", side_effect=AssertionError("unexpected profile read")):
    from workbench_atlas import infrastructure_source_authorities as authorities
    assert authorities._parser().parse_args([]).pack_root is None
'''
        result = subprocess.run(
            [sys.executable, "-I", "-c", program, str(ROOT)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_file_verification_uses_selected_lock_or_explicit_pack_root(self):
        document = authorities.load_authority_registry()
        revision = "f" * 40
        document["pack_commit"] = revision
        data = b"source authority fixture\n"
        for citation in document["citations"]:
            if citation["source_id"] == "SRC-PACK":
                citation["revision"] = revision
            citation.update(
                anchor="source authority fixture", line_start=1, line_end=1,
                file_sha256=hashlib.sha256(data).hexdigest(),
            )
        selected_lock = {
            "pack": {"revision": revision, "snapshot_id": document["snapshot_id"]},
            "lock_id": document["source_lock_id"],
            "sources": [
                {"source_id": row["source_id"], "revision": row["revision"]}
                for row in document["citations"] if row["source_id"] != "SRC-PACK"
            ],
        }
        default_source_root = authorities.atlas_source_root()
        for supplied, source_root in (
            (None, default_source_root),
            (Path("/explicit/pack"), default_source_root),
            (None, Path("/selected/source-cache")),
        ):
            with self.subTest(pack_root=supplied, source_root=source_root), patch.object(
                authorities.source_lock, "load_source_lock",
                side_effect=AssertionError("unexpected default profile read"),
            ), patch.object(Path, "read_bytes", return_value=data), patch.object(
                authorities, "_pack_file_at_revision", return_value=data,
            ) as pack_read:
                result = authorities.verify_authority_sources(
                    document, selected_lock, source_root=source_root,
                    pack_root=supplied,
                )
                self.assertGreater(result["verified_file_count"], 0)
                expected = supplied or source_root / "SRC-PACK" / revision
                self.assertTrue(pack_read.called)
                self.assertTrue(all(call.args[0] == expected for call in pack_read.call_args_list))


if __name__ == "__main__":
    unittest.main()
