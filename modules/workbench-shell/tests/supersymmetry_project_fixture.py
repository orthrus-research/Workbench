"""Shared Supersymmetry Git project fixture for Workbench Shell tests."""

from __future__ import annotations

from pathlib import Path
import subprocess


PACK_TOML = """\
name = "Supersymmetry"
author = "SymmetricDevs"
version = "test"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

[versions]
forge = "14.23.5.2860"
minecraft = "1.12.2"
"""


def _run_git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def create_supersymmetry_project(parent: Path) -> Path:
    project = parent / "supersymmetry"
    project.mkdir()
    for directory in ("config", "groovy", "mods"):
        (project / directory).mkdir()
    (project / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
    (project / "index.toml").write_text("", encoding="utf-8")
    _run_git(project, "init", "--quiet")
    _run_git(project, "config", "user.name", "Workbench Test")
    _run_git(project, "config", "user.email", "workbench@example.invalid")
    _run_git(project, "add", ".")
    _run_git(project, "commit", "--quiet", "-m", "fixture")
    return project
