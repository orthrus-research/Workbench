"""Default validation invocation results use Core's revisioned custody."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation"))
sys.path.insert(0, str(ROOT / "api/src"))
sys.path.insert(0, str(ROOT / "core/src"))

import invocation
from core_run_custody import open_validation_invocation
from workbench_api.host_filesystem import DurableRecordError
from workbench_core import durable_records
from workbench_core.storage.registered import ResourceCatalog
from workbench_core import user_config_home
import workbench_core.validation_invocation_records as core_invocations


RUN_ID = "20260926T120000000000Z-1234abcd"


class InvocationCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.suite = self.home / "suite"
        self.suite.mkdir()
        self.config = self.home / "config"
        selected_home = patch.object(
            user_config_home, "default_user_config_home", return_value=self.config,
        )
        selected_home.start()
        self.addCleanup(selected_home.stop)

    def _new(self, *, result: Path | None = None) -> invocation.Invocation:
        with patch.object(invocation, "new_run_id", return_value=RUN_ID):
            return invocation.Invocation(self.suite, result, ("preflight", "python"))

    @property
    def _default_path(self) -> Path:
        return self.suite / ".workbench/validation/invocations" / f"{RUN_ID}.json"

    def test_default_revisions_keep_v1_bytes_uri_and_registered_store(self) -> None:
        result = self._new()
        self.assertEqual(self._default_path, result.path)
        initial = result.path.read_bytes()
        self.assertEqual(
            (json.dumps(result.document, indent=2, sort_keys=True) + "\n").encode(),
            initial,
        )
        with result:
            with result.phase("preflight"):
                result.document["source_fingerprint"] = "sha256:source"
                result.write()
            with result.phase("python"):
                pass
        terminal = json.loads(result.path.read_text(encoding="utf-8"))
        self.assertEqual("workbench-validation-invocation-v1", terminal["format"])
        self.assertEqual("passed", terminal["state"])
        self.assertEqual(RUN_ID, terminal["run_id"])
        rows = ResourceCatalog(self.config).inventory(workspace=self.suite)["record_stores"]
        self.assertEqual(
            [("validation-invocations-v1", str(self._default_path.parent))],
            [(row["family"], row["path"]) for row in rows],
        )

    def test_prior_failed_result_is_never_overwritten_by_same_run_id(self) -> None:
        result = self._new()
        with self.assertRaisesRegex(RuntimeError, "broken preflight"):
            with result:
                with result.phase("preflight"):
                    raise RuntimeError("broken preflight")
        before = result.path.read_bytes()
        self.assertEqual("failed", json.loads(before)["state"])
        with self.assertRaises(DurableRecordError) as refusal:
            self._new()
        self.assertEqual("collision", refusal.exception.code)
        self.assertEqual(before, result.path.read_bytes())

    def test_changed_or_replaced_same_byte_revision_refuses_without_overwrite(self) -> None:
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                run_id = RUN_ID if not replaced else RUN_ID + "-copy"
                with patch.object(invocation, "new_run_id", return_value=run_id):
                    result = invocation.Invocation(self.suite, None, ("preflight", "python"))
                original = result.path.read_bytes()
                if replaced:
                    displaced = result.path.with_suffix(".old")
                    result.path.rename(displaced)
                    result.path.write_bytes(original)
                    result.path.chmod(0o600)
                    before = original
                else:
                    result.path.write_bytes(b"{\"changed\":true}\n")
                    before = result.path.read_bytes()
                with self.assertRaises(DurableRecordError) as refusal:
                    result.write()
                self.assertEqual("changed", refusal.exception.code)
                self.assertEqual(before, result.path.read_bytes())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_hard_exit_before_first_link_leaves_stage_and_refuses_retry(self) -> None:
        writer = open_validation_invocation(
            self.suite, RUN_ID, configuration_home=self.config,
        )
        original_link = durable_records.os.link

        def exit_before_link(source: Path, destination: Path, *args: object, **kwargs: object):
            if destination == writer.path:
                os._exit(71)
            return original_link(source, destination, *args, **kwargs)

        child = os.fork()
        if child == 0:
            with patch.object(durable_records.os, "link", side_effect=exit_before_link):
                writer.write(b"{\"state\":\"running\"}\n")
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        self.assertFalse(writer.path.exists())
        stages = list(writer.path.parent.glob(f".{writer.path.name}.*"))
        self.assertEqual(1, len(stages))
        before = stages[0].read_bytes()
        with self.assertRaises(DurableRecordError) as refusal:
            writer.write(b"{\"state\":\"running\"}\n")
        self.assertEqual("incomplete", refusal.exception.code)
        self.assertEqual(before, stages[0].read_bytes())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_hard_exit_after_revision_retains_new_bytes_and_refuses_stale_writer(self) -> None:
        result = self._new()
        result.document["source_fingerprint"] = "sha256:after-revision"
        replacement = (json.dumps(result.document, indent=2, sort_keys=True) + "\n").encode()
        original_replace = core_invocations.replace_private_bytes

        def exit_after_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            os._exit(71)

        child = os.fork()
        if child == 0:
            with patch.object(core_invocations, "replace_private_bytes", side_effect=exit_after_replace):
                result.write()
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        self.assertEqual(replacement, result.path.read_bytes())
        with self.assertRaises(DurableRecordError) as refusal:
            result.write()
        self.assertEqual("changed", refusal.exception.code)
        self.assertEqual(replacement, result.path.read_bytes())

    def test_explicit_result_keeps_fresh_arbitrary_path_contract(self) -> None:
        explicit = self.home / "known-result.json"
        result = self._new(result=explicit)
        self.assertEqual(explicit, result.path)
        self.assertEqual("running", json.loads(explicit.read_bytes())["state"])
        with self.assertRaisesRegex(RuntimeError, "intentional failure"):
            with result:
                with result.phase("preflight"):
                    raise RuntimeError("intentional failure")
        retained = explicit.read_bytes()
        self.assertEqual("failed", json.loads(retained)["state"])
        with self.assertRaises(FileExistsError):
            self._new(result=explicit)
        self.assertEqual(retained, explicit.read_bytes())
        self.assertFalse(self.config.exists())

    def test_explicit_result_cannot_bypass_core_inside_invocation_store(self) -> None:
        for target in (
            self._default_path.parent / "known.json",
            self._default_path.parent / "nested" / "known.json",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(OSError, "Core-owned invocations"):
                    self._new(result=target)
                self.assertFalse(target.exists())
        self.assertFalse(self._default_path.parent.exists())
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
