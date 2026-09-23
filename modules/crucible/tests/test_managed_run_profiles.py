from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
for source in (PROJECT_INTELLIGENCE_SOURCE, CRUCIBLE_SOURCE, ROOT / "modules/workbench-shell/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

import workbench_crucible_run_profiles.profiles as profiles_module  # noqa: E402
import workbench_crucible_run_profiles as run_profiles_package  # noqa: E402
from workbench_shell import run_commands as workbench_shell  # noqa: E402
from workbench_core.cli import main as core_main  # noqa: E402
from workbench_crucible_run_profiles.profiles import (  # noqa: E402
    ManagedRunProfileError,
    execute_managed_run_plan,
    load_catalog,
    render_managed_run_plan,
    resolve_managed_run_plan,
)
from workbench_project_intelligence.workspace_doctor import (  # noqa: E402
    validate_workspace_doctor_report,
)


CATALOG_PATH = (
    ROOT
    / "profiles/packs/supersymmetry/run-profiles/managed-run-profiles-v1.json"
)
CATALOG_SCHEMA_PATH = (
    ROOT / "modules/crucible/schemas/managed-run-profile-catalog-v1.schema.json"
)
PLAN_SCHEMA_PATH = ROOT / "modules/crucible/schemas/managed-run-plan-v1.schema.json"
WORLDGEN_PROFILE_PATH = (
    ROOT
    / "profiles/packs/supersymmetry/worldgen/worldgen-iteration-profile-v2.json"
)
FIXTURE = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
    "worldgen-prototype-fixture"
)
GROOVY_PLAN = (
    FIXTURE / "examples/groovy/postInit/world_studio_mega_regions.groovy"
)
CANDIDATE_LOCK = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
    "candidate-lock-v1.json"
)
def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


CATALOG_VALIDATOR = Draft202012Validator(_json(CATALOG_SCHEMA_PATH))
PLAN_VALIDATOR = Draft202012Validator(_json(PLAN_SCHEMA_PATH))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _binding(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _ready_doctor_report(
    runtime_template: Path, server_jar: Path, built_artifact: Path
) -> dict[str, object]:
    """Return a full, semantically valid Shell-owned Doctor report fixture."""

    python = Path(sys.executable).resolve()
    server = {
        **_binding(server_jar),
        "relative_path": server_jar.relative_to(runtime_template).as_posix(),
    }
    artifact = {
        "state": "observed",
        **_binding(built_artifact),
        "reason": None,
    }
    java = {
        **_binding(python),
        "version": "25.0.4",
        "major": 25,
        "version_output": "openjdk version \"25.0.4\"",
    }
    gradle = {
        **_binding(python),
        "version": "Gradle 9.6.1",
        "version_output": "Gradle 9.6.1",
    }
    candidate = _json(CANDIDATE_LOCK)
    command = shlex.join(
        [
            "python3",
            "tools/workbench.py",
            "worldgen",
            "dev",
            "--profile",
            "supersymmetry",
            "--runtime-template",
            str(runtime_template),
            "--strata-root",
            str(ROOT),
            "--java-cmd",
            str(python),
            "--gradle-cmd",
            str(python),
        ]
    )
    report: dict[str, object] = {
        "format": "workbench-project-intelligence-workspace-doctor-report-v1",
        "schema_version": 1,
        "read_only": True,
        "capability": "worldgen-dev",
        "summary": {
            "status": "ready",
            "blockers": 0,
            "warnings": 0,
            "information": 0,
        },
        "target": {
            "workspace": {
                "state": "observed",
                "requested_path": str(ROOT),
                "root": str(FIXTURE),
                "kind": "gradle-project",
            },
            "repository": {
                "state": "observed",
                "root": str(ROOT),
                "branch": "test",
                "head": "0" * 40,
                "dirty": False,
            },
            "profile": {
                "state": "declared",
                "path": str(WORLDGEN_PROFILE_PATH),
                "sha256": _sha256(WORLDGEN_PROFILE_PATH),
                "profile_id": "workbench-pack:supersymmetry:worldgen-iteration-v2",
                "pack_profile_id": "workbench-pack:supersymmetry",
                "selection": "explicit-name",
                "world_type": "wb_proto",
                "plan": {
                    "state": "observed",
                    "path": str(GROOVY_PLAN),
                    "sha256": _sha256(GROOVY_PLAN),
                },
            },
            "platform": {
                "state": "declared",
                "kind": "cleanroom",
                "minecraft_version": "1.12.2",
                "cleanroom_version": "0.6.8-alpha",
                "forge_version": candidate["forge"]["version"],
                "mappings": candidate["mappings"],
                "maturity": candidate["maturity"],
                "profile_id": "workbench-platform:cleanroom:0.6.8-alpha",
                "candidate_lock": {
                    "path": str(CANDIDATE_LOCK),
                    "sha256": _sha256(CANDIDATE_LOCK),
                    "candidate_id": candidate["candidate_id"],
                },
                "evidence": [str(WORLDGEN_PROFILE_PATH), str(CANDIDATE_LOCK)],
            },
            "build": {
                "state": "observed",
                "provider": "gradle",
                "root": str(FIXTURE),
                "scripts": ["build.gradle"],
                "plugins": ["java"],
                "dependency_coordinates": [],
                "declared_java": {
                    "toolchain_language": "25",
                    "source_compatibility": "8",
                    "target_compatibility": "8",
                },
                "wrapper": None,
                "selected_tool": {
                    "state": "observed",
                    "selected": gradle,
                    "required_version": "9.6.1",
                },
            },
            "java_roles": [
                {
                    "role": "cleanroom-runtime",
                    "state": "observed",
                    "required": {"minimum_major": 25, "proven": "25.0.4"},
                    "selected": java,
                    "reason": None,
                }
            ],
            "surfaces": {
                "source": ["src/main/java"],
                "resources": ["src/main/resources"],
                "generated": [],
                "groovy": [str(GROOVY_PLAN)],
                "configuration": [],
                "mixin_configs": [],
                "mod_descriptors": [],
                "selected_groovy_plan": [str(GROOVY_PLAN)],
            },
            "runtime": {
                "state": "observed",
                "template": str(runtime_template),
                "safe_to_provision": True,
                "unsafe_entries": [],
                "skipped_entries": [],
                "jar_inventory": [
                    {
                        **server,
                        "mod_ids": [],
                        "archive_state": "valid",
                    }
                ],
                "duplicate_mod_ids": [],
                "server_jar_glob": "cleanroom-0.6.8-alpha.jar",
                "server_jar": server,
                "server_jar_candidates": [server],
                "problems": [],
            },
            "integrations": {
                "state": "bounded",
                "items": [
                    {"id": "current-worldgen-artifact", **artifact},
                    {
                        "id": "strata",
                        "state": "observed",
                        "root": str(ROOT),
                        "entrypoint": str(
                            ROOT / "modules/crucible/tools/run_strata_observation.py"
                        ),
                        "reason": None,
                    },
                ],
            },
        },
        "findings": [],
        "repair_plan": [],
        "next_commands": [
            {
                "id": "worldgen-dev",
                "purpose": "Run one exact disposable worldgen development iteration.",
                "command": command,
                "available": True,
                "blocked_by": [],
            }
        ],
        "limitations": [
            "This test fixture describes a bounded dedicated-server target."
        ],
    }
    validate_workspace_doctor_report(report)
    return report


def _option_value(arguments: list[str], option: str) -> str:
    index = arguments.index(option)
    return arguments[index + 1]


class ManagedRunProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        fixture_root = Path(self.temporary.name)
        runtime_template = fixture_root / "runtime-template"
        runtime_template.mkdir()
        server_jar = runtime_template / "cleanroom-0.6.8-alpha.jar"
        server_jar.write_bytes(b"managed run server fixture")
        built_artifact = fixture_root / "worldgen-prototype-0.4.0.jar"
        built_artifact.write_bytes(b"managed run mod fixture")
        self.doctor_report = _ready_doctor_report(
            runtime_template, server_jar, built_artifact
        )

    def _resolve(self, recipe_name: str, **overrides: object) -> dict[str, object]:
        return resolve_managed_run_plan(
            ROOT,
            profile_name="supersymmetry",
            recipe_name=recipe_name,
            doctor_report=self.doctor_report,
            **overrides,
        )

    def assert_plan_valid(self, plan: dict[str, object]) -> None:
        errors = sorted(
            PLAN_VALIDATOR.iter_errors(plan), key=lambda error: list(error.path)
        )
        self.assertEqual(
            [],
            [(list(error.path), error.message) for error in errors],
        )
        semantic_validator = getattr(
            profiles_module, "validate_managed_run_plan", None
        )
        if semantic_validator is not None:
            semantic_validator(plan)

    def test_checked_catalog_loads_and_both_schemas_are_valid(self) -> None:
        catalog_schema = _json(CATALOG_SCHEMA_PATH)
        plan_schema = _json(PLAN_SCHEMA_PATH)
        Draft202012Validator.check_schema(catalog_schema)
        Draft202012Validator.check_schema(plan_schema)

        raw_catalog = _json(CATALOG_PATH)
        CATALOG_VALIDATOR.validate(raw_catalog)
        loaded = load_catalog(CATALOG_PATH)
        for key, value in raw_catalog.items():
            self.assertEqual(value, loaded[key])

    def test_preview_is_deterministic_and_rendered_without_iteration_output(self) -> None:
        label = f"managed-preview-{uuid.uuid4().hex}"
        iteration = ROOT / ".workbench/iterations/worldgen" / label
        self.assertFalse(iteration.exists())
        doctor_before = deepcopy(self.doctor_report)

        first = self._resolve("fast", label=label)
        second = self._resolve("fast", label=label)

        self.assertEqual(first, second)
        self.assertEqual(doctor_before, self.doctor_report)
        self.assertFalse(iteration.exists())
        self.assert_plan_valid(first)
        rendered = render_managed_run_plan(first)
        self.assertEqual(rendered, render_managed_run_plan(second))
        self.assertIn("fast", rendered.lower())
        self.assertIn("ready", rendered.lower())
        self.assertIn("preview", rendered.lower())

    def test_available_recipes_resolve_distinct_effective_runs(self) -> None:
        plans = {
            name: self._resolve(name, label=f"managed-{name}-test")
            for name in ("fast", "debug", "worldgen", "performance")
        }
        for plan in plans.values():
            self.assert_plan_valid(plan)
            self.assertEqual("ready", plan["status"])

        self.assertEqual("fast", plans["fast"]["effective"]["mode"])
        self.assertEqual("debug", plans["debug"]["effective"]["mode"])
        self.assertEqual("debug", plans["worldgen"]["effective"]["mode"])
        self.assertEqual(
            "performance", plans["performance"]["effective"]["mode"]
        )
        self.assertEqual(16, plans["fast"]["effective"]["region"]["chunk_count"])
        for name in ("debug", "worldgen", "performance"):
            self.assertEqual(
                256, plans[name]["effective"]["region"]["chunk_count"]
            )

        for name in ("fast", "debug", "worldgen"):
            self.assertFalse(
                plans[name]["effective"]["diagnostics"]["record_jfr"]
            )
        self.assertTrue(
            plans["performance"]["effective"]["diagnostics"]["record_jfr"]
        )

        stages = {
            name: set(plan["effective"]["stages"])
            for name, plan in plans.items()
        }
        self.assertNotIn("open_viewer", stages["fast"])
        self.assertNotIn("open_viewer", stages["debug"])
        self.assertIn("open_viewer", stages["worldgen"])
        self.assertIn("performance", stages["performance"])

        for name, expected_mode in {
            "fast": "fast",
            "debug": "debug",
            "worldgen": "debug",
            "performance": "performance",
        }.items():
            arguments = plans[name]["runner"]["arguments"]
            self.assertEqual(expected_mode, _option_value(arguments, "--mode"))
        self.assertIn("--no-open", plans["fast"]["runner"]["arguments"])
        self.assertIn("--no-open", plans["debug"]["runner"]["arguments"])
        self.assertNotIn("--no-open", plans["worldgen"]["runner"]["arguments"])

    def test_proof_is_explicitly_unavailable_and_cannot_execute(self) -> None:
        plan = self._resolve("proof", label="managed-proof-test")
        self.assert_plan_valid(plan)
        self.assertEqual("blocked", plan["status"])
        self.assertEqual("unavailable", plan["availability"]["state"])
        calls: list[object] = []

        def runner(arguments: list[str], *, root: Path) -> int:
            calls.append((arguments, root))
            return 0

        with self.assertRaisesRegex(ManagedRunProfileError, "blocked|unavailable"):
            execute_managed_run_plan(plan, root=ROOT, runner=runner)
        self.assertEqual([], calls)

    def test_unsupported_sides_fail_closed_before_execution(self) -> None:
        for side in ("client", "integrated-server"):
            with self.subTest(side=side):
                with self.assertRaisesRegex(
                    ManagedRunProfileError, "side|dedicated-server"
                ):
                    self._resolve("fast", side=side)

    def test_execution_delegates_exact_worldgen_arguments_and_exit_code(self) -> None:
        plan = self._resolve(
            "debug",
            label="managed-execution-test",
            seed=-17,
            region="1,-2,3,4",
            heap="2048M",
        )
        self.assert_plan_valid(plan)
        calls: list[tuple[list[str], Path]] = []

        def runner(arguments: list[str], *, root: Path) -> int:
            calls.append((list(arguments), root))
            return 23

        code = execute_managed_run_plan(plan, root=ROOT, runner=runner)

        self.assertEqual(23, code)
        self.assertEqual(
            [(list(plan["runner"]["arguments"]), ROOT.resolve())], calls
        )
        delegated = calls[0][0]
        self.assertEqual("debug", _option_value(delegated, "--mode"))
        self.assertEqual("-17", _option_value(delegated, "--seed"))
        self.assertEqual("1,-2,3,4", _option_value(delegated, "--region"))
        self.assertEqual("2048M", _option_value(delegated, "--heap"))
        self.assertNotIn("--skip-build", delegated)
        self.assertIn("--label", plan["runner"]["command"])
        self.assertNotIn("--label", plan["runner"]["reproduction_command"])

    def test_unknown_catalog_recipe_and_symlinked_catalog_are_rejected(self) -> None:
        with self.assertRaisesRegex(ManagedRunProfileError, "catalog|unavailable"):
            resolve_managed_run_plan(
                ROOT,
                profile_name="missing_pack_profile",
                recipe_name="fast",
                doctor_report=self.doctor_report,
            )
        with self.assertRaisesRegex(ManagedRunProfileError, "unknown.*recipe"):
            self._resolve("not_a_recipe")

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            catalog_link = base / "catalog.json"
            try:
                catalog_link.symlink_to(CATALOG_PATH)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ManagedRunProfileError, "symlink"):
                load_catalog(catalog_link)

    def test_doctor_must_bind_the_exact_profile_plan(self) -> None:
        changed = deepcopy(self.doctor_report)
        other_plan = Path(self.temporary.name) / "other-plan.groovy"
        other_plan.write_text("mods.worldStudio.fake {}\n", encoding="utf-8")
        changed["target"]["profile"]["plan"] = {
            "state": "observed",
            "path": str(other_plan),
            "sha256": _sha256(other_plan),
        }
        with self.assertRaisesRegex(ManagedRunProfileError, "exact.*Groovy plan"):
            resolve_managed_run_plan(
                ROOT,
                profile_name="supersymmetry",
                recipe_name="fast",
                doctor_report=changed,
            )

    def test_semantic_validator_rejects_plan_identity_tampering_when_exported(self) -> None:
        semantic_validator = getattr(
            profiles_module, "validate_managed_run_plan", None
        )
        if semantic_validator is None:
            self.skipTest("managed run semantic validator is not exported")
        plan = self._resolve("fast", label="managed-semantic-test")
        semantic_validator(plan)
        tampered = deepcopy(plan)
        tampered["effective"]["seed"] += 1
        with self.assertRaises(ManagedRunProfileError):
            semantic_validator(tampered)

        argument_tampered = deepcopy(plan)
        mode_index = argument_tampered["runner"]["arguments"].index("--mode") + 1
        argument_tampered["runner"]["arguments"][mode_index] = "performance"
        argument_tampered["runner"]["command"] = shlex.join(
            [
                "python3",
                "tools/workbench.py",
                "worldgen",
                "dev",
                *argument_tampered["runner"]["arguments"],
            ]
        )
        reproduction_arguments = list(argument_tampered["runner"]["arguments"])
        label_index = reproduction_arguments.index("--label")
        del reproduction_arguments[label_index : label_index + 2]
        argument_tampered["runner"]["reproduction_command"] = shlex.join(
            ["python3", "tools/workbench.py", "worldgen", "dev", *reproduction_arguments]
        )
        argument_tampered["plan_id"] = profiles_module.PLAN_ID_PREFIX + profiles_module._canonical_sha256(
            {key: value for key, value in argument_tampered.items() if key != "plan_id"}
        )
        with self.assertRaisesRegex(ManagedRunProfileError, "disagree"):
            semantic_validator(argument_tampered)

        unresolved = deepcopy(plan)
        unresolved["target"]["runtime_template"] = {
            "state": "unresolved",
            "path": None,
            "jar_inventory_sha256": None,
            "server_jar": None,
        }
        unresolved["target"]["candidate_lock"] = None
        unresolved["target"]["platform_profile_canonical_sha256"] = None
        unresolved["target"]["java"] = None
        unresolved["target"]["gradle"] = None
        unresolved["target"]["strata_root"] = None
        unresolved["target"]["target_id"] = profiles_module.TARGET_ID_PREFIX + profiles_module._canonical_sha256(
            {key: value for key, value in unresolved["target"].items() if key != "target_id"}
        )
        unresolved["plan_id"] = profiles_module.PLAN_ID_PREFIX + profiles_module._canonical_sha256(
            {key: value for key, value in unresolved.items() if key != "plan_id"}
        )
        with self.assertRaisesRegex(ManagedRunProfileError, "unresolved target"):
            semantic_validator(unresolved)

    def test_execution_rejects_bound_file_drift(self) -> None:
        plan = self._resolve("fast", label="managed-freshness-test")
        server = Path(plan["target"]["runtime_template"]["server_jar"]["path"])
        server.write_bytes(b"managed run server fixture changed after preview")
        calls: list[object] = []

        def runner(arguments: list[str], *, root: Path) -> int:
            calls.append((arguments, root))
            return 0

        with self.assertRaisesRegex(ManagedRunProfileError, "changed after"):
            execute_managed_run_plan(plan, root=ROOT, runner=runner)
        self.assertEqual([], calls)

    def test_shell_show_and_json_are_read_only(self) -> None:
        label = f"managed-shell-preview-{uuid.uuid4().hex}"
        iteration = ROOT / ".workbench/iterations/worldgen" / label
        self.assertFalse(iteration.exists())
        for flag in ("--show", "--json"):
            output = io.StringIO()
            errors = io.StringIO()
            with patch(
                "workbench_core.dispatch_setup._activate_user_setup",
                return_value=True,
            ), patch.object(
                workbench_shell,
                "_worldgen_doctor_report",
                return_value=self.doctor_report,
            ) as doctor, redirect_stdout(output), redirect_stderr(errors):
                code = core_main(
                    [
                        "run",
                        "fast",
                        "--profile",
                        "supersymmetry",
                        "--label",
                        label,
                        flag,
                    ]
                )
            self.assertEqual(0, code)
            self.assertEqual("", errors.getvalue())
            self.assertEqual(1, doctor.call_count)
            if flag == "--json":
                self.assert_plan_valid(json.loads(output.getvalue()))
            else:
                self.assertIn("Preview resolution was read-only", output.getvalue())
            self.assertFalse(iteration.exists())

    def test_shell_replans_before_delegating_execution(self) -> None:
        label = f"managed-shell-execute-{uuid.uuid4().hex}"
        output = io.StringIO()
        errors = io.StringIO()
        with patch(
            "workbench_core.dispatch_setup._activate_user_setup",
            return_value=True,
        ), patch.object(
            workbench_shell,
            "_worldgen_doctor_report",
            return_value=self.doctor_report,
        ) as doctor, patch.object(
            run_profiles_package,
            "execute_managed_run_plan",
            return_value=19,
        ) as execute, redirect_stdout(output), redirect_stderr(errors):
            code = core_main(
                [
                    "run",
                    "fast",
                    "--profile",
                    "supersymmetry",
                    "--label",
                    label,
                ]
            )
        self.assertEqual(19, code)
        self.assertEqual("", errors.getvalue())
        self.assertEqual(2, doctor.call_count)
        self.assertEqual(1, execute.call_count)
        delegated_plan = execute.call_args.args[0]
        self.assertEqual(label, _option_value(delegated_plan["runner"]["arguments"], "--label"))
        self.assertIn("Executing the resolved plan", output.getvalue())


if __name__ == "__main__":
    unittest.main()
