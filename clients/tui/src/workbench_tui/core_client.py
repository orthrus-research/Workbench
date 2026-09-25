"""Versioned subprocess adapter to an installed Workbench Core command."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RESOLUTION_ID = re.compile(r"workbench-environment-resolution:sha256:[0-9a-f]{64}\Z")
_SETUP_CHECK_FORMATS = {"workbench-setup-check-v1", "workbench-setup-check-v2"}
_SETUP_PLAN_FORMATS = {
    "workbench-setup-plan-v1",
    "workbench-setup-plan-v2",
    "workbench-setup-plan-v3",
}
_MIGRATION_FORMAT = "workbench-user-config-migration-v1"
_MIGRATION_STATES = {"ready", "conflict", "nothing-to-import", "explicit-config-home", "imported"}
_MIGRATION_FILE_STATES = {"copy", "already-present", "conflict", "copied"}


class CoreClientError(RuntimeError):
    """The selected Core is unavailable or returned an unsupported record."""


@dataclass(frozen=True)
class CommandOutput:
    arguments: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SetupInputs:
    """Only user-selected CLI inputs; Core owns the resulting setup selection."""

    mode: str
    workspace: str
    profile_config: str = ""
    state_root: str = ""
    java_home: str = ""
    git_executable: str = ""

    def option_args(self) -> tuple[str, ...]:
        if self.mode not in {"full", "review", "repair"}:
            raise ValueError("choose a setup journey")
        if not self.workspace.strip():
            raise ValueError("choose a workspace")
        if self.mode == "full" and not self.profile_config.strip():
            raise ValueError("full developer setup needs an existing workbench.toml")
        if self.mode == "review" and (self.profile_config.strip() or self.java_home.strip()):
            raise ValueError("review-only setup cannot select a profile or Java runtime")
        args: list[str] = []
        if self.mode == "repair":
            args.append("--repair")
        for flag, value in (
            ("--workspace", self.workspace),
            ("--profile-config", self.profile_config),
            ("--state-root", self.state_root),
            ("--java-home", self.java_home),
            ("--git-executable", self.git_executable),
        ):
            if value.strip():
                args.extend((flag, str(Path(value).expanduser().absolute())))
        return tuple(args)


class CoreClient:
    """Runs one explicitly selected Workbench CLI; never imports Core modules."""

    def __init__(self, command: Sequence[str], *, cwd: Path | None = None) -> None:
        if not command or any(not part for part in command):
            raise ValueError("a Workbench command is required")
        self.command = tuple(command)
        self.cwd = cwd

    async def call(
        self,
        *arguments: str,
        timeout: float = 45,
        allowed_exit: Sequence[int] = (0,),
        max_output: int = 8 * 1024 * 1024,
    ) -> CommandOutput:
        # Core's JSON endpoints emit Unicode directly. A redirected Python
        # stdout may otherwise use the host's legacy code page.
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                *arguments,
                cwd=self.cwd,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CoreClientError(f"cannot start selected Workbench Core: {exc}") from exc
        async def read_limited(stream: asyncio.StreamReader) -> bytes:
            chunks: list[bytes] = []
            total = 0
            while chunk := await stream.read(64 * 1024):
                total += len(chunk)
                if total > max_output:
                    raise CoreClientError("Workbench response exceeds the client display limit")
                chunks.append(chunk)
            return b"".join(chunks)

        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(read_limited(process.stdout))
        stderr_task = asyncio.create_task(read_limited(process.stderr))
        wait_task = asyncio.create_task(process.wait())
        try:
            stdout_bytes, stderr_bytes, _ = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, wait_task), timeout=timeout
            )
        except (asyncio.CancelledError, TimeoutError, CoreClientError) as exc:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
            for task in (stdout_task, stderr_task, wait_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
            if isinstance(exc, TimeoutError):
                raise CoreClientError(
                    f"Workbench command timed out after {timeout:g}s"
                ) from exc
            raise
        result = CommandOutput(
            arguments=tuple(arguments),
            exit_code=process.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )
        if result.exit_code not in allowed_exit:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
            raise CoreClientError(f"Workbench exited {result.exit_code}: {detail[:1200]}")
        return result

    async def json_record(
        self,
        *arguments: str,
        timeout: float = 45,
        allowed_exit: Sequence[int] = (0,),
    ) -> Any:
        output = await self.call(
            *arguments, timeout=timeout, allowed_exit=allowed_exit
        )
        try:
            return json.loads(output.stdout)
        except json.JSONDecodeError as exc:
            detail = output.stderr.strip()
            if not output.stdout.strip():
                message = f"Workbench exited {output.exit_code} without JSON"
            else:
                message = "Workbench returned invalid JSON"
            if detail:
                message += f": {detail[:1200]}"
            raise CoreClientError(message) from exc

    async def version(self) -> Mapping[str, Any]:
        record = await self.json_record("version", "--json")
        if not isinstance(record, dict) or record.get("component_id") != "workbench-core":
            raise CoreClientError("selected command is not a compatible Workbench Core")
        if not isinstance(record.get("version"), str):
            raise CoreClientError("Core version record is incomplete")
        return record

    async def setup_check(self, options: Sequence[str] = ()) -> Mapping[str, Any]:
        record = await self.json_record(
            "setup", "--check", "--json", *options, allowed_exit=(0, 1)
        )
        if not isinstance(record, dict) or record.get("format") not in _SETUP_CHECK_FORMATS:
            raise CoreClientError("unsupported Workbench setup check format")
        if not isinstance(record.get("dependencies"), list):
            raise CoreClientError("Workbench setup check has no dependency list")
        return record

    async def environment_resolve(self, workspace: str = "") -> Mapping[str, Any]:
        arguments = ["environment", "resolve"]
        if workspace:
            arguments.append(workspace)
        record = await self.json_record(*arguments, "--json")
        if not isinstance(record, dict) or record.get("format") != "workbench-environment-resolution-v1":
            raise CoreClientError("unsupported Workbench environment resolution format")
        selected = record.get("workspace")
        if (
            not isinstance(selected, dict)
            or not isinstance(selected.get("path"), str)
            or not isinstance(selected.get("source"), str)
            or not isinstance(record.get("resolution_id"), str)
            or _RESOLUTION_ID.fullmatch(record["resolution_id"]) is None
        ):
            raise CoreClientError("Workbench environment resolution is incomplete")
        return record

    @staticmethod
    def _migration_record(record: Any, *, preview: bool) -> Mapping[str, Any]:
        if (
            not isinstance(record, dict)
            or record.get("format") != _MIGRATION_FORMAT
            or type(record.get("schema_version")) is not int
            or record["schema_version"] != 1
            or not isinstance(record.get("state"), str)
            or record["state"] not in _MIGRATION_STATES
            or not isinstance(record.get("files"), list)
            or len(record["files"]) > 64
        ):
            raise CoreClientError("unsupported Workbench configuration migration record")
        for key in ("source", "destination"):
            path = record.get(key)
            if not isinstance(path, str) or not path or not Path(path).is_absolute():
                raise CoreClientError("Workbench configuration migration has invalid paths")
        seen: set[str] = set()
        for row in record["files"]:
            if not isinstance(row, dict):
                raise CoreClientError("Workbench configuration migration has invalid file entries")
            name = row.get("name")
            digest = row.get("sha256")
            state = row.get("state")
            if (
                not isinstance(name, str)
                or not name
                or name in seen
                or Path(name).name != name
                or "\\" in name
                or not name.endswith(".json")
                or not isinstance(digest, str)
                or _DIGEST.fullmatch(digest) is None
                or not isinstance(state, str)
                or state not in _MIGRATION_FILE_STATES
            ):
                raise CoreClientError("Workbench configuration migration has invalid file entries")
            seen.add(name)
        states = {row["state"] for row in record["files"]}
        if (
            (record["state"] == "ready" and ("copy" not in states or "conflict" in states))
            or (record["state"] == "conflict" and "conflict" not in states)
            or (record["state"] in {"nothing-to-import", "explicit-config-home"}
                and ("copy" in states or "conflict" in states))
        ):
            raise CoreClientError("Workbench configuration migration state is inconsistent")
        if preview and (
            record["state"] == "imported"
            or any(row["state"] == "copied" for row in record["files"])
        ):
            raise CoreClientError("Workbench returned an import result instead of a migration preview")
        return record

    async def migration_preview(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "settings", "migrate", "--dry-run", "--json", allowed_exit=(0, 1)
        )
        return self._migration_record(record, preview=True)

    async def migration_import(self, reviewed: Mapping[str, Any]) -> Mapping[str, Any]:
        if reviewed.get("state") != "ready":
            raise CoreClientError("configuration import requires a ready, reviewed migration preview")
        current = await self.migration_preview()
        if current != reviewed:
            raise CoreClientError(
                "legacy configuration changed before import; review the migration again"
            )
        record = await self.json_record(
            "settings", "migrate", "--json", allowed_exit=(0, 1)
        )
        result = self._migration_record(record, preview=False)
        if result["state"] != "imported":
            raise CoreClientError(
                f"configuration import did not complete ({result['state']}); review the migration again"
            )
        return result

    async def setup_plan(self, options: Sequence[str]) -> Mapping[str, Any]:
        record = await self.json_record("setup", "--plan", "--json", *options)
        if not isinstance(record, dict) or record.get("format") not in _SETUP_PLAN_FORMATS:
            raise CoreClientError("unsupported Workbench setup plan format")
        if not isinstance(record.get("plan_id"), str) or not isinstance(record.get("actions"), list):
            raise CoreClientError("Workbench setup plan is incomplete")
        return record

    async def setup_apply(
        self, plan_id: str, options: Sequence[str]
    ) -> Mapping[str, Any]:
        if not plan_id.startswith("workbench-setup-plan-"):
            raise CoreClientError("invalid setup plan identity")
        record = await self.json_record(
            "setup", "--apply", plan_id, "--json", *options, timeout=3600
        )
        if not isinstance(record, dict) or record.get("applied_plan_id") != plan_id:
            raise CoreClientError("Workbench setup result does not match the reviewed plan")
        return record

    async def modules(self) -> list[dict[str, Any]]:
        record = await self.json_record("modules", "list", "--json")
        if not isinstance(record, list) or not all(isinstance(item, dict) for item in record):
            raise CoreClientError("unsupported module inventory")
        return record

    async def profiles(self) -> list[dict[str, Any]]:
        record = await self.json_record("profiles", "list", "--json")
        if not isinstance(record, list) or not all(isinstance(item, dict) for item in record):
            raise CoreClientError("unsupported profile inventory")
        return record

    async def catalog(self) -> Mapping[str, Any]:
        record = await self.json_record("console", "catalog", "--json")
        if (
            not isinstance(record, dict)
            or record.get("format_version") != "workbench-live-console-command-catalog-v2"
            or not isinstance(record.get("commands"), list)
            or not isinstance(record.get("catalog_digest"), str)
            or not _DIGEST.fullmatch(record["catalog_digest"])
        ):
            raise CoreClientError("unsupported Workbench command catalog")
        return record

    async def workspace_home(self, workspace: str) -> Mapping[str, Any]:
        record = await self.json_record("open", workspace, "--json")
        if not isinstance(record, dict) or record.get("format") != "workbench-workspace-home-v2":
            raise CoreClientError("unsupported Workspace Home format")
        return record

    async def java_inventory(self, profile_config: str = "") -> Mapping[str, Any]:
        args = ["setup", "--list-java", "--json"]
        if profile_config.strip():
            args.extend(("--profile-config", str(Path(profile_config).expanduser().absolute())))
        record = await self.json_record(*args)
        if not isinstance(record, dict) or record.get("format") != "workbench-java-inventory-v1":
            raise CoreClientError("unsupported Java inventory")
        return record

    async def command_review(
        self, catalog: Mapping[str, Any], action: Mapping[str, Any], values: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        arguments = self._bound_command(catalog, action, values)
        record = await self.json_record(*arguments, "--review-json")
        if (
            not isinstance(record, dict)
            or record.get("format_version") != "workbench-live-console-command-review-v2"
            or record.get("catalog_digest") != catalog["catalog_digest"]
            or record.get("action_digest") != action["action_digest"]
            or record.get("command_id") != action["command_id"]
            or not isinstance(record.get("review_digest"), str)
            or not _DIGEST.fullmatch(record["review_digest"])
        ):
            raise CoreClientError("Workbench review does not match the selected action")
        return record

    async def open_document(
        self, catalog: Mapping[str, Any], action: Mapping[str, Any]
    ) -> CommandOutput:
        if action.get("risk") != "read-only" or not isinstance(action.get("document"), str):
            raise CoreClientError("this catalog entry is not a read-only document")
        arguments = self._bound_command(catalog, action, {})
        return await self.call(*arguments, timeout=45)

    async def run_reviewed_command(
        self,
        catalog: Mapping[str, Any],
        action: Mapping[str, Any],
        values: Mapping[str, Any],
        review: Mapping[str, Any],
    ) -> CommandOutput:
        if review.get("risk") != "read-only" or action.get("risk") != "read-only":
            raise CoreClientError("this prototype launches read-only catalog actions")
        digest = review.get("review_digest")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise CoreClientError("the selected review has no valid digest")
        arguments = self._bound_command(catalog, action, values)
        return await self.call(
            *arguments,
            "--expect-review-digest",
            digest,
            "--execute",
            "--no-retain",
            "--console",
            "plain",
            timeout=120,
            allowed_exit=tuple(range(0, 256)),
        )

    @staticmethod
    def _bound_command(
        catalog: Mapping[str, Any], action: Mapping[str, Any], values: Mapping[str, Any]
    ) -> tuple[str, ...]:
        catalog_digest = catalog.get("catalog_digest")
        action_digest = action.get("action_digest")
        command_id = action.get("command_id")
        if not all(isinstance(item, str) and item for item in (catalog_digest, action_digest, command_id)):
            raise CoreClientError("catalog action identity is incomplete")
        if not _DIGEST.fullmatch(catalog_digest) or not _DIGEST.fullmatch(action_digest):
            raise CoreClientError("catalog action digest is invalid")
        known_keys = {item.get("key") for item in action.get("options", []) if isinstance(item, dict)}
        if not set(values) <= known_keys:
            raise CoreClientError("input is not declared by the selected action")
        args = [
            "console", "run", command_id,
            "--expect-catalog-digest", catalog_digest,
            "--expect-action-digest", action_digest,
        ]
        for key, value in values.items():
            args.extend(("--set", f"{key}:={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"))
        return tuple(args)
