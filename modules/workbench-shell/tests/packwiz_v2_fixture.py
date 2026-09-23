"""Authentic Packwiz V2 receipt sealing for focused consumer tests."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any, Mapping, Sequence

from workbench_shell.runtime_materialize import (
    PACKWIZ_V2_POLICY,
    PACKWIZ_V2_POLICY_VERSION,
    _canonical_bytes,
    _packwiz_decisions_sha256,
    _packwiz_initial_state_bytes,
)


def seal_packwiz_v2_receipt(
    value: Mapping[str, Any],
    *,
    files: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a verifier-valid V2 receipt around a consumer fixture payload."""

    receipt = deepcopy(dict(value))
    receipt["format"] = "workbench-packwiz-materialization-receipt-v2"
    receipt["schema_version"] = 2
    receipt.setdefault("plan_id", "sha256:" + "2" * 64)
    target = receipt.setdefault("target", {})
    instance_uri = str(target["instance_root_uri"])
    if not instance_uri.endswith("/instance"):
        raise AssertionError("V2 fixture instance URI must end in /instance")
    fixture_uri = instance_uri.removesuffix("/instance")

    decision_rows = [deepcopy(dict(row)) for row in files]
    decision_rows.sort(key=lambda row: row["metadata_path"])
    decisions = [
        {
            field: row[field]
            for field in (
                "metadata_path",
                "metafile_sha256",
                "output_path",
                "name",
                "side",
                "declared_default",
                "applied",
            )
        }
        for row in decision_rows
    ]
    decisions_sha256 = _packwiz_decisions_sha256(decisions)
    variant_id = "sha256:" + sha256(_canonical_bytes({
        "plan_id": receipt["plan_id"],
        "policy": PACKWIZ_V2_POLICY,
        "policy_version": PACKWIZ_V2_POLICY_VERSION,
        "decisions_sha256": decisions_sha256,
    })).hexdigest()
    target.update({
        "variant": "packwiz-v2",
        "variant_id": variant_id,
        "variant_root_uri": fixture_uri,
        "fixture_root_uri": fixture_uri,
        "instance_root_uri": instance_uri,
        "receipt_uri": (
            fixture_uri + "/receipts/packwiz-materialization-v2.json"
        ),
    })

    payload = receipt.setdefault("payload", {})
    payload["root_uri"] = instance_uri + "/.minecraft"
    launcher = receipt.setdefault("launcher", {})
    manifest_sha256 = str(
        launcher.get("manifest_sha256_after", "5" * 64)
    )
    launcher.update({
        "manifest_uri": instance_uri + "/mmc-pack.json",
        "manifest_sha256_before": manifest_sha256,
        "manifest_sha256_after": manifest_sha256,
    })

    initial_bytes = _packwiz_initial_state_bytes(decisions)
    final_bytes = b'{"cachedSide":"client"}'
    policy_sha256 = "sha256:" + sha256(_canonical_bytes({
        "policy": PACKWIZ_V2_POLICY,
        "policy_version": PACKWIZ_V2_POLICY_VERSION,
    })).hexdigest()
    options = {
        "policy": PACKWIZ_V2_POLICY,
        "policy_version": PACKWIZ_V2_POLICY_VERSION,
        "policy_sha256": policy_sha256,
        "decisions_sha256": decisions_sha256,
        "optional_count": len(decision_rows),
        "enabled_count": sum(
            int(row["declared_default"] is True) for row in decision_rows
        ),
        "disabled_count": sum(
            int(row["declared_default"] is False) for row in decision_rows
        ),
        "installer_state": {
            "relative_path": "packwiz.json",
            "uri": payload["root_uri"] + "/packwiz.json",
            "cached_side": "client",
            "initial": {
                "sha256": sha256(initial_bytes).hexdigest(),
                "size": len(initial_bytes),
            },
            "final": {
                "sha256": sha256(final_bytes).hexdigest(),
                "size": len(final_bytes),
            },
        },
        "files": decision_rows,
    }
    receipt["packwiz_options"] = options
    receipt["option_policy"] = {
        "side": "client",
        "optional_files": PACKWIZ_V2_POLICY,
        "launcher_metadata": "isolated-empty-sentinel",
    }
    receipt["bootstrap_source"] = {
        "fixture_root_uri": "file:///bootstrap",
        "receipt_uri": "file:///bootstrap/receipts/cleanroom-client-bootstrap-v1.json",
        "receipt_sha256": "6" * 64,
        "receipt_size": 256,
        "launcher_tree": {
            "tree_sha256": "sha256:" + "7" * 64,
            "file_count": 2,
            "total_bytes": 512,
        },
    }
    receipt.setdefault("source_snapshot", {
        "tree_sha256": "sha256:" + "8" * 64,
    })
    receipt.setdefault("refreshed_pack", {
        "manifest_sha256": "9" * 64,
        "index": {"actual_sha256": "a" * 64},
    })
    receipt.setdefault("tools", {
        "packwiz": {"sha256": "b" * 64, "size": 1},
        "installer": {
            "url": "https://example.invalid/packwiz-installer.jar",
            "source_revision": "c" * 40,
            "sha256": "d" * 64,
            "size": 2,
            "entrypoint": "link.infra.packwiz.installer.Main",
        },
        "java": {"identity": {"runtime_id": "sha256:" + "e" * 64}},
    })

    source = receipt["source_snapshot"]
    pack = receipt["refreshed_pack"]
    tools = receipt["tools"]
    identity = {
        "plan_id": receipt["plan_id"],
        "source_tree_sha256": source["tree_sha256"],
        "pack_manifest_sha256": pack["manifest_sha256"],
        "pack_index_sha256": pack["index"]["actual_sha256"],
        "packwiz": {
            "sha256": tools["packwiz"]["sha256"],
            "size": tools["packwiz"]["size"],
        },
        "installer": {
            field: tools["installer"][field]
            for field in (
                "url",
                "source_revision",
                "sha256",
                "size",
                "entrypoint",
            )
        },
        "java": dict(tools["java"]["identity"]),
        "launcher_manifest_sha256": launcher["manifest_sha256_before"],
        "payload_tree_sha256": payload["tree_sha256"],
        "variant_id": variant_id,
        "target": dict(target),
        "launcher": dict(launcher),
        "payload": dict(payload),
        "bootstrap_source": dict(receipt["bootstrap_source"]),
        "packwiz_options": dict(options),
    }
    receipt["materialization_id"] = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    return receipt


def optional_file(*, enabled: bool = False) -> dict[str, Any]:
    return {
        "metadata_path": "mods/optional.pw.toml",
        "metafile_sha256": "f" * 64,
        "output_path": "mods/optional.jar",
        "output_sha256": "1" * 64 if enabled else None,
        "output_size": 42 if enabled else None,
        "name": "Optional fixture",
        "side": "client",
        "declared_default": enabled,
        "applied": enabled,
        "present": enabled,
    }
