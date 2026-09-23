#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_ROOT = (
    REPOSITORY_ROOT
    / "profiles"
    / "platforms"
    / "cleanroom"
    / "candidates"
    / "0.6.8-alpha"
)
PLAN_PATH = CANDIDATE_ROOT / "worldgen-observatory-probe-plan-v1.json"
JAVA_ROOT = CANDIDATE_ROOT / "worldgen-observatory-fixture" / "src" / "main" / "java"

EXPECTED_CANONICAL_ROLES = {
    "cleanroom-worldgen:write.chunk_primer": "block_write.chunk_primer",
    "cleanroom-worldgen:write.world_api": "block_write.world_api",
    "cleanroom-worldgen:write.chunk_storage": "block_write.chunk_storage",
    "cleanroom-worldgen:event_bus.post": "event_dispatch.bus_post",
    "cleanroom-worldgen:event_bus.listener_invoke": (
        "event_dispatch.per_listener_callsite"
    ),
}

PUT_PATTERN = re.compile(
    r'hooks\.put\(\s*"(?P<mixin>[^"]+)"\s*,(?P<body>.*?)\n\s*\);',
    re.DOTALL,
)
HOOK_PATTERN = re.compile(
    r'new HookSpec\(\s*"(?P<hook_id>[^"]+)"\s*,\s*'
    r'"(?P<marker>[^"]+)"\s*,\s*"(?P<method>[^"]+)"\s*,\s*'
    r'"(?P<descriptor>[^"]+)"\s*\)'
)
MIXIN_PATTERN = re.compile(
    r"@Mixin\(\s*(?:value\s*=\s*)?(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\.class"
)
IMPORT_PATTERN = re.compile(
    r"^import\s+(?P<class>[A-Za-z_$][A-Za-z0-9_.$]*)\s*;\s*$",
    re.MULTILINE,
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def target_class_for_mixin(mixin_class: str) -> str:
    source_path = JAVA_ROOT.joinpath(*mixin_class.split(".")).with_suffix(".java")
    source = source_path.read_text(encoding="utf-8")
    match = MIXIN_PATTERN.search(source)
    if match is None:
        raise AssertionError(f"could not parse @Mixin target from {source_path}")
    simple_name = match.group("target")
    imports = {
        imported.rsplit(".", 1)[-1]: imported
        for imported in IMPORT_PATTERN.findall(source)
    }
    if simple_name not in imports:
        raise AssertionError(
            f"could not resolve @Mixin target {simple_name} in {source_path}"
        )
    return imports[simple_name]


def parse_plugin_hooks(plugin_path: Path) -> list[dict[str, Any]]:
    source = plugin_path.read_text(encoding="utf-8")
    hooks: list[dict[str, Any]] = []
    for group in PUT_PATTERN.finditer(source):
        mixin_class = group.group("mixin")
        target_class = target_class_for_mixin(mixin_class)
        parsed_group = list(HOOK_PATTERN.finditer(group.group("body")))
        if not parsed_group:
            raise AssertionError(f"no HookSpec entries parsed for {mixin_class}")
        for match in parsed_group:
            hooks.append(
                {
                    "raw_hook_id": match.group("hook_id"),
                    "merged_method_marker": match.group("marker"),
                    "mixin_class": mixin_class,
                    "target_class": target_class,
                    "target_method": match.group("method"),
                    "target_descriptor": match.group("descriptor"),
                    "expected_cardinality": 1,
                }
            )
    return hooks


class CleanroomProbePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_json(PLAN_PATH)

    def test_plan_is_content_addressed_and_explicitly_static(self) -> None:
        material = deepcopy(self.plan)
        plan_id = material.pop("plan_id")
        self.assertEqual(
            "cleanroom-worldgen-observatory-probe-plan:sha256:"
            + canonical_digest(material),
            plan_id,
        )
        self.assertEqual(
            {
                "capture_success": "not_claimed",
                "install_time_application": "not_observed",
                "kind": "static-source-binding-plan",
                "runtime_reachability": "not_observed",
            },
            self.plan["evidence_boundary"],
        )

    def test_candidate_catalog_and_raw_transport_are_byte_bound(self) -> None:
        binding = self.plan["binding"]
        candidate_lock_path = CANDIDATE_ROOT / binding["candidate_lock_path"]
        catalog_path = CANDIDATE_ROOT / binding["worldgen_hook_catalog_path"]
        self.assertEqual(
            binding["candidate_lock_sha256"], sha256_file(candidate_lock_path)
        )
        self.assertEqual(
            binding["worldgen_hook_catalog_sha256"], sha256_file(catalog_path)
        )
        self.assertEqual(
            binding["candidate_id"], load_json(candidate_lock_path)["candidate_id"]
        )
        self.assertEqual(binding["catalog_id"], load_json(catalog_path)["catalog_id"])

        raw = self.plan["raw_transport"]
        raw_schema_path = CANDIDATE_ROOT / raw["schema_path"]
        raw_schema = load_json(raw_schema_path)
        self.assertEqual(raw["schema_sha256"], sha256_file(raw_schema_path))
        self.assertEqual(raw["schema_id"], raw_schema["$id"])
        self.assertEqual(raw["format"], raw_schema["properties"]["format"]["const"])

    def test_plan_exactly_matches_every_java_hook_spec(self) -> None:
        source = self.plan["plugin_source"]
        plugin_path = CANDIDATE_ROOT / source["path"]
        self.assertEqual(source["sha256"], sha256_file(plugin_path))
        parsed = parse_plugin_hooks(plugin_path)
        planned = [
            {
                key: hook[key]
                for key in (
                    "raw_hook_id",
                    "merged_method_marker",
                    "mixin_class",
                    "target_class",
                    "target_method",
                    "target_descriptor",
                    "expected_cardinality",
                )
            }
            for hook in self.plan["hooks"]
        ]
        self.assertEqual(parsed, planned)
        self.assertEqual(len(parsed), len({hook["raw_hook_id"] for hook in parsed}))
        self.assertTrue(all(hook["expected_cardinality"] == 1 for hook in planned))

    def test_five_query_closing_roles_are_complete_and_unique(self) -> None:
        roles = {
            hook["raw_hook_id"]: hook["canonical_role"]
            for hook in self.plan["hooks"]
            if hook["canonical_role"] is not None
        }
        self.assertEqual(EXPECTED_CANONICAL_ROLES, roles)
        self.assertEqual(len(roles), len(set(roles.values())))
        self.assertEqual(5, len(roles))


if __name__ == "__main__":
    unittest.main()
