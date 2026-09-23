"""Manual custody/reclamation safety on disposable, complete snapshots."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import check_lifecycle as life
from workbench_core import check_snapshots as snapshots
from workbench_core import check_storage as files
from workbench_core.storage import manager
import test_check_snapshots as fixtures


class CheckLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CheckSnapshotsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.attempt = self.fixture.attempt
        self.root = self.attempt.parent.parent.parent
        self.base = self.fixture.root
        files.write_bytes(self.attempt / 'input.txt', b'complete saved source')
        describe = self.fixture.describe
        def description(value):
            fields, views, summary = describe(value)
            fields['retained_inputs'] = [{'role': 'fixture-source', 'content': {
                **snapshots.file_content(self.attempt / 'input.txt'), 'media_type': 'text/plain'}}]
            return fields, views, summary
        self.fixture.describe = description
        self.fixture.publish()
        self.record = life.register(self.root, self.attempt, inputs={'fixture-source': 'input.txt'}, context={'owner': 'fixture'})

    def item(self, path=None):
        path = path or self.attempt
        return next(row for row in manager.inventory_storage(self.root)['items'] if row['path'] == str(path))

    def retire(self):
        plan = manager.plan_cleanup(self.root, selector=self.item()['item_id'])
        self.assertEqual('ready', plan['status'], plan.get('blockers'))
        receipt = manager.execute_cleanup(self.root, plan)
        return next(row for row in manager.inventory_storage(self.root)['items'] if row['resource_id'] == receipt['result']['trash_id'])

    def test_inventory_categories_and_complete_discoverability_without_owner(self):
        self.assertEqual('eligible', self.item()['deletion']['state'])
        view = life.overview(self.root)
        self.assertEqual('disabled', view['automatic_retention'])
        self.assertEqual({'evidence', 'saved-inputs', 'query-indexes', 'maintenance-records'}, set(view['checks'][0]['categories']))
        self.assertEqual('fixture', view['checks'][0]['context']['owner'])

    def test_pin_protects_live_and_trash_without_changing_producer_receipt(self):
        original = (self.attempt / 'snapshot/publication.json').read_bytes()
        life.pin(self.root, self.attempt.name, 'MVP proof')
        self.assertEqual('blocked', manager.plan_cleanup(self.root, selector=self.item()['item_id'], allow_review=True)['status'])
        life.pin(self.root, self.attempt.name, 'MVP proof', remove=True)
        self.assertEqual(original, (self.attempt / 'snapshot/publication.json').read_bytes())
        trash = self.retire()
        life.pin(self.root, self.attempt.name, 'investigation')
        self.assertEqual('blocked', manager.plan_purge_trash(self.root, selector=trash['item_id'], confirmation=trash['resource_id'])['status'])

    def test_restore_then_verified_export_purge_and_honest_history(self):
        publication = (self.attempt / 'snapshot/publication.json').read_bytes()
        trash = self.retire()
        self.assertEqual('retired', life.history(self.root)['checks'][0]['state'])
        manager.execute_restore_trash(self.root, manager.plan_restore_trash(self.root, selector=trash['item_id']))
        self.assertEqual(publication, (self.attempt / 'snapshot/publication.json').read_bytes())
        bundle = self.base / 'bundle'
        life.export_bundle(self.root, self.attempt.name, bundle)
        self.assertEqual('complete-inspectable-evidence', life.verify_bundle(bundle)['closure'])
        trash = self.retire()
        plan = manager.plan_purge_trash(self.root, selector=trash['item_id'], confirmation=trash['resource_id'])
        self.assertEqual('ready', plan['status'], plan['blockers'])
        receipt = manager.execute_purge_trash(self.root, plan)
        self.assertGreater(receipt['result']['purged_bytes'], 0)
        self.assertEqual('expired', life.history(self.root)['checks'][0]['state'])
        self.assertEqual(publication, (bundle / 'payload/snapshot/publication.json').read_bytes())
        self.assertEqual('inspectable-evidence-only', life.verify_bundle(bundle)['native_reproduction']['state'])

    def test_purge_requires_export_and_rechecks_modified_bundle(self):
        trash = self.retire()
        plan = manager.plan_purge_trash(self.root, selector=trash['item_id'], confirmation=trash['resource_id'])
        self.assertEqual('blocked', plan['status'])
        bundle = self.base / 'bundle'
        life.export_bundle(self.root, self.attempt.name, bundle)
        plan = manager.plan_purge_trash(self.root, selector=trash['item_id'], confirmation=trash['resource_id'])
        (bundle / 'payload/input.txt').write_bytes(b'changed')
        with self.assertRaisesRegex(manager.RuntimeManagerError, 'verified local'):
            manager.execute_purge_trash(self.root, plan)
        self.assertTrue(Path(trash['path']).exists())

    def test_missing_payload_is_not_expired_or_reconstructed(self):
        (self.attempt / 'input.txt').unlink()
        self.assertEqual('protected', self.item()['deletion']['state'])
        with self.assertRaises((ValueError, OSError)):
            life.export_bundle(self.root, self.attempt.name, self.base / 'bundle')
        with self.assertRaises((ValueError, OSError)):
            life.reconcile(self.root)

    def test_registry_rebuild_preserves_pin_and_cannot_repair_missing_evidence(self):
        life.pin(self.root, self.attempt.name, 'proof')
        (life._ledger(self.root) / (self.attempt.name + '.json')).unlink()
        result = life.reconcile(self.root)
        self.assertEqual([self.attempt.name], result['registered'])
        self.assertFalse(result['missing_evidence_reconstructed'])
        self.assertEqual(['proof'], life.pins(self.root, self.attempt.name))
        self.assertEqual('protected', self.item()['deletion']['state'])

    def test_missing_pin_authority_is_not_rebuilt_as_unpinned(self):
        life.pin(self.root, self.attempt.name, 'required proof')
        life._pin_path(self.root, self.attempt.name).unlink()
        self.assertEqual('protected', self.item()['deletion']['state'])
        with self.assertRaisesRegex(ValueError, 'pin authority is missing'):
            life.reconcile(self.root)
        with self.assertRaisesRegex(ValueError, 'pin authority is missing'):
            life.pin(self.root, self.attempt.name, 'required proof', remove=True)
        self.assertEqual('unavailable', life.history(self.root)['checks'][0]['state'])

    def test_unknown_registry_schema_and_symlink_refuse_collection(self):
        (life._ledger(self.root) / 'future.json').write_text('{"format":"future-v3"}')
        self.assertEqual('protected', self.item()['deletion']['state'])
        with self.assertRaisesRegex(ValueError, 'unknown registry'):
            life.reconcile(self.root)
        (life._ledger(self.root) / 'future.json').unlink()
        (self.attempt / 'outside').symlink_to(self.base)
        self.assertEqual('protected', self.item()['deletion']['state'])

    def test_unknown_envelope_refuses_deletion(self):
        path = self.attempt / 'snapshot/publication.json'
        value = json.loads(path.read_bytes()); value['manifest']['format'] = 'future-envelope'
        path.write_text(json.dumps(value))
        self.assertEqual('protected', self.item()['deletion']['state'])

    def test_stale_preview_cannot_override_new_pin(self):
        plan = manager.plan_cleanup(self.root, selector=self.item()['item_id'])
        life.pin(self.root, self.attempt.name, 'active proof')
        with self.assertRaisesRegex(manager.RuntimeManagerError, 'no longer eligible'):
            manager.execute_cleanup(self.root, plan)
        self.assertTrue(self.attempt.exists())

    def test_active_read_blocks_collection_and_pin_even_after_preview(self):
        plan = manager.plan_cleanup(self.root, selector=self.item()['item_id'])
        with self.fixture.open():
            with self.assertRaisesRegex(manager.RuntimeManagerError, 'active check'):
                manager.execute_cleanup(self.root, plan)
            with self.assertRaisesRegex(ValueError, 'active check'):
                life.pin(self.root, self.attempt.name, 'test')
        self.assertTrue(self.attempt.exists())

    def test_cross_process_lease_protects_inventory_and_collection(self):
        code = "import fcntl,sys; f=open(sys.argv[1], 'r'); fcntl.flock(f,fcntl.LOCK_SH); print('ready',flush=True); sys.stdin.read()"
        process = subprocess.Popen(['python3', '-c', code, str(self.root / '.workbench/runtime-manager/checks.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual('ready\n', process.stdout.readline())
            self.assertEqual('active', self.item()['deletion']['state'])
            with self.assertRaisesRegex(ValueError, 'active reader'):
                life.pin(self.root, self.attempt.name, 'test')
        finally:
            process.communicate('stop', timeout=10)

    def test_group_allocation_counts_shared_inodes_once(self):
        one = self.root / '.workbench/cache/one'; two = one.with_name('two')
        one.mkdir(parents=True); two.mkdir()
        (one / 'value').write_bytes(b'x' * 65536)
        os.link(one / 'value', two / 'value')
        a,b = self.item(one), self.item(two)
        alone = life.group_preview(self.root, [a['item_id']])
        together = life.group_preview(self.root, [a['item_id'], b['item_id'], a['item_id']])
        self.assertEqual(0, alone['reclaimable_allocated_bytes'])
        self.assertEqual(65536, together['reclaimable_allocated_bytes'])
        self.assertEqual(131072, together['logical_bytes'])

    def test_trash_holds_shared_required_dependency_until_purge(self):
        dependency = self.root / '.workbench/cache/shared'; dependency.mkdir(parents=True)
        (dependency / 'input').write_text('required')
        # Replace only fixture custody before use, simulating owner registration with a required input reference.
        value = {k:v for k,v in self.record.items() if k != 'id'}
        value['references'] = ['.workbench/cache/shared']
        record = files.seal('check-custody', value)
        for path in [self.attempt / life.MANIFEST, life._ledger(self.root) / (self.attempt.name + '.json')]:
            path.write_bytes(files.canonical(record))
        self.assertEqual('protected', self.item(dependency)['deletion']['state'])
        trash = self.retire()
        self.assertEqual('protected', self.item(dependency)['deletion']['state'])
        self.assertEqual([trash['item_id']], self.item(dependency)['deletion']['referenced_by'])
        # A changed trash payload cannot erase the shared dependency edge.
        (Path(trash['path']) / 'unexpected').write_text('partial mutation')
        self.assertEqual('protected', self.item(dependency)['deletion']['state'])
        self.assertEqual('protected', self.item(Path(trash['path']))['deletion']['state'])


    def test_selected_index_survives_reconciliation_and_stale_index_is_managed_cache(self):
        original = (self.attempt / 'snapshot/publication.json').read_bytes()
        snapshots.rebuild(self.attempt, scope=self.fixture.scope, expected=self.fixture.expected)
        selected = self.fixture.index_path()
        result = life.reconcile(self.root)
        retired = [row for row in result['maintenance'] if row['state'] == 'retired-derived-index']
        self.assertEqual(1, len(retired))
        cache = Path(retired[0]['path'])
        self.assertEqual('eligible', self.item(cache)['deletion']['state'])
        self.assertTrue(selected.exists())
        self.assertEqual(original, (self.attempt / 'snapshot/publication.json').read_bytes())
        with self.fixture.open() as opened:
            self.assertEqual('ready', opened.query(self.fixture.query(opened))['state'])
        self.assertEqual([], life.reconcile(self.root)['maintenance'])

    def test_interrupted_derived_index_move_is_reconciled_from_intent(self):
        snapshots.rebuild(self.attempt, scope=self.fixture.scope, expected=self.fixture.expected)
        write = files.write_json
        def fail_completion(path, value, **kwargs):
            if str(path).endswith('-completed.json'):
                raise OSError('interrupted after index move')
            return write(path, value, **kwargs)
        with patch.object(files, 'write_json', side_effect=fail_completion):
            with self.assertRaisesRegex(OSError, 'interrupted after'):
                life.reconcile(self.root)
        result = life.reconcile(self.root)
        self.assertIn('recovered-derived-retirement', [row['state'] for row in result['maintenance']])
        self.assertTrue(self.fixture.index_path().exists())

    def test_shared_dependency_bundle_is_complete_and_detects_changed_payload(self):
        dependency = self.root / '.workbench/cache/shared'; dependency.mkdir(parents=True)
        (dependency / 'input').write_text('required bytes')
        value = {k:v for k,v in self.record.items() if k != 'id'}
        value['references'] = ['.workbench/cache/shared']
        record = files.seal('check-custody', value)
        for path in [self.attempt / life.MANIFEST, life._ledger(self.root) / (self.attempt.name + '.json')]:
            path.write_bytes(files.canonical(record))
        bundle_path = self.base / 'bundle'
        life.export_bundle(self.root, self.attempt.name, bundle_path)
        bundle = life.verify_bundle(bundle_path)
        dependency_path = bundle_path / bundle['dependencies']['.workbench/cache/shared']['payload']
        self.assertEqual('required bytes', (dependency_path / 'input').read_text())
        (dependency_path / 'input').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'dependency bytes changed'):
            life.verify_bundle(bundle_path)

    def test_pin_protected_trash_can_be_restored_without_releasing_pin(self):
        trash = self.retire()
        life.pin(self.root, self.attempt.name, 'proof')
        manager.execute_restore_trash(self.root, manager.plan_restore_trash(self.root, selector=trash['item_id']))
        self.assertEqual('protected', self.item()['deletion']['state'])
        self.assertEqual(['proof'], life.pins(self.root, self.attempt.name))

    def test_unknown_index_generation_refuses_reconciliation(self):
        (self.attempt / 'snapshot/indexes/future').mkdir()
        with self.assertRaisesRegex(ValueError, 'unknown query generation'):
            life.reconcile(self.root)
        self.assertTrue(self.fixture.index_path().exists())

    def test_interrupted_cleanup_is_restore_only(self):
        trash = self.retire()
        transactions = list((self.root / '.workbench/runtime-manager/trash').glob('*.json'))
        transaction = json.loads(transactions[0].read_bytes())
        receipt_path = self.root / '.workbench/runtime-manager/operations' / (transaction['_cleanup_plan_id'].split(':')[-1] + '.json')
        receipt = json.loads(receipt_path.read_bytes())
        receipt.pop('receipt_id'); receipt['status'] = 'running'; receipt['completed_at'] = None
        receipt['result'] = {'resource_ids': [], 'trash_id': None, 'restored_item_id': None, 'purged_bytes': 0, 'output_paths': []}
        receipt['actions'][0].update(status='running', completed_at=None, bytes_processed=0)
        receipt['receipt_id'] = manager._identity(manager.RECEIPT_PREFIX, receipt)
        manager.validate_operation_receipt(receipt)
        receipt_path.write_bytes(manager._canonical_json(receipt))
        plan = manager.plan_purge_trash(self.root, selector=trash['item_id'], confirmation=trash['resource_id'])
        self.assertIn('cleanup-interrupted', [row['code'] for row in plan['blockers']])
        manager.execute_restore_trash(self.root, manager.plan_restore_trash(self.root, selector=trash['item_id']))
        self.assertTrue(self.attempt.exists())

    def test_external_paths_and_unregistered_history_are_protected(self):
        unmanaged = self.root / '.workbench/check-attempts/unmanaged'
        unmanaged.mkdir(); (unmanaged / 'private').write_text('unknown')
        self.assertEqual('protected', self.item(unmanaged)['deletion']['state'])
        with self.assertRaisesRegex(ValueError, 'outside this managed store'):
            value = {k:v for k,v in self.record.items() if k != 'id'}
            value['references'] = ['external/private']
            life._validate(files.seal('check-custody', value), self.root)

    def test_group_preview_obeys_pins(self):
        life.pin(self.root, self.attempt.name, 'proof')
        preview = life.group_preview(self.root, [self.item()['item_id']])
        self.assertEqual(0, preview['reclaimable_allocated_bytes'])
        self.assertEqual([self.item()['item_id']], preview['blocked_items'])

    def test_export_interrupt_retains_partial_without_verified_receipt(self):
        with patch('workbench_core.check_lifecycle.shutil.copyfile', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                life.export_bundle(self.root, self.attempt.name, self.base / 'bundle')
        self.assertFalse((self.base / 'bundle').exists())
        self.assertTrue(list(self.base.glob('.check-bundle-*')))
        with self.assertRaisesRegex(ValueError, 'verified local'):
            life.require_export(self.root, self.record)
