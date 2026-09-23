"""Core custody for a profile-selected, hash-locked ZIP build toolchain."""

from pathlib import Path
import shutil
from uuid import uuid4

from . import check_storage as storage
from .artifact_store import fetch_verified_artifact
from .filesystem_paths import native_path
from .runtime_java import _extract_zip


def prepare_zip_toolchain(root, policy, *, executable):
    """Acquire once, retain exact extracted bytes, and return its ordinary home."""
    if (not isinstance(policy, dict) or not isinstance(executable, str)
            or not {'archive_root', 'archive_url', 'archive_sha256', 'archive_size'}.issubset(policy)
            or not policy['archive_url'].startswith('https://')
            or not isinstance(policy['archive_size'], int) or policy['archive_size'] <= 0
            or len(policy['archive_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in policy['archive_sha256'])):
        raise ValueError('build toolchain requires a selected exact ZIP archive')
    for name in (policy['archive_root'], executable):
        storage.safe_path(name)
    root = Path(root).absolute()
    storage.initialize(root)
    archive, _ = fetch_verified_artifact(url=policy['archive_url'],
        expected_sha256=policy['archive_sha256'], expected_size=policy['archive_size'],
        state_root=root / '.workbench', label='selected build toolchain')
    parent = root / '.workbench/build-toolchains'
    native_path(parent).mkdir(parents=True, exist_ok=True)
    destination = parent / policy['archive_sha256']
    with storage.execution_lock(parent):
        if native_path(destination).exists():
            retained = storage.read_json(destination / 'receipt.json', byte_limit=None)
            if retained.get('policy') != policy or retained.get('executable') != executable:
                raise ValueError('retained build toolchain policy changed')
            observed = storage.tree_manifest(destination / 'content')
            if observed != retained['files']:
                raise ValueError('retained build toolchain bytes changed')
            home = destination / 'content' / policy['archive_root']
            if not native_path(home / executable).is_file():
                raise ValueError('retained build toolchain executable is missing')
            return home
        staging = parent / ('.toolchain-' + uuid4().hex)
        native_path(staging).mkdir(mode=0o700)
        try:
            _extract_zip(archive, staging / 'content', label='build toolchain')
            home = staging / 'content' / policy['archive_root']
            if not native_path(home).is_dir() or not native_path(home / executable).is_file():
                raise ValueError('build toolchain archive lacks the selected executable')
            rows = storage.tree_manifest(staging / 'content')
            storage.write_json(staging / 'receipt.json', {'policy': policy,
                'executable': executable, 'files': rows}, byte_limit=None)
            native_path(staging).rename(native_path(destination))
        except BaseException:
            shutil.rmtree(native_path(staging), ignore_errors=True)
            raise
    return destination / 'content' / policy['archive_root']
