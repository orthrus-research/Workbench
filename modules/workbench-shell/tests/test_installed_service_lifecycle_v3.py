"""Explicit service processes preserve custody across stop/restart.

Native package changes wait for active services. No portable installer,
side-by-side rollback manager or implicit background daemon is claimed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from jsonschema import Draft202012Validator

from workbench_api import ModuleError
from workbench_core.package_guard import package_change
from workbench_core.service.host import LocalServiceEndpointV3, ServiceHostV3Error
from workbench_shell.installed_service import (
    build_installed_discovery_registry_v3,
    compose_installed_discovery_service_v3,
    probe_installed_service_v3,
)

ROOT = Path(__file__).resolve().parents[3]


class InstalledServiceEndpointTests(unittest.TestCase):
    def test_discovery_and_duplicate_endpoint_preserve_exact_owner(self):
        registry, distribution = build_installed_discovery_registry_v3(ROOT)
        self.assertEqual({row["capability_key"] for row in registry["capability_descriptors"]},
                         {"crucible.service.capabilities", "crucible.service.context-list"})
        self.assertEqual(registry["service_distribution_id"], distribution["id"])
        self.assertFalse(registry["descriptor_authority_admissions"])
        self.assertFalse(registry["profile_policy_admissions"])
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            composition = compose_installed_discovery_service_v3(ROOT, base / "service")
            endpoint = base / "service.sock"
            try:
                with LocalServiceEndpointV3(composition.host, endpoint_path=endpoint):
                    competitor = LocalServiceEndpointV3(composition.host, endpoint_path=endpoint)
                    with self.assertRaises(ServiceHostV3Error):
                        competitor.start()
                    competitor.close()
                    self.assertTrue(endpoint.exists())
                    probe = probe_installed_service_v3(ROOT, endpoint_path=endpoint, credential_path=composition.authenticator.path)
                    self.assertEqual(probe["state"], "ready")
                    self.assertTrue(probe["authenticated"])
                    self.assertEqual(probe["registry_id"], registry["registry_id"])
                    self.assertFalse(any(probe["claims"].values()))
            finally:
                composition.close()
            self.assertFalse(endpoint.exists())

    @unittest.skipIf(os.name == "nt", "AF_UNIX path bound is POSIX-specific")
    def test_overlong_endpoint_fails_before_binding(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            composition = compose_installed_discovery_service_v3(ROOT, base / "service")
            endpoint = base / ("x" * 108)
            try:
                with self.assertRaisesRegex(ServiceHostV3Error, "AF_UNIX path bound"):
                    LocalServiceEndpointV3(composition.host, endpoint_path=endpoint).start()
                self.assertFalse(endpoint.exists())
            finally:
                composition.close()


@unittest.skipUnless(os.name == "posix" and Path("/proc/self/cmdline").is_file(),
                     "native process/crash fixtures require the POSIX /proc provider")
class InstalledServiceLifecycleV3Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.service = self.base / "service"
        self.endpoint = self.service / "run/service.sock"
        self.ready = self.base / "ready.json"

    def _spawn(self):
        output = (self.base / f"child-{time.monotonic_ns()}.log").open("w+")
        self.addCleanup(output.close)
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                       "WORKBENCH_STATE_ROOT": str(self.base / "state")}
        process = subprocess.Popen([
            sys.executable, str(ROOT / "tools/workbench.py"), "service-host-v3",
            "--service-root", str(self.service), "--endpoint", str(self.endpoint),
            "--ready-file", str(self.ready), "--process-nonce", "service-process-nonce:" + "a" * 32,
        ], cwd=ROOT, env=environment, stdout=output, stderr=output, start_new_session=True)
        self.addCleanup(self._stop, process)
        return process, output

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(5)

    def _start(self):
        self.ready.unlink(missing_ok=True)
        process, output = self._spawn()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output.seek(0)
                self.fail("native service failed: " + output.read())
            if self.ready.is_file():
                ready = json.loads(self.ready.read_text())
                if ready["pid"] == process.pid:
                    self.assertEqual(ready["format"], "workbench-installed-service-ready-v2")
                    Draft202012Validator(json.loads((ROOT / "modules/workbench-shell/schemas/installed-service-ready-v2.schema.json").read_text())).validate(ready)
                    self.assertEqual(ready["schema_version"], 2)
                    self.assertEqual(len(ready["owner_environment_id"]), 64)
                    self.assertEqual(len(ready["package_fingerprint"]), 64)
                    self.assertFalse(ready["claims"]["release_qualified"])
                    return process, ready
            time.sleep(0.025)
        self.fail("native service did not publish readiness")

    def _probe(self, ready):
        return probe_installed_service_v3(ROOT, endpoint_path=self.endpoint,
                                          credential_path=Path(ready["credential_path"]))

    def test_start_probe_stop_restart_preserves_store_and_rejects_active_update(self):
        process, ready = self._start()
        self.assertEqual(self._probe(ready)["state"], "ready")
        sentinel = Path(ready["store_path"]) / "developer-owned-retained-job"
        sentinel.write_text("preserve")
        with self.assertRaisesRegex(ModuleError, "active Workbench"), package_change():
            self.fail("an active service cannot allow package mutation")
        self._stop(process)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(self.endpoint.exists())
        restarted, fresh = self._start()
        self.assertNotEqual(fresh["service_instance_id"], ready["service_instance_id"])
        self.assertEqual(self._probe(fresh)["state"], "ready")
        self._stop(restarted)
        self.assertEqual(sentinel.read_text(), "preserve")

    def test_duplicate_process_and_bad_credential_do_not_replace_healthy_owner(self):
        process, ready = self._start()
        duplicate, output = self._spawn()
        self.assertNotEqual(duplicate.wait(30), 0)
        self.assertIsNone(process.poll())
        self.assertEqual(self._probe(ready)["state"], "ready")
        bad = self.base / "bad.token"
        bad.write_text("not-the-owner-token")
        bad.chmod(0o600)
        with self.assertRaises((ValueError, OSError)):
            probe_installed_service_v3(ROOT, endpoint_path=self.endpoint, credential_path=bad)
        self.assertEqual(self._probe(ready)["state"], "ready")

    def test_crash_requires_explicit_stale_endpoint_cleanup_and_retains_store(self):
        process, ready = self._start()
        sentinel = Path(ready["store_path"]) / "developer-data"
        sentinel.write_text("retained")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(10)
        self.assertTrue(self.endpoint.exists())
        duplicate, output = self._spawn()
        self.assertNotEqual(duplicate.wait(30), 0)
        self.assertTrue(self.endpoint.exists())
        self.assertEqual(sentinel.read_text(), "retained")
        # This fixture owns the exact dead process and path. Production
        # callers must independently prove that custody before cleanup.
        self.endpoint.unlink()
        restarted, fresh = self._start()
        self.assertEqual(self._probe(fresh)["state"], "ready")
        self._stop(restarted)
        self.assertEqual(sentinel.read_text(), "retained")
