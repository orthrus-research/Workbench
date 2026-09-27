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
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_TOOL_POLICY_ID = re.compile(r"workbench-managed-tool-policy:sha256:[0-9a-f]{64}\Z")
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
_WORKSPACE_FORMATS = {"workbench-user-workspaces-v1", "workbench-user-workspaces-v2", "workbench-user-workspaces-v3"}
_PACK_RELEASE_SCHEMA = "workbench.pack-release.v1"
_GITHUB_RELEASE_ID = re.compile(r"github-release:sha256:[0-9a-f]{64}\Z")
_SAVED_RELEASE_ID = re.compile(r"(?:github|profile)-release:sha256:[0-9a-f]{64}\Z")
_PACK_INSTANCE_PLAN_ID = re.compile(r"workbench-pack-release-client-composition-plan:sha256:[0-9a-f]{64}\Z")
_PACK_INSTANCE_RECORD_ID = re.compile(r"workbench-pack-instance-choice:sha256:[0-9a-f]{64}\Z")
_PACK_POLICY_PLAN_ID = re.compile(r"workbench-pack-release-derived-policies-plan:sha256:[0-9a-f]{64}\Z")
_PRISM_ZIP_STAGE_PLAN_ID = re.compile(r"workbench-prism-zip-stage-plan:sha256:[0-9a-f]{64}\Z")


class CoreClientError(RuntimeError):
    """The selected Core is unavailable or returned an unsupported record."""


def _project_source_lock_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"relative_path", "sha256", "repository", "revision", "tree"}
        and isinstance(value["relative_path"], str)
        and value["relative_path"].startswith("profiles/")
        and isinstance(value["sha256"], str)
        and _DIGEST.fullmatch(value["sha256"]) is not None
        and isinstance(value["repository"], str)
        and value["repository"].startswith("https://")
        and value["repository"].endswith(".git")
        and isinstance(value["revision"], str)
        and _GIT_ID.fullmatch(value["revision"]) is not None
        and isinstance(value["tree"], str)
        and _GIT_ID.fullmatch(value["tree"]) is not None
    )


def _managed_tool_lock_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("format") == "workbench-managed-tool-policy-lock-v1"
        and isinstance(value.get("lock_id"), str)
        and _TOOL_POLICY_ID.fullmatch(value["lock_id"]) is not None
        and isinstance(value.get("assets"), dict)
        and set(value["assets"]) == {"prism", "go", "packwiz"}
        and isinstance(value.get("host_variant"), dict)
        and set(value["host_variant"]) == {"os", "architecture"}
    )


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
        terminate_wait_seconds: float = 5,
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
                    await asyncio.wait_for(process.wait(), timeout=terminate_wait_seconds)
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
        terminate_wait_seconds: float = 5,
        allowed_exit: Sequence[int] = (0,),
    ) -> Any:
        output = await self.call(
            *arguments, timeout=timeout, terminate_wait_seconds=terminate_wait_seconds,
            allowed_exit=allowed_exit,
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

    @staticmethod
    def _pack_release_record(record: Any, *, action: str) -> Mapping[str, Any]:
        statuses = {
            "show": {"selected"},
            "check": {"update_available", "current", "ignored", "unavailable"},
            "accept": {"accepted"},
            "ignore": {"ignored"},
            "prepare": {"prepared"},
        }
        if (
            not isinstance(record, dict)
            or record.get("schema") != _PACK_RELEASE_SCHEMA
            or record.get("action") != action
            or record.get("status") not in statuses[action]
            or not isinstance(record.get("selected_version"), str)
        ):
            raise CoreClientError("Core returned an unsupported pack release result")
        candidate = record.get("candidate")
        if record["status"] == "update_available" or action in {"accept", "ignore", "prepare"}:
            if (
                not isinstance(candidate, dict)
                or not isinstance(candidate.get("release_id"), str)
                or (_SAVED_RELEASE_ID if action == "prepare" else _GITHUB_RELEASE_ID).fullmatch(candidate["release_id"]) is None
                or not isinstance(candidate.get("version"), str)
                or not isinstance(candidate.get("tag"), str)
                or not isinstance(candidate.get("asset_name"), str)
                or type(candidate.get("asset_size")) is not int
                or candidate["asset_size"] <= 0
            ):
                raise CoreClientError("Core returned an incomplete pack release candidate")
        elif candidate is not None and not isinstance(candidate, dict):
            raise CoreClientError("Core returned an invalid pack release candidate")
        if action in {"accept", "prepare"} and not isinstance(record.get("artifact_path"), str):
            raise CoreClientError("Core did not report the verified pack archive")
        if action == "show":
            selected = record.get("selected")
            if (
                not isinstance(selected, dict)
                or selected.get("version") != record["selected_version"]
                or not isinstance(selected.get("release_id"), str)
                or _SAVED_RELEASE_ID.fullmatch(selected["release_id"]) is None
                or type(selected.get("asset_size")) is not int
                or selected["asset_size"] <= 0
                or record.get("artifact_state") not in {"none", "verified", "missing", "changed", "other_root"}
                or (record["artifact_state"] == "verified"
                    and not isinstance(selected.get("artifact_path"), str))
            ):
                raise CoreClientError("Core returned an incomplete saved pack choice")
        return record

    async def pack_release_show(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "release", "show", "--profile", "supersymmetry", "--json",
            timeout=15,
        )
        return self._pack_release_record(record, action="show")

    async def pack_release_check(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "release", "check", "--profile", "supersymmetry", "--json",
            timeout=15,
        )
        return self._pack_release_record(record, action="check")

    async def pack_release_accept(self, release_id: str) -> Mapping[str, Any]:
        if _GITHUB_RELEASE_ID.fullmatch(release_id) is None:
            raise CoreClientError("select the exact release offered by Core")
        record = await self.json_record(
            "pack", "release", "accept", "--profile", "supersymmetry",
            "--expected-release-id", release_id, "--json", timeout=600,
        )
        result = self._pack_release_record(record, action="accept")
        if result["candidate"]["release_id"] != release_id:
            raise CoreClientError("Core selected a different pack release")
        return result

    async def pack_release_ignore(self, release_id: str) -> Mapping[str, Any]:
        if _GITHUB_RELEASE_ID.fullmatch(release_id) is None:
            raise CoreClientError("select the exact release offered by Core")
        record = await self.json_record(
            "pack", "release", "ignore", "--profile", "supersymmetry",
            "--expected-release-id", release_id, "--json", timeout=15,
        )
        result = self._pack_release_record(record, action="ignore")
        if result["candidate"]["release_id"] != release_id:
            raise CoreClientError("Core ignored a different pack release")
        return result

    async def pack_release_prepare(self, release_id: str) -> Mapping[str, Any]:
        if _SAVED_RELEASE_ID.fullmatch(release_id) is None:
            raise CoreClientError("select the exact release offered by Core")
        record = await self.json_record(
            "pack", "release", "prepare", "--profile", "supersymmetry",
            "--expected-release-id", release_id, "--json", timeout=600,
        )
        result = self._pack_release_record(record, action="prepare")
        if result["candidate"]["release_id"] != release_id:
            raise CoreClientError("Core prepared a different pack release")
        return result

    @staticmethod
    def _pack_instance_record(record: Any, action: str) -> Mapping[str, Any]:
        if (not isinstance(record, dict)
                or record.get("schema") != "workbench.pack-instance.v1"
                or record.get("action") != action):
            raise CoreClientError("Core returned an unsupported pack instance result")
        if action.startswith("zip-stage-"):
            stage = record.get("stage")
            if (not isinstance(stage, dict)
                    or not isinstance(stage.get("plan_id"), str)
                    or _PRISM_ZIP_STAGE_PLAN_ID.fullmatch(stage["plan_id"]) is None
                    or not isinstance(stage.get("archive_path"), str)
                    or not Path(stage["archive_path"]).is_absolute()
                    or not isinstance(stage.get("source_path"), str)
                    or not Path(stage["source_path"]).is_absolute()
                    or not isinstance(stage.get("source_sha256"), str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", stage["source_sha256"]) is None
                    or type(stage.get("source_size")) is not int
                    or stage["source_size"] <= 0):
                raise CoreClientError("Core returned an incomplete Prism ZIP staging result")
            if action == "zip-stage-plan" and stage.get("action") not in {"direct", "copy"}:
                raise CoreClientError("Core returned an unsupported Prism ZIP staging plan")
            if action == "zip-stage-apply" and stage.get("outcome") not in {
                    "direct", "copied", "reused"}:
                raise CoreClientError("Core did not retain the reviewed Prism ZIP")
        elif action.startswith("zip-"):
            source = record.get("source")
            if (not isinstance(source, dict)
                    or not isinstance(source.get("plan_id"), str)
                    or _PACK_INSTANCE_PLAN_ID.fullmatch(source["plan_id"]) is None
                    or source.get("source_kind") != "user-prism-zip"
                    or type(source.get("file_count")) is not int
                    or source["file_count"] <= 0):
                raise CoreClientError("Core returned an incomplete ZIP source result")
        elif action.startswith("fresh-policy-"):
            policy = record.get("policy")
            if (not isinstance(policy, dict)
                    or policy.get("schema") != "workbench.pack-release.fresh-setup.v1"):
                raise CoreClientError("Core returned an incomplete release policy result")
            if action == "fresh-policy-status" and (
                    policy.get("status") not in {"ready", "review_required", "unavailable"}
                    or (policy.get("status") == "review_required" and (
                        not isinstance(policy.get("required_external_files"), list)
                        or not isinstance(policy.get("optional_external_files"), list)
                        or not isinstance(policy.get("suggestions"), dict)))):
                raise CoreClientError("Core returned an incomplete release policy review")
            if action == "fresh-policy-plan" and (
                    policy.get("status") != "planned"
                    or not isinstance(policy.get("policy_plan"), dict)
                    or not isinstance(policy["policy_plan"].get("plan_id"), str)
                    or _PACK_POLICY_PLAN_ID.fullmatch(policy["policy_plan"]["plan_id"]) is None):
                raise CoreClientError("Core returned an incomplete release policy plan")
            if action == "fresh-policy-apply" and (
                    policy.get("status") != "ready"
                    or not isinstance(policy.get("policy_plan_id"), str)
                    or _PACK_POLICY_PLAN_ID.fullmatch(policy["policy_plan_id"]) is None):
                raise CoreClientError("Core did not retain the reviewed release policy")
        elif action.startswith("fresh-"):
            fresh = record.get("fresh")
            if (not isinstance(fresh, dict)
                    or fresh.get("schema") != "workbench.pack-release.fresh-setup.v1"):
                raise CoreClientError("Core returned an incomplete fresh source result")
            if action == "fresh-status" and (
                    fresh.get("status") not in {"unavailable", "pending", "ready", "invalid"}
                    or not isinstance(fresh.get("files"), list)
                    or fresh.get("provider_state") not in {"available", "unavailable"}):
                raise CoreClientError("Core returned an incomplete fresh source status")
            if action == "fresh-publish":
                composition = fresh.get("composition_result")
                if (fresh.get("status") != "ready" or not isinstance(composition, dict)
                        or not isinstance(composition.get("plan_id"), str)
                        or _PACK_INSTANCE_PLAN_ID.fullmatch(composition["plan_id"]) is None):
                    raise CoreClientError("Core did not publish a complete fresh source")
        elif action.startswith("root-"):
            root = record.get("prism_root")
            if (not isinstance(root, dict)
                    or not isinstance(root.get("plan_id"), str)
                    or re.fullmatch(
                        r"workbench-prism-data-root-plan:sha256:[0-9a-f]{64}",
                        root["plan_id"],
                    ) is None):
                raise CoreClientError("Core returned an incomplete Prism folder review")
            if action == "root-plan" and (
                    root.get("action") not in {"initialize", "reuse", "reconcile", "blocked"}
                    or root.get("state") not in {"ready", "blocked"}
                    or not isinstance(root.get("blockers"), list)):
                raise CoreClientError("Core returned an incomplete Prism folder plan")
            if action in {"root-reconcile", "root-abandon"} and root.get("outcome") not in {
                    "reconciled", "abandoned"}:
                raise CoreClientError("Core did not report a Prism folder recovery outcome")
        elif action == "install-status":
            installations = record.get("installations")
            if (not isinstance(installations, list)
                    or record.get("count") != len(installations)
                    or any(not isinstance(row, dict)
                           or not isinstance(row.get("plan_id"), str)
                           or re.fullmatch(
                               r"workbench-pack-release-client-install-plan:sha256:[0-9a-f]{64}",
                               row["plan_id"],
                           ) is None
                           or not isinstance(row.get("instance_path"), str)
                           for row in installations)):
                raise CoreClientError("Core returned an incomplete installed instance list")
        elif action.startswith("install-"):
            installation = record.get("installation")
            if (not isinstance(installation, dict)
                    or not isinstance(installation.get("plan_id"), str)
                    or re.fullmatch(
                        r"workbench-pack-release-client-install-plan:sha256:[0-9a-f]{64}",
                        installation["plan_id"],
                    ) is None
                    or (action != "install-abandon" and (
                        not isinstance(installation.get("instance_path"), str)
                        or not Path(installation["instance_path"]).is_absolute()))
                    or (action == "install-abandon" and (
                        installation.get("outcome") != "abandoned"
                        or not isinstance(installation.get("retained_stage_path"), str)))
                    or not isinstance(record.get("source_plan_id"), str)
                    or _PACK_INSTANCE_PLAN_ID.fullmatch(record["source_plan_id"]) is None):
                raise CoreClientError("Core returned an incomplete instance installation")
        elif action.startswith("launch-"):
            launch = record.get("launch")
            identity = (launch.get("plan_id" if action == "launch-plan" else "launch_plan_id")
                        if isinstance(launch, dict) else None)
            if (not isinstance(launch, dict)
                    or not isinstance(identity, str)
                    or re.fullmatch(
                        r"workbench-pack-release-client-launch-plan:sha256:[0-9a-f]{64}",
                        identity,
                    ) is None
                    or not isinstance(record.get("source_plan_id"), str)
                    or _PACK_INSTANCE_PLAN_ID.fullmatch(record["source_plan_id"]) is None):
                raise CoreClientError("Core returned an incomplete Prism launch")
        else:
            choice = record.get("choice")
            if (not isinstance(choice, dict)
                    or not isinstance(choice.get("record_id"), str)
                    or _PACK_INSTANCE_RECORD_ID.fullmatch(choice["record_id"]) is None
                    or choice.get("source_kind") not in {None, "user-prism-zip", "published-release"}
                    or (choice.get("source_plan_id") is not None
                        and (not isinstance(choice["source_plan_id"], str)
                             or _PACK_INSTANCE_PLAN_ID.fullmatch(choice["source_plan_id"]) is None))):
                raise CoreClientError("Core returned an incomplete pack instance choice")
        return record

    async def pack_instance_choice_show(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "choice-show", "--profile", "supersymmetry", "--json",
            timeout=15,
        )
        return self._pack_instance_record(record, "choice-show")

    async def pack_instance_zip_plan(self, archive: str) -> Mapping[str, Any]:
        if not Path(archive).is_absolute():
            raise CoreClientError("choose an absolute Prism ZIP path")
        record = await self.json_record(
            "pack", "instance", "zip-plan", "--profile", "supersymmetry",
            "--archive", archive, "--json", timeout=600,
        )
        return self._pack_instance_record(record, "zip-plan")

    async def pack_instance_zip_stage_plan(self, archive: str) -> Mapping[str, Any]:
        if not Path(archive).is_absolute():
            raise CoreClientError("choose an absolute Prism ZIP path")
        record = await self.json_record(
            "pack", "instance", "zip-stage-plan", "--profile", "supersymmetry",
            "--archive", archive, "--json", timeout=600,
        )
        return self._pack_instance_record(record, "zip-stage-plan")

    async def pack_instance_zip_stage_apply(
        self, archive: str, plan_id: str,
    ) -> Mapping[str, Any]:
        if not Path(archive).is_absolute() or _PRISM_ZIP_STAGE_PLAN_ID.fullmatch(plan_id) is None:
            raise CoreClientError("select an exact reviewed Prism ZIP transfer")
        record = await self.json_record(
            "pack", "instance", "zip-stage-apply", "--profile", "supersymmetry",
            "--archive", archive, "--expected-plan-id", plan_id, "--json", timeout=3600,
        )
        result = self._pack_instance_record(record, "zip-stage-apply")
        if result["stage"]["plan_id"] != plan_id:
            raise CoreClientError("Core staged a different Prism ZIP")
        return result

    async def pack_instance_zip_import(self, archive: str, plan_id: str) -> Mapping[str, Any]:
        if not Path(archive).is_absolute() or _PACK_INSTANCE_PLAN_ID.fullmatch(plan_id) is None:
            raise CoreClientError("select an exact reviewed Prism ZIP")
        record = await self.json_record(
            "pack", "instance", "zip-import", "--profile", "supersymmetry",
            "--archive", archive, "--expected-plan-id", plan_id, "--json", timeout=1800,
        )
        result = self._pack_instance_record(record, "zip-import")
        if result["source"]["plan_id"] != plan_id:
            raise CoreClientError("Core imported a different Prism ZIP")
        return result

    @staticmethod
    def _fresh_optional_args(optional_mode: str) -> tuple[str, ...]:
        if optional_mode not in {"default", "omit"}:
            raise CoreClientError("choose whether to include the optional mod")
        return ("--optional-mode", optional_mode)

    @staticmethod
    def _fresh_pair_args(
        resourcepack_pairs: tuple[tuple[int, int], ...],
        optional_pairs: tuple[tuple[int, int], ...],
    ) -> tuple[str, ...]:
        if not resourcepack_pairs:
            raise CoreClientError("select at least one release resource pack")
        arguments: list[str] = []
        for flag, pairs in (("--resourcepack-pair", resourcepack_pairs),
                            ("--optional-pair", optional_pairs)):
            if len(set(pairs)) != len(pairs):
                raise CoreClientError("select each release file once")
            for project_id, file_id in pairs:
                if (type(project_id) is not int or type(file_id) is not int
                        or project_id <= 0 or file_id <= 0):
                    raise CoreClientError("select exact release file IDs")
                arguments.extend((flag, f"{project_id}:{file_id}"))
        return tuple(arguments)

    async def pack_instance_fresh_policy_status(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "fresh-policy-status", "--profile", "supersymmetry",
            "--json", timeout=600,
        )
        return self._pack_instance_record(record, "fresh-policy-status")

    async def pack_instance_fresh_policy_plan(
        self, resourcepack_pairs: tuple[tuple[int, int], ...],
        optional_pairs: tuple[tuple[int, int], ...],
    ) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "fresh-policy-plan", "--profile", "supersymmetry",
            *self._fresh_pair_args(resourcepack_pairs, optional_pairs),
            "--json", timeout=1200,
        )
        return self._pack_instance_record(record, "fresh-policy-plan")

    async def pack_instance_fresh_policy_apply(
        self, resourcepack_pairs: tuple[tuple[int, int], ...],
        optional_pairs: tuple[tuple[int, int], ...], plan_id: str,
    ) -> Mapping[str, Any]:
        if _PACK_POLICY_PLAN_ID.fullmatch(plan_id) is None:
            raise CoreClientError("select the exact reviewed release policy")
        record = await self.json_record(
            "pack", "instance", "fresh-policy-apply", "--profile", "supersymmetry",
            *self._fresh_pair_args(resourcepack_pairs, optional_pairs),
            "--expected-policy-plan-id", plan_id, "--json", timeout=1200,
        )
        result = self._pack_instance_record(record, "fresh-policy-apply")
        if result["policy"]["policy_plan_id"] != plan_id:
            raise CoreClientError("Core retained a different release policy")
        return result

    async def pack_instance_fresh_status(self, optional_mode: str) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "fresh-status", "--profile", "supersymmetry",
            *self._fresh_optional_args(optional_mode), "--json", timeout=600,
        )
        return self._pack_instance_record(record, "fresh-status")

    async def pack_instance_fresh_overrides(self, optional_mode: str) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "fresh-overrides", "--profile", "supersymmetry",
            *self._fresh_optional_args(optional_mode), "--json", timeout=1200,
        )
        return self._pack_instance_record(record, "fresh-overrides")

    async def pack_instance_fresh_file(
        self, project_id: int, file_id: int, optional_mode: str,
    ) -> Mapping[str, Any]:
        if (type(project_id) is not int or project_id <= 0
                or type(file_id) is not int or file_id <= 0):
            raise CoreClientError("choose an exact file offered by Core")
        record = await self.json_record(
            "pack", "instance", "fresh-file", "--profile", "supersymmetry",
            "--project-id", str(project_id), "--file-id", str(file_id),
            *self._fresh_optional_args(optional_mode), "--json", timeout=1800,
        )
        result = self._pack_instance_record(record, "fresh-file")
        if (result["fresh"].get("project_id") != project_id
                or result["fresh"].get("file_id") != file_id):
            raise CoreClientError("Core acquired another release file")
        return result

    async def pack_instance_fresh_publish(
        self, override_plan_id: str, optional_mode: str,
    ) -> Mapping[str, Any]:
        if not override_plan_id.startswith("workbench-pack-release-override-custody-plan:sha256:"):
            raise CoreClientError("select exact retained official overrides")
        record = await self.json_record(
            "pack", "instance", "fresh-publish", "--profile", "supersymmetry",
            "--override-plan-id", override_plan_id,
            *self._fresh_optional_args(optional_mode), "--json", timeout=1800,
        )
        return self._pack_instance_record(record, "fresh-publish")

    async def pack_instance_install_prepare(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "install-prepare", "--profile", "supersymmetry",
            "--json", timeout=3600,
        )
        return self._pack_instance_record(record, "install-prepare")

    async def pack_instance_root_plan(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "root-plan", "--profile", "supersymmetry",
            "--json", timeout=30,
        )
        return self._pack_instance_record(record, "root-plan")

    async def pack_instance_root_recover(
        self, plan_id: str, action: str,
    ) -> Mapping[str, Any]:
        if action not in {"reconcile", "abandon"} or re.fullmatch(
            r"workbench-prism-data-root-plan:sha256:[0-9a-f]{64}", plan_id,
        ) is None:
            raise CoreClientError("select the exact interrupted Prism folder")
        record = await self.json_record(
            "pack", "instance", "root-" + action, "--profile", "supersymmetry",
            "--expected-root-plan-id", plan_id, "--json", timeout=600,
        )
        result = self._pack_instance_record(record, "root-" + action)
        if result["prism_root"]["plan_id"] != plan_id:
            raise CoreClientError("Core recovered another Prism folder plan")
        return result

    async def pack_instance_install_status(self) -> Mapping[str, Any]:
        record = await self.json_record(
            "pack", "instance", "install-status", "--profile", "supersymmetry",
            "--json", timeout=600,
        )
        return self._pack_instance_record(record, "install-status")

    async def pack_instance_install_apply(self, plan_id: str) -> Mapping[str, Any]:
        if re.fullmatch(
            r"workbench-pack-release-client-install-plan:sha256:[0-9a-f]{64}", plan_id,
        ) is None:
            raise CoreClientError("select the exact reviewed instance installation")
        record = await self.json_record(
            "pack", "instance", "install-apply", "--profile", "supersymmetry",
            "--expected-install-plan-id", plan_id, "--json", timeout=3600,
        )
        result = self._pack_instance_record(record, "install-apply")
        if result["installation"]["plan_id"] != plan_id:
            raise CoreClientError("Core installed another instance plan")
        return result

    async def pack_instance_install_recover(
        self, plan_id: str, action: str,
    ) -> Mapping[str, Any]:
        if action not in {"reconcile", "abandon"} or re.fullmatch(
            r"workbench-pack-release-client-install-plan:sha256:[0-9a-f]{64}", plan_id,
        ) is None:
            raise CoreClientError("select the exact interrupted instance installation")
        record = await self.json_record(
            "pack", "instance", "install-" + action, "--profile", "supersymmetry",
            "--expected-install-plan-id", plan_id, "--json", timeout=600,
        )
        result = self._pack_instance_record(record, "install-" + action)
        if result["installation"]["plan_id"] != plan_id:
            raise CoreClientError("Core recovered another instance plan")
        return result

    async def pack_instance_launch_plan(self, install_plan_id: str, mode: str) -> Mapping[str, Any]:
        if mode not in {"show", "launch"}:
            raise CoreClientError("choose a Prism action")
        record = await self.json_record(
            "pack", "instance", "launch-plan", "--profile", "supersymmetry",
            "--expected-install-plan-id", install_plan_id, "--launch-mode", mode,
            "--json", timeout=30,
        )
        return self._pack_instance_record(record, "launch-plan")

    async def pack_instance_launch_run(
        self, install_plan_id: str, launch_plan_id: str, mode: str,
    ) -> Mapping[str, Any]:
        if mode not in {"show", "launch"}:
            raise CoreClientError("choose a Prism action")
        record = await self.json_record(
            "pack", "instance", "launch-run", "--profile", "supersymmetry",
            "--expected-install-plan-id", install_plan_id,
            "--expected-launch-plan-id", launch_plan_id, "--launch-mode", mode,
            "--json", timeout=86400, terminate_wait_seconds=90,
        )
        result = self._pack_instance_record(record, "launch-run")
        if result["launch"]["launch_plan_id"] != launch_plan_id:
            raise CoreClientError("Core launched another Prism plan")
        return result

    async def pack_instance_choice_select(
        self, plan_id: str, *, expected_record_id: str,
        launcher_root: str, workspace_name: str,
        source_kind: str = "user-prism-zip",
    ) -> Mapping[str, Any]:
        if (_PACK_INSTANCE_PLAN_ID.fullmatch(plan_id) is None
                or _PACK_INSTANCE_RECORD_ID.fullmatch(expected_record_id) is None
                or not Path(launcher_root).is_absolute() or not workspace_name
                or source_kind not in {"user-prism-zip", "published-release"}):
            raise CoreClientError("select a retained source, launcher root, and workspace")
        record = await self.json_record(
            "pack", "instance", "choice-select", "--profile", "supersymmetry",
            "--source-plan-id", plan_id, "--launcher-root", launcher_root,
            "--source-kind", source_kind,
            "--workspace-name", workspace_name,
            "--expected-record-id", expected_record_id, "--json", timeout=30,
        )
        result = self._pack_instance_record(record, "choice-select")
        if (result["choice"].get("source_plan_id") != plan_id
                or result["choice"].get("source_kind") != source_kind):
            raise CoreClientError("Core saved a different instance source")
        return result

    @staticmethod
    def _workspace_record(record: Any) -> Mapping[str, Any]:
        if (
            not isinstance(record, dict)
            or record.get("format") not in _WORKSPACE_FORMATS
            or (record.get("format"), record.get("schema_version")) not in {
                ("workbench-user-workspaces-v1", 1),
                ("workbench-user-workspaces-v2", 2),
                ("workbench-user-workspaces-v3", 3),
            }
            or not isinstance(record.get("record_id"), str)
            or not isinstance(record.get("entries"), list)
            or any(
                not isinstance(row, dict)
                or not isinstance(row.get("name"), str)
                or not isinstance(row.get("path"), str)
                or (record.get("schema_version") == 3 and (
                    "managed_java_feature" not in row
                    or type(row.get("managed_java_feature")) not in {int, type(None)}
                    or row.get("managed_java_feature") not in {None, 8}
                    or (row.get("managed_java_feature") is not None and row.get("java_home") is not None)
                ))
                for row in record["entries"]
            )
        ):
            raise CoreClientError("unsupported Workbench workspace choices")
        return record

    async def workspace_choices(self) -> Mapping[str, Any]:
        return self._workspace_record(await self.json_record(
            "settings", "workspace", "list", "--json"
        ))

    async def register_workspace(
        self, name: str, path: str, *, make_default: bool,
        expected_record_id: str,
    ) -> Mapping[str, Any]:
        if (re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None
                or not path or "\x00" in path
                or not (Path(path).is_absolute() or path == "~"
                                    or path.startswith("~/"))
                or not expected_record_id):
            raise CoreClientError("choose a workspace name, Linux folder, and current revision")
        arguments = ["settings", "workspace", "add", name, path]
        if make_default:
            arguments.append("--default")
        arguments.extend(("--expected-record-id", expected_record_id, "--json"))
        result = self._workspace_record(await self.json_record(*arguments))
        selected = next((row for row in result["entries"] if row["name"] == name), None)
        if (selected is None or selected["path"] != path
                or (make_default and result.get("default") != name)):
            raise CoreClientError("Core saved a different workspace")
        return result

    async def save_workspace_choice(
        self, name: str, *, profile_config: str | None,
        java_home: str | None, expected_record_id: str,
        managed_java_feature: int | None = None,
    ) -> Mapping[str, Any]:
        if not name or not expected_record_id:
            raise CoreClientError("choose a registered workspace and current revision")
        arguments = ["settings", "workspace", "select", name]
        arguments.extend(("--clear-profile",) if profile_config is None else ("--profile-config", profile_config))
        if java_home is not None and managed_java_feature is not None:
            raise CoreClientError("choose a Java path or a managed Java feature")
        if managed_java_feature is not None:
            arguments.extend(("--java-feature", str(managed_java_feature)))
        else:
            arguments.extend(("--clear-java",) if java_home is None else ("--java-home", java_home))
        arguments.extend(("--expected-record-id", expected_record_id, "--json"))
        result = self._workspace_record(await self.json_record(*arguments))
        if result["format"] not in {"workbench-user-workspaces-v2", "workbench-user-workspaces-v3"}:
            raise CoreClientError("Core did not save versioned workspace choices")
        selected = next((row for row in result["entries"] if row["name"] == name), None)
        if selected is None or selected.get("profile_config") != profile_config or selected.get("java_home") != java_home or selected.get("managed_java_feature") != managed_java_feature:
            raise CoreClientError("Core workspace choice result differs from the requested values")
        return result

    async def acquire_workspace_java(
        self, name: str, *, expected_record_id: str,
    ) -> Mapping[str, Any]:
        if not name or not expected_record_id:
            raise CoreClientError("choose a saved workspace and current revision")
        result = await self.json_record(
            "settings", "workspace", "acquire", name,
            "--expected-record-id", expected_record_id, "--json", timeout=600,
        )
        if (
            not isinstance(result, dict)
            or result.get("format") != "workbench-java-runtime-result-v2"
            or result.get("source") != "managed"
            or result.get("outcome") not in {"provisioned", "reused"}
            or not isinstance(result.get("receipt"), dict)
            or not isinstance(result["receipt"].get("policy"), dict)
            or type(result["receipt"]["policy"].get("feature_version")) is not int
            or not isinstance(result["receipt"].get("target"), dict)
            or not isinstance(result["receipt"]["target"].get("java_home_uri"), str)
        ):
            raise CoreClientError("Core did not return a managed Java receipt")
        return result

    async def export_environment_share(
        self, name: str, *, bind_project_source_lock: bool = False,
        bind_managed_tools: bool = False,
    ) -> Mapping[str, Any]:
        if not name:
            raise CoreClientError("choose a saved workspace to share")
        if bind_managed_tools and not bind_project_source_lock:
            raise CoreClientError("managed-tool lock requires a project source lock")
        record = await self.json_record(
            "settings", "environment", "export", name,
            *(("--bind-project-source-lock",) if bind_project_source_lock else ()),
            *(("--bind-managed-tools",) if bind_managed_tools else ()),
            "--json",
        )
        if (
            not isinstance(record, dict)
            or record.get("format") != "workbench-environment-share-export-v1"
            or not isinstance(record.get("share"), dict)
            or not isinstance(record["share"].get("share_id"), str)
            or (bind_project_source_lock and (
                record["share"].get("format") != (
                    "workbench-environment-share-v3" if bind_managed_tools
                    else "workbench-environment-share-v2"
                )
                or not isinstance(record["share"].get("lock"), dict)
                or not _project_source_lock_record(
                    record["share"]["lock"].get("project_source_lock")
                )
            ))
            or (bind_managed_tools and not _managed_tool_lock_record(
                record["share"]["lock"].get("managed_tool_lock")
            ))
            or not isinstance(record.get("resource"), dict)
            or not isinstance(record["resource"].get("path"), str)
        ):
            raise CoreClientError("Core did not return an environment share")
        return record

    @staticmethod
    def _environment_import_args(
        source: str, name: str, workspace: str, *,
        config: str = "", java_home: str = "", acquire_managed_java: bool = False,
    ) -> tuple[str, ...]:
        if not source.strip() or not name.strip() or not workspace.strip():
            raise CoreClientError("choose a share file, local name and workspace")
        arguments = [source, "--name", name, "--workspace", workspace]
        if config.strip():
            arguments.extend(("--config", config))
        if java_home.strip():
            arguments.extend(("--java-home", java_home))
        if acquire_managed_java:
            arguments.append("--acquire-managed-java")
        return tuple(arguments)

    async def plan_environment_import(
        self, source: str, name: str, workspace: str, *,
        config: str = "", java_home: str = "", acquire_managed_java: bool = False,
    ) -> Mapping[str, Any]:
        arguments = self._environment_import_args(
            source, name, workspace, config=config, java_home=java_home,
            acquire_managed_java=acquire_managed_java,
        )
        record = await self.json_record(
            "settings", "environment", "plan", *arguments, "--json",
            allowed_exit=(0, 1),
        )
        if (
            not isinstance(record, dict)
            or record.get("format") not in {
                "workbench-environment-import-plan-v4",
                "workbench-environment-import-plan-v3",
                ("workbench-environment-import-plan-v2"
                 if acquire_managed_java else "workbench-environment-import-plan-v1"),
            }
            or (record.get("format") == "workbench-environment-import-plan-v3"
                and not _project_source_lock_record(record.get("project_source_lock")))
            or (record.get("format") == "workbench-environment-import-plan-v4"
                and (not _project_source_lock_record(record.get("project_source_lock"))
                     or not _managed_tool_lock_record(record.get("managed_tool_lock"))))
            or (acquire_managed_java and record.get("acquire_managed_java") is not True)
            or record.get("state") not in {"ready", "blocked"}
            or not isinstance(record.get("plan_id"), str)
            or not isinstance(record.get("blockers"), list)
            or not all(isinstance(item, str) for item in record["blockers"])
            or not isinstance(record.get("unresolved_inputs"), list)
            or not all(isinstance(item, str) for item in record["unresolved_inputs"])
        ):
            raise CoreClientError("Core did not return a compatible environment import plan")
        return record

    async def import_environment_share(
        self, source: str, name: str, workspace: str, *,
        expected_plan_id: str, config: str = "", java_home: str = "",
        acquire_managed_java: bool = False,
    ) -> Mapping[str, Any]:
        if not expected_plan_id:
            raise CoreClientError("review an exact environment import plan first")
        arguments = self._environment_import_args(
            source, name, workspace, config=config, java_home=java_home,
            acquire_managed_java=acquire_managed_java,
        )
        record = await self.json_record(
            "settings", "environment", "import", *arguments,
            "--plan-id", expected_plan_id, "--json",
            **({"timeout": 600} if acquire_managed_java else {}),
        )
        if (
            not isinstance(record, dict)
            or record.get("format") not in {
                "workbench-environment-import-result-v4",
                "workbench-environment-import-result-v3",
                ("workbench-environment-import-result-v2"
                 if acquire_managed_java else "workbench-environment-import-result-v1"),
            }
            or (record.get("format") == "workbench-environment-import-result-v3"
                and not _project_source_lock_record(record.get("project_source_lock")))
            or (record.get("format") == "workbench-environment-import-result-v4"
                and (not _project_source_lock_record(record.get("project_source_lock"))
                     or not _managed_tool_lock_record(record.get("managed_tool_lock"))))
            or (
                acquire_managed_java and (
                    not isinstance(record.get("managed_java"), dict)
                    or record["managed_java"].get("outcome") not in {"reused", "provisioned"}
                    or not isinstance(record["managed_java"].get("runtime_id"), str)
                )
            )
            or record.get("plan_id") != expected_plan_id
            or record.get("outcome") not in {"bound", "reused"}
            or not isinstance(record.get("resource"), dict)
            or not isinstance(record["resource"].get("path"), str)
            or not isinstance(record.get("unresolved_inputs"), list)
        ):
            raise CoreClientError("Core did not return an exact environment import result")
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
        if not isinstance(record, dict) or record.get("format") not in {"workbench-environment-resolution-v1", "workbench-environment-resolution-v2"}:
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
