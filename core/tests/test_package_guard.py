"""Activity exclusion is environment-wide and survives asynchronous closure."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import os
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api import ModuleError
from workbench_api.canonical import content_id
from workbench_api.service import ServiceHandlerRegistration, ServicePhysicalLeasePorts, ServiceV3Error
from workbench_core.package_guard import PackageActivity, package_change, guard_root, environment_fingerprint
from workbench_core.service.runtime import ServiceRuntimeV3


class PackageGuardTests(unittest.TestCase):
    def test_client_state_and_cache_environment_cannot_change_exclusion_domain(self):
        expected = guard_root()
        with patch.dict(os.environ, {'WORKBENCH_STATE_ROOT':'/other/state', 'XDG_STATE_HOME':'/other/state2',
                                    'XDG_CACHE_HOME':'/other/cache', 'LOCALAPPDATA':'/other/local', 'HOME':'/other/home'}):
            self.assertEqual(expected, guard_root())

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership/mode check; Windows uses the account known folder')
    def test_nonprivate_lease_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o755)
            with self.assertRaisesRegex(ModuleError, 'private'):
                PackageActivity(root=root)
            root.chmod(0o700)

    def test_activity_blocks_mutation_and_releases_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with PackageActivity(root=root):
                with self.assertRaisesRegex(ModuleError, 'active Workbench'), package_change(root=root):
                    self.fail('must reject')
            with package_change(root=root):
                with self.assertRaises(ModuleError):
                    PackageActivity(root=root)

    def test_cross_process_activity_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with PackageActivity(root=root):
                result = subprocess.run([sys.executable, '-c',
                    'from pathlib import Path; from workbench_core.package_guard import package_change; '
                    'with_guard = package_change(root=Path(__import__("sys").argv[1])); with_guard.__enter__()',
                    str(root)], capture_output=True, text=True, check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('active Workbench', result.stderr)

    def test_external_package_drift_requires_restart(self):
        with tempfile.TemporaryDirectory() as temporary, patch('workbench_core.package_guard.environment_fingerprint', return_value='original') as fingerprint:
            activity = PackageActivity(root=Path(temporary))
            try:
                fingerprint.return_value = 'reinstalled'
                with self.assertRaisesRegex(ModuleError, 'outside Workbench'):
                    activity.check()
            finally:
                activity.close()

    def test_same_version_reinstallation_changes_environment_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = root / 'sample.dist-info'
            installed.mkdir()
            distribution = SimpleNamespace(metadata={'Name':'sample'}, version='1.0.0', read_text=lambda name:'unchanged', _path=installed)
            with patch('workbench_core.package_guard.metadata.distributions', return_value=(distribution,)):
                before = environment_fingerprint()
                installed.rename(root / 'previous.dist-info')
                installed.mkdir()
                self.assertNotEqual(before, environment_fingerprint())

    def test_nonwaiting_close_retains_service_and_package_leases_for_direct_work(self):
        entered, finish = threading.Event(), threading.Event()
        released, results = [], []
        registration = ServiceHandlerRegistration(
            method='test/query', capability_id=content_id('capability', {}), capability_version='1.0.0',
            handler_id=content_id('handler', {}), implementation_id=content_id('implementation', {}),
            mutation_boundary='none', asynchronous=False, maximum_concurrency=1,
            handler=lambda context, args: (entered.set(), finish.wait(10), {})[-1],
            context_binding='none', input_binding='none')
        class Store:
            def recover_incomplete(self, *, actor_id): return ()
        leases = ServicePhysicalLeasePorts('test', lambda path: lambda: released.append(path), lambda path: None)
        with tempfile.TemporaryDirectory() as temporary, patch('workbench_core.package_guard.guard_root', return_value=Path(temporary)/'environment'):
            runtime = ServiceRuntimeV3(Path(temporary)/'service', registrations=(registration,), store_factory=lambda root, leases: Store(), physical_leases=leases)
            request = {'method':'test/query', 'capability_id':registration.capability_id, 'arguments':{}, 'context_ref_id':None, 'input_binding_id':None, 'request_id':'test', 'idempotency_key':None}
            thread = threading.Thread(target=lambda: results.append(runtime.dispatch(request)))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                runtime.close(wait=False)
                self.assertEqual([], released)
                with self.assertRaises(ModuleError), package_change(): pass
                with self.assertRaisesRegex(ServiceV3Error, 'no longer admits'):
                    runtime.dispatch(request)
            finally:
                finish.set()
                thread.join(10)
                runtime.close()
            self.assertEqual(1, len(released))
            self.assertEqual(1, len(results))
            with package_change(): pass
