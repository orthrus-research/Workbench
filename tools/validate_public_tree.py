#!/usr/bin/env python3
"""Reject private control-plane material from the tracked Workbench tree."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


PRIVATE_BASENAMES = {
    "AGENTS.md",
    "AGENTS.override.md",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "CODEX.md",
    "GEMINI.md",
    "GEMINI.local.md",
    "JUNIE.md",
    "QWEN.md",
    "SKILL.md",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".cursorignore",
    ".aiderignore",
}

PRIVATE_SEGMENTS = {
    ".agent",
    ".agents",
    ".skills",
    ".ai",
    ".codex",
    ".claude",
    ".gemini",
    ".junie",
    ".qwen",
    ".augment",
    ".cody",
    ".cursor",
    ".continue",
    ".cline",
    ".windsurf",
    ".roo",
    ".openhands",
    ".opencode",
    ".serena",
    "program",
}

PRIVATE_GLOBS = (
    ".aider*",
    ".github/copilot-instructions.md",
    ".github/copilot/**",
    ".github/agents/**",
    ".github/chatmodes/**",
    ".github/instructions/**",
    ".github/prompts/**",
    "modules/workbench-shell/data/developer-product-v2-task-ledger-*",
    "modules/workbench-shell/schemas/developer-product-v2-task-ledger-*",
    "modules/workbench-shell/schemas/developer-product-v2-task-evidence-*",
    "modules/workbench-shell/evidence/developer-product-v2-evidence-index-*",
    "packaging/portable/*dp01-ledger-source-snapshot-*",
    "modules/atlas/contracts/atlas-m4-retained-evidence-closure-handoff-v1.md",
    "modules/atlas/schemas/atlas-m4-retained-evidence-closure-v1.schema.json",
    "packaging/release/suite/**",
)

PRIVATE_FILENAME_GLOBS = (
    "agents.*.md", "claude.*.md", "codex.*.md", "gemini.*.md",
    "junie.*.md", "qwen.*.md", "*.agent.md",
)

PRIVATE_MARKDOWN_PATTERNS = (
    (
        re.compile(r"\b(?:ATLAS|BLUEPRINTS|CRUCIBLE)-M[0-9]+-[A-Z][0-9]+\b"),
        "internal component milestone ID",
    ),
    (
        re.compile(
            r"\bWORKBENCH-SHELL-(?:RFC|RF|FS)[0-9]+(?:-[A-Z][0-9]+)?\b"
        ),
        "internal Shell milestone ID",
    ),
    (
        re.compile(r"\bSUPERSYMMETRY-P[0-9]+-[A-Z][0-9]+\b"),
        "internal profile milestone ID",
    ),
)

# These roots are already excluded from source by repository policy and never
# exist in a sterile export. Pruning them makes `--filesystem` useful in a
# developer checkout with large IDE and package-manager caches.
GENERATED_DIRECTORY_NAMES = {
    ".git",
    ".workbench",
    ".pixi",
    ".firecrawl",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".vscode-test",
    ".gradle",
    ".idea",
    ".intellijPlatform",
    "build",
    "dist",
    "out",
}


def _run_git(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _tracked_paths(root: Path) -> list[str]:
    cached = set(_run_git(root, "ls-files", "--cached"))
    deleted = set(_run_git(root, "ls-files", "--deleted"))
    untracked = set(_run_git(root, "ls-files", "--others", "--exclude-standard"))
    return sorted((cached - deleted) | untracked)


def _filesystem_paths(root: Path) -> list[str]:
    paths: list[str] = []
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(directory)
        retained_directories: list[str] = []
        for name in directory_names:
            candidate = current / name
            relative = candidate.relative_to(root).as_posix()
            if name in GENERATED_DIRECTORY_NAMES:
                continue
            if candidate.is_symlink():
                paths.append(relative)
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            candidate = current / name
            paths.append(candidate.relative_to(root).as_posix())
    return sorted(paths)


def private_reason(path: str) -> str | None:
    pure = PurePosixPath(path)
    basename = pure.name.casefold()
    if basename in {name.casefold() for name in PRIVATE_BASENAMES}:
        return f"private harness basename {pure.name!r}"
    if any(fnmatch.fnmatchcase(basename, pattern) for pattern in PRIVATE_FILENAME_GLOBS):
        return "private agent instructions"
    if any(part.casefold() in PRIVATE_SEGMENTS for part in pure.parts):
        return "private harness directory"
    for pattern in PRIVATE_GLOBS:
        if fnmatch.fnmatchcase(path.casefold(), pattern.casefold()):
            return f"private path pattern {pattern!r}"
    return None


def private_markdown_reason(path: str, text: str) -> str | None:
    """Return why public Markdown contains private coordination language."""

    if not path.casefold().endswith(".md"):
        return None
    for pattern, reason in PRIVATE_MARKDOWN_PATTERNS:
        if pattern.search(text) is not None:
            return reason
    return None


def _markdown_content_failures(root: Path, paths: list[str]) -> list[str]:
    failures: list[str] = []
    for relative in paths:
        if not relative.casefold().endswith(".md"):
            continue
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}: cannot inspect public Markdown: {exc}")
            continue
        reason = private_markdown_reason(relative, text)
        if reason is not None:
            failures.append(f"{relative}: {reason}")
    return failures


def validate(root: Path, *, filesystem: bool) -> list[str]:
    paths = _filesystem_paths(root) if filesystem else _tracked_paths(root)
    failures = [
        f"{path}: {reason}"
        for path in paths
        if (reason := private_reason(path)) is not None
    ]
    failures.extend(_markdown_content_failures(root, paths))
    if not filesystem:
        ignored_tracked = sorted(
            set(
                _run_git(
                    root,
                    "ls-files",
                    "--cached",
                    "--ignored",
                    "--exclude-standard",
                )
            )
            - set(_run_git(root, "ls-files", "--deleted"))
        )
        failures.extend(
            f"{path}: tracked even though it is ignored" for path in ignored_tracked
        )
    return sorted(set(failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository or exported tree root",
    )
    parser.add_argument(
        "--filesystem",
        action="store_true",
        help="check an exported filesystem tree instead of Git's tracked paths",
    )
    args = parser.parse_args(argv)

    failures = validate(args.root.resolve(), filesystem=args.filesystem)
    if failures:
        print("public tree validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("public tree validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
