"""Private, ordered custody for active-instance registration attempts.

The owner supplies exact images and receipt meaning. Core owns the fixed
historical attempt path, backup/after bytes, source-commit ordering marks and
no-replace promotion. Restart inspection is read-only and never guesses that
Groovy compiled or that a changed external payload is safe to overwrite.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Iterator
from uuid import uuid4

from workbench_api.registration_attempts import (
    RegistrationAttemptError, RegistrationImage,
)
from workbench_api.source_transactions import SourceImage, SourceTransactionError

from . import check_storage
from .durable_records import (
    private_record_lock, publish_immutable_bytes, read_private_bytes,
    replace_private_bytes,
)
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .output_routing import _private_directory
from .source_checkouts import _rename_noreplace
from .source_transactions import CoreSourceTransactions
from .storage.registered import ResourceCatalog


KIND = "workbench-registration-attempt-v1"
_ID = re.compile(r"sha256:([0-9a-f]{64})\Z")
_MAX_OPERATIONS = 64
_MAX_FILE = 16 * 1024 * 1024
_MAX_TOTAL = 32 * 1024 * 1024
_MAX_RECEIPT = 2 * 1024 * 1024


def _fail(code: str, message: str) -> None:
    raise RegistrationAttemptError(f"registration.{code}", message)


def _identity(path: Path) -> tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RegistrationAttemptError("registration.unavailable", f"registration path is unavailable: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or getattr(path, "is_junction", lambda: False)():
        _fail("unsafe", "registration path traverses a redirect")
    return info.st_dev, info.st_ino


def _no_redirects(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("path", "registration custody requires normalized absolute paths")
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            _fail("unsafe", "registration path traverses a redirect")


def _relative(value: str) -> str:
    if type(value) is not str or not value or "\\" in value or any(
        character in value for character in ":\0\r\n"
    ):
        _fail("path", "registration image path is not portable")
    selected = PurePosixPath(value)
    if (selected.is_absolute() or selected.as_posix() != value or "." in selected.parts
            or ".." in selected.parts):
        _fail("path", "registration image path is not normalized")
    return value


def _canonical(value: object) -> bytes:
    return check_storage.canonical(value) + b"\n"


def _read_json(raw: bytes, *, label: str) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in pairs:
            if key in value:
                _fail("record", f"registration {label} repeats a JSON key")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw, object_pairs_hook=unique,
            parse_constant=lambda _value: _fail("record", f"registration {label} contains a nonfinite number"),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationAttemptError("registration.record", f"registration {label} is invalid JSON") from exc
    if not isinstance(value, dict):
        _fail("record", f"registration {label} is not an object")
    return value


def _receipt(raw: bytes, *, plan_id: str, state: str, final: Path) -> dict:
    if type(raw) is not bytes or len(raw) > _MAX_RECEIPT:
        _fail("receipt", "registration receipt exceeds its byte bound")
    value = _read_json(raw, label="receipt")
    if (not isinstance(value, dict) or value.get("format") != "workbench-registration-receipt-v1"
            or value.get("transaction_id") != plan_id or value.get("state") != state
            or not isinstance(value.get("target"), dict)
            or value["target"].get("receipt_uri") != (final / "receipt.json").as_uri()):
        _fail("receipt", "registration receipt differs from the reviewed attempt")
    return value


def _write_file(path: Path, raw: bytes, *, limit: int) -> None:
    _private_directory(path.parent)
    secure_private_path(path.parent, directory=True)
    publish_immutable_bytes(path, raw, byte_limit=limit)
    if read_private_bytes(path, byte_limit=limit) != raw:
        _fail("changed", "registration attempt file did not reopen exactly")


class _Attempt:
    def __init__(
        self, *, state_root: Path, workspace: Path, payload: Path,
        plan_id: str, selection_id: str, catalog: ResourceCatalog,
    ):
        for path in (state_root, workspace, payload):
            _no_redirects(path)
        if _ID.fullmatch(plan_id) is None or _ID.fullmatch(selection_id) is None:
            _fail("binding", "registration plan or selection identity is invalid")
        self.state_root = state_root
        self.workspace = workspace
        self.payload = payload
        self.plan_id = plan_id
        self.selection_id = selection_id
        self.catalog = catalog
        self.digest = plan_id[7:]
        self.root = state_root / "registrations"
        self.path = self.root / f".apply-{self.digest}"
        self.transaction_path = self.root / self.digest
        self.staging_token = uuid4().hex
        self._rows: list[dict] = []
        self._stage_identity: tuple[int, int] | None = None
        self._prepared = False
        self._promoted = False
        self._stages: set[int] = set()
        self._attempts: set[int] = set()
        self._root_identities = {
            "state": _identity(state_root), "workspace": _identity(workspace),
            "payload": _identity(payload), "registrations": _identity(self.root),
        }

    @property
    def promoted(self) -> bool:
        return self._promoted

    def _check_roots(self) -> None:
        for path in (self.state_root, self.workspace, self.payload, self.root):
            _no_redirects(path)
        if {
            "state": _identity(self.state_root), "workspace": _identity(self.workspace),
            "payload": _identity(self.payload), "registrations": _identity(self.root),
        } != self._root_identities:
            _fail("changed", "registration state, target, or payload root was replaced")
        if not private_path(self.state_root, directory=True) or not private_path(self.root, directory=True):
            _fail("unsafe", "registration state root lost private custody")

    def prepare(self, images: tuple[RegistrationImage, ...], receipt: bytes) -> None:
        self._check_roots()
        if self._prepared or not isinstance(images, tuple) or not 1 <= len(images) <= _MAX_OPERATIONS:
            _fail("input", "registration attempt images are invalid")
        prepared = _receipt(receipt, plan_id=self.plan_id, state="prepared", final=self.transaction_path)
        rows = []
        total = 0
        for image in images:
            if (not isinstance(image, RegistrationImage) or type(image.before) is not bytes
                    or type(image.after) is not bytes or type(image.mode) is not int
                    or not 0 <= image.mode <= 0o7777):
                _fail("input", "registration source image is invalid")
            relative = _relative(image.path)
            if len(image.before) > _MAX_FILE or len(image.after) > _MAX_FILE:
                _fail("bounds", "registration source image exceeds its byte bound")
            total += len(image.before) + len(image.after)
            if total > _MAX_TOTAL:
                _fail("bounds", "registration source images exceed their aggregate bound")
            target = self.payload.joinpath(*PurePosixPath(relative).parts)
            _no_redirects(target.parent)
            parent = _identity(target.parent)
            rows.append({
                "path": relative, "before_sha256": sha256(image.before).hexdigest(),
                "after_sha256": sha256(image.after).hexdigest(),
                "before_size": len(image.before), "after_size": len(image.after),
                "mode": image.mode, "parent_device": parent[0], "parent_inode": parent[1],
            })
        if len({row["path"] for row in rows}) != len(rows):
            _fail("input", "registration source images are repeated")
        outputs = prepared.get("outputs")
        if (not isinstance(outputs, list)
                or [item.get("path") for item in outputs] != [row["path"] for row in rows]
                or any(item.get("backup_path") != f"backups/{row['path']}"
                       for item, row in zip(outputs, rows))):
            _fail("receipt", "registration receipt backup paths differ from source images")
        if self.path.exists() or self.path.is_symlink() or self.transaction_path.exists() or self.transaction_path.is_symlink():
            _fail("exists", "registration attempt already exists; restart review is required")
        self.path.mkdir(mode=0o700)
        secure_private_path(self.path, directory=True)
        self._stage_identity = _identity(self.path)
        manifest = check_storage.seal(KIND, {
            "format": KIND, "schema_version": 1, "plan_id": self.plan_id,
            "selection_id": self.selection_id, "workspace": str(self.workspace),
            "payload": str(self.payload), "state_root": str(self.state_root),
            "registrations_root": str(self.root), "staging_token": self.staging_token,
            "stage_device": self._stage_identity[0], "stage_inode": self._stage_identity[1],
            "root_identities": self._root_identities, "operations": rows,
            "prepared_receipt_sha256": sha256(receipt).hexdigest(),
        })
        _write_file(self.path / "attempt.json", _canonical(manifest), limit=32 * 1024)
        for image in images:
            _write_file(self.path / "backups" / image.path, image.before, limit=_MAX_FILE)
            _write_file(self.path / "after" / image.path, image.after, limit=_MAX_FILE)
        _write_file(self.path / "receipt.json", receipt, limit=_MAX_RECEIPT)
        self._rows = rows
        self._prepared = True
        fsync_directory(self.path)
        fsync_directory(self.root)

    def inspect(self) -> dict:
        """Classify retained bytes and source files without changing either."""

        self._check_roots()
        stage_exists = self.path.exists() or self.path.is_symlink()
        final_exists = self.transaction_path.exists() or self.transaction_path.is_symlink()
        if stage_exists == final_exists:
            _fail("state", "registration has no single retained attempt to inspect")
        retained = self.path if stage_exists else self.transaction_path
        _no_redirects(retained)
        if not private_path(retained, directory=True):
            _fail("unsafe", "registration retained attempt is not private")
        manifest = _read_json(
            read_private_bytes(retained / "attempt.json", byte_limit=32 * 1024),
            label="attempt manifest",
        )
        seal = manifest.get("id")
        body = {key: value for key, value in manifest.items() if key != "id"}
        if seal != check_storage.seal(KIND, body)["id"]:
            _fail("record", "registration attempt manifest seal changed")
        expected = {
            "format": KIND, "schema_version": 1, "plan_id": self.plan_id,
            "selection_id": self.selection_id, "workspace": str(self.workspace),
            "payload": str(self.payload), "state_root": str(self.state_root),
            "registrations_root": str(self.root),
        }
        if any(body.get(key) != value for key, value in expected.items()):
            _fail("binding", "registration attempt differs from the selected workspace or plan")
        if body.get("root_identities") != {
            key: list(value) for key, value in self._root_identities.items()
        } or [body.get("stage_device"), body.get("stage_inode")] != list(_identity(retained)):
            _fail("changed", "registration attempt or bound root was replaced")
        token = body.get("staging_token")
        if type(token) is not str or re.fullmatch(r"[0-9a-f]{32}", token) is None:
            _fail("record", "registration source staging token is invalid")
        rows = body.get("operations")
        if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_OPERATIONS:
            _fail("record", "registration operation journal is invalid")
        if any(not isinstance(row, dict) or type(row.get("path")) is not str for row in rows):
            _fail("record", "registration operation rows are invalid")
        if len({row["path"] for row in rows}) != len(rows):
            _fail("record", "registration operation paths are repeated")
        receipt_raw = read_private_bytes(retained / "receipt.json", byte_limit=_MAX_RECEIPT)
        receipt = _read_json(receipt_raw, label="receipt")
        receipt_state = receipt.get("state")
        if receipt_state not in {"prepared", "applied"}:
            _fail("receipt", "registration receipt state is invalid")
        _receipt(receipt_raw, plan_id=self.plan_id, state=receipt_state, final=self.transaction_path)
        plan = receipt.get("plan")
        if (not isinstance(plan, dict) or plan.get("plan_id") != self.plan_id
                or not isinstance(plan.get("active_instance"), dict)
                or plan["active_instance"].get("selection_id") != self.selection_id
                or receipt["target"].get("payload_root_uri") != self.payload.as_uri()):
            _fail("receipt", "registration receipt differs from the selected source plan")
        prepared = dict(receipt, state="prepared")
        prepared_raw = json.dumps(
            prepared, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8") + b"\n"
        if sha256(prepared_raw).hexdigest() != body.get("prepared_receipt_sha256"):
            _fail("receipt", "registration receipt differs from the retained plan")
        outputs = receipt.get("outputs")
        if (not isinstance(outputs, list) or len(outputs) != len(rows)
                or any(not isinstance(output, dict) for output in outputs)):
            _fail("receipt", "registration receipt outputs are invalid")

        def markers(directory: str, kind: str) -> list[dict]:
            root = retained / directory
            if not root.exists() and not root.is_symlink():
                return []
            _no_redirects(root)
            if not private_path(root, directory=True):
                _fail("unsafe", "registration journal directory lost private custody")
            names = sorted(member.name for member in root.iterdir())
            if names != [f"{ordinal:03d}.json" for ordinal in range(len(names))]:
                _fail("order", "registration journal has missing or unexpected ordinals")
            return [
                _read_json(read_private_bytes(root / name, byte_limit=4096), label=kind)
                for name in names
            ]

        stages = markers("stages", "stage")
        attempts = markers("attempts", "source attempt")
        if len(stages) > len(rows) or len(attempts) > len(rows) or (attempts and len(stages) != len(rows)):
            _fail("order", "registration source journal has impossible operation order")
        transaction = CoreSourceTransactions(owner_id="workbench-shell").open(
            self.payload, binding=f"registration:{self.plan_id}", staging_token=token,
        )
        classified: list[dict] = []
        review_required = len(stages) != len(rows)
        total = 0
        for ordinal, row in enumerate(rows):
            if not isinstance(row, dict):
                _fail("record", "registration operation row is invalid")
            relative = _relative(row.get("path"))
            target = self.payload.joinpath(*PurePosixPath(relative).parts)
            _no_redirects(target.parent)
            if [row.get("parent_device"), row.get("parent_inode")] != list(_identity(target.parent)):
                _fail("changed", "registration source parent changed after staging")
            if (type(row.get("mode")) is not int or not 0 <= row["mode"] <= 0o7777
                    or any(type(row.get(f"{side}_size")) is not int or not 0 <= row[f"{side}_size"] <= _MAX_FILE
                           or type(row.get(f"{side}_sha256")) is not str
                           or re.fullmatch(r"[0-9a-f]{64}", row[f"{side}_sha256"]) is None
                           for side in ("before", "after"))):
                _fail("record", "registration source image row is invalid")
            total += row["before_size"] + row["after_size"]
            if total > _MAX_TOTAL:
                _fail("bounds", "registration source images exceed their aggregate bound")
            images = []
            for side in ("before", "after"):
                image_root = "backups" if side == "before" else "after"
                raw = read_private_bytes(retained / image_root / relative, byte_limit=_MAX_FILE)
                if len(raw) != row[f"{side}_size"] or sha256(raw).hexdigest() != row[f"{side}_sha256"]:
                    _fail("changed", "registration retained source image changed")
                images.append(SourceImage("file", raw, mode=row["mode"]))
            before, after = images
            output = outputs[ordinal]
            if (output.get("path") != relative or output.get("backup_path") != f"backups/{relative}"
                    or output.get("before_sha256") != row["before_sha256"]
                    or output.get("content_sha256") != row["after_sha256"]
                    or output.get("size") != row["after_size"]):
                _fail("receipt", "registration receipt outputs differ from retained images")
            stage_recorded = ordinal < len(stages)
            attempted = ordinal < len(attempts)
            if stage_recorded:
                stage = stages[ordinal]
                if stage != {
                    "format": "workbench-registration-source-stage-v1", "ordinal": ordinal,
                    "path": relative, "staged_relative": stage.get("staged_relative"),
                    "staging_token": token,
                }:
                    _fail("record", "registration source stage record changed")
                reference = transaction.attach(
                    relative, before=before, after=after,
                    staged_relative=stage["staged_relative"], attempted=attempted,
                )
                state = transaction.classify(reference)
                staged = self.payload.joinpath(*PurePosixPath(stage["staged_relative"]).parts)
                stage_present = staged.exists() or staged.is_symlink()
                if (not attempted and not stage_present) or (state == "after" and stage_present):
                    review_required = True
            else:
                stage_present = False
                state = "other"
                for label, image in (("after", after), ("before", before)):
                    try:
                        transaction._match(target, image)
                    except SourceTransactionError as exc:
                        if exc.code != "stale":
                            raise
                    else:
                        state = label
                        break
            if attempted:
                attempt = attempts[ordinal]
                if attempt != {
                    "format": "workbench-registration-source-attempt-v1", "ordinal": ordinal,
                    "path": relative, "staging_token": token,
                }:
                    _fail("record", "registration source attempt record changed")
            prefix = f".{target.name}.workbench-{token}-"
            expected_stage = (
                PurePosixPath(stages[ordinal]["staged_relative"]).name
                if stage_recorded else None
            )
            extra_stage_present = any(
                member.name != expected_stage
                for member in target.parent.iterdir()
                if member.name.startswith(prefix) and member.name.endswith(".tmp")
            )
            if [row["parent_device"], row["parent_inode"]] != list(_identity(target.parent)):
                _fail("changed", "registration source parent changed during stage inventory")
            if extra_stage_present:
                review_required = True
            if state == "other" or (state == "after" and not attempted):
                review_required = True
            classified.append({
                "path": relative, "source_state": state,
                "stage_recorded": stage_recorded, "attempted": attempted,
                "stage_present": stage_present,
                "extra_stage_present": extra_stage_present,
            })
        if receipt_state == "applied" and (
            stage_exists or len(attempts) != len(rows)
            or any(row["source_state"] != "after" for row in classified)
        ):
            review_required = True
        self._check_roots()
        return {
            "format": "workbench-registration-attempt-inspection-v1",
            "plan_id": self.plan_id, "selection_id": self.selection_id,
            "attempt_uri": retained.as_uri(),
            "receipt_uri": (self.transaction_path / "receipt.json").as_uri(),
            "receipt_state": receipt_state,
            "journal_status": "review-required" if review_required else "consistent",
            "operations": classified,
            "outstanding_checks": [
                "Recovery action is not automated by this inspection.",
                "Relaunch the selected instance to check Groovy compilation and registration.",
            ],
        }

    def finalize_committed(self) -> dict:
        """Finish receipt publication after every ordered source edit is proven.

        This restart action never rewrites the installed payload. Partial or
        changed source edits stay protected for separate review or recovery.
        """

        def complete(inspection: dict) -> bool:
            return bool(inspection["operations"]) and all(
                row["source_state"] == "after" and row["stage_recorded"]
                and row["attempted"] and not row["stage_present"]
                and not row["extra_stage_present"]
                for row in inspection["operations"]
            )

        staged = self.path.exists() or self.path.is_symlink()
        retained = self.path if staged else self.transaction_path
        retained_identity = _identity(retained)
        inspected = self.inspect()
        if not complete(inspected):
            _fail("incomplete", "registration source edits are not all proven committed")
        if not staged:
            if (inspected["receipt_state"] != "applied"
                    or inspected["journal_status"] != "consistent"):
                _fail("state", "retained registration is not a completed application")
            return inspected
        if (inspected["receipt_state"] == "prepared"
                and inspected["journal_status"] != "consistent"):
            _fail("review", "registration attempt requires review before receipt publication")
        self._check_roots()
        if _identity(retained) != retained_identity:
            _fail("changed", "registration attempt changed after inspection")
        path = retained / "receipt.json"
        observed = read_private_bytes(path, byte_limit=_MAX_RECEIPT)
        receipt = _receipt(
            observed, plan_id=self.plan_id,
            state=inspected["receipt_state"], final=self.transaction_path,
        )
        if inspected["receipt_state"] == "prepared":
            applied = _canonical(dict(receipt, state="applied"))
            replace_private_bytes(
                path, applied, byte_limit=_MAX_RECEIPT,
                expected_sha256="sha256:" + sha256(observed).hexdigest(),
            )
            if read_private_bytes(path, byte_limit=_MAX_RECEIPT) != applied:
                _fail("changed", "applied registration receipt did not reopen exactly")
        # Recheck source and receipt immediately before the no-replace rename.
        reviewed = self.inspect()
        if (
            reviewed["receipt_state"] != "applied"
            or not complete(reviewed)
            or _identity(retained) != retained_identity
        ):
            _fail("changed", "registration attempt changed before promotion")
        if self.transaction_path.exists() or self.transaction_path.is_symlink():
            _fail("exists", "registration destination appeared after review")
        try:
            _rename_noreplace(retained, self.transaction_path)
        except OSError as exc:
            raise RegistrationAttemptError(
                "registration.publish", "registration attempt could not be promoted without replacement",
            ) from exc
        fsync_directory(self.root)
        final = self.inspect()
        if (final["receipt_state"] != "applied"
                or final["journal_status"] != "consistent"
                or not complete(final)):
            _fail("changed", "promoted registration changed before final verification")
        return final

    def resume_partial(self) -> dict:
        """Replay only an ordered, fully staged source prefix after process exit.

        An attempted operation before the final attempt marker must already
        have consumed its stage and reached its after image. The final attempt
        may still be at its before image only while that exact stage exists.
        This excludes interrupted rollback, whose original stage was consumed
        before its source could return to the before image.
        """

        if self.transaction_path.exists() or self.transaction_path.is_symlink():
            return self.finalize_committed()

        def recoverable(inspection: dict) -> None:
            operations = inspection["operations"]
            if (inspection["receipt_state"] != "prepared"
                    or inspection["journal_status"] != "consistent"
                    or not operations
                    or any(not row["stage_recorded"] or row["extra_stage_present"]
                           for row in operations)):
                _fail("review", "registration partial attempt lacks a complete source stage proof")
            attempted = sum(bool(row["attempted"]) for row in operations)
            for ordinal, row in enumerate(operations):
                state, present = row["source_state"], row["stage_present"]
                if ordinal < attempted - 1:
                    valid = state == "after" and not present
                elif ordinal == attempted - 1:
                    valid = (state == "after" and not present) or (state == "before" and present)
                else:
                    valid = state == "before" and present
                if not valid:
                    _fail("review", "registration source order cannot prove safe resumption")

        inspected = self.inspect()
        if inspected["receipt_state"] == "applied":
            return self.finalize_committed()
        recoverable(inspected)
        retained = self.path
        manifest_raw = read_private_bytes(retained / "attempt.json", byte_limit=32 * 1024)
        manifest = _read_json(manifest_raw, label="attempt manifest")
        token = manifest["staging_token"]
        rows = manifest["operations"]
        transaction = CoreSourceTransactions(owner_id="workbench-shell").open(
            self.payload, binding=f"registration:{self.plan_id}", staging_token=token,
        )
        for ordinal in range(len(rows)):
            inspected = self.inspect()
            recoverable(inspected)
            if read_private_bytes(retained / "attempt.json", byte_limit=32 * 1024) != manifest_raw:
                _fail("changed", "registration attempt manifest changed during resumption")
            operation = inspected["operations"][ordinal]
            if operation["source_state"] == "after":
                continue
            row = rows[ordinal]
            relative = row["path"]
            before = read_private_bytes(retained / "backups" / relative, byte_limit=_MAX_FILE)
            after = read_private_bytes(retained / "after" / relative, byte_limit=_MAX_FILE)
            if (len(before) != row["before_size"] or sha256(before).hexdigest() != row["before_sha256"]
                    or len(after) != row["after_size"] or sha256(after).hexdigest() != row["after_sha256"]):
                _fail("changed", "registration retained source image changed during resumption")
            stage = _read_json(
                read_private_bytes(retained / "stages" / f"{ordinal:03d}.json", byte_limit=4096),
                label="source stage",
            )
            if stage != {
                "format": "workbench-registration-source-stage-v1", "ordinal": ordinal,
                "path": relative, "staged_relative": stage.get("staged_relative"),
                "staging_token": token,
            }:
                _fail("record", "registration source stage changed during resumption")
            reference = transaction.attach(
                relative,
                before=SourceImage("file", before, mode=row["mode"]),
                after=SourceImage("file", after, mode=row["mode"]),
                staged_relative=stage["staged_relative"], attempted=True,
            )
            if not operation["attempted"]:
                _write_file(retained / "attempts" / f"{ordinal:03d}.json", _canonical({
                    "format": "workbench-registration-source-attempt-v1", "ordinal": ordinal,
                    "path": relative, "staging_token": token,
                }), limit=4096)
                # The marker must reopen before Core can replace source.
                marked = self.inspect()
                recoverable(marked)
                if not marked["operations"][ordinal]["attempted"]:
                    _fail("changed", "registration source attempt marker did not reopen")
            transaction.commit(reference)
        return self.finalize_committed()

    def record_stage(self, ordinal: int, staged_relative: str | None) -> None:
        self._check_roots()
        if (not self._prepared or type(ordinal) is not int
                or ordinal != len(self._stages) or ordinal >= len(self._rows)):
            _fail("order", "registration source stages must be recorded in order")
        if type(staged_relative) is not str or not staged_relative:
            _fail("stage", "registration source stage path is missing")
        body = {
            "format": "workbench-registration-source-stage-v1", "ordinal": ordinal,
            "path": self._rows[ordinal]["path"], "staged_relative": staged_relative,
            "staging_token": self.staging_token,
        }
        _write_file(self.path / "stages" / f"{ordinal:03d}.json", _canonical(body), limit=4096)
        self._stages.add(ordinal)

    def mark_attempted(self, ordinal: int) -> None:
        self._check_roots()
        if (not self._prepared or len(self._stages) != len(self._rows)
                or type(ordinal) is not int or ordinal != len(self._attempts)
                or ordinal >= len(self._rows)):
            _fail("order", "registration source commits must be attempted in reviewed order")
        body = {
            "format": "workbench-registration-source-attempt-v1", "ordinal": ordinal,
            "path": self._rows[ordinal]["path"], "staging_token": self.staging_token,
        }
        _write_file(self.path / "attempts" / f"{ordinal:03d}.json", _canonical(body), limit=4096)
        self._attempts.add(ordinal)

    def publish_applied(self, receipt: bytes) -> None:
        self._check_roots()
        if not self._prepared or self._promoted or len(self._attempts) != len(self._rows):
            _fail("order", "registration cannot publish applied receipt before all source attempts")
        _receipt(receipt, plan_id=self.plan_id, state="applied", final=self.transaction_path)
        path = self.path / "receipt.json"
        before = read_private_bytes(path, byte_limit=_MAX_RECEIPT)
        _receipt(before, plan_id=self.plan_id, state="prepared", final=self.transaction_path)
        replace_private_bytes(
            path, receipt, byte_limit=_MAX_RECEIPT,
            expected_sha256="sha256:" + sha256(before).hexdigest(),
        )
        if read_private_bytes(path, byte_limit=_MAX_RECEIPT) != receipt:
            _fail("changed", "applied registration receipt did not reopen exactly")

    def restore_prepared(self, receipt: bytes) -> None:
        """Downgrade only this attempt's exact applied receipt after failure."""

        self._check_roots()
        _receipt(receipt, plan_id=self.plan_id, state="prepared", final=self.transaction_path)
        path = (self.transaction_path if self._promoted else self.path) / "receipt.json"
        observed = read_private_bytes(path, byte_limit=_MAX_RECEIPT)
        if observed == receipt:
            return
        _receipt(observed, plan_id=self.plan_id, state="applied", final=self.transaction_path)
        replace_private_bytes(
            path, receipt, byte_limit=_MAX_RECEIPT,
            expected_sha256="sha256:" + sha256(observed).hexdigest(),
        )
        if read_private_bytes(path, byte_limit=_MAX_RECEIPT) != receipt:
            _fail("changed", "prepared registration receipt did not reopen exactly")

    def promote(self) -> None:
        self._check_roots()
        if not self._prepared or self._promoted:
            _fail("state", "registration attempt is not available for promotion")
        if _identity(self.path) != self._stage_identity:
            _fail("changed", "registration attempt stage was replaced")
        retained = read_private_bytes(self.path / "receipt.json", byte_limit=_MAX_RECEIPT)
        try:
            _receipt(retained, plan_id=self.plan_id, state="applied", final=self.transaction_path)
        except RegistrationAttemptError:
            _receipt(retained, plan_id=self.plan_id, state="prepared", final=self.transaction_path)
        if self.transaction_path.exists() or self.transaction_path.is_symlink():
            _fail("exists", "registration destination appeared after review")
        try:
            _rename_noreplace(self.path, self.transaction_path)
        except OSError as exc:
            if (self.transaction_path.exists() and not self.path.exists()
                    and _identity(self.transaction_path) == self._stage_identity):
                self._promoted = True
            raise RegistrationAttemptError("registration.publish", "registration attempt cannot be promoted without replacement") from exc
        self._promoted = True
        if _identity(self.transaction_path) != self._stage_identity:
            _fail("changed", "promoted registration attempt changed identity")
        fsync_directory(self.root)

    def discard(self) -> None:
        if self._promoted or self._stage_identity is None or (
            not self.path.exists() and not self.path.is_symlink()
        ):
            return
        self._check_roots()
        if _identity(self.path) != self._stage_identity:
            _fail("changed", "registration attempt stage changed before cleanup")
        for parent, directories, files in os.walk(self.path, followlinks=False):
            for name in directories + files:
                member = Path(parent) / name
                info = member.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    _fail("unsafe", "registration attempt has an unsafe member; cleanup is protected")
        shutil.rmtree(self.path)
        fsync_directory(self.root)


class CoreRegistrationAttempts:
    def __init__(self, *, configuration_home: Path, owner_id: str):
        if owner_id not in {"workbench-shell", "local-host"}:
            _fail("owner", "registration attempt owner is unsupported")
        self.catalog = ResourceCatalog(configuration_home)
        self.owner_id = owner_id

    @contextmanager
    def open(
        self, *, state_root: Path, workspace: Path, payload: Path,
        plan_id: str, selection_id: str,
    ) -> Iterator[_Attempt]:
        for path in (state_root, workspace, payload):
            _no_redirects(path)
        if _ID.fullmatch(plan_id) is None or _ID.fullmatch(selection_id) is None:
            _fail("binding", "registration plan or selection identity is invalid")
        _identity(workspace)
        _identity(payload)
        _private_directory(state_root)
        secure_private_path(state_root, directory=True)
        root = state_root / "registrations"
        _private_directory(root)
        secure_private_path(root, directory=True)
        self.catalog.register_record_store(
            family="registration-attempt-v1", owner_id=self.owner_id,
            workspace=workspace, root=root,
        )
        lease_root = self.catalog.root / "registration-attempt-leases"
        _private_directory(lease_root)
        secure_private_path(lease_root, directory=True)
        with private_record_lock(lease_root / f"{plan_id[7:]}.lock", wait=True):
            yield _Attempt(
                state_root=state_root, workspace=workspace, payload=payload,
                plan_id=plan_id, selection_id=selection_id, catalog=self.catalog,
            )


__all__ = ["CoreRegistrationAttempts"]
