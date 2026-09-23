"""Exercise check custody across real processes on each supported host."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workbench_core import check_lifecycle as lifecycle


class HostFileLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def child(self, mode):
        result = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from workbench_core import check_lifecycle as lifecycle
root = Path(sys.argv[1])
if sys.argv[2] == 'probe':
    print('busy' if lifecycle.busy(root) else 'free')
else:
    try:
        with lifecycle.lease(root, exclusive=sys.argv[2] == 'exclusive'):
            print('acquired')
    except ValueError as error:
        assert 'active reader, writer or collector' in str(error), str(error)
        print('busy')
""", str(self.root), mode], check=True, capture_output=True, text=True, timeout=15)
        return result.stdout.strip()

    def test_shared_readers_exclude_collection_without_writing_lock_bytes(self):
        with lifecycle.lease(self.root):
            lock = self.root / '.workbench/runtime-manager/checks.lock'
            original = lock.read_bytes()
            self.assertEqual('acquired', self.child('shared'))
            self.assertEqual('busy', self.child('exclusive'))
            self.assertEqual('busy', self.child('probe'))
        self.assertEqual(original, lock.read_bytes())
        self.assertEqual('free', self.child('probe'))
        self.assertEqual('acquired', self.child('exclusive'))
    def test_collector_excludes_readers_and_collectors_then_releases(self):
        with lifecycle.lease(self.root, exclusive=True):
            for mode in ('shared', 'exclusive', 'probe'):
                self.assertEqual('busy', self.child(mode))
        self.assertEqual('acquired', self.child('shared'))
        self.assertEqual('acquired', self.child('exclusive'))

    def test_read_only_probe_does_not_create_state(self):
        self.assertFalse(lifecycle.busy(self.root))
        self.assertEqual([], list(self.root.iterdir()))

    def test_nesting_preserves_lease_and_body_errors(self):
        with lifecycle.lease(self.root):
            with lifecycle.lease(self.root):
                self.assertEqual('busy', self.child('exclusive'))
            with self.assertRaisesRegex(ValueError, 'cannot collect'):
                with lifecycle.lease(self.root, exclusive=True):
                    self.fail('upgraded a shared lease')
        with self.assertRaisesRegex(BlockingIOError, 'operation failed'):
            with lifecycle.lease(self.root):
                raise BlockingIOError('operation failed')
        self.assertEqual('acquired', self.child('exclusive'))


if __name__ == '__main__':
    unittest.main()
