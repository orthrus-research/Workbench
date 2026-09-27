"""Finite history acceptance on disposable stores; no development policy activation."""
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from workbench_core import check_retention as retention
from workbench_core import check_lifecycle as life
from workbench_core import check_storage as files
from workbench_core import check_snapshots as snapshots
from workbench_core.storage import manager
import test_check_lifecycle as fixtures


class CheckRetentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CheckLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.attempt = self.fixture.root, self.fixture.attempt

    def enable(self, **changes):
        settings = {**retention.RECOMMENDATION, 'min_age_days': 0, 'trash_days': 0, **changes}
        proposal = retention.propose(self.root, settings)
        return retention.configure(self.root, settings, proposal['id'])

    def more(self, label):
        fixture = self.fixture.fixture
        fixture.attempt = files.allocate_attempt(self.root, 'fixture-check')
        self.fixture.attempt = fixture.attempt
        fixture.source = fixture.attempt / 'result.json'
        fixture.expected = {'attempt_id': fixture.attempt.name, 'request_id': 'fixture-request'}
        fixture.value['unselected']['version'] = label
        files.write_bytes(fixture.attempt / 'input.txt', b'complete saved source')
        fixture.publish()
        return life.register(self.root, fixture.attempt, inputs={'fixture-source': 'input.txt'}, context={'owner': 'fixture'})

    def test_disabled_and_keep_everything_are_explicit_and_non_destructive(self):
        before = files.tree_manifest(self.root)
        self.assertEqual('disabled', retention.maintain(self.root)['state'])
        self.assertEqual(before, files.tree_manifest(self.root))
        self.enable(mode='keep-everything', max_bytes=1)
        self.assertEqual('disabled', retention.maintain(self.root)['state'])
        self.assertTrue(self.attempt.exists())
        self.assertIn('growing', retention.status(self.root)['notices'][0])

    def test_first_use_status_and_preview_never_create_a_store(self):
        root = self.fixture.base / 'unused-store'
        self.assertEqual(0, retention.status(root)['allocated_bytes'])
        self.assertEqual([], retention.preview(root)['quarantine_candidates'])
        self.assertEqual('disabled', retention.maintain(root)['state'])
        self.assertFalse(root.exists())

    def test_returning_result_is_protected_until_current_execution_finishes(self):
        self.enable(max_bytes=1)
        self.assertEqual('complete', retention.maintain(self.root, protect_attempts=[self.attempt.name])['state'])
        self.assertTrue(self.attempt.exists())
        self.assertEqual('complete', retention.maintain(self.root)['state'])
        self.assertFalse(self.attempt.exists())

    def test_consent_binds_scope_preferences_previous_policy_and_version(self):
        settings = dict(retention.RECOMMENDATION)
        proposal = retention.propose(self.root, settings)
        self.assertIn('permanent expiry', proposal['disclosure'])
        with self.assertRaisesRegex(ValueError, 'exact current'):
            retention.configure(self.root, {**settings, 'trash_days': 0}, proposal['id'])
        self.enable(mode='keep-everything')
        with self.assertRaisesRegex(ValueError, 'exact current'):
            retention.configure(self.root, settings, proposal['id'])
        for field, value in [('max_count', True), ('max_bytes', 0), ('trash_days', -1), ('mode', 'automatic')]:
            with self.assertRaises(ValueError):
                retention.propose(self.root, {**settings, field: value})
        path = retention._directory(self.root) / 'policy.json'
        row = files.read_json(path); row.pop('id'); row['format'] = 'future-v9'
        path.write_bytes(files.canonical(files.seal('check-retention-policy', row)))
        with self.assertRaisesRegex(ValueError, 'unsupported retention policy'):
            retention.maintain(self.root)
        self.assertTrue(self.attempt.exists())

    def test_full_expiry_has_verified_export_reclaim_and_small_truthful_history(self):
        self.enable(max_bytes=1)
        result = retention.maintain(self.root)
        self.assertEqual('complete', result['state'], result)
        self.assertGreater(result['allocated_bytes_unlinked'], 0)
        self.assertFalse(self.attempt.exists())
        history = life.history(self.root)['checks']
        self.assertEqual('expired', history[0]['state'])
        self.assertEqual(self.fixture.record['summary'], history[0]['original_summary'])
        self.assertEqual([], list((self.root / '.workbench/cache').iterdir()))
        self.assertEqual([], list(life._ledger(self.root).iterdir()))
        self.assertEqual(1, retention.status(self.root)['compacted_history']['expired_count'])

    def test_prior_manual_expiry_metadata_compacts_without_deleting_user_export(self):
        bundle = self.fixture.base / 'manual-bundle'
        life.export_bundle(self.root, self.attempt.name, bundle)
        before = files.tree_manifest(bundle)
        trash = self.fixture.retire()
        manager.execute_purge_trash(self.root, manager.plan_purge_trash(self.root, selector=trash['item_id'], confirmation=trash['resource_id']))
        self.enable()
        result = retention.maintain(self.root)
        self.assertEqual('complete', result['state'], result)
        self.assertEqual([], list(life._ledger(self.root).iterdir()))
        self.assertEqual([], list((self.root / '.workbench/runtime-manager/check-exports').iterdir()))
        self.assertEqual(before, files.tree_manifest(bundle))
        self.assertEqual('expired', life.history(self.root)['checks'][0]['state'])

    def test_age_grace_and_manual_restore_restart_eligibility(self):
        self.enable(max_bytes=1, min_age_days=2, trash_days=7)
        self.assertEqual([], retention.preview(self.root)['quarantine_candidates'])
        result = retention.maintain(self.root, now='2027-01-01T00:00:00Z')
        self.assertEqual('complete', result['state'], result)
        self.assertEqual('retired', life.history(self.root)['checks'][0]['state'])
        trash = next(row for row in manager.inventory_storage(self.root)['items'] if row['kind'] == 'trash')
        plan = manager.plan_restore_trash(self.root, selector=trash['item_id'], now='2027-01-02T00:00:00Z')
        manager.execute_restore_trash(self.root, plan, now='2027-01-02T00:00:00Z')
        self.assertEqual([], retention.preview(self.root, now='2027-01-03T00:00:00Z')['quarantine_candidates'])
        retention.maintain(self.root, now='2027-01-05T00:00:00Z')
        self.assertEqual('retired', life.history(self.root)['checks'][0]['state'])
        result = retention.maintain(self.root, now='2027-01-13T00:00:00Z')
        self.assertEqual('complete', result['state'], result)
        self.assertEqual('expired', life.history(self.root)['checks'][0]['state'])

    def test_active_context_latest_failure_and_explicit_correction_pin(self):
        key = retention.context_key(self.fixture.record['context'])
        correction = self.more('manual correction')
        life.pin(self.root, self.attempt.name, 'failure investigation')
        self.enable(max_bytes=1, active_contexts=[key])
        result = retention.maintain(self.root)
        self.assertEqual('complete', result['state'], result)
        self.assertTrue(self.attempt.exists())
        self.assertTrue((self.root / '.workbench/check-attempts' / correction['attempt_id']).exists())
        view = retention.status(self.root)
        self.assertEqual(2, view['live_count'])
        self.assertTrue(view['notices'])
        life.pin(self.root, self.attempt.name, 'failure investigation', remove=True)
        retention.maintain(self.root)
        self.assertFalse(self.attempt.exists())
        self.assertEqual(1, retention.status(self.root)['live_count'])

    def test_active_reader_refuses_stale_preview_and_collection(self):
        self.enable(max_bytes=1)
        self.assertTrue(retention.preview(self.root)['quarantine_candidates'])
        with self.fixture.fixture.open():
            with self.assertRaisesRegex(ValueError, 'active check'):
                retention.maintain(self.root)
        self.assertTrue(self.attempt.exists())

    def test_required_proof_dependencies_and_unknown_caches_are_preserved(self):
        dependent = self.more('dependent')
        # Supported fixture owner registers an explicit required proof dependency.
        path = self.root / '.workbench/check-attempts' / dependent['attempt_id']
        row = {k:v for k,v in dependent.items() if k != 'id'}
        row['references'] = [str(self.attempt.relative_to(self.root))]
        record = files.seal('check-custody', row)
        (path / life.MANIFEST).write_bytes(files.canonical(record))
        (life._ledger(self.root) / (path.name + '.json')).write_bytes(files.canonical(record))
        life.pin(self.root, path.name, 'required correction proof')
        unknown = self.root / '.workbench/cache/unknown'; unknown.mkdir(parents=True)
        (unknown / 'data').write_bytes(b'not authorized')
        self.enable(max_bytes=1)
        self.assertEqual('complete', retention.maintain(self.root)['state'])
        self.assertTrue(self.attempt.exists())
        self.assertTrue((unknown / 'data').exists())

    def test_known_stores_per_volume_missing_coverage_is_not_zero(self):
        other = self.fixture.base / 'other'; other.mkdir()
        (other / '.workbench').mkdir(); (other / '.workbench/payload').write_bytes(b'x' * 8192)
        self.enable(known_stores=[str(other), str(self.fixture.base / 'missing')])
        view = retention.status(self.root)
        self.assertEqual(2, len(view['stores']))
        self.assertEqual(1, len(view['volumes']))
        self.assertEqual(1, len(view['coverage_gaps']))
        self.assertEqual('deferred', retention.maintain(self.root)['state'])
        self.assertTrue(self.attempt.exists())

    def test_capacity_deferral_preserves_complete_evidence_and_external_export(self):
        bundle = self.fixture.base / 'user-export'
        life.export_bundle(self.root, self.attempt.name, bundle)
        original = files.tree_manifest(bundle)
        self.enable(max_bytes=1)
        disk = shutil.disk_usage(self.root)
        with patch.object(retention.shutil, 'disk_usage', return_value=type(disk)(disk.total, disk.total, 0)):
            self.assertEqual('deferred-capacity', retention.maintain(self.root)['state'])
        self.assertTrue(self.attempt.exists())
        self.assertEqual('complete', retention.maintain(self.root)['state'])
        self.assertEqual(original, files.tree_manifest(bundle))

    def test_failed_durability_flush_cannot_quarantine_or_certify_expiry(self):
        self.enable(max_bytes=1)
        with patch.object(retention, '_durable', side_effect=OSError('unable to flush completed output')):
            result = retention.maintain(self.root)
        self.assertEqual('deferred', result['state'])
        self.assertTrue(self.attempt.exists())
        self.assertEqual([], retention.expiry(self.root))
        self.assertEqual('complete', retention.maintain(self.root)['state'])

    def test_manager_refusal_is_a_deferred_result_preserving_recovery(self):
        self.enable(max_bytes=1)
        with patch.object(manager, 'plan_purge_trash', side_effect=manager.RuntimeManagerError('changed trash observation')):
            result = retention.maintain(self.root)
        self.assertEqual('deferred', result['state'])
        self.assertEqual('retired', life.history(self.root)['checks'][0]['state'])
        self.assertTrue(list((self.root / '.workbench/cache').glob('check-recovery-*')))
        self.assertEqual('complete', retention.maintain(self.root)['state'])

    def test_interrupted_export_retry_and_no_false_expiry(self):
        self.enable(max_bytes=1)
        copy = life.shutil.copyfile
        with patch.object(life.shutil, 'copyfile', side_effect=OSError('disk full during copy')):
            result = retention.maintain(self.root)
        self.assertEqual('deferred', result['state'])
        self.assertEqual('retired', life.history(self.root)['checks'][0]['state'])
        self.assertEqual('complete', retention.maintain(self.root)['state'])
        self.assertEqual('expired', life.history(self.root)['checks'][0]['state'])

    def test_interrupted_compaction_resumes_exact_moves(self):
        self.enable(max_bytes=1)
        move = retention.os.rename
        failed = []
        def interrupt(source, destination):
            if '/check-maintenance-' in str(destination) and '/payload/' in str(destination) and not failed:
                failed.append(True); raise OSError('interrupted metadata retirement')
            return move(source, destination)
        with patch.object(retention.os, 'rename', side_effect=interrupt):
            self.assertEqual('deferred', retention.maintain(self.root)['state'])
        self.assertEqual('expired', life.history(self.root)['checks'][0]['state'])
        result = retention.maintain(self.root)
        self.assertEqual('complete', result['state'], result)
        self.assertEqual(1, retention.status(self.root)['compacted_history']['expired_count'])

    def test_interrupted_compaction_after_quarantine_resumes_without_double_accounting(self):
        self.enable(max_bytes=1)
        execute = retention._execute
        interrupted = []
        def stop(root, current, action, item, **kwargs):
            unit = Path(item['path']) / retention.UNIT
            if action == 'purge' and unit.exists() and files.read_json(unit)['purpose'] == 'released-terminal-metadata' and not interrupted:
                interrupted.append(True)
                raise OSError('interruption after metadata quarantine')
            return execute(root, current, action, item, **kwargs)
        with patch.object(retention, '_execute', side_effect=stop):
            self.assertEqual('deferred', retention.maintain(self.root)['state'])
        self.assertTrue(interrupted)
        result = retention.maintain(self.root)
        self.assertEqual('complete', result['state'], result)
        self.assertEqual(1, retention.status(self.root)['compacted_history']['expired_count'])
        self.assertEqual([], list((self.root / '.workbench/trash').iterdir()))

    def test_repeated_runs_and_index_upgrades_bound_eligible_payload_and_metadata(self):
        self.enable(max_count=1, max_bytes=16 * 1024**2, metadata_count=2)
        sizes = []
        for version in range(9):
            self.more('pack-version-' + str(version))
            f = self.fixture.fixture
            snapshots.rebuild(f.attempt, scope=f.scope, expected=f.expected)
            result = retention.maintain(self.root)
            self.assertEqual('complete', result['state'], result)
            view = retention.status(self.root)
            self.assertEqual(1, view['live_count'])
            self.assertGreater(result['allocated_bytes_unlinked'], 0)
            sizes.append(view['allocated_bytes'])
        # A later pass compacts the prior pass's terminal receipts and old expiry rows.
        self.assertEqual('complete', retention.maintain(self.root)['state'])
        self.assertLessEqual(len(retention.expiry(self.root)), 2)
        self.assertLessEqual(sizes[-1], max(sizes[3:6]) + 32768)
        self.assertGreaterEqual(retention.status(self.root)['compacted_history']['forgotten_count'], 7)
        self.assertLessEqual(len(list((self.root / '.workbench/runtime-manager/operations').glob('*.json'))), 8)

    def test_repeated_rebuilds_do_not_accumulate_terminal_journal_directories(self):
        self.enable()
        for _ in range(5):
            f = self.fixture.fixture
            snapshots.rebuild(f.attempt, scope=f.scope, expected=f.expected)
            result = retention.maintain(self.root)
            self.assertEqual('complete', result['state'], result)
            self.assertLessEqual(len(list((self.attempt / 'snapshot-operations').iterdir())), 2)
