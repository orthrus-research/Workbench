from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_mixins import (  # noqa: E402
    RAW_TRANSFORMER_CHAIN_FORMAT,
    TransformerChainValidationError,
    build_transformer_chain_receipt,
    parse_raw_transformer_chain,
    parse_transformer_chain_receipt,
)
from workbench_crucible_runtime_snapshot import bind_known_receipt  # noqa: E402


CAPTURE = "capture:chain"
PROVIDER_URI = "file:/runtime/cleanroom.jar"
TRANSFORMER_URI = "file:/runtime/foundation.jar"
TARGETS = {
    "example.CleanMixService": "a" * 64,
    "example.FoundationTransformerProvider": "b" * 64,
}


def _row(event: str, payload: dict) -> dict:
    return {
        "format": RAW_TRANSFORMER_CHAIN_FORMAT,
        "capture_id": CAPTURE,
        "sequence": 0,
        "event": event,
        "payload": payload,
    }


def _chain(wrapper: str | None = None) -> list[dict]:
    return [
        {
            "ordinal": 0,
            "reported_name": "example.Transformer",
            "implementation_class": "example.Transformer",
            "wrapper_class": wrapper,
            "implementation_loader_class": "example.Loader",
            "implementation_loader_identity": "example.Loader@1",
            "code_source_uri": TRANSFORMER_URI,
            "priority": 0,
            "delegation_excluded": False if wrapper else None,
        }
    ]


def _rows() -> list[dict]:
    rows = [
        _row(
            "capture_start",
            {
                "agent_id": "workbench-cleanmix-transformer-chain-agent-v1",
                "java_version": "25.0.4",
                "targets": [
                    {"target_class": target, "expected_input_sha256": digest}
                    for target, digest in TARGETS.items()
                ],
                "redefine_supported": False,
                "retransform_supported": False,
            },
        ),
        _row("transformer_installed", {"target_count": 2, "retransform_requested": False}),
    ]
    for target, digest in TARGETS.items():
        rows.append(
            _row(
                "transform_applied",
                {
                    "target_class": target,
                    "defining_loader_class": "example.Loader",
                    "defining_loader_identity": "example.Loader@1",
                    "target_code_source_uri": PROVIDER_URI,
                    "input_sha256": digest,
                    "output_sha256": "c" * 64,
                    "reason": None,
                },
            )
        )
    rows.extend(
        [
            _row(
                "provider_created",
                {
                    "provider_class": "example.FoundationTransformerProvider",
                    "provider_identity": "example.Provider@1",
                    "provider_loader_class": "example.Loader",
                    "provider_loader_identity": "example.Loader@1",
                    "provider_code_source_uri": PROVIDER_URI,
                    "provider_exclusions": ["example.Reentrant"],
                },
            ),
            _row(
                "refresh_requested",
                {
                    "refresh_id": 1,
                    "reason_code": "initial_cache_miss",
                    "phase": "PREINIT",
                    "provider_identity": "example.Provider@1",
                    "delegated_cache_present": False,
                    "previous_transformer_count": -1,
                    "live_transformer_count": 1,
                    "supersedes_refresh_id": None,
                },
            ),
            _row(
                "chain_epoch",
                {
                    "epoch": 1,
                    "refresh_id": 1,
                    "refresh_reason": "initial_cache_miss",
                    "phase": "PREINIT",
                    "provider_class": "example.FoundationTransformerProvider",
                    "provider_identity": "example.Provider@1",
                    "provider_loader_class": "example.Loader",
                    "provider_loader_identity": "example.Loader@1",
                    "provider_code_source_uri": PROVIDER_URI,
                    "foundation_code_source_uri": TRANSFORMER_URI,
                    "previous_transformer_count": 1,
                    "live_chain": _chain(),
                    "delegated_chain": _chain("example.LegacyTransformerHandle"),
                    "provider_exclusions": ["example.Reentrant"],
                    "foundation_transformer_exclusions": ["org.spongepowered.asm."],
                },
            ),
            _row(
                "capture_end",
                {
                    "health": "healthy",
                    "transformed_targets": sorted(TARGETS),
                    "epoch_count": 1,
                    "refresh_count": 1,
                    "unresolved_refresh_count": 0,
                    "exclusion_change_count": 0,
                    "max_live_transformer_count": 1,
                    "max_delegated_transformer_count": 1,
                    "observer_failure_count": 0,
                    "write_failure": False,
                },
            ),
        ]
    )
    for index, row in enumerate(rows):
        row["sequence"] = index
    return rows


def _encoded(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        for row in rows
    )


def _receipt() -> dict:
    rows = parse_raw_transformer_chain(_encoded(_rows()))
    return build_transformer_chain_receipt(
        session={
            "capture_id": CAPTURE,
            "launch_id": "launch:chain",
            "profile_id": "workbench-platform:cleanroom:test",
            "side": "dedicated_server",
            "candidate_lock_sha256": "d" * 64,
            "toolchain_lock_sha256": "e" * 64,
        },
        inputs={
            "agent_artifact": {"label": "agent.jar", "sha256": "1" * 64, "size_bytes": 10},
            "candidate_lock": {"label": "candidate.json", "sha256": "d" * 64, "size_bytes": 20},
            "fixture_result": {"label": "result.json", "sha256": "2" * 64, "size_bytes": 30},
            "launch_log": {"label": "launch.log", "sha256": "3" * 64, "size_bytes": 40},
            "raw_trace": {"label": "chain.ndjson", "sha256": "4" * 64, "size_bytes": 50},
            "toolchain_lock": {"label": "toolchain.json", "sha256": "e" * 64, "size_bytes": 60},
        },
        artifacts=[
            {"artifact_sha256": "5" * 64, "size_bytes": 70, "label": "cleanroom.jar", "code_source_uri": PROVIDER_URI},
            {"artifact_sha256": "6" * 64, "size_bytes": 80, "label": "foundation.jar", "code_source_uri": TRANSFORMER_URI},
        ],
        raw_events=rows,
        limitations=["Synthetic transformer-chain fixture."],
    )


class MixinTransformerChainReceiptTests(unittest.TestCase):
    def test_healthy_epoch_is_closed_and_snapshot_bindable(self) -> None:
        receipt = _receipt()
        admitted = parse_transformer_chain_receipt(receipt)
        schema = json.loads(
            (ROOT / "modules/crucible/schemas/mixin-transformer-chain-epoch-receipt-v1.schema.json").read_bytes()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(admitted)
        self.assertTrue(admitted["boundaries"]["transformer_chain_epochs_proved"])
        self.assertFalse(admitted["boundaries"]["final_class_bytes_proved"])
        self.assertEqual(admitted["epochs"][0]["live_chain"][0]["ordinal"], 0)
        binding = bind_known_receipt(
            json.dumps(admitted, separators=(",", ":"), sort_keys=True).encode(),
            source_label="receipts/transformer-chain.json",
        )
        self.assertEqual(binding["role"], "mixin.transformer-chain")

    def test_raw_epoch_rejects_reordered_ordinals(self) -> None:
        rows = _rows()
        epoch = next(row for row in rows if row["event"] == "chain_epoch")
        epoch["payload"]["live_chain"][0]["ordinal"] = 2
        with self.assertRaisesRegex(TransformerChainValidationError, "ordinals"):
            parse_raw_transformer_chain(_encoded(rows))

    def test_raw_footer_rejects_stale_exclusion_count(self) -> None:
        rows = _rows()
        rows[-1]["payload"]["exclusion_change_count"] = 1
        with self.assertRaisesRegex(TransformerChainValidationError, "exclusion change count"):
            parse_raw_transformer_chain(_encoded(rows))

    def test_identity_rejects_summary_tampering(self) -> None:
        receipt = _receipt()
        tampered = deepcopy(receipt)
        tampered["summary"]["epoch_count"] = 2
        with self.assertRaisesRegex(TransformerChainValidationError, "identity mismatch"):
            parse_transformer_chain_receipt(tampered)


if __name__ == "__main__":
    unittest.main()
