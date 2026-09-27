"""Core's fixed Feature Change session-owner child and restart custody."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.durable_resources import DurableResourceError
from workbench_api.host_filesystem import DurableRecordError
from workbench_api.record_stores import (
    SessionOwnerAllocationError, SessionOwnerSetupIntent, record_store_scope,
    session_owner_scope,
)
from workbench_core.storage.record_stores import CoreRecordStores


FAMILY = "feature-change-session-context-v1"


class SessionOwnerAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "suite"
        self.workspace.mkdir()
        self.environment = patch.dict(
            os.environ, {"WORKBENCH_STATE_ROOT": str(self.root / "state")},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.root / "config",
            owner_id="workbench-shell",
        )
        self.scope = record_store_scope(self.provider)
        self.scope.__enter__()
        self.addCleanup(lambda: self.scope.__exit__(None, None, None))

    @staticmethod
    def _session(digit: str) -> str:
        return "work-session-v2-" + digit * 32

    @staticmethod
    def _intent(request_digest: str = "a") -> SessionOwnerSetupIntent:
        return SessionOwnerSetupIntent(
            session_record_id="session-record:test",
            session_record_uri="file:///tmp/session.json",
            session_record_sha256="sha256:" + "b" * 64,
            runtime_config_uri="file:///tmp/runtime.json",
            runtime_config_sha256="sha256:" + "c" * 64,
            setup_request_sha256="sha256:" + request_digest * 64,
            workspace_uri="file:///tmp/project",
        )

    def _created(self, digit: str):
        session_id = self._session(digit)
        with session_owner_scope(FAMILY, self.workspace, session_id, create=True) as owner:
            reference = owner.reference
            reference.state_root.mkdir(mode=0o700)
            start = reference.path / "start-result-v1.json"
            start.write_bytes(b"{}\n")
            start.chmod(0o600)
            owner.record_started(b"{}\n")
        return reference

    def test_create_restart_and_nested_open_keep_exact_session_identity(self) -> None:
        first = self._created("a")
        with session_owner_scope(FAMILY, self.workspace, first.session_id, create=False) as held:
            self.assertEqual(first, held.verify_started())
            second = self._created("b")
            self.assertNotEqual(first.allocation_id, second.allocation_id)
            self.assertEqual(first, held.verify_started())
        with session_owner_scope(FAMILY, self.workspace, second.session_id, create=False) as held:
            self.assertEqual(second, held.verify_started())

    def test_setup_intent_pins_started_readback_and_exact_request(self) -> None:
        session_id = self._session("3")
        intent = self._intent()
        with session_owner_scope(FAMILY, self.workspace, session_id, create=True) as held:
            held.record_setup_intent(intent)
            reference = held.reference
            reference.state_root.mkdir(mode=0o700)
            start = reference.path / "start-result-v1.json"
            start.write_bytes(b"{}\n")
            start.chmod(0o600)
            held.record_started(b"{}\n")
        with session_owner_scope(FAMILY, self.workspace, session_id, create=False) as held:
            self.assertEqual(reference, held.verify_setup_intent(intent))
            self.assertEqual(b"{}\n", held.read_started_result())
            with self.assertRaises(SessionOwnerAllocationError) as refusal:
                held.verify_setup_intent(self._intent("d"))
            self.assertEqual("owner.changed", refusal.exception.code)

    def test_setup_replacement_and_partial_or_legacy_owner_remain_protected(self) -> None:
        session_id = self._session("4")
        intent = self._intent()
        with session_owner_scope(FAMILY, self.workspace, session_id, create=True) as held:
            held.record_setup_intent(intent)
            reference = held.reference
            reference.state_root.mkdir(mode=0o700)
            start = reference.path / "start-result-v1.json"
            start.write_bytes(b"{}\n")
            start.chmod(0o600)
            held.record_started(b"{}\n")
        setup = self.provider.open(FAMILY, self.workspace).root / "owner-allocations" / f"{session_id}.setup.json"
        displaced = setup.with_name(setup.name + ".old")
        setup.rename(displaced)
        setup.write_bytes(displaced.read_bytes())
        setup.chmod(0o600)
        with self.assertRaises(SessionOwnerAllocationError) as refusal:
            with session_owner_scope(FAMILY, self.workspace, session_id, create=False):
                pass
        self.assertEqual("owner.changed", refusal.exception.code)
        self.assertTrue(displaced.exists())

        legacy = self._created("5")
        with session_owner_scope(FAMILY, self.workspace, legacy.session_id, create=False) as held:
            self.assertEqual(b"{}\n", held.read_started_result())
            with self.assertRaises(SessionOwnerAllocationError) as refusal:
                held.verify_setup_intent(intent)
            self.assertEqual("owner.incomplete", refusal.exception.code)
        with session_owner_scope(FAMILY, self.workspace, self._session("6"), create=True) as held:
            held.reference.state_root.mkdir(mode=0o700)
            with self.assertRaises(SessionOwnerAllocationError) as refusal:
                held.record_setup_intent(intent)
            self.assertEqual("owner.unclaimed", refusal.exception.code)

    def test_foreign_workspace_and_owner_fail_before_child_write(self) -> None:
        foreign = self.root / "foreign"
        foreign.mkdir()
        with self.assertRaises(DurableResourceError):
            with session_owner_scope(FAMILY, foreign, self._session("c"), create=True):
                pass
        self.assertFalse((self.root / "state").exists())
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.root / "config",
            owner_id="blueprints",
        )
        with record_store_scope(provider):
            with self.assertRaises(DurableResourceError):
                with session_owner_scope(FAMILY, self.workspace, self._session("c"), create=True):
                    pass
        self.assertFalse((self.root / "state").exists())

    def test_unknown_existing_owner_is_retained_and_refused(self) -> None:
        store = self.provider.open(FAMILY, self.workspace)
        owner = store.root / "session-owners" / self._session("d")
        owner.mkdir(parents=True, mode=0o700)
        payload = owner / "unknown"
        payload.write_bytes(b"keep\n")
        with self.assertRaises(SessionOwnerAllocationError) as refusal:
            with session_owner_scope(FAMILY, self.workspace, self._session("d"), create=True):
                pass
        self.assertEqual("owner.unclaimed", refusal.exception.code)
        self.assertEqual(b"keep\n", payload.read_bytes())

    def test_replaced_state_root_and_start_result_refuse_restart(self) -> None:
        for digit, target_name in (("e", "owner-state"), ("f", "start-result-v1.json")):
            with self.subTest(target_name=target_name):
                reference = self._created(digit)
                target = reference.path / target_name
                displaced = reference.path / (target_name + "-old")
                target.rename(displaced)
                if target_name == "owner-state":
                    target.mkdir(mode=0o700)
                else:
                    target.write_bytes(displaced.read_bytes())
                    target.chmod(0o600)
                with self.assertRaises(SessionOwnerAllocationError) as refusal:
                    with session_owner_scope(FAMILY, self.workspace, reference.session_id, create=False):
                        pass
                self.assertEqual("owner.changed", refusal.exception.code)
                self.assertTrue(displaced.exists())

    def test_replaced_marker_with_same_bytes_refuses_restart(self) -> None:
        reference = self._created("1")
        marker = reference.path / "core-owner-allocation-v1.json"
        displaced = marker.with_name(marker.name + ".old")
        marker.rename(displaced)
        marker.write_bytes(displaced.read_bytes())
        marker.chmod(0o600)
        with self.assertRaises(SessionOwnerAllocationError) as refusal:
            with session_owner_scope(FAMILY, self.workspace, reference.session_id, create=False):
                pass
        self.assertEqual("owner.changed", refusal.exception.code)

    def test_same_session_second_writer_cannot_enter_held_lease(self) -> None:
        reference = self._created("2")
        with session_owner_scope(FAMILY, self.workspace, reference.session_id, create=False):
            with self.assertRaises(DurableRecordError) as refusal:
                with session_owner_scope(FAMILY, self.workspace, reference.session_id, create=False):
                    pass
            self.assertEqual("busy", refusal.exception.code)


if __name__ == "__main__":
    unittest.main()
