"""Bounded exact-runtime runner for Atlas Worldgen Observatory queries.

The runner treats canonical Crucible bundles as the only evidence authority.
Its SQLite files are process-local sorting spools: they are created under a
temporary directory, contain only derived comparison rows, and are deleted
before the report is returned.  This lets two large runs be admitted and
released sequentially instead of retaining both object graphs in memory.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping, Sequence
import zlib

from workbench_crucible_observatory import canonical_json_bytes

from .query import (
    QUERY_CONTRACT_ID,
    AdmittedWorldgenBundle,
    _answer,
    _capture_identity,
    _iter_semantic_base_items,
    _query_bundle,
    _semantic_value,
    first_divergence,
    load_admitted_worldgen_bundle,
    which_handler_changed_event,
    who_wrote_block,
)


SUITE_FORMAT = "workbench-atlas-worldgen-exact-query-suite-v1"
DEFAULT_EVENT_CLASS = "net.minecraftforge.event.terraingen.OreGenEvent$GenerateMinable"
DEFAULT_EVENT_BUS = "forge-bus:ore_gen_bus"


class ExactQuerySuiteError(ValueError):
    """The bounded exact query request could not be resolved safely."""


def _bundle(value: AdmittedWorldgenBundle) -> Mapping[str, Any]:
    bundle, already_validated = _query_bundle(value)
    if not already_validated:
        raise ExactQuerySuiteError("exact query suite requires admitted evidence")
    return bundle


def _comparison_scope(
    bundle: Mapping[str, Any],
    requested: str | None,
    *,
    checkpoint_id: str | None,
    chunk: tuple[int, int] | None,
) -> str:
    if requested is not None and (checkpoint_id is not None or chunk is not None):
        raise ExactQuerySuiteError(
            "comparison scope digest and checkpoint selector are mutually exclusive"
        )
    fingerprints = list(bundle["semantic_fingerprints"])
    if checkpoint_id is not None:
        fingerprints = [
            row for row in fingerprints if row["checkpoint_id"] == checkpoint_id
        ]
    if chunk is not None:
        fingerprints = [
            row
            for row in fingerprints
            if row["scope"]["chunk"] == {"x": chunk[0], "z": chunk[1]}
        ]
    scopes = sorted(
        {row["scope"]["comparison_scope_sha256"] for row in fingerprints}
    )
    if requested is not None:
        if requested not in scopes:
            raise ExactQuerySuiteError(
                "primary bundle lacks the requested comparison scope"
            )
        return requested
    if len(scopes) != 1:
        raise ExactQuerySuiteError(
            "comparison selector must resolve exactly one primary-bundle scope"
        )
    return scopes[0]


def select_event_span_id(
    admitted: AdmittedWorldgenBundle,
    *,
    event_class: str,
    bus_id: str,
    dimension_id: int,
    chunk_x: int,
    chunk_z: int,
    occurrence: int = 0,
) -> str:
    """Resolve one exact post span from a fully bounded canonical selector."""

    if occurrence < 0:
        raise ExactQuerySuiteError("event occurrence must be non-negative")
    candidates: list[str] = []
    for record in _bundle(admitted)["records"]:
        if record["record_type"] != "event_dispatch":
            continue
        payload = record["payload"]
        scope = record["scope"]
        chunk = scope["chunk"]
        if (
            payload["boundary"] == "post_enter"
            and payload["event_class"] == event_class
            and payload["bus_id"] == bus_id
            and scope["dimension_id"] == dimension_id
            and chunk == {"x": chunk_x, "z": chunk_z}
        ):
            span_id = record["causality"]["span_id"]
            if span_id is None:
                raise ExactQuerySuiteError("selected event post has no exact span ID")
            candidates.append(span_id)
    if occurrence >= len(candidates):
        raise ExactQuerySuiteError(
            "event selector resolved "
            f"{len(candidates)} occurrences, not requested occurrence {occurrence}"
        )
    return candidates[occurrence]


def _fingerprint_cohort(
    bundle: Mapping[str, Any],
    comparison_scope_sha256: str,
) -> tuple[
    dict[tuple[int, int, int, str], tuple[Any, ...]],
    set[int],
]:
    fingerprints = [
        row
        for row in bundle["semantic_fingerprints"]
        if row["scope"]["comparison_scope_sha256"] == comparison_scope_sha256
    ]
    cohort: dict[tuple[int, int, int, str], tuple[Any, ...]] = {}
    checkpoint_ordinals: set[int] = set()
    for fingerprint in fingerprints:
        scope = fingerprint["scope"]
        key = (
            scope["dimension_id"],
            scope["chunk"]["x"],
            scope["chunk"]["z"],
            scope["stage_id"],
        )
        cohort[key] = (
            fingerprint["canonicalization_id"],
            tuple(fingerprint["included_domains"]),
            tuple(fingerprint["excluded_observation_fields"]),
        )
        checkpoint_ordinals.add(fingerprint["checkpoint_ordinal"])
    return cohort, checkpoint_ordinals


def _encode_order_component(value: Any) -> bytes:
    if isinstance(value, bool):
        raise ExactQuerySuiteError("boolean is not a semantic comparison-key component")
    if isinstance(value, int):
        if value < -(1 << 63) or value >= (1 << 63):
            raise ExactQuerySuiteError("comparison-key integer exceeds signed 64-bit range")
        return b"I" + (value + (1 << 63)).to_bytes(8, "big")
    if isinstance(value, str):
        encoded = value.encode("utf-8").replace(b"\x00", b"\x00\xff")
        return b"S" + encoded + b"\x00\x00"
    raise ExactQuerySuiteError(
        f"unsupported semantic comparison-key component: {type(value).__name__}"
    )


def _encode_order_key(key: Iterable[Any]) -> bytes:
    return b"".join(_encode_order_component(value) for value in key)


def _cohort_json(
    cohort: Mapping[tuple[int, int, int, str], tuple[Any, ...]],
) -> bytes:
    return canonical_json_bytes(
        [
            {
                "dimension_id": key[0],
                "chunk": {"x": key[1], "z": key[2]},
                "stage_id": key[3],
                "canonicalization_id": value[0],
                "included_domains": list(value[1]),
                "excluded_observation_fields": list(value[2]),
            }
            for key, value in sorted(cohort.items())
        ]
    )


def _write_comparison_spool(
    admitted: AdmittedWorldgenBundle,
    destination: Path,
    *,
    comparison_scope_sha256: str,
) -> None:
    bundle = _bundle(admitted)
    if bundle["publication"]["state"] != "completed":
        raise ExactQuerySuiteError("comparison spool requires a completed capture")
    cohort, checkpoint_ordinals = _fingerprint_cohort(
        bundle,
        comparison_scope_sha256,
    )
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=FILE;
            PRAGMA locking_mode=EXCLUSIVE;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY NOT NULL,
                value BLOB NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE items (
                base_key BLOB NOT NULL,
                base_key_json BLOB NOT NULL,
                stage_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                semantic_value BLOB NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("capture_id", _capture_identity(bundle).encode("ascii")),
                ("cohort", _cohort_json(cohort)),
                ("comparison_scope_sha256", comparison_scope_sha256.encode("ascii")),
            ),
        )
        pending: list[tuple[bytes, bytes, str, str, int, bytes]] = []
        for item in _iter_semantic_base_items(
            bundle,
            set(cohort),
            checkpoint_ordinals,
        ):
            pending.append(
                (
                    _encode_order_key(item.comparison_key),
                    canonical_json_bytes(list(item.comparison_key)),
                    item.stage_id,
                    item.record_type,
                    item.ordinal,
                    zlib.compress(
                        canonical_json_bytes(_semantic_value(item.record)),
                        level=1,
                    ),
                )
            )
            if len(pending) == 2048:
                connection.executemany(
                    "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
                    pending,
                )
                pending.clear()
        if pending:
            connection.executemany(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
                pending,
            )
        connection.execute(
            "CREATE INDEX items_comparison_order ON items(base_key, ordinal)"
        )
        connection.commit()
    finally:
        connection.close()


def _metadata(connection: sqlite3.Connection, key: str) -> bytes:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None or not isinstance(row[0], bytes):
        raise ExactQuerySuiteError(f"comparison spool lacks {key} metadata")
    return row[0]


def _spool_rows(
    connection: sqlite3.Connection,
) -> Iterable[tuple[tuple[bytes, int], bytes, str, str, int, bytes, int]]:
    cursor = connection.execute(
        """
        SELECT base_key, base_key_json, stage_id, record_type, ordinal, semantic_value
        FROM items
        ORDER BY base_key, ordinal
        """
    )
    previous: bytes | None = None
    occurrence = 0
    for row in cursor:
        base_key = row[0]
        if base_key == previous:
            occurrence += 1
        else:
            previous = base_key
            occurrence = 0
        yield ((base_key, occurrence), *row[1:], occurrence)


def _comparison_key(base_key_json: bytes, occurrence: int) -> list[Any]:
    value = json.loads(base_key_json)
    if not isinstance(value, list):
        raise ExactQuerySuiteError("comparison spool contains an invalid key")
    value.append(occurrence)
    return value


def _public_spool_item(
    row: tuple[tuple[bytes, int], bytes, str, str, int, bytes, int],
    capture_id: str,
) -> dict[str, Any]:
    return {
        "comparison_key": _comparison_key(row[1], row[6]),
        "stage_id": row[2],
        "record_type": row[3],
        "ordinal": row[4],
        "value": json.loads(zlib.decompress(row[5])),
        "capture_id": capture_id,
    }


def _compare_semantic_spools(
    left_path: Path,
    right_path: Path,
) -> dict[str, Any]:
    """Compare two runner-owned ephemeral spools in their creating process."""

    left = sqlite3.connect(f"file:{left_path}?mode=ro", uri=True)
    right = sqlite3.connect(f"file:{right_path}?mode=ro", uri=True)
    try:
        left_capture = _metadata(left, "capture_id").decode("ascii")
        right_capture = _metadata(right, "capture_id").decode("ascii")
        scope_sha = _metadata(left, "comparison_scope_sha256").decode("ascii")
        scope = {"comparison_scope_sha256": scope_sha}
        if _metadata(right, "comparison_scope_sha256").decode("ascii") != scope_sha:
            raise ExactQuerySuiteError("comparison spools name different scopes")
        left_cohort = _metadata(left, "cohort")
        right_cohort = _metadata(right, "cohort")
        if left_cohort == b"[]" or right_cohort == b"[]":
            return _answer(
                query="first-divergence",
                status="unavailable",
                bundles=[],
                capture_ids=[left_capture, right_capture],
                scope=scope,
                limitations=["one or both captures lack the exact comparison scope"],
            )
        if left_cohort != right_cohort:
            return _answer(
                query="first-divergence",
                status="unavailable",
                bundles=[],
                capture_ids=[left_capture, right_capture],
                scope=scope,
                limitations=[
                    "comparison cohorts differ in chunks, stages, domains, or canonicalizer"
                ],
            )

        left_rows = iter(_spool_rows(left))
        right_rows = iter(_spool_rows(right))
        left_row = next(left_rows, None)
        right_row = next(right_rows, None)
        last_equal_rows: tuple[Any, Any] | None = None
        comparison_item_count = 0
        while left_row is not None or right_row is not None:
            if left_row is None:
                difference = "missing-left"
            elif right_row is None:
                difference = "missing-right"
            elif left_row[0] < right_row[0]:
                difference = "missing-right"
                right_row = None
            elif right_row[0] < left_row[0]:
                difference = "missing-left"
                left_row = None
            elif left_row[5] != right_row[5]:
                difference = "changed-value"
            else:
                last_equal_rows = (left_row, right_row)
                comparison_item_count += 1
                left_row = next(left_rows, None)
                right_row = next(right_rows, None)
                continue

            differing = left_row or right_row
            assert differing is not None
            last_equal = None
            if last_equal_rows is not None:
                last_equal = {
                    "comparison_key": _comparison_key(
                        last_equal_rows[0][1],
                        last_equal_rows[0][6],
                    ),
                    "record_type": last_equal_rows[0][3],
                    "left_ordinal": last_equal_rows[0][4],
                    "right_ordinal": last_equal_rows[1][4],
                }
            return _answer(
                query="first-divergence",
                status="diverged",
                bundles=[],
                capture_ids=[left_capture, right_capture],
                scope=scope,
                result={
                    "difference": difference,
                    "last_equal": last_equal,
                    "first_difference": {
                        "comparison_key": _comparison_key(differing[1], differing[6]),
                        "stage_id": differing[2],
                        "record_type": differing[3],
                        "left": (
                            None
                            if left_row is None
                            else _public_spool_item(left_row, left_capture)
                        ),
                        "right": (
                            None
                            if right_row is None
                            else _public_spool_item(right_row, right_capture)
                        ),
                    },
                },
            )

        last_equal = None
        if last_equal_rows is not None:
            last_equal = {
                "comparison_key": _comparison_key(
                    last_equal_rows[0][1],
                    last_equal_rows[0][6],
                ),
                "record_type": last_equal_rows[0][3],
                "left_ordinal": last_equal_rows[0][4],
                "right_ordinal": last_equal_rows[1][4],
            }
        return _answer(
            query="first-divergence",
            status="equal",
            bundles=[],
            capture_ids=[left_capture, right_capture],
            scope=scope,
            limitations=[
                "equality is bounded to the declared comparison scope and domains"
            ],
            result={
                "comparison_item_count": comparison_item_count,
                "last_equal": last_equal,
                "first_difference": None,
            },
        )
    finally:
        left.close()
        right.close()


def _changed_actor_ids(answer: Mapping[str, Any]) -> set[str]:
    result = answer.get("result")
    if not isinstance(result, Mapping):
        return set()
    handlers = result.get("handlers")
    if not isinstance(handlers, list):
        return set()
    return {
        row["actor"]["mod_id"]
        for row in handlers
        if isinstance(row, Mapping) and row.get("changed") is True
    }


def run_exact_query_suite(
    *,
    primary_bundle_path: Path | str,
    comparison_bundle_path: Path | str,
    crash_bundle_path: Path | str,
    dimension_id: int,
    block_position: tuple[int, int, int],
    event_span_id: str | None = None,
    event_class: str = DEFAULT_EVENT_CLASS,
    event_bus_id: str = DEFAULT_EVENT_BUS,
    event_chunk: tuple[int, int] = (64, 64),
    event_occurrence: int = 0,
    comparison_scope_sha256: str | None = None,
    comparison_checkpoint_id: str | None = None,
    comparison_chunk: tuple[int, int] | None = None,
    expected_divergence_status: str = "either",
    expected_writer_mod_id: str | None = None,
    expected_handler_mod_id: str | None = None,
    scratch_directory: Path | str | None = None,
) -> dict[str, Any]:
    """Run the exact success queries and all three crash rejection checks."""

    if expected_divergence_status not in {"equal", "diverged", "either"}:
        raise ExactQuerySuiteError("expected divergence status is invalid")
    scratch_parent = None if scratch_directory is None else Path(scratch_directory)
    if scratch_parent is not None:
        scratch_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="atlas-worldgen-query-",
        dir=scratch_parent,
    ) as temporary:
        temporary_root = Path(temporary)
        left_spool = temporary_root / "primary.sqlite3"
        right_spool = temporary_root / "comparison.sqlite3"

        primary = load_admitted_worldgen_bundle(primary_bundle_path)
        primary_value = _bundle(primary)
        scope_sha = _comparison_scope(
            primary_value,
            comparison_scope_sha256,
            checkpoint_id=comparison_checkpoint_id,
            chunk=comparison_chunk,
        )
        resolved_event_span = event_span_id or select_event_span_id(
            primary,
            event_class=event_class,
            bus_id=event_bus_id,
            dimension_id=dimension_id,
            chunk_x=event_chunk[0],
            chunk_z=event_chunk[1],
            occurrence=event_occurrence,
        )
        writer_answer = who_wrote_block(
            primary,
            dimension_id=dimension_id,
            x=block_position[0],
            y=block_position[1],
            z=block_position[2],
        )
        event_answer = which_handler_changed_event(
            primary,
            event_span_id=resolved_event_span,
        )
        _write_comparison_spool(
            primary,
            left_spool,
            comparison_scope_sha256=scope_sha,
        )
        primary_capture_id = _capture_identity(primary_value)
        del primary_value
        del primary
        gc.collect()

        comparison = load_admitted_worldgen_bundle(comparison_bundle_path)
        comparison_capture_id = _capture_identity(_bundle(comparison))
        _write_comparison_spool(
            comparison,
            right_spool,
            comparison_scope_sha256=scope_sha,
        )
        del comparison
        gc.collect()
        divergence_answer = _compare_semantic_spools(left_spool, right_spool)

        crash = load_admitted_worldgen_bundle(crash_bundle_path)
        crash_rejections = {
            "who_wrote_block": who_wrote_block(
                crash,
                dimension_id=dimension_id,
                x=block_position[0],
                y=block_position[1],
                z=block_position[2],
            ),
            "which_handler_changed_event": which_handler_changed_event(
                crash,
                event_span_id=resolved_event_span,
            ),
            "first_divergence": first_divergence(
                crash,
                crash,
                comparison_scope_sha256=scope_sha,
            ),
        }
        crash_capture_id = _capture_identity(_bundle(crash))

    writer_mod_id = None
    writer_result = writer_answer.get("result")
    if isinstance(writer_result, Mapping):
        final_writer = writer_result.get("final_writer")
        if isinstance(final_writer, Mapping):
            actor = final_writer.get("initiating_actor")
            if isinstance(actor, Mapping):
                writer_mod_id = actor.get("mod_id")
    changed_actor_ids = _changed_actor_ids(event_answer)
    event_result = event_answer.get("result")
    changed_handler_count = (
        event_result.get("changed_handler_count", 0)
        if isinstance(event_result, Mapping)
        else 0
    )
    gates = {
        "writer_answered": writer_answer["status"] == "answered",
        "event_answered": (
            event_answer["status"] == "answered"
            and changed_handler_count > 0
        ),
        "divergence_closed": (
            divergence_answer["status"] in {"equal", "diverged"}
            and (
                expected_divergence_status == "either"
                or divergence_answer["status"] == expected_divergence_status
            )
        ),
        "writer_identity_matches": (
            expected_writer_mod_id is None or writer_mod_id == expected_writer_mod_id
        ),
        "handler_identity_matches": (
            expected_handler_mod_id is None
            or expected_handler_mod_id in changed_actor_ids
        ),
        "all_queries_reject_crash": all(
            answer["status"] == "incomplete-evidence"
            for answer in crash_rejections.values()
        ),
    }
    return {
        "format": SUITE_FORMAT,
        "query_contract_id": QUERY_CONTRACT_ID,
        "authority": {
            "evidence_owner": "Crucible",
            "interpretation_owner": "Atlas",
            "sorting_spool": "ephemeral-derived-non-authoritative",
        },
        "inputs": {
            "primary_capture_id": primary_capture_id,
            "comparison_capture_id": comparison_capture_id,
            "crash_capture_id": crash_capture_id,
            "dimension_id": dimension_id,
            "block_position": list(block_position),
            "event_span_id": resolved_event_span,
            "event_selector": {
                "mode": "explicit-span" if event_span_id is not None else "occurrence",
                "event_class": event_class,
                "bus_id": event_bus_id,
                "chunk": {"x": event_chunk[0], "z": event_chunk[1]},
                "occurrence": event_occurrence,
            },
            "comparison_scope_sha256": scope_sha,
            "comparison_checkpoint_id": comparison_checkpoint_id,
            "comparison_chunk": (
                None if comparison_chunk is None else list(comparison_chunk)
            ),
        },
        "expectations": {
            "divergence_status": expected_divergence_status,
            "writer_mod_id": expected_writer_mod_id,
            "handler_mod_id": expected_handler_mod_id,
            "crash_status": "incomplete-evidence",
        },
        "answers": {
            "who_wrote_block": writer_answer,
            "which_handler_changed_event": event_answer,
            "first_divergence": divergence_answer,
        },
        "crash_rejections": crash_rejections,
        "gates": gates,
        "passed": all(gates.values()),
    }


def write_exact_query_report(path: Path | str, report: Mapping[str, Any]) -> None:
    """Atomically write a derived suite report; it is not an evidence seal."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(report))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--primary-bundle", type=Path, required=True)
    result.add_argument("--comparison-bundle", type=Path, required=True)
    result.add_argument("--crash-bundle", type=Path, required=True)
    result.add_argument("--dimension-id", type=int, required=True)
    result.add_argument("--block-position", type=int, nargs=3, required=True)
    result.add_argument("--event-span-id")
    result.add_argument("--event-class", default=DEFAULT_EVENT_CLASS)
    result.add_argument("--event-bus-id", default=DEFAULT_EVENT_BUS)
    result.add_argument("--event-chunk", type=int, nargs=2, default=(64, 64))
    result.add_argument("--event-occurrence", type=int, default=0)
    result.add_argument("--comparison-scope-sha256")
    result.add_argument("--comparison-checkpoint-id")
    result.add_argument("--comparison-chunk", type=int, nargs=2)
    result.add_argument(
        "--expect-divergence-status",
        choices=("equal", "diverged", "either"),
        default="either",
    )
    result.add_argument("--expected-writer-mod-id")
    result.add_argument("--expected-handler-mod-id")
    result.add_argument("--scratch-directory", type=Path)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        report = run_exact_query_suite(
            primary_bundle_path=arguments.primary_bundle,
            comparison_bundle_path=arguments.comparison_bundle,
            crash_bundle_path=arguments.crash_bundle,
            dimension_id=arguments.dimension_id,
            block_position=tuple(arguments.block_position),
            event_span_id=arguments.event_span_id,
            event_class=arguments.event_class,
            event_bus_id=arguments.event_bus_id,
            event_chunk=tuple(arguments.event_chunk),
            event_occurrence=arguments.event_occurrence,
            comparison_scope_sha256=arguments.comparison_scope_sha256,
            comparison_checkpoint_id=arguments.comparison_checkpoint_id,
            comparison_chunk=(
                None
                if arguments.comparison_chunk is None
                else tuple(arguments.comparison_chunk)
            ),
            expected_divergence_status=arguments.expect_divergence_status,
            expected_writer_mod_id=arguments.expected_writer_mod_id,
            expected_handler_mod_id=arguments.expected_handler_mod_id,
            scratch_directory=arguments.scratch_directory,
        )
        write_exact_query_report(arguments.output, report)
    except (ExactQuerySuiteError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"Atlas exact worldgen query suite: {exc}", file=os.sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
