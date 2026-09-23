#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
)
from workbench_crucible_observatory.cleanroom_raw import RawAdmission  # noqa: E402
from workbench_crucible_observatory.cleanroom_runtime_custody import (  # noqa: E402
    build_exact_actor_inventory,
    build_foundation_class_dump_manifest,
    build_verified_class_dump_sha256,
    collect_cleanroom_runtime_custody,
    locate_foundation_class_dump,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def minimal_class(
    internal_name: str,
    methods: tuple[tuple[str, str], ...] = (),
) -> bytes:
    name = internal_name.encode("utf-8")
    parent = b"java/lang/Object"
    entries = [
        b"\x01" + struct.pack(">H", len(name)) + name,
        b"\x07" + struct.pack(">H", 1),
        b"\x01" + struct.pack(">H", len(parent)) + parent,
        b"\x07" + struct.pack(">H", 3),
    ]
    method_rows = []
    for method_name, descriptor in methods:
        encoded_name = method_name.encode("utf-8")
        encoded_descriptor = descriptor.encode("utf-8")
        name_index = len(entries) + 1
        entries.append(
            b"\x01" + struct.pack(">H", len(encoded_name)) + encoded_name
        )
        descriptor_index = len(entries) + 1
        entries.append(
            b"\x01"
            + struct.pack(">H", len(encoded_descriptor))
            + encoded_descriptor
        )
        method_rows.append(
            struct.pack(">HHHH", 0x0001, name_index, descriptor_index, 0)
        )
    return b"".join(
        (
            struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(entries) + 1),
            b"".join(entries),
            struct.pack(">HHH", 0x0021, 2, 4),
            struct.pack(">HHH", 0, 0, len(method_rows)),
            b"".join(method_rows),
            struct.pack(">H", 0),
        )
    )


def actor(
    class_name: str | None,
    method_name: str | None,
    descriptor: str | None,
    namespace: str | None,
    source: str | None,
    *,
    mod_id: str | None,
) -> dict[str, Any]:
    return {
        "binding": "runtime_class",
        "mod_id": mod_id,
        "class_name": class_name,
        "method_name": method_name,
        "method_descriptor": descriptor,
        "mapping_namespace": namespace,
        "code_source_sha256": source,
        "transformed_class_sha256": None,
    }


def admission(*actors: dict[str, Any]) -> RawAdmission:
    return RawAdmission(
        rows=tuple({"actor": value} for value in actors),
        capture_nonce="custody-test",
        requested_mode="lossless-fixture",
        schema_id="test-schema",
        schema_sha256=digest("schema"),
        probe_plan_id="test-plan",
        control_summary={},
        coverage_summary={},
        health_summary={},
    )


def plan(*target_classes: str) -> dict[str, Any]:
    return {
        "hooks": [
            {"target_class": class_name}
            for class_name in target_classes
        ]
    }


class CleanroomRuntimeCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.case = Path(self.temporary.name) / "case"
        self.dump = self.case / "server" / "CLASS_DUMP" / "1"
        self.dump.mkdir(parents=True)

    def write_class(
        self,
        class_name: str,
        *,
        internal_name: str | None = None,
        methods: tuple[tuple[str, str], ...] = (),
    ) -> bytes:
        path = self.dump.joinpath(*class_name.split(".")).with_suffix(".class")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = minimal_class(
            internal_name or class_name.replace(".", "/"),
            methods,
        )
        path.write_bytes(data)
        return data

    def test_locates_the_single_numbered_foundation_dump(self) -> None:
        self.assertEqual(
            locate_foundation_class_dump(self.case),
            self.dump.resolve(),
        )

    def test_manifests_the_complete_dump_tree_deterministically(self) -> None:
        alpha = self.write_class(
            "mods.alpha.Generator",
            methods=(("generate", "()V"),),
        )
        beta = self.write_class(
            "mods.beta.Decorator",
            methods=(("decorate", "()V"),),
        )

        manifest = build_foundation_class_dump_manifest(self.dump)

        expected_classes = [
            {
                "relative_path": "mods/alpha/Generator.class",
                "size": len(alpha),
                "sha256": hashlib.sha256(alpha).hexdigest(),
            },
            {
                "relative_path": "mods/beta/Decorator.class",
                "size": len(beta),
                "sha256": hashlib.sha256(beta).hexdigest(),
            },
        ]
        material = {
            "format": "workbench-foundation-class-dump-manifest-v1",
            "class_count": 2,
            "total_size_bytes": len(alpha) + len(beta),
            "classes": expected_classes,
        }
        self.assertEqual(manifest.class_dump_directory, self.dump.resolve())
        self.assertEqual(manifest.class_count, 2)
        self.assertEqual(manifest.total_size_bytes, len(alpha) + len(beta))
        self.assertEqual(
            [
                {
                    "relative_path": entry.relative_path,
                    "size": entry.size,
                    "sha256": entry.sha256,
                }
                for entry in manifest.entries
            ],
            expected_classes,
        )
        self.assertEqual(
            manifest.manifest_sha256,
            hashlib.sha256(canonical_json_bytes(material)).hexdigest(),
        )
        self.assertEqual(
            build_foundation_class_dump_manifest(self.dump).manifest_sha256,
            manifest.manifest_sha256,
        )

    def test_dump_manifest_changes_when_any_class_changes(self) -> None:
        self.write_class("mods.alpha.Generator")
        before = build_foundation_class_dump_manifest(self.dump)
        self.write_class(
            "mods.alpha.Generator",
            methods=(("generate", "()V"),),
        )
        after = build_foundation_class_dump_manifest(self.dump)

        self.assertNotEqual(before.manifest_sha256, after.manifest_sha256)
        self.assertNotEqual(before.entries[0].sha256, after.entries[0].sha256)

    def test_dump_manifest_rejects_empty_and_non_class_trees(self) -> None:
        with self.assertRaisesRegex(CaptureValidationError, "generation is empty"):
            build_foundation_class_dump_manifest(self.dump)

        (self.dump / "notice.txt").write_text("not a class", encoding="utf-8")
        with self.assertRaisesRegex(CaptureValidationError, "non-class file"):
            build_foundation_class_dump_manifest(self.dump)

    def test_dump_manifest_rejects_symlinks_and_path_anomalies(self) -> None:
        outside = Path(self.temporary.name) / "Outside.class"
        outside.write_bytes(minimal_class("Outside"))
        link = self.dump / "Linked.class"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaisesRegex(CaptureValidationError, "must not be a symlink"):
            build_foundation_class_dump_manifest(self.dump)

        link.unlink()
        anomalous = self.dump / "bad\\name.class"
        anomalous.write_bytes(minimal_class("Bad"))
        with self.assertRaisesRegex(CaptureValidationError, "path component"):
            build_foundation_class_dump_manifest(self.dump)

    def test_dump_manifest_rejects_mutation_while_hashing(self) -> None:
        self.write_class("mods.alpha.Generator")
        real_fstat = os.fstat
        calls = 0

        def changing_fstat(descriptor: int) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            observed = real_fstat(descriptor)
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns + (1 if calls == 2 else 0),
                st_ctime_ns=observed.st_ctime_ns,
            )

        with patch(
            "workbench_crucible_observatory.cleanroom_runtime_custody.os.fstat",
            side_effect=changing_fstat,
        ):
            with self.assertRaisesRegex(CaptureValidationError, "mutated while reading"):
                build_foundation_class_dump_manifest(self.dump)
        self.assertEqual(
            locate_foundation_class_dump(self.case / "server"),
            self.dump.resolve(),
        )

    def test_rejects_multiple_or_missing_numbered_dumps(self) -> None:
        (self.dump.parent / "2").mkdir()
        with self.assertRaisesRegex(CaptureValidationError, "exactly one numbered"):
            locate_foundation_class_dump(self.case)

        empty_case = Path(self.temporary.name) / "empty-case"
        (empty_case / "server" / "CLASS_DUMP").mkdir(parents=True)
        with self.assertRaisesRegex(CaptureValidationError, "exactly one numbered"):
            locate_foundation_class_dump(empty_case)

    def test_hashes_raw_actor_and_plan_target_classes(self) -> None:
        source = digest("shared-mod-jar")
        raw = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "(II)V",
                "jvm_descriptor",
                source,
                mod_id="alpha",
            )
        )
        actor_bytes = self.write_class("mods.alpha.Generator")
        plan_bytes = self.write_class("net.minecraft.world.World")

        hashes = build_verified_class_dump_sha256(
            raw,
            plan("net.minecraft.world.World"),
            self.dump,
        )

        self.assertEqual(
            dict(hashes),
            {
                "mods.alpha.Generator": hashlib.sha256(actor_bytes).hexdigest(),
                "net.minecraft.world.World": hashlib.sha256(plan_bytes).hexdigest(),
            },
        )

    def test_rejects_missing_or_internally_mismatched_class_dump(self) -> None:
        source = digest("source")
        raw = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "()V",
                "jvm_descriptor",
                source,
                mod_id="alpha",
            )
        )
        with self.assertRaisesRegex(CaptureValidationError, "is missing"):
            build_verified_class_dump_sha256(raw, plan("mods.alpha.Generator"), self.dump)

        self.write_class(
            "mods.alpha.Generator",
            internal_name="mods/alpha/DifferentGenerator",
        )
        with self.assertRaisesRegex(CaptureValidationError, "this_class does not match"):
            build_verified_class_dump_sha256(raw, plan("mods.alpha.Generator"), self.dump)

    def test_shared_jar_digest_never_selects_the_mod_owner(self) -> None:
        shared_source = digest("shared-mod-jar")
        raw = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "()V",
                "jvm_descriptor",
                shared_source,
                mod_id="alpha",
            ),
            actor(
                "mods.beta.Decorator",
                "decorate",
                "()V",
                "jvm_descriptor",
                shared_source,
                mod_id=None,
            ),
        )
        self.write_class(
            "mods.alpha.Generator",
            methods=(("generate", "()V"),),
        )
        self.write_class(
            "mods.beta.Decorator",
            methods=(("decorate", "()V"),),
        )

        inventory = build_exact_actor_inventory(
            raw,
            self.dump,
            trusted_class_prefix_owners={
                "mods.alpha": "alpha",
                "mods.beta": "beta",
            },
            loaded_mod_source_sha256={
                "alpha": shared_source,
                "beta": shared_source,
            },
        )

        by_class = {row["class_name"]: dict(row) for row in inventory}
        self.assertEqual(by_class["mods.alpha.Generator"]["mod_id"], "alpha")
        self.assertEqual(by_class["mods.beta.Decorator"]["mod_id"], "beta")

    def test_rejects_ambiguous_class_prefix_ownership(self) -> None:
        source = digest("shared")
        raw = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "()V",
                "jvm_descriptor",
                source,
                mod_id=None,
            )
        )
        with self.assertRaisesRegex(CaptureValidationError, "ambiguous"):
            build_exact_actor_inventory(
                raw,
                self.dump,
                trusted_class_prefix_owners={
                    "mods": "container",
                    "mods.alpha": "alpha",
                },
                loaded_mod_source_sha256={
                    "container": source,
                    "alpha": source,
                },
            )

    def test_rejects_owner_or_source_contradictions(self) -> None:
        expected_source = digest("expected")
        raw_owner_mismatch = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "()V",
                "jvm_descriptor",
                expected_source,
                mod_id="beta",
            )
        )
        with self.assertRaisesRegex(CaptureValidationError, "contradicts trusted owner"):
            build_exact_actor_inventory(
                raw_owner_mismatch,
                self.dump,
                trusted_class_prefix_owners={"mods.alpha": "alpha"},
                loaded_mod_source_sha256={"alpha": expected_source},
            )

        raw_source_mismatch = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "()V",
                "jvm_descriptor",
                digest("unexpected"),
                mod_id="alpha",
            )
        )
        with self.assertRaisesRegex(CaptureValidationError, "contradicts loaded source"):
            build_exact_actor_inventory(
                raw_source_mismatch,
                self.dump,
                trusted_class_prefix_owners={"mods.alpha": "alpha"},
                loaded_mod_source_sha256={"alpha": expected_source},
            )

    def test_collects_normalizer_inputs_as_one_receipt(self) -> None:
        source = digest("alpha")
        raw = admission(
            actor(
                "mods.alpha.Generator",
                "generate",
                "()V",
                "jvm_descriptor",
                source,
                mod_id="alpha",
            )
        )
        class_bytes = self.write_class(
            "mods.alpha.Generator",
            methods=(("generate", "()V"),),
        )

        custody = collect_cleanroom_runtime_custody(
            self.case,
            raw,
            plan("mods.alpha.Generator"),
            trusted_class_prefix_owners={"mods.alpha": "alpha"},
            loaded_mod_source_sha256={"alpha": source},
        )

        self.assertEqual(custody.class_dump_directory, self.dump.resolve())
        self.assertEqual(custody.class_dump_manifest.class_count, 1)
        self.assertEqual(
            custody.class_dump_sha256["mods.alpha.Generator"],
            hashlib.sha256(class_bytes).hexdigest(),
        )
        self.assertEqual(custody.actor_inventory[0]["mod_id"], "alpha")

    def test_rejects_non_jvm_descriptor_without_translation(self) -> None:
        source = digest("forge")
        raw = admission(
            actor(
                "net.minecraftforge.fml.common.eventhandler.EventPriority",
                "invoke",
                "(Event)void",
                "jvm_descriptor",
                source,
                mod_id=None,
            )
        )
        self.write_class(
            "net.minecraftforge.fml.common.eventhandler.EventPriority",
            methods=(
                (
                    "invoke",
                    "(Lnet/minecraftforge/fml/common/eventhandler/Event;)V",
                ),
            ),
        )

        with self.assertRaisesRegex(
            CaptureValidationError,
            "absent from final transformed class dump.*no translation attempted",
        ):
            build_exact_actor_inventory(
                raw,
                self.dump,
                trusted_class_prefix_owners={"net.minecraftforge": "forge"},
                loaded_mod_source_sha256={"forge": source},
            )

    def test_java_source_label_requires_literal_binary_method(self) -> None:
        source = digest("workbench")
        exact = admission(
            actor(
                "dev.workbench.Driver",
                "run",
                "(Lnet/minecraft/server/MinecraftServer;)V",
                "java_source",
                source,
                mod_id="workbench",
            )
        )
        self.write_class(
            "dev.workbench.Driver",
            methods=(("run", "(Lnet/minecraft/server/MinecraftServer;)V"),),
        )
        inventory = build_exact_actor_inventory(
            exact,
            self.dump,
            trusted_class_prefix_owners={"dev.workbench": "workbench"},
            loaded_mod_source_sha256={"workbench": source},
        )
        self.assertEqual(inventory[0]["mapping_namespace"], "java_source")

        source_syntax = admission(
            actor(
                "dev.workbench.Driver",
                "run",
                "void (MinecraftServer)",
                "java_source",
                source,
                mod_id="workbench",
            )
        )
        with self.assertRaisesRegex(CaptureValidationError, "no translation attempted"):
            build_exact_actor_inventory(
                source_syntax,
                self.dump,
                trusted_class_prefix_owners={"dev.workbench": "workbench"},
                loaded_mod_source_sha256={"workbench": source},
            )


if __name__ == "__main__":
    unittest.main()
