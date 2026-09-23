"""Reject native wheel file/launcher collisions; this is not a Python sandbox."""
from __future__ import annotations

from email.parser import BytesParser
from importlib import metadata
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sysconfig
import zipfile
import configparser

from packaging.utils import canonicalize_name
from workbench_api import ModuleError


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def validate_wheel_ownership(path: Path, distribution_name: str) -> None:
    """Allow shared namespace directories, but never foreign-owned files.

    Only package-root and purelib/platlib data-scheme members are admitted.
    Headers, arbitrary data destinations and raw scripts need another explicit
    installation contract and are deliberately unsupported by module lifecycle.
    """
    selected = canonicalize_name(distribution_name)
    paths = {name: Path(value).resolve() for name, value in sysconfig.get_paths().items()}
    owners: dict[str, set[str]] = {}
    script_owners: dict[str, set[str]] = {}
    try:
        for distribution in metadata.distributions():
            owner = canonicalize_name(distribution.metadata['Name'])
            for member in distribution.files or ():
                owners.setdefault(_key(Path(distribution.locate_file(member))), set()).add(owner)
            for entry in distribution.entry_points:
                if entry.group in {'console_scripts', 'gui_scripts'}:
                    script_owners.setdefault(os.path.normcase(entry.name), set()).add(owner)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise ModuleError('cannot verify installed distribution file ownership') from exc

    targets: set[str] = set()

    def admit(target: Path, root: Path) -> None:
        if not target.resolve().is_relative_to(root):
            raise ModuleError('wheel member escapes its installation scheme')
        key = _key(target)
        if key in targets:
            raise ModuleError(f'wheel repeats an installation target: {target.name}')
        targets.add(key)
        foreign = owners.get(key, set()) - {selected}
        if foreign:
            raise ModuleError('wheel would overwrite files owned by ' + ', '.join(sorted(foreign)))
        if (target.exists() or target.is_symlink()) and (key not in owners or target.is_dir()):
            raise ModuleError(f'wheel would overwrite an unowned file or directory: {target.name}')
        for parent in target.parents:
            if parent == root:
                break
            if parent.exists() and not parent.is_dir():
                raise ModuleError('wheel requires a directory occupied by an installed file')

    with zipfile.ZipFile(path) as archive:
        wheel_names = [name for name in archive.namelist() if name.endswith('.dist-info/WHEEL')]
        if len(wheel_names) != 1 or archive.getinfo(wheel_names[0]).file_size > 65536:
            raise ModuleError('wheel must declare one bounded installation scheme')
        wheel = BytesParser().parsebytes(archive.read(wheel_names[0]))
        purelib = wheel.get_all('Root-Is-Purelib', [])
        if len(purelib) != 1 or purelib[0].lower() not in {'true', 'false'}:
            raise ModuleError('wheel installation scheme is invalid')
        root = paths['purelib' if purelib[0].lower() == 'true' else 'platlib']
        metadata_root = wheel_names[0].rsplit('/', 1)[0]
        metadata_names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if metadata_names != [metadata_root + '/METADATA']:
            raise ModuleError('wheel metadata and installation scheme have different owners')
        data_root = metadata_root.removesuffix('.dist-info') + '.data'
        for member in archive.infolist():
            relative = PurePosixPath(member.filename)
            if not relative.parts or relative.is_absolute() or '..' in relative.parts or '\\' in member.filename or ':' in member.filename or '\0' in member.filename or stat.S_ISLNK(member.external_attr >> 16):
                raise ModuleError('wheel contains an unsafe member path')
            if member.is_dir():
                continue
            destination = root
            if relative.parts[0].endswith('.data'):
                if relative.parts[0] != data_root or len(relative.parts) < 3 or relative.parts[1] not in {'purelib', 'platlib'}:
                    raise ModuleError('module wheels support only purelib/platlib .data layouts')
                destination = paths[relative.parts[1]]
                relative = PurePosixPath(*relative.parts[2:])
            admit(destination.joinpath(*relative.parts), destination)
        entries_path = metadata_root + '/entry_points.txt'
        if entries_path not in archive.namelist():
            return
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(archive.read(entries_path).decode('utf-8'))
        for group in ('console_scripts', 'gui_scripts'):
            for name in parser[group] if parser.has_section(group) else ():
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) or name.casefold() in {'workbench', 'workbench.exe', 'workbench-script.py'}:
                    raise ModuleError('module wheels cannot replace the Workbench launcher or declare unsafe script names')
                if script_owners.get(os.path.normcase(name), set()) - {selected}:
                    raise ModuleError(f'entry-point launcher is owned by another distribution: {name}')
                for suffix in ('', '.exe', '-script.py'):
                    admit(paths['scripts'] / (name + suffix), paths['scripts'])
