"""Core retains selected ZIP toolchains without trusting changed extracted files."""

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import build_toolchain_setup


class BuildToolchainSetupTests(unittest.TestCase):
    def test_prepared_toolchain_reuses_verified_files_and_refuses_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / 'gradle.zip'
            with ZipFile(archive, 'w') as bundle:
                bundle.writestr('gradle-test/bin/gradle', b'#!/bin/sh\nexit 0\n')
            policy = {'archive_root': 'gradle-test', 'archive_url': 'https://example.invalid/gradle.zip',
                      'archive_sha256': sha256(archive.read_bytes()).hexdigest(),
                      'archive_size': archive.stat().st_size}
            with patch.object(build_toolchain_setup, 'fetch_verified_artifact', return_value=(archive, 'reused')) as fetch:
                home = build_toolchain_setup.prepare_zip_toolchain(base / 'state', policy, executable='bin/gradle')
                self.assertEqual(b'#!/bin/sh\nexit 0\n', (home / 'bin/gradle').read_bytes())
                self.assertEqual(home, build_toolchain_setup.prepare_zip_toolchain(
                    base / 'state', policy, executable='bin/gradle'))
                self.assertEqual(2, fetch.call_count)
                (home / 'bin/gradle').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'bytes changed'):
                    build_toolchain_setup.prepare_zip_toolchain(base / 'state', policy, executable='bin/gradle')


if __name__ == '__main__':
    unittest.main()
