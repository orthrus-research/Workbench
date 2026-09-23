"""Core service mechanics accept an explicit store without domain imports."""
from pathlib import Path
import tempfile
import unittest

from workbench_api.canonical import content_id
from workbench_api.service import ServiceHandlerRegistration, ServicePhysicalLeasePorts
from workbench_core.service.runtime import ServiceRuntimeV3


class ServiceOwnershipTests(unittest.TestCase):
    def test_direct_query_uses_explicit_owner_store_and_releases_instance(self):
        released = []
        supplied = []
        class Store:
            def recover_incomplete(self, *, actor_id):
                supplied.append(actor_id)
                return ()
        store = Store()
        leases = ServicePhysicalLeasePorts('test-leases', lambda path: lambda: released.append(path), lambda path: None)
        registration = ServiceHandlerRegistration(
            method='example/query', capability_id=content_id('capability', {}),
            capability_version='0.1.0', handler_id=content_id('handler', {}),
            implementation_id=content_id('implementation', {}), mutation_boundary='none',
            asynchronous=False, maximum_concurrency=1, handler=lambda context, arguments: {'value': arguments['value']},
            context_binding='none', input_binding='none',
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def factory(selected_root, selected_leases):
                self.assertEqual(root, selected_root)
                self.assertIs(leases, selected_leases)
                return store
            with ServiceRuntimeV3(root, registrations=(registration,), store_factory=factory, physical_leases=leases) as runtime:
                self.assertIs(store, runtime.store)
                result = runtime.dispatch({'method':'example/query', 'capability_id':registration.capability_id, 'arguments':{'value':7}, 'context_ref_id':None, 'input_binding_id':None, 'request_id':'test', 'idempotency_key':None})
                self.assertEqual({'outcome':'succeeded','result':{'value':7}}, result)
            self.assertEqual([root/'service-writer.lock'], released)
            self.assertEqual(1, len(supplied))

    def test_runtime_requires_an_explicit_store_factory(self):
        with self.assertRaises(TypeError):
            ServiceRuntimeV3(Path('/unused'), registrations=(), physical_leases=None)
