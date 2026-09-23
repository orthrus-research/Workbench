"""Storage identities use portable POSIX paths before native containment checks."""
from pathlib import Path
import tempfile
import unittest

from workbench_core.storage.manager import (
    RuntimeManagerError,
    _canonical_relative_path,
    _inventory_bound_path,
    inventory_storage,
)


class StorageWirePathTests(unittest.TestCase):
    def test_posix_wire_spelling_converts_to_native_path_components(self):
        self.assertEqual(Path('.workbench') / 'cache' / 'item',
            _canonical_relative_path('.workbench/cache/item', 'probe'))

    def test_noncanonical_traversal_backslash_drive_and_unc_values_are_rejected(self):
        for value in ('', '.', '..', '../item', 'a/../item', 'a/..', 'a/./item',
            './item', 'a//item', 'a/', '/item', '//server/share/item',
            '\\server\\share', 'a\\item', 'C:/item', 'C:item', 'a/item:stream',
            'a\x00item', None, 1):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeManagerError, 'canonical relative path'):
                _canonical_relative_path(value, 'probe')

    def test_native_containment_still_rejects_outside_and_storage_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            storage = workspace / '.workbench'
            for value in ('.workbench', 'outside/item'):
                with self.subTest(value=value), self.assertRaisesRegex(RuntimeManagerError, 'escapes'):
                    _inventory_bound_path(value, workspace, storage, 'probe')
            self.assertEqual(storage / 'cache' / 'item',
                _inventory_bound_path('.workbench/cache/item', workspace, storage, 'probe'))

    def test_inventory_accepts_its_own_posix_wire_paths_on_the_native_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            cache = workspace / '.workbench/cache/item'
            cache.mkdir(parents=True)
            (cache / 'payload').write_bytes(b'cache')
            report = inventory_storage(workspace)
            item = next(row for row in report['items'] if Path(row['path']) == cache)
            self.assertEqual('.workbench/cache/item', item['relative_path'])


if __name__ == '__main__':
    unittest.main()
