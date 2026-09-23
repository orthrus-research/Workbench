from __future__ import annotations

import json
import argparse
from dataclasses import replace
import importlib
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from workbench_shell.catalog import (
    Catalog,
    CatalogError,
    SuiteSpec,
    _manual_commands,
    build_catalog,
    parse_assignments,
    redact_argv,
)


ROOT = Path(__file__).resolve().parents[3]


class CatalogTests(unittest.TestCase):
    def test_disabled_profiles_remove_choices_without_disabling_generic_commands(self) -> None:
        from workbench_api.profiles import profile_scope

        with profile_scope(disabled=("supersymmetry", "cleanroom")):
            catalog = build_catalog(ROOT)
        self.assertEqual(catalog.command("project.qualify").availability, "unavailable")
        self.assertEqual(catalog.command("dev.run").availability, "unavailable")
        self.assertEqual(catalog.command("dev.fixture-run").availability, "unavailable")
        self.assertEqual(catalog.command("developer-features.plan-example").availability, "unavailable")
        self.assertEqual(catalog.command("atlas.recipes-assess-plan").availability, "unavailable")
        self.assertNotEqual(catalog.command("atlas.recipes-search").availability, "unavailable")
        self.assertNotEqual(catalog.command("workspace.open").availability, "unavailable")
        self.assertEqual(catalog.command("project.qualify").field("profile").choices, ())

    def test_pack_disable_keeps_platform_fixture_capability_available(self) -> None:
        from workbench_api.profiles import profile_scope

        with profile_scope(disabled=("supersymmetry",)):
            catalog = build_catalog(ROOT)
        self.assertEqual(catalog.command("dev.run").availability, "unavailable")
        self.assertNotEqual(catalog.command("dev.fixture-run").availability, "unavailable")

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = build_catalog(ROOT)

    def test_catalog_covers_every_public_workbench_leaf(self) -> None:
        expected = {
            "workspace.open",
            "diagnose.latest",
            "diagnose.capsule-create",
            "diagnose.capsule-inspect",
            "diagnose.capsule-verify",
            "diagnose.capsule-run",
            "sentinel.mixin-diagnose",
            "relay.locate",
            "dev.run",
            "dev.show",
            "dev.build",
            "dev.stage",
            "dev.launch",
            "dev.launch-server",
            "dev.check",
            "dev.fixture-plan",
            "dev.fixture-run",
            "dev.fixture-recover",
            "change.material-fluid-start",
            "change.material-fluid-open",
            "change.material-fluid-test",
            "change.material-fluid-apply",
            "change.material-fluid-verify",
            "change.material-fluid-rollback",
            "change.material-fluid-recover",
            "explorer.search",
            "pack-program.groovy-dev",
            "pack-program.groovy-check",
            "pack-program.groovy-session",
            "pack-program.recipe-invalidations",
            "doctor.inspect",
            "cleanroom.fixture-build",
            "runs.managed",
            "runtime.create",
            "storage.list",
            "storage.inspect",
            "storage.cleanup",
            "storage.restore",
            "storage.purge",
            "world.snapshot",
            "world.restore",
            "world-studio.iterate",
            "world-studio.cockpit-run",
            "world-studio.cockpit-compare",
            "world-studio.cockpit-show",
            "world-studio.subsurface-summary",
            "world-studio.subsurface-explain",
            "world-studio.subsurface-section",
            "world-studio.subsurface-compare",
            "process-studio.effects-compare",
            "shell.recipe-capture-plan",
            "shell.recipe-capture-prepare",
            "shell.recipe-capture-run",
            "shell.recipe-capture-show",
            "shell.recipe-capture-cancel",
            "shell.recipe-capture-export",
        }
        self.assertTrue(expected <= {item.command_id for item in self.catalog.commands})

    def test_workspace_open_catalog_has_no_legacy_format_selector(self) -> None:
        command = self.catalog.command("workspace.open")
        self.assertEqual(
            ["workspace", "json", "session", "state_root"],
            [field.key for field in command.fields],
        )

    def test_catalog_exposes_authority_suites_and_truthful_horizons(self) -> None:
        suites = {item.suite_id: item for item in self.catalog.suites}
        for suite in (
            "dev",
            "explorer",
            "pack-program",
            "process-studio",
            "atlas",
            "blueprints",
            "developer-features",
            "manuals",
            "mixin",
            "cleanroom",
            "runs",
        ):
            self.assertGreater(len(self.catalog.for_suite(suite)), 0)
        self.assertEqual(suites["sentinel"].availability, "experimental")
        self.assertEqual(suites["relay"].availability, "experimental")
        self.assertEqual(
            ["sentinel.mixin-diagnose"],
            [item.command_id for item in self.catalog.for_suite("sentinel")],
        )
        self.assertEqual(
            ["relay.locate"],
            [item.command_id for item in self.catalog.for_suite("relay")],
        )
        self.assertGreater(len(self.catalog.for_suite("crucible")), 0)

    def test_atlas_catalog_exposes_only_the_current_command_set(self) -> None:
        self.assertEqual(
            {
                "atlas.answer",
                "atlas.continuation-compose",
                "atlas.continuation-invalidate",
                "atlas.continuation-resume",
                "atlas.continuation-start",
                "atlas.query",
                "atlas.scans-import",
                "atlas.scans-show",
                "atlas.scans-audit",
                "atlas.scans-export",
                "atlas.observations-context",
                "atlas.observations-session",
                "atlas.observations-crafting-exposure",
                "atlas.observations-search",
                "atlas.observations-inspect",
                "atlas.observations-relationships",
                "atlas.observations-evidence",
                "atlas.observations-index",
                "atlas.observations-import-snapshot",
                "atlas.recipes-assess-plan",
                "atlas.recipes-compare-runtime",
                "atlas.recipes-context",
                "atlas.recipes-import-capture",
                "atlas.recipes-impact",
                "atlas.recipes-index",
                "atlas.recipes-inspect",
                "atlas.recipes-routes",
                "atlas.recipes-audit-dead-ends",
                "atlas.recipes-search",
                "atlas.runtime-consumers",
                "atlas.runtime-machine-recipes",
                "atlas.runtime-process-chain",
                "atlas.runtime-producers",
                "atlas.semantic-check",
                "atlas.semantic-impact",
                "atlas.semantic-why",
                "atlas.which-handler-changed-event",
                "atlas.who-wrote-block",
                "atlas.worldgen-exact-suite",
            },
            {item.command_id for item in self.catalog.for_suite("atlas")},
        )

    def test_sentinel_and_relay_catalog_routes_preserve_owner_boundaries(self) -> None:
        sentinel = self.catalog.command("sentinel.mixin-diagnose")
        sentinel_argv, _ = sentinel.build_argv(
            {"artifacts": ["a.jar", "b.jar"], "json": True},
            root=ROOT,
            execute=True,
        )
        self.assertEqual(
            ["diagnose", "mixins", "a.jar", "b.jar", "--json"],
            sentinel_argv[2:],
        )
        self.assertEqual("read-only", sentinel.risk)

        relay = self.catalog.command("relay.locate")
        with self.assertRaisesRegex(CatalogError, "requires exactly one"):
            relay.build_argv(
                {"identity": "machine:example:press"},
                root=ROOT,
                execute=True,
            )
        relay_argv, _ = relay.build_argv(
            {
                "identity": "machine:example:press",
                "explorer_result": "retained.json",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(
            [
                "relay",
                "locate",
                "machine:example:press",
                "--explorer-result",
                "retained.json",
                "--json",
            ],
            relay_argv[2:],
        )
        self.assertEqual("read-only", relay.risk)

    def test_worldgen_compatibility_surfaces_remain_explicitly_experimental(self) -> None:
        expected_routes = {
            "world-studio.cockpit-run": ("cockpit", "run"),
            "world-studio.cockpit-compare": ("cockpit", "compare"),
            "world-studio.cockpit-show": ("cockpit", "show"),
            "world-studio.cockpit-open": ("cockpit", "open"),
            "world-studio.qualifier-run": ("qualify", "run"),
            "world-studio.qualifier-assess": ("qualify", "assess"),
            "world-studio.qualifier-scan": ("qualify", "scan"),
            "world-studio.qualifier-show": ("qualify", "show"),
            "world-studio.qualifier-open": ("qualify", "open"),
        }
        for command_id, route in expected_routes.items():
            with self.subTest(command_id=command_id):
                command = self.catalog.command(command_id)
                self.assertEqual("experimental", command.availability)
                self.assertEqual("world-studio", command.suite_id)
                self.assertEqual(
                    route,
                    command.argv_template[2:4],
                )

    def test_material_fluid_runtime_commands_require_exact_profile_config(self) -> None:
        change_id = "workbench-feature-change:sha256:" + "1" * 64
        for command_id in (
            "change.material-fluid-test",
            "change.material-fluid-verify",
        ):
            with self.subTest(command_id=command_id):
                command = self.catalog.command(command_id)
                self.assertEqual("experimental", command.availability)
                self.assertTrue(command.field("runtime_config").required)
                with self.assertRaisesRegex(CatalogError, "runtime-config"):
                    command.build_argv(
                        {"change_id": change_id}, root=ROOT, execute=True
                    )
                argv, intent = command.build_argv(
                    {
                        "change_id": change_id,
                        "runtime_config": "/private/runtime-v1.json",
                        "json": True,
                    },
                    root=ROOT,
                    execute=True,
                )
                self.assertEqual("execute", intent)
                self.assertEqual(
                    [
                        "change",
                        command_id.rsplit("-", 1)[1],
                        "material-fluid-recipe",
                        change_id,
                        "--runtime-config",
                        "/private/runtime-v1.json",
                        "--json",
                    ],
                    argv[2:],
                )

        rollback = self.catalog.command("change.material-fluid-rollback")
        self.assertFalse(rollback.field("runtime_config").required)
        self.assertTrue(any("runtime-unverified" in row for row in rollback.limitations))

    def test_susy_mod_dev_preview_is_read_only_and_uses_exact_public_route(self) -> None:
        primary = self.catalog.command("dev.run")
        self.assertEqual(primary.suite_id, "dev")
        self.assertEqual(primary.availability, "experimental")
        self.assertEqual(primary.risk, "mutating")
        self.assertEqual(primary.preview, "inert-only")
        self.assertEqual(
            primary.field("side").choices,
            ("auto", "client", "server", "both"),
        )
        self.assertEqual(primary.field("side").default, "auto")
        for conditionally_scoped in (
            "project",
            "timeout",
            "launcher",
            "offline_name",
            "memory",
            "launch_timeout",
            "shutdown_timeout",
        ):
            self.assertIsNone(primary.field(conditionally_scoped).default)
        self.assertTrue(primary.field("run").required_group)
        self.assertTrue(primary.field("pack").required_group)
        self.assertEqual(primary.field("run").mutex_group, "developer-input")
        self.assertEqual(primary.field("pack").mutex_group, "developer-input")
        self.assertFalse(primary.field("server_template").required_group)
        self.assertTrue(primary.field("launcher_profile").sensitive)
        self.assertTrue(primary.field("runtime_experiment").repeat)
        resumed, resumed_intent = primary.build_argv(
            {
                "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                "side": "both",
                "launcher": "prism",
                "launcher_root": "/tmp/prism-root",
                "server_template": "/tmp/susy-server",
                "memory": 6144,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(resumed_intent, "execute")
        self.assertEqual(
            resumed[2:],
            [
                "dev",
                "run",
                "--run",
                "susy-mod-20260820T120000000000Z-abcdef123456",
                "--side",
                "both",
                "--launcher",
                "prism",
                "--launcher-root",
                "/tmp/prism-root",
                "--server-template",
                "/tmp/susy-server",
                "--memory",
                "6144",
                "--json",
            ],
        )
        fresh, fresh_intent = primary.build_argv(
            {
                "project": "/tmp/source-mod",
                "pack": "/tmp/supersymmetry",
                "side": "client",
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(fresh_intent, "execute")
        self.assertEqual(
            fresh[2:],
            [
                "dev",
                "run",
                "--project",
                "/tmp/source-mod",
                "--pack",
                "/tmp/supersymmetry",
                "--side",
                "client",
            ],
        )
        with self.assertRaisesRegex(CatalogError, "requires exactly one of"):
            primary.build_argv({}, root=ROOT, execute=True)
        with self.assertRaisesRegex(CatalogError, "mutually exclusive"):
            primary.build_argv(
                {
                    "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                    "pack": "/tmp/supersymmetry",
                },
                root=ROOT,
                execute=True,
            )

        command = self.catalog.command("dev.show")
        self.assertEqual(command.suite_id, "dev")
        self.assertEqual(command.availability, "experimental")
        self.assertEqual(command.risk, "read-only")
        self.assertEqual(command.preview, "none")
        self.assertEqual(
            {field.key for field in command.fields},
            {"project", "pack", "pack_mod", "java_home", "json"},
        )
        self.assertTrue(command.field("pack").required)

        argv, intent = command.build_argv(
            {
                "project": "/tmp/source-mod",
                "pack": "/tmp/supersymmetry",
                "pack_mod": "sample.pw.toml",
                "java_home": "/tmp/jdk17",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            argv[2:],
            [
                "dev",
                "show",
                "--project",
                "/tmp/source-mod",
                "--pack",
                "/tmp/supersymmetry",
                "--pack-mod",
                "sample.pw.toml",
                "--java-home",
                "/tmp/jdk17",
                "--json",
            ],
        )

        for command_id, action in (("dev.build", "build"), ("dev.stage", "stage")):
            with self.subTest(command_id=command_id):
                executable = self.catalog.command(command_id)
                self.assertEqual(executable.risk, "mutating")
                self.assertEqual(executable.preview, "inert-only")
                built, built_intent = executable.build_argv(
                    {
                        "project": "/tmp/source-mod",
                        "pack": "/tmp/supersymmetry",
                        "timeout": 90,
                        "json": True,
                    },
                    root=ROOT,
                    execute=True,
                )
                self.assertEqual(built_intent, "execute")
                self.assertEqual(built[2:4], ["dev", action])
                self.assertIn("--timeout", built)

        launch = self.catalog.command("dev.launch")
        self.assertEqual(launch.risk, "mutating")
        self.assertEqual(launch.preview, "inert-only")
        self.assertTrue(launch.field("run").required)
        self.assertTrue(launch.field("launcher_profile").sensitive)
        experiment = launch.field("runtime_experiment")
        self.assertTrue(experiment.repeat)
        self.assertEqual(
            experiment.choices,
            (
                "susy-reccomplex-arg3",
                "susy-reccomplex-susycore-0112-flag",
            ),
        )
        self.assertNotIn("pack", {field.key for field in launch.fields})
        launched, launch_intent = launch.build_argv(
            {
                "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                "launcher": "prism",
                "launcher_root": "/tmp/prism-root",
                "runtime_experiment": ["susy-reccomplex-arg3"],
                "memory": 6144,
                "launch_timeout": 90.5,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(launch_intent, "execute")
        self.assertEqual(launched[2:4], ["dev", "launch"])
        self.assertEqual(
            launched[4:],
            [
                "--run",
                "susy-mod-20260820T120000000000Z-abcdef123456",
                "--launcher",
                "prism",
                "--launcher-root",
                "/tmp/prism-root",
                "--runtime-experiment",
                "susy-reccomplex-arg3",
                "--memory",
                "6144",
                "--launch-timeout",
                "90.5",
                "--json",
            ],
        )

        server = self.catalog.command("dev.launch-server")
        self.assertEqual(server.suite_id, "dev")
        self.assertEqual(server.availability, "experimental")
        self.assertEqual(server.risk, "mutating")
        self.assertEqual(server.preview, "inert-only")
        self.assertEqual(
            {field.key for field in server.fields},
            {
                "run",
                "server_template",
                "accept_minecraft_eula",
                "server_java",
                "runtime_experiment",
                "memory",
                "launch_timeout",
                "shutdown_timeout",
                "json",
            },
        )
        self.assertTrue(server.field("run").required)
        self.assertFalse(server.field("server_template").required)
        self.assertTrue(server.field("server_template").required_group)
        self.assertEqual(
            server.field("server_template").mutex_group,
            "server-template-source",
        )
        self.assertEqual(server.field("server_template").kind, "path")
        self.assertEqual(server.field("accept_minecraft_eula").kind, "boolean")
        self.assertTrue(server.field("accept_minecraft_eula").required_group)
        self.assertEqual(server.field("server_java").kind, "path")
        server_experiment = server.field("runtime_experiment")
        self.assertTrue(server_experiment.repeat)
        self.assertEqual(
            server_experiment.choices,
            (
                "susy-reccomplex-arg3",
                "susy-reccomplex-susycore-0112-flag",
                "susy-server-shutdown-bridge",
            ),
        )
        self.assertEqual(server.field("memory").default, 8192)
        self.assertEqual(server.field("launch_timeout").default, 600.0)
        self.assertEqual(server.field("shutdown_timeout").default, 180.0)
        server_argv, server_intent = server.build_argv(
            {
                "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                "server_template": "/tmp/susy-server",
                "server_java": "/tmp/jdk/bin/java",
                "runtime_experiment": [
                    "susy-reccomplex-arg3",
                    "susy-server-shutdown-bridge",
                ],
                "memory": 6144,
                "launch_timeout": 90.5,
                "shutdown_timeout": 45.25,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(server_intent, "execute")
        self.assertEqual(server_argv[2:4], ["dev", "launch-server"])
        self.assertEqual(
            server_argv[4:],
            [
                "--run",
                "susy-mod-20260820T120000000000Z-abcdef123456",
                "--server-template",
                "/tmp/susy-server",
                "--server-java",
                "/tmp/jdk/bin/java",
                "--runtime-experiment",
                "susy-reccomplex-arg3",
                "--runtime-experiment",
                "susy-server-shutdown-bridge",
                "--memory",
                "6144",
                "--launch-timeout",
                "90.5",
                "--shutdown-timeout",
                "45.25",
                "--json",
            ],
        )
        server_auto_argv, server_auto_intent = server.build_argv(
            {
                "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                "accept_minecraft_eula": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(server_auto_intent, "execute")
        self.assertEqual(
            server_auto_argv[2:],
            [
                "dev",
                "launch-server",
                "--run",
                "susy-mod-20260820T120000000000Z-abcdef123456",
                "--accept-minecraft-eula",
            ],
        )
        with self.assertRaisesRegex(CatalogError, "requires exactly one of"):
            server.build_argv(
                {"run": "susy-mod-20260820T120000000000Z-abcdef123456"},
                root=ROOT,
                execute=True,
            )
        with self.assertRaisesRegex(CatalogError, "mutually exclusive"):
            server.build_argv(
                {
                    "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                    "server_template": "/tmp/susy-server",
                    "accept_minecraft_eula": True,
                },
                root=ROOT,
                execute=True,
            )

        check = self.catalog.command("dev.check")
        self.assertEqual(check.suite_id, "dev")
        self.assertEqual(check.availability, "experimental")
        self.assertEqual(check.risk, "mutating")
        self.assertEqual(check.preview, "inert-only")
        self.assertEqual(
            {field.key for field in check.fields},
            {
                "run",
                "side",
                "server_template",
                "accept_minecraft_eula",
                "server_java",
                "runtime_experiment",
                "memory",
                "launch_timeout",
                "shutdown_timeout",
                "json",
            },
        )
        self.assertTrue(check.field("run").required)
        self.assertTrue(check.field("side").required)
        self.assertEqual(check.field("side").kind, "choice")
        self.assertEqual(check.field("side").choices, ("server",))
        self.assertFalse(check.field("server_template").required)
        self.assertTrue(check.field("server_template").required_group)
        self.assertEqual(
            check.field("server_template").mutex_group,
            "server-template-source",
        )
        self.assertEqual(check.field("server_template").kind, "path")
        self.assertEqual(check.field("accept_minecraft_eula").kind, "boolean")
        self.assertTrue(check.field("accept_minecraft_eula").required_group)
        self.assertEqual(check.field("server_java").kind, "path")
        check_experiment = check.field("runtime_experiment")
        self.assertTrue(check_experiment.repeat)
        self.assertEqual(
            check_experiment.choices,
            (
                "susy-reccomplex-arg3",
                "susy-reccomplex-susycore-0112-flag",
                "susy-server-shutdown-bridge",
            ),
        )
        self.assertEqual(check.field("memory").default, 8192)
        self.assertEqual(check.field("launch_timeout").default, 600.0)
        self.assertEqual(check.field("shutdown_timeout").default, 180.0)
        check_argv, check_intent = check.build_argv(
            {
                "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                "side": "server",
                "server_template": "/tmp/susy-server",
                "server_java": "/tmp/jdk/bin/java",
                "runtime_experiment": [
                    "susy-reccomplex-arg3",
                    "susy-server-shutdown-bridge",
                ],
                "memory": 6144,
                "launch_timeout": 90.5,
                "shutdown_timeout": 45.25,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(check_intent, "execute")
        self.assertEqual(check_argv[2:4], ["dev", "check"])
        self.assertEqual(
            check_argv[4:],
            [
                "--run",
                "susy-mod-20260820T120000000000Z-abcdef123456",
                "--side",
                "server",
                "--server-template",
                "/tmp/susy-server",
                "--server-java",
                "/tmp/jdk/bin/java",
                "--runtime-experiment",
                "susy-reccomplex-arg3",
                "--runtime-experiment",
                "susy-server-shutdown-bridge",
                "--memory",
                "6144",
                "--launch-timeout",
                "90.5",
                "--shutdown-timeout",
                "45.25",
                "--json",
            ],
        )
        check_auto_argv, check_auto_intent = check.build_argv(
            {
                "run": "susy-mod-20260820T120000000000Z-abcdef123456",
                "side": "server",
                "accept_minecraft_eula": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(check_auto_intent, "execute")
        self.assertEqual(
            check_auto_argv[2:],
            [
                "dev",
                "check",
                "--run",
                "susy-mod-20260820T120000000000Z-abcdef123456",
                "--side",
                "server",
                "--accept-minecraft-eula",
            ],
        )

    def test_recipe_invalidation_preview_is_read_only_and_explicit(self) -> None:
        command = self.catalog.command("pack-program.recipe-invalidations")
        self.assertEqual(command.availability, "available")
        self.assertEqual(command.risk, "read-only")
        self.assertEqual(command.preview, "none")

        argv, intent = command.build_argv(
            {
                "workspace": "/tmp/supersymmetry",
                "receipt": "/tmp/candidate-v3.json",
                "baseline_receipt": "/tmp/baseline-v3.json",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            argv[2:],
            [
                "runtime-diagnose",
                "/tmp/supersymmetry",
                "--recipe-invalidations",
                "--receipt",
                "/tmp/candidate-v3.json",
                "--baseline-receipt",
                "/tmp/baseline-v3.json",
                "--json",
            ],
        )
        limitations = " ".join(command.limitations)
        self.assertIn("preview feature", limitations)
        self.assertIn("explicit Supersymmetry profile", limitations)
        self.assertIn("cross-channel deduplication", limitations)

        single_argv, single_intent = command.build_argv(
            {
                "workspace": "/tmp/supersymmetry",
                "receipt": "/tmp/candidate-v3.json",
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual(single_intent, "execute")
        self.assertNotIn("--baseline-receipt", single_argv)

    def test_managed_run_is_preview_first_and_exact(self) -> None:
        command = self.catalog.command("runs.managed")
        argv, intent = command.build_argv(
            {
                "recipe": "debug",
                "profile": "supersymmetry",
                "region": "-2,-2,4,4",
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual(intent, "preview")
        self.assertEqual(argv[-1], "--show")
        self.assertIn("tools/workbench.py", argv[1])
        self.assertEqual(argv[2:5], ["run", "debug", "--profile"])
        execute_argv, execute_intent = command.build_argv(
            {"recipe": "debug", "profile": "supersymmetry", "show": True},
            root=ROOT,
            execute=True,
        )
        self.assertEqual(execute_intent, "execute")
        self.assertNotIn("--show", execute_argv)

    def test_material_fluid_catalog_preserves_reviewed_plan_consent(self) -> None:
        plan = self.catalog.command("shell.material-fluid-plan")
        plan_argv, plan_intent = plan.build_argv(
            {
                "workspace": "/tmp/supersymmetry",
                "name": "Pilot Coolant",
                "color": "0x425d73",
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual(plan_intent, "execute")
        self.assertEqual(plan_argv[2:4], ["material-fluid", "plan"])
        self.assertNotIn("--show", plan_argv)
        self.assertNotIn("--apply", plan_argv)

        reviewed_id = "sha256:" + ("4" * 64)
        values = {
            "workspace": "/tmp/supersymmetry",
            "name": "Pilot Coolant",
            "color": "0x425d73",
            "plan_id": reviewed_id,
            "launcher_executable": "/tmp/prismlauncher",
            "launcher_root": "/tmp/prism-root",
        }
        run = self.catalog.command("shell.material-fluid-run")
        preview, preview_intent = run.build_argv(
            values,
            root=ROOT,
            execute=False,
        )
        self.assertEqual(preview_intent, "preview")
        self.assertEqual(preview[2:4], ["material-fluid", "run"])
        self.assertEqual(preview[-1], "--show")
        self.assertEqual(preview[preview.index("--plan-id") + 1], reviewed_id)
        self.assertNotIn("--apply", preview)

        execute, execute_intent = run.build_argv(
            values,
            root=ROOT,
            execute=True,
        )
        self.assertEqual(execute_intent, "execute")
        self.assertNotIn("--show", execute)
        self.assertNotIn("--apply", execute)
        self.assertEqual(execute[execute.index("--plan-id") + 1], reviewed_id)

    def test_feature_studio_catalog_is_complete_and_preview_bound(self) -> None:
        actions = self.catalog.for_suite("feature-studio")
        self.assertEqual(
            [item.command_id for item in actions],
            [
                "feature-studio.inspect",
                "feature-studio.plan",
                "feature-studio.verify",
                "feature-studio.explain",
                "feature-studio.export",
            ],
        )
        self.assertEqual(
            {item.command_id: item.risk for item in actions},
            {
                "feature-studio.inspect": "read-only",
                "feature-studio.plan": "read-only",
                "feature-studio.verify": "mutating",
                "feature-studio.explain": "read-only",
                "feature-studio.export": "writes-output",
            },
        )
        reviewed_id = "sha256:" + "a" * 64
        verify = self.catalog.command("feature-studio.verify")
        values = {
            "workspace": "/tmp/source",
            "name": "Pilot Coolant",
            "color": "0x425d73",
            "plan_id": reviewed_id,
            "launcher_executable": "/tmp/launcher",
            "launcher_root": "/tmp/launcher-root",
        }
        preview, preview_intent = verify.build_argv(values, root=ROOT, execute=False)
        json_preview, json_preview_intent = verify.build_argv(
            {**values, "json": True}, root=ROOT, execute=False
        )
        execute, execute_intent = verify.build_argv(
            {**values, "json": True}, root=ROOT, execute=True
        )
        self.assertEqual(preview_intent, "preview")
        self.assertEqual(json_preview_intent, "preview")
        self.assertEqual(execute_intent, "execute")
        self.assertEqual(preview[2:4], ["studio", "verify"])
        self.assertEqual(preview[-1], "--show")
        self.assertIn("--show", json_preview)
        self.assertIn("--json", json_preview)
        self.assertNotIn("--show", execute)
        self.assertIn("--json", execute)
        export = self.catalog.command("feature-studio.export")
        exported, export_intent = export.build_argv(
            {
                "workspace": "/tmp/source",
                "name": "Pilot Coolant",
                "color": "0x425d73",
                "plan_id": reviewed_id,
                "output": "/tmp/export",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(export_intent, "execute")
        self.assertEqual(exported[2:4], ["studio", "export"])
        self.assertNotIn("--apply", exported)
        self.assertIn("--json", exported)
        export_preview, export_preview_intent = export.build_argv(
            {
                "workspace": "/tmp/source",
                "name": "Pilot Coolant",
                "color": "0x425d73",
                "plan_id": reviewed_id,
                "output": "/tmp/export",
                "json": True,
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual(export_preview_intent, "preview")
        self.assertIn("--show", export_preview)
        self.assertIn("--json", export_preview)
        inspect_receipt, inspect_intent = self.catalog.command(
            "feature-studio.inspect"
        ).build_argv(
            {"receipt": "/tmp/receipt.json"},
            root=ROOT,
            execute=True,
        )
        self.assertEqual(inspect_intent, "execute")
        self.assertEqual(inspect_receipt[2:4], ["studio", "inspect"])
        self.assertNotIn("--name", inspect_receipt)
        self.assertEqual(
            inspect_receipt[inspect_receipt.index("--receipt") + 1],
            "/tmp/receipt.json",
        )

    def test_process_studio_effect_comparison_is_exact_and_owner_delegated(self) -> None:
        actions = self.catalog.for_suite("process-studio")
        self.assertEqual(
            [item.command_id for item in actions],
            ["process-studio.recipes-compare", "process-studio.effects-compare"],
        )
        recipe_command = actions[0]
        self.assertEqual("read-only", recipe_command.risk)
        self.assertIn("Atlas runtime recipe authority", recipe_command.authority)
        self.assertIn(
            "does not prove causality",
            " ".join(recipe_command.limitations),
        )
        recipe_argv, recipe_intent = recipe_command.build_argv(
            {
                "baseline": "/tmp/before",
                "candidate": "/tmp/after",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual("execute", recipe_intent)
        self.assertEqual(
            ["process", "recipes", "compare", "/tmp/before", "/tmp/after"],
            recipe_argv[2:7],
        )
        self.assertIn("--json", recipe_argv)

        command = actions[1]
        self.assertEqual(command.risk, "writes-output")
        self.assertEqual(command.preview, "none")
        wording = " ".join(
            (
                command.title,
                command.summary,
                *(field.help for field in command.fields),
                *command.limitations,
            )
        ).casefold()
        self.assertIsNone(
            re.search(
                r"\b(?:verifier|verdict|verify|verification|pass|passed|safe)\b",
                wording,
            )
        )
        self.assertIn("bounded observed-effect comparison", wording)
        self.assertIn(
            "does not establish causality, playability, construction approval",
            wording,
        )
        self.assertIn("without --envelope, the output is comparison-only", wording)
        self.assertIn("live atlas/profile adapter integration is unavailable", wording)
        self.assertIn("no runtime authority is claimed", wording)
        self.assertIn("does not prove prior authorship or trusted custody", wording)
        self.assertNotIn("atlas-owned", command.authority.casefold())
        argv, intent = command.build_argv(
            {
                "baseline": "/tmp/baseline.json",
                "candidate": "/tmp/candidate.json",
                "envelope": "/tmp/envelope.json",
                "fixture_adapters": True,
                "output": "/tmp/comparison.json",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            argv[2:],
            [
                "process",
                "effects",
                "compare",
                "--baseline",
                "/tmp/baseline.json",
                "--candidate",
                "/tmp/candidate.json",
                "--envelope",
                "/tmp/envelope.json",
                "--fixture-adapters",
                "--output",
                "/tmp/comparison.json",
                "--json",
            ],
        )
        for missing, message in (
            (
                {
                    "candidate": "/tmp/candidate.json",
                    "fixture_adapters": True,
                },
                "--baseline",
            ),
            (
                {
                    "baseline": "/tmp/baseline.json",
                    "fixture_adapters": True,
                },
                "--candidate",
            ),
            (
                {
                    "baseline": "/tmp/baseline.json",
                    "candidate": "/tmp/candidate.json",
                },
                "--fixture-adapters",
            ),
        ):
            with self.subTest(missing=message), self.assertRaisesRegex(
                CatalogError, message
            ):
                command.build_argv(missing, root=ROOT, execute=True)

    def test_developer_feature_catalog_is_typed_and_lifecycle_bound(self) -> None:
        actions = self.catalog.for_suite("developer-features")
        self.assertEqual(
            [item.command_id for item in actions],
            [
                "developer-features.examples",
                "developer-features.records",
                "developer-features.options",
                "developer-features.plan-example",
                "developer-features.plan-material-fluid-recipe",
                "developer-features.plan-recipe-change",
                "developer-features.plan-quest-for-process",
                "developer-features.check",
                "developer-features.compare-recipe-runtime",
                "developer-features.apply",
                "developer-features.rollback",
                "developer-features.recover",
                "developer-features.present",
                "developer-features.transaction",
            ],
        )
        self.assertEqual(
            {item.command_id: item.risk for item in actions},
            {
                "developer-features.examples": "read-only",
                "developer-features.records": "read-only",
                "developer-features.options": "read-only",
                "developer-features.plan-example": "writes-output",
                "developer-features.plan-material-fluid-recipe": "writes-output",
                "developer-features.plan-recipe-change": "writes-output",
                "developer-features.plan-quest-for-process": "writes-output",
                "developer-features.check": "read-only",
                "developer-features.compare-recipe-runtime": "mutating",
                "developer-features.apply": "mutating",
                "developer-features.rollback": "mutating",
                "developer-features.recover": "mutating",
                "developer-features.present": "read-only",
                "developer-features.transaction": "read-only",
            },
        )

        recipe_plan = self.catalog.command(
            "developer-features.plan-recipe-change"
        )
        plan_argv, plan_intent = recipe_plan.build_argv(
            {
                "workspace": "/tmp/supersymmetry",
                "recipe_script": "postInit/mixtures.groovy",
                "recipe_map": "MIXER",
                "item_input": [
                    {"amount": 6, "ore": "dustCopperSulfate"},
                    {"amount": 1, "item": "minecraft:bucket"},
                ],
                "fluid_output": [
                    {"amount": 1000, "fluid": "copper_sulfate_solution"}
                ],
                "duration": 60,
                "voltage_tier": "LV",
                "compact_json": True,
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual(plan_intent, "inert")
        self.assertEqual(
            plan_argv[2:5], ["feature", "plan", "recipe-change"]
        )
        self.assertEqual(plan_argv.count("--item-input"), 2)
        self.assertIn(
            '{"amount":6,"ore":"dustCopperSulfate"}', plan_argv
        )
        self.assertNotIn("--consent", plan_argv)
        self.assertIn("--compact-json", plan_argv)
        self.assertNotIn("--json", plan_argv)

        example = self.catalog.command("developer-features.plan-example")
        example_argv, example_intent = example.build_argv(
            {
                "example_key": "supersymmetry-quest-for-process-gas-atomizer",
                "workspace": "/tmp/supersymmetry",
                "compact_json": True,
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual("inert", example_intent)
        self.assertEqual(
            [
                "feature",
                "plan",
                "example",
                "supersymmetry-quest-for-process-gas-atomizer",
                "/tmp/supersymmetry",
            ],
            example_argv[2:7],
        )
        self.assertEqual("--compact-json", example_argv[-1])
        self.assertNotIn("--consent", example_argv)

        option_argv, option_intent = self.catalog.command(
            "developer-features.options"
        ).build_argv(
            {
                "family": "recipe-change",
                "workspace": "/tmp/supersymmetry",
                "query": "chemistry",
                "limit": 12,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual("execute", option_intent)
        self.assertIn("--query", option_argv)
        self.assertIn("chemistry", option_argv)
        self.assertIn("--limit", option_argv)

        reviewed_id = "workbench-plan:sha256:" + "c" * 64
        apply_argv, apply_intent = self.catalog.command(
            "developer-features.apply"
        ).build_argv(
            {
                "family": "recipe-change",
                "plan": reviewed_id,
                "consent": reviewed_id,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(apply_intent, "execute")
        self.assertEqual(apply_argv[2:5], ["feature", "apply", "recipe-change"])
        self.assertEqual(apply_argv[5], reviewed_id)
        self.assertEqual(apply_argv[-2:], ["--consent", reviewed_id])

        present_argv, present_intent = self.catalog.command(
            "developer-features.present"
        ).build_argv(
            {
                "family": "quest-for-process",
                "collection": "plans",
                "record": "/tmp/plan.json",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(present_intent, "execute")
        self.assertEqual(
            present_argv[2:],
            [
                "feature",
                "present",
                "quest-for-process",
                "plans",
                "/tmp/plan.json",
                "--json",
            ],
        )

        transaction_argv, transaction_intent = self.catalog.command(
            "developer-features.transaction"
        ).build_argv(
            {
                "family": "recipe-change",
                "plan": reviewed_id,
                "state_root": "/tmp/workbench-state",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(transaction_intent, "execute")
        self.assertEqual(
            transaction_argv[2:],
            [
                "feature",
                "transaction",
                "recipe-change",
                reviewed_id,
                "--state-root",
                "/tmp/workbench-state",
                "--json",
            ],
        )

        compare_argv, compare_intent = self.catalog.command(
            "developer-features.compare-recipe-runtime"
        ).build_argv(
            {
                "family": "recipe-change",
                "plan": reviewed_id,
                "consent": reviewed_id,
                "order": "candidate-first",
                "launcher_executable": "/tmp/prism",
                "launcher_root": "/tmp/prism-root",
                "state_root": "/tmp/workbench-state",
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(compare_intent, "execute")
        self.assertEqual(
            compare_argv[2:6],
            ["feature", "compare-runtime", "recipe-change", reviewed_id],
        )
        self.assertEqual(compare_argv.count(reviewed_id), 2)
        self.assertIn("--consent", compare_argv)
        self.assertIn("candidate-first", compare_argv)

    def test_atlas_complete_exploration_keeps_bounds_optional_and_owner_validated(self) -> None:
        from io import StringIO
        from workbench_atlas_recipe_health import cli as atlas_recipes

        command = self.catalog.command("atlas.recipes-impact")
        self.assertEqual(command.field("exploration").choices, ("bounded", "complete-finite"))
        for key in ("exploration", "max_depth", "max_nodes"):
            self.assertIsNone(command.field(key).default)
        values = {"path": "/tmp/graph", "selection_id": "gt-recipe:fixture", "json": True}
        default_argv, _ = command.build_argv(values, root=ROOT, execute=True)
        self.assertEqual(default_argv[2:], ["atlas", "recipes", "impact", "/tmp/graph", "gt-recipe:fixture", "--json"])
        parsed = atlas_recipes.build_parser().parse_args(default_argv[4:])
        self.assertEqual(parsed.exploration, "bounded")
        self.assertIsNone(parsed.max_depth)
        self.assertIsNone(parsed.max_nodes)

        complete, intent = command.build_argv({**values, "exploration": "complete-finite"}, root=ROOT, execute=True)
        self.assertEqual(intent, "execute")
        self.assertEqual(complete[2:], ["atlas", "recipes", "impact", "/tmp/graph", "gt-recipe:fixture", "--exploration", "complete-finite", "--json"])
        self.assertEqual(atlas_recipes.build_parser().parse_args(complete[4:]).exploration, "complete-finite")
        with self.assertRaises(CatalogError):
            command.build_argv({**values, "exploration": "unlimited"}, root=ROOT, execute=True)

        # The catalog composes argv; Atlas owns the conditional mode/bound rule.
        for bound, value in (("max_depth", 4), ("max_nodes", 500)):
            with self.subTest(bound=bound):
                mixed, _ = command.build_argv({**values, "exploration": "complete-finite", bound: value}, root=ROOT, execute=True)
                error = StringIO()
                with mock.patch.object(atlas_recipes, "open_recipe_health") as opened:
                    self.assertEqual(atlas_recipes.main(mixed[4:], output=StringIO(), error=error), 2)
                opened.assert_not_called()
                self.assertIn("cannot be combined with depth or node bounds", error.getvalue())

    def test_atlas_observation_catalog_exposes_linked_queries_and_explicit_evidence_reader(self) -> None:
        from workbench_atlas_observations.cli import build_parser
        cases = (
            ("session", {"path": "/graph"}),
            ("context", {"path": "/graph"}),
            ("search", {"path": "/graph", "query": "copper", "kind": "material", "limit": 2, "cursor": "bound-cursor"}),
            ("inspect", {"path": "/graph", "selection_id": "exact-node"}),
            ("crafting-exposure", {"path": "/graph", "selection_id": "exact-node", "max_depth": 3, "max_nodes": 20}),
            ("relationships", {"path": "/graph", "selection_id": "exact-node", "direction": "incoming", "relation": "observed-with"}),
            ("evidence", {"path": "/graph", "selection_id": "exact-node", "snapshot": "/snapshot", "pack_profile": "supersymmetry"}),
            ("index", {"path": "/graph", "max_source_bytes": 1000000, "max_index_bytes": 1048576}),
            ("import-snapshot", {"path": "/snapshot", "pack_profile": "supersymmetry", "output": "/new-graph", "side": "candidate"}),
        )
        for action, values in cases:
            with self.subTest(action=action):
                command = self.catalog.command("atlas.observations-" + action)
                argv, _ = command.build_argv({**values, "json": True}, root=ROOT, execute=True)
                self.assertEqual(["atlas", "observations", action], argv[2:5])
                args = build_parser().parse_args(argv[4:])
                self.assertEqual(action, args.action)
                self.assertTrue(args.json)
                self.assertEqual("mutating" if action in {"index", "import-snapshot"} else "read-only", command.risk)
        exposure = self.catalog.command("atlas.observations-crafting-exposure")
        argv, _ = exposure.build_argv({"path": "/graph", "selection_id": "exact-node"}, root=ROOT, execute=True)
        self.assertNotIn("--max-depth", argv)
        self.assertNotIn("--max-nodes", argv)
        self.assertIsNone(build_parser().parse_args(argv[4:]).max_depth)
        from workbench_api.profiles import profile_scope
        with profile_scope(disabled=("supersymmetry", "cleanroom")):
            available = build_catalog(ROOT)
        self.assertNotEqual("unavailable", available.command("atlas.observations-search").availability)

    def test_dead_end_audit_catalog_keeps_whole_inventory_and_export_options(self) -> None:
        from workbench_atlas_recipe_health.cli import build_parser

        command = self.catalog.command("atlas.recipes-audit-dead-ends")
        for output_format in (None, "json", "csv"):
            values = {"path": "/tmp/recipe evidence"}
            if output_format:
                values[output_format] = True
            argv, _ = command.build_argv(values, root=ROOT, execute=True)
            parsed = build_parser().parse_args(argv[4:])
            self.assertEqual("audit-dead-ends", parsed.action)
            self.assertEqual(Path(values["path"]), parsed.path)
            self.assertFalse(hasattr(parsed, "limit"))
            self.assertEqual(output_format == "json", parsed.json)
            self.assertEqual(output_format == "csv", parsed.csv)
        self.assertEqual("read-only", command.risk)

    def test_recipe_capture_catalog_routes_exact_inputs_to_the_owner(self) -> None:
        from io import StringIO
        from threading import Event
        from types import SimpleNamespace
        from workbench_shell import recipe_capture

        shared = {"state_root": "/tmp/retained captures é", "json": True}
        cases = (
            ("plan", {"workspace": "/tmp/branch é", "runtime": "/tmp/server",
                      "java_home": "/tmp/Java 8", "pack_profile": "supersymmetry", "heap_mib": 8192}),
            ("prepare", {"attempt": "recipe-capture-attempt", "confirm": "exact-reviewed-request"}),
            ("run", {"attempt": "recipe-capture-attempt", "confirm": "exact-prepared-record", "accept_eula": True}),
            ("show", {"attempt": "recipe-capture-attempt"}),
            ("cancel", {"attempt": "recipe-capture-attempt"}),
            ("export", {"attempt": "recipe-capture-attempt", "output": "/tmp/completed scan.zip"}),
        )
        selected = [command for command in self.catalog.for_suite("shell")
                    if command.command_id.startswith("shell.recipe-capture-")]
        self.assertEqual(8, len(selected))
        context = SimpleNamespace(state_root=Path("/default/state"), cancelled=Event(), check_cancelled=lambda: None)
        for action, values in cases:
            with self.subTest(action=action):
                command = self.catalog.command("shell.recipe-capture-" + action)
                argv, intent = command.build_argv({**values, **shared}, root=ROOT, execute=True)
                self.assertEqual(["capture", "recipes", action], argv[2:5])
                self.assertEqual("execute", intent)
                self.assertEqual("experimental", command.availability)
                self.assertEqual("read-only" if action == "show" else
                                 "writes-output" if action in {"plan", "export"} else "mutating", command.risk)
                with mock.patch.object(recipe_capture, action, return_value={"state": "fixture"}) as owner:
                    self.assertEqual(0, recipe_capture.main(argv[4:], context=context, output=StringIO(), error=StringIO()))
                args, kwargs = owner.call_args
                self.assertEqual(Path(shared["state_root"]), args[0])
                if action == "plan":
                    self.assertEqual(Path(values["workspace"]), kwargs["source"])
                    self.assertEqual(Path(values["runtime"]), kwargs["runtime"])
                    self.assertEqual(Path(values["java_home"]), kwargs["java_home"])
                    self.assertEqual(8192, kwargs["heap_mib"])
                    self.assertEqual("supersymmetry", kwargs["profile"])
                else:
                    self.assertEqual(values["attempt"], args[1])
                    if action in {"prepare", "run"}:
                        self.assertEqual(values["confirm"], args[2])
                    if action == "run":
                        self.assertTrue(kwargs["accept_eula"])
                    if action == "export":
                        self.assertEqual(Path(values["output"]), args[2])

    def test_recipe_capture_catalog_requires_explicit_selection_and_confirmation(self) -> None:
        plan = self.catalog.command("shell.recipe-capture-plan")
        selected = {"workspace": "/checkout", "runtime": "/server", "java_home": "/jdk",
                    "pack_profile": "supersymmetry"}
        for missing in ("workspace", "pack_profile"):
            with self.subTest(missing=missing), self.assertRaises(CatalogError):
                plan.build_argv({key: value for key, value in selected.items() if key != missing}, root=ROOT, execute=True)
        argv, _ = plan.build_argv({"workspace": "/checkout", "pack_profile": "supersymmetry"},
                                  root=ROOT, execute=True)
        self.assertNotIn("--runtime", argv)
        self.assertNotIn("--java-home", argv)
        fixture_set = self.catalog.command("shell.recipe-capture-fixtures-set")
        for missing in ("workspace", "pack_profile", "runtime", "java_home"):
            with self.subTest(missing=missing), self.assertRaises(CatalogError):
                fixture_set.build_argv({key: value for key, value in selected.items() if key != missing},
                                       root=ROOT, execute=True)
        for action in ("prepare", "run"):
            command = self.catalog.command("shell.recipe-capture-" + action)
            with self.assertRaises(CatalogError):
                command.build_argv({"attempt": "recipe-capture-attempt"}, root=ROOT, execute=True)
        run = self.catalog.command("shell.recipe-capture-run")
        argv, _ = run.build_argv({"attempt": "recipe-capture-attempt", "confirm": "exact-id"}, root=ROOT, execute=True)
        self.assertNotIn("--accept-eula", argv)  # Consent is never inferred by a client.

    def test_captured_routes_keep_owner_defaults_and_explicit_zero_depth(self) -> None:
        from workbench_atlas_recipe_health.cli import build_parser

        command = self.catalog.command("atlas.recipes-routes")
        values = {"path": "/tmp/recipe evidence", "selection_id": "item-variant:fixture"}
        argv, _ = command.build_argv(values, root=ROOT, execute=True)
        parsed = build_parser().parse_args(argv[4:])
        self.assertEqual("routes", parsed.action)
        self.assertEqual(Path(values["path"]), parsed.path)
        self.assertEqual(values["selection_id"], parsed.selection_id)
        for key in ("max_depth", "max_resources", "max_recipes"):
            self.assertIsNone(getattr(parsed, key))
        self.assertFalse(parsed.json)

        argv, _ = command.build_argv(
            {**values, "max_depth": 0, "max_resources": 7, "max_recipes": 9, "json": True},
            root=ROOT, execute=True,
        )
        parsed = build_parser().parse_args(argv[4:])
        self.assertEqual((0, 7, 9), (parsed.max_depth, parsed.max_resources, parsed.max_recipes))
        self.assertTrue(parsed.json)

    def test_capture_import_requires_explicit_profile_and_bound_manifest(self) -> None:
        from workbench_atlas_recipe_health.cli import build_parser

        command = self.catalog.command("atlas.recipes-import-capture")
        values = {"path": "/tmp/capture", "output": "/tmp/new graph",
                  "input_manifest": "/tmp/inputs.json", "pack_profile": "supersymmetry"}
        argv, _ = command.build_argv(values, root=ROOT, execute=True)
        parsed = build_parser().parse_args(argv[4:])
        self.assertEqual("import-capture", parsed.action)
        self.assertEqual("supersymmetry", parsed.pack_profile)
        self.assertEqual(Path(values["output"]), parsed.output)
        self.assertEqual(Path(values["input_manifest"]), parsed.input_manifest)
        self.assertEqual(1024 * 1024 * 1024, parsed.max_source_bytes)
        for missing in ("input_manifest", "pack_profile", "output"):
            with self.subTest(missing=missing), self.assertRaises(CatalogError):
                command.build_argv({k: v for k, v in values.items() if k != missing}, root=ROOT, execute=True)

    def test_atlas_recipe_catalog_uses_exact_context_bound_routes(self) -> None:
        commands = {
            item.command_id: item
            for item in self.catalog.for_suite("atlas")
            if item.command_id.startswith("atlas.recipes-")
        }
        self.assertEqual(
            set(commands),
            {
                "atlas.recipes-import-capture",
                "atlas.recipes-context",
                "atlas.recipes-index",
                "atlas.recipes-search",
                "atlas.recipes-inspect",
                "atlas.recipes-routes",
                "atlas.recipes-audit-dead-ends",
                "atlas.recipes-impact",
                "atlas.recipes-assess-plan",
                "atlas.recipes-compare-runtime",
            },
        )
        self.assertEqual("mutating", commands["atlas.recipes-index"].risk)
        self.assertEqual("mutating", commands["atlas.recipes-import-capture"].risk)
        self.assertTrue(
            all(
                item.risk == "read-only"
                for command_id, item in commands.items()
                if command_id not in {"atlas.recipes-index", "atlas.recipes-import-capture"}
            )
        )
        index_argv, intent = commands["atlas.recipes-index"].build_argv(
            {
                "path": "/tmp/graph",
                "max_source_bytes": 2000000000,
                "max_index_bytes": 1200000000,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual("execute", intent)
        self.assertEqual(
            index_argv[2:],
            [
                "atlas",
                "recipes",
                "index",
                "/tmp/graph",
                "--max-source-bytes",
                "2000000000",
                "--max-index-bytes",
                "1200000000",
                "--json",
            ],
        )
        self.assertIn(
            "query-index.sqlite3",
            " ".join(commands["atlas.recipes-index"].limitations),
        )
        search_argv, intent = commands["atlas.recipes-search"].build_argv(
            {
                "path": "/tmp/supersymmetry",
                "query": "copper sulfate",
                "limit": 25,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            search_argv[2:],
            [
                "atlas",
                "recipes",
                "search",
                "/tmp/supersymmetry",
                "copper sulfate",
                "--limit",
                "25",
                "--json",
            ],
        )
        self.assertTrue(commands["atlas.recipes-inspect"].limitations)
        impact_argv, intent = commands["atlas.recipes-impact"].build_argv(
            {
                "path": "/tmp/graph",
                "selection_id": "gt-recipe:sha256:fixture",
                "max_depth": 3,
                "max_nodes": 100,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            impact_argv[2:],
            [
                "atlas",
                "recipes",
                "impact",
                "/tmp/graph",
                "gt-recipe:sha256:fixture",
                "--max-depth",
                "3",
                "--max-nodes",
                "100",
                "--json",
            ],
        )
        assess_argv, intent = commands["atlas.recipes-assess-plan"].build_argv(
            {
                "path": "/tmp/graph",
                "plan": "workbench-plan:sha256:fixture",
                "state_root": "/tmp/state",
                "max_depth": 3,
                "max_nodes": 100,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            assess_argv[2:],
            [
                "atlas",
                "recipes",
                "assess-plan",
                "/tmp/graph",
                "workbench-plan:sha256:fixture",
                "--state-root",
                "/tmp/state",
                "--max-depth",
                "3",
                "--max-nodes",
                "100",
                "--json",
            ],
        )
        compare_argv, intent = commands["atlas.recipes-compare-runtime"].build_argv(
            {
                "before_path": "/tmp/baseline-graph",
                "after_path": "/tmp/candidate-graph",
                "max_recipes": 50000,
                "max_recipe_deltas": 250,
                "max_resources": 300,
                "max_depth": 3,
                "max_nodes": 1000,
                "json": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            compare_argv[2:],
            [
                "atlas",
                "recipes",
                "compare-runtime",
                "/tmp/baseline-graph",
                "/tmp/candidate-graph",
                "--max-recipes",
                "50000",
                "--max-recipe-deltas",
                "250",
                "--max-resources",
                "300",
                "--max-depth",
                "3",
                "--max-nodes",
                "1000",
                "--json",
            ],
        )

    def test_runtime_explorer_wizard_preserves_query_and_repeatable_evidence(self) -> None:
        command = self.catalog.command("explorer.search")
        argv, intent = command.build_argv(
            {
                "query": ["kind:mixin", "TargetMixin"],
                "project": "mods/example",
                "runtime_db": ".workbench/runtime.sqlite",
                "receipt": ["one.json", "two.json"],
                "kind": ["mixin", "transformer"],
                "details": True,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual("execute", intent)
        self.assertEqual(argv[2], "explore")
        self.assertIn("kind:mixin", argv)
        self.assertEqual(2, argv.count("--receipt"))
        self.assertEqual(2, argv.count("--kind"))
        self.assertIn("--details", argv)
        filter_only, _ = command.build_argv(
            {"kind": ["recipe"], "owner": ["example"]},
            root=ROOT,
            execute=True,
        )
        self.assertNotIn("query", filter_only)
        self.assertEqual(1, filter_only.count("--kind"))
        self.assertEqual(1, filter_only.count("--owner"))

    def test_repeatable_integer_assignment_preserves_each_value(self) -> None:
        command = self.catalog.command("world-studio.qualifier-run")
        values = parse_assignments(
            command,
            ["profile=supersymmetry", "seeds:=[1,2]"],
        )
        argv, _ = command.build_argv(values, root=ROOT, execute=False)
        self.assertEqual(2, argv.count("--seed"))
        self.assertEqual(["1", "2"], [argv[index + 1] for index, item in enumerate(argv) if item == "--seed"])

    def test_plan_then_apply_cannot_apply_in_preview(self) -> None:
        command = self.catalog.command("storage.cleanup")
        preview, intent = command.build_argv(
            {"selector": "runtime:one", "apply": True},
            root=ROOT,
            execute=False,
        )
        self.assertEqual(intent, "preview")
        self.assertNotIn("--apply", preview)
        execute, intent = command.build_argv(
            {"selector": "runtime:one"}, root=ROOT, execute=True
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(execute[-1], "--apply")

    def test_permanent_purge_requires_exact_confirmation(self) -> None:
        command = self.catalog.command("storage.purge")
        preview, intent = command.build_argv(
            {"selector": "trash:one"}, root=ROOT, execute=False
        )
        self.assertEqual(intent, "preview")
        self.assertEqual(preview[-1], "--show")
        with self.assertRaisesRegex(CatalogError, "exact confirm"):
            command.build_argv(
                {"selector": "trash:one"}, root=ROOT, execute=True
            )
        execute, intent = command.build_argv(
            {"selector": "trash:one", "confirm": "trash:one"},
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertNotIn("--show", execute)
        self.assertEqual(execute[-2:], ["--confirm", "trash:one"])

    def test_worldgen_requires_explicit_execute_and_has_every_parser_option(self) -> None:
        command = self.catalog.command("world-studio.iterate")
        expected = {
            "profile", "profile_file", "runtime_template", "strata_root",
            "java_cmd", "gradle_cmd", "plan", "artifact",
            "observatory_artifact", "seed", "region", "mode",
            "label", "heap", "diagnostic_sample_modulo", "server_port",
            "viewer_port", "startup_timeout", "scan_timeout", "stop_timeout",
            "compare", "skip_build", "no_open",
        }
        self.assertEqual({item.key for item in command.fields}, expected)
        argv, intent = command.build_argv(
            {"profile": "supersymmetry"}, root=ROOT, execute=False
        )
        self.assertEqual(intent, "inert")
        self.assertEqual(argv[2:4], ["worldgen", "dev"])
        self.assertEqual(argv[-2:], ["--profile", "supersymmetry"])

    def test_worldgen_requires_exactly_one_explicit_profile_source(self) -> None:
        command = self.catalog.command("world-studio.iterate")
        with self.assertRaisesRegex(CatalogError, "requires exactly one"):
            command.build_argv({}, root=ROOT, execute=False)
        with self.assertRaisesRegex(CatalogError, "mutually exclusive"):
            command.build_argv(
                {"profile": "supersymmetry", "profile_file": "profile.yaml"},
                root=ROOT,
                execute=False,
            )

    def test_atlas_and_blueprints_preserve_global_option_placement(self) -> None:
        atlas = self.catalog.command("atlas.who-wrote-block")
        argv, _ = atlas.build_argv(
            {
                "bundle": "bundle.json",
                "output": "answer.json",
                "dimension_id": 0,
                "position": [1, 64, 2],
            },
            root=ROOT,
            execute=True,
        )
        query_index = argv.index("who-wrote-block")
        self.assertLess(argv.index("--bundle"), query_index)
        self.assertGreater(argv.index("--dimension-id"), query_index)

        blueprint = self.catalog.command("blueprints.plan")
        argv, _ = blueprint.build_argv(
            {"workspace": ".workbench/blueprints/a", "planning_evidence": "evidence.json"},
            root=ROOT,
            execute=True,
        )
        plan_index = argv.index("plan")
        self.assertLess(argv.index("--workspace"), plan_index)
        self.assertGreater(argv.index("--planning-evidence"), plan_index)

    def test_assignments_are_typed_and_never_shell_split(self) -> None:
        command = self.catalog.command("runs.managed")
        values = parse_assignments(
            command,
            [
                "recipe=fast; touch should-not-exist",
                "profile=supersymmetry",
                "seed:=42",
            ],
        )
        argv, _ = command.build_argv(values, root=ROOT, execute=False)
        self.assertIn("fast; touch should-not-exist", argv)
        self.assertNotIn("touch", argv)
        self.assertIn("42", argv)

    def test_mutex_and_unknown_values_fail_before_process_launch(self) -> None:
        doctor = self.catalog.command("doctor.inspect")
        with self.assertRaisesRegex(CatalogError, "mutually exclusive"):
            doctor.build_argv(
                {"profile": "a", "profile_file": "b.yaml"},
                root=ROOT,
                execute=True,
            )
        with self.assertRaisesRegex(CatalogError, "unknown option"):
            doctor.build_argv({"magic": "yes"}, root=ROOT, execute=True)

    def test_cleanroom_fixture_build_is_one_shell_free_profile_action(self) -> None:
        command = self.catalog.command("cleanroom.fixture-build")
        self.assertEqual(command.suite_id, "cleanroom")
        self.assertEqual(command.authority.split(";")[0], "Cleanroom platform profile fixture lock")
        self.assertEqual(command.risk, "mutating")
        self.assertEqual(command.preview, "inert-only")
        gradle = ROOT / ".workbench/toolchains/gradle/bin/gradle"
        java_home = ROOT / ".workbench/toolchains/java-25"
        input_digest = "sha256:" + "1" * 64
        state_root = ROOT / ".workbench/cleanroom-fixture"
        argv, intent = command.build_argv(
            {
                "gradle_cmd": gradle,
                "java_home": java_home,
                "expected_input_digest": input_digest,
                "state_root": state_root,
            },
            root=ROOT,
            execute=True,
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(
            argv,
            [
                sys.executable,
                str(
                    ROOT
                    / "profiles/platforms/cleanroom/tools/"
                    "run_generic_mod_fixture_build.py"
                ),
                "--gradle-cmd",
                str(gradle),
                "--java-home",
                str(java_home),
                "--expected-input-digest",
                input_digest,
                "--state-root",
                str(state_root),
            ],
        )
        with self.assertRaisesRegex(CatalogError, "requires --java-home"):
            command.build_argv(
                {"gradle_cmd": gradle}, root=ROOT, execute=True
            )

    def test_catalog_projection_is_json_serializable_and_has_all_manuals(self) -> None:
        projection = self.catalog.public_dict()
        self.assertIn("workbench-live-console-command-catalog-v2", json.dumps(projection))
        self.assertRegex(projection["catalog_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(
            all(
                re.fullmatch(r"sha256:[0-9a-f]{64}", row["action_digest"])
                for row in projection["commands"]
            )
        )
        guide_count = len(list((ROOT / "modules/manuals/guides").rglob("*.md")))
        self.assertEqual(len(self.catalog.for_suite("manuals")), guide_count + 1)

    def test_manual_order_and_catalog_digest_are_platform_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide_root = root / "modules/manuals/guides"
            readme = guide_root / "mixed-case/README.md"
            lowercase = guide_root / "mixed-case/alpha.md"
            for path in (readme, lowercase):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {path.stem}\n", encoding="utf-8")

            with mock.patch.object(
                Path,
                "rglob",
                return_value=iter((readme, lowercase)),
            ):
                posix_commands = _manual_commands(root)

            def windows_style_less(left: Path, right: Path) -> bool:
                return left.as_posix().casefold() < right.as_posix().casefold()

            with (
                mock.patch.object(
                    Path,
                    "rglob",
                    return_value=iter((lowercase, readme)),
                ),
                mock.patch.object(Path, "__lt__", new=windows_style_less),
            ):
                self.assertEqual(
                    [lowercase, readme],
                    sorted((readme, lowercase)),
                )
                windows_commands = _manual_commands(root)

            expected_documents = [
                "modules/manuals/README.md",
                "modules/manuals/guides/mixed-case/README.md",
                "modules/manuals/guides/mixed-case/alpha.md",
            ]
            self.assertEqual(
                expected_documents,
                [command.document for command in posix_commands],
            )
            self.assertEqual(posix_commands, windows_commands)

            suite = SuiteSpec(
                "manuals",
                "Manuals",
                "Practical checked-in teaching beside tools.",
                "Manuals",
                "experimental",
            )
            posix_catalog = Catalog(root, (suite,), tuple(posix_commands))
            windows_catalog = Catalog(root, (suite,), tuple(windows_commands))
            self.assertEqual(
                posix_catalog.catalog_digest,
                windows_catalog.catalog_digest,
            )

    def test_action_digest_binds_risk_preview_and_exact_argv_template(self) -> None:
        command = self.catalog.command("doctor.inspect")
        digest = command.action_digest(root=ROOT)
        self.assertEqual(digest, command.action_digest(root=ROOT))
        self.assertNotEqual(
            digest,
            replace(command, risk="mutating").action_digest(root=ROOT),
        )
        self.assertNotEqual(
            digest,
            replace(command, preview="inert-only").action_digest(root=ROOT),
        )
        self.assertNotEqual(
            digest,
            replace(
                command,
                argv_template=(*command.argv_template[:-1], "--changed", command.argv_template[-1]),
            ).action_digest(root=ROOT),
        )

    def test_catalog_identity_is_stable_across_install_roots(self) -> None:
        relocated_root = Path("/opt/workbench-portable/suite")
        relocated = replace(self.catalog, root=relocated_root)
        command = self.catalog.command("doctor.inspect")

        self.assertEqual(
            command.action_digest(root=ROOT),
            command.action_digest(root=relocated_root),
        )
        self.assertEqual(self.catalog.catalog_digest, relocated.catalog_digest)
        preview = command.public_dict(root=relocated_root)["command_preview"]
        self.assertNotIn(str(relocated_root), preview)
        self.assertNotIn(sys.executable, preview)
        self.assertTrue(preview.startswith("python "), preview)

    def test_sensitive_catalog_fields_are_digest_bound_and_redacted(self) -> None:
        for command_id in ("shell.material-fluid-run", "feature-studio.verify"):
            with self.subTest(command_id=command_id):
                command = self.catalog.command(command_id)
                launcher_profile = command.field("launcher_profile")
                self.assertTrue(launcher_profile.sensitive)
                self.assertTrue(launcher_profile.public_dict()["sensitive"])
                self.assertEqual(
                    ["tool", "--launcher-profile", "<redacted>"],
                    redact_argv(
                        ["tool", "--launcher-profile", "PrivateProfile"],
                        command.fields,
                    ),
                )
                self.assertEqual(
                    ["tool", "--launcher-profile=<redacted>"],
                    redact_argv(
                        ["tool", "--launcher-profile=PrivateProfile"],
                        command.fields,
                    ),
                )
                insensitive = replace(launcher_profile, sensitive=False)
                changed = replace(
                    command,
                    fields=tuple(
                        insensitive if field.key == "launcher_profile" else field
                        for field in command.fields
                    ),
                )
                self.assertNotEqual(
                    command.action_digest(root=ROOT),
                    changed.action_digest(root=ROOT),
                )

        for command in self.catalog.commands:
            for field in command.fields:
                if "retained values are redacted" in field.help:
                    self.assertTrue(
                        field.sensitive,
                        f"{command.command_id}.{field.key} promises redaction",
                    )

    def test_search_is_literal_fuzzy_and_suite_scoped(self) -> None:
        self.assertEqual(self.catalog.search("purge")[0].command_id, "storage.purge")
        results = self.catalog.search("world", suite_id="atlas")
        self.assertTrue(results)
        self.assertTrue(all(item.suite_id == "atlas" for item in results))

    def test_retained_storage_options_reach_owner_before_subcommand(self) -> None:
        from workbench_core.storage.cli import build_parser

        for action in ("list", "inspect", "cleanup", "restore", "purge"):
            with self.subTest(action=action):
                values = {"checks": True, "checks_root": "/tmp/retired check store"}
                if action != "list":
                    values["selector"] = "exact-resource"
                argv, intent = self.catalog.command("storage." + action).build_argv(
                    values, root=ROOT, execute=False
                )
                parsed = build_parser().parse_args(argv[2:])
                self.assertTrue(parsed.checks)
                self.assertEqual(parsed.checks_root, Path(values["checks_root"]))
                self.assertEqual(parsed.storage_action, action)
                if action in ("cleanup", "restore", "purge"):
                    self.assertEqual(intent, "preview")
                    self.assertFalse(getattr(parsed, "apply", False))
                    self.assertIsNone(getattr(parsed, "confirm", None))

    def test_catalog_fields_track_authoritative_parser_destinations(self) -> None:
        for source in (
            ROOT / "modules/crucible/src",
            ROOT / "modules/blueprints/src",
            ROOT / "modules/atlas/src",
            ROOT / "modules/atlas/src/workbench_atlas",
            ROOT / "modules/subsurface-studio/src",
            ROOT / "modules/pack-program-studio/src",
            ROOT / "modules/worldgen-cockpit/src",
            ROOT / "modules/worldgen-qualifier/src",
        ):
            if str(source) not in sys.path:
                sys.path.insert(0, str(source))

        manager = importlib.import_module(
            "workbench_core.storage.cli"
        )
        worldgen = importlib.import_module(
            "workbench_crucible_worldgen_iteration.cli"
        )
        blueprints = importlib.import_module("workbench_blueprints.cli")
        standards = importlib.import_module("workbench_blueprints.standards")
        runtime_graph = importlib.import_module("runtime_graph")
        corpus_bridge = importlib.import_module("workbench_atlas.corpus_bridge")
        subsurface = importlib.import_module("workbench_subsurface_studio.cli")
        cockpit = importlib.import_module("workbench_worldgen_cockpit.cli")
        qualifier = importlib.import_module("workbench_worldgen_qualifier.cli")
        pack_program = importlib.import_module("workbench_pack_program_studio.cli")
        developer_features = importlib.import_module(
            "workbench_shell.developer_feature_cli"
        )
        atlas_recipes = importlib.import_module(
            "workbench_shell.atlas_recipe_cli"
        )
        atlas_observations = importlib.import_module("workbench_atlas_observations.cli")
        process_recipes = importlib.import_module(
            "workbench_shell.process_recipe_cli"
        )

        from workbench_shell import workspace_commands, inspection_commands, run_commands

        checks = [
            ("workspace.open", workspace_commands._open_parser(), (), {}),
            ("doctor.inspect", inspection_commands._doctor_parser(), (), {}),
            ("runs.managed", run_commands._managed_run_parser(), (), {}),
            ("pack-program.groovy-dev", pack_program.build_parser(), ("dev",), {}),
            ("pack-program.groovy-check", pack_program.build_parser(), ("check",), {}),
            ("pack-program.groovy-session", pack_program.build_parser(), ("session",), {}),
            ("storage.list", manager.build_parser(), ("storage", "list"), {}),
            ("storage.inspect", manager.build_parser(), ("storage", "inspect"), {}),
            ("storage.cleanup", manager.build_parser(), ("storage", "cleanup"), {}),
            ("storage.restore", manager.build_parser(), ("storage", "restore"), {}),
            ("storage.purge", manager.build_parser(), ("storage", "purge"), {}),
            ("runtime.create", manager.build_parser(), ("runtime", "create"), {"profile_name": "profile"}),
            ("world.snapshot", manager.build_parser(), ("world", "snapshot"), {}),
            ("world.restore", manager.build_parser(), ("world", "restore"), {"profile_name": "profile"}),
            ("world-studio.iterate", worldgen.build_parser(), (), {}),
            ("world-studio.cockpit-run", cockpit.build_parser(), ("run",), {}),
            ("world-studio.cockpit-compare", cockpit.build_parser(), ("compare",), {}),
            ("world-studio.cockpit-show", cockpit.build_parser(), ("show",), {}),
            ("world-studio.cockpit-open", cockpit.build_parser(), ("open",), {}),
            ("world-studio.qualifier-run", qualifier.build_parser(), ("run",), {}),
            ("world-studio.qualifier-assess", qualifier.build_parser(), ("assess",), {}),
            ("world-studio.qualifier-scan", qualifier.build_parser(), ("scan",), {}),
            ("world-studio.qualifier-show", qualifier.build_parser(), ("show",), {}),
            ("world-studio.qualifier-open", qualifier.build_parser(), ("open",), {}),
            ("world-studio.subsurface-summary", subsurface.build_parser(), ("summary",), {}),
            ("world-studio.subsurface-layers", subsurface.build_parser(), ("layers",), {}),
            ("world-studio.subsurface-map", subsurface.build_parser(), ("map",), {}),
            ("world-studio.subsurface-explain", subsurface.build_parser(), ("explain",), {}),
            ("world-studio.subsurface-section", subsurface.build_parser(), ("section",), {}),
            ("world-studio.subsurface-definitions", subsurface.build_parser(), ("definitions",), {}),
            ("world-studio.subsurface-fluids", subsurface.build_parser(), ("fluids",), {}),
            ("world-studio.subsurface-compare", subsurface.build_parser(), ("compare",), {}),
            ("atlas.runtime-machine-recipes", runtime_graph._parser(), ("machine-recipes",), {}),
            ("atlas.runtime-producers", runtime_graph._parser(), ("producers",), {}),
            ("atlas.runtime-consumers", runtime_graph._parser(), ("consumers",), {}),
            ("atlas.runtime-process-chain", runtime_graph._parser(), ("process-chain",), {}),
            ("atlas.answer", corpus_bridge._parser(), ("answer",), {}),
            ("atlas.query", corpus_bridge._parser(), ("query",), {}),
            ("atlas.continuation-start", corpus_bridge._parser(), ("continuation-start",), {}),
            ("atlas.recipes-context", atlas_recipes.build_parser(), ("context",), {}),
            ("atlas.recipes-import-capture", atlas_recipes.build_parser(), ("import-capture",), {}),
            ("atlas.recipes-index", atlas_recipes.build_parser(), ("index",), {}),
            ("atlas.recipes-search", atlas_recipes.build_parser(), ("search",), {}),
            ("atlas.recipes-inspect", atlas_recipes.build_parser(), ("inspect",), {}),
            ("atlas.recipes-routes", atlas_recipes.build_parser(), ("routes",), {}),
            ("atlas.recipes-audit-dead-ends", atlas_recipes.build_parser(), ("audit-dead-ends",), {}),
            ("atlas.recipes-impact", atlas_recipes.build_parser(), ("impact",), {}),
            ("atlas.recipes-assess-plan", atlas_recipes.build_parser(), ("assess-plan",), {}),
            ("atlas.recipes-compare-runtime", atlas_recipes.build_parser(), ("compare-runtime",), {}),
            *(("atlas.observations-" + action, atlas_observations.build_parser(), (action,), {})
              for action in ("context", "search", "inspect", "relationships", "evidence", "crafting-exposure", "session", "index", "import-snapshot")),
            ("process-studio.recipes-compare", process_recipes.build_parser(), (), {}),
            ("developer-features.examples", developer_features.build_parser(), ("examples",), {}),
            ("developer-features.records", developer_features.build_parser(), ("records",), {}),
            ("developer-features.options", developer_features.build_parser(), ("options",), {}),
            (
                "developer-features.plan-example",
                developer_features.build_parser(),
                ("plan", "example"),
                {},
            ),
            (
                "developer-features.plan-material-fluid-recipe",
                developer_features.build_parser(),
                ("plan", "material-fluid-recipe"),
                {},
            ),
            (
                "developer-features.plan-recipe-change",
                developer_features.build_parser(),
                ("plan", "recipe-change"),
                {},
            ),
            (
                "developer-features.plan-quest-for-process",
                developer_features.build_parser(),
                ("plan", "quest-for-process"),
                {},
            ),
            ("developer-features.check", developer_features.build_parser(), ("check",), {}),
            (
                "developer-features.compare-recipe-runtime",
                developer_features.build_parser(),
                ("compare-runtime",),
                {},
            ),
            ("developer-features.apply", developer_features.build_parser(), ("apply",), {}),
            ("developer-features.rollback", developer_features.build_parser(), ("rollback",), {}),
            ("developer-features.recover", developer_features.build_parser(), ("recover",), {}),
            ("developer-features.present", developer_features.build_parser(), ("present",), {}),
            (
                "developer-features.transaction",
                developer_features.build_parser(),
                ("transaction",),
                {},
            ),
            ("blueprints.init", blueprints._parser(), ("init",), {}),
            ("blueprints.plan", blueprints._parser(), ("plan",), {}),
            ("blueprints.simulate", blueprints._parser(), ("simulate",), {}),
            ("blueprints.standard-compile", standards._parser(), ("compile",), {}),
            ("blueprints.standard-check", standards._parser(), ("check",), {}),
        ]
        for command_id, parser, path, renames in checks:
            with self.subTest(command_id=command_id):
                expected = {
                    renames.get(destination, destination)
                    for destination in _parser_destinations(parser, path)
                }
                actual = {
                    field.key for field in self.catalog.command(command_id).fields
                }
                self.assertEqual(actual, expected)

    def test_advanced_evidence_wizards_match_owner_help(self) -> None:
        command_ids = (
            "pack-program.recipe-invalidations",
            "atlas.worldgen-exact-suite",
            "mixin.import-runtime-service",
            "mixin.import-provider-enumeration",
            "mixin.import-defining-loader-trace",
            "mixin.import-cleanmix-audit",
            "mixin.assemble-runtime-service",
            "mixin.assemble-transformation-ledger",
            "crucible.manifest-installed-mod-set",
            "crucible.manifest-configuration-set",
            "crucible.audit-runtime-session",
            "crucible.evaluate-hook-health",
            "crucible.normalize-worldgen-case",
            "crucible.evaluate-worldgen-matrix",
            "crucible.assemble-observatory-proof",
            "crucible.external-diagnostic-health",
            "crucible.foundation-bytecode-manifest",
            "crucible.foundation-bytecode-compare",
            "crucible.hook-catalog-check",
            "crucible.hook-catalog-write",
            "crucible.synthetic-observatory-fixtures",
            "world-studio.summarize-log",
            "world-studio.summarize-jfr",
            "world-studio.compare-logs",
            "world-studio.gtceu-trace-assemble",
        )
        for command_id in command_ids:
            command = self.catalog.command(command_id)
            template = [
                token.replace("{python}", sys.executable).replace(
                    "{root}", str(ROOT)
                )
                for token in command.argv_template
            ]
            placeholder = next(
                index
                for index, token in enumerate(template)
                if token.startswith("{options:")
            )
            help_argv = [*template[:placeholder], "--help"]
            with self.subTest(command_id=command_id):
                completed = subprocess.run(
                    help_argv,
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
                for option in command.fields:
                    expected = option.primary_flag if option.flags else option.key
                    self.assertIn(expected, completed.stdout)

    def test_repeatable_grouped_option_preserves_each_owner_occurrence(self) -> None:
        command = self.catalog.command("crucible.evaluate-hook-health")
        values = {
            option.key: "value"
            for option in command.fields
            if option.required and option.key != "case"
        }
        values["case"] = [
            ["aa-1", "required-complete", "a.ndjson", "a" * 64, "a.json", "b" * 64],
            ["crash", "excluded-incomplete", "c.ndjson", "c" * 64, "c.json", "d" * 64],
        ]
        argv, _ = command.build_argv(values, root=ROOT, execute=True)
        self.assertEqual(argv.count("--case"), 2)

    def test_catalog_does_not_publish_removed_route_tombstones(self) -> None:
        removed = {
            "atlas.runtime-check-contract",
            "atlas.runtime-check-producer-binding",
            "atlas.runtime-check",
            "atlas.runtime-check-raw",
            "atlas.runtime-capture-offline",
            "atlas.runtime-normalize",
        }
        self.assertTrue(removed.isdisjoint(self.catalog.commands))
        self.assertFalse(
            [
                command.command_id
                for command in self.catalog.commands
                if command.availability == "unavailable"
            ]
        )


class AtlasScanCatalogTests(unittest.TestCase):
    def test_completed_scan_catalog_matches_standalone_owner_parser(self):
        from workbench_atlas_recipe_health.scan_cli import build_parser
        catalog = build_catalog(ROOT)
        for action in ("import", "show", "audit", "export"):
            with self.subTest(action=action):
                command = catalog.command("atlas.scans-" + action)
                expected = _parser_destinations(build_parser(), (action,))
                expected.discard("action")
                self.assertEqual(expected, {field.key for field in command.fields})
                values = {"path": "Scan with spaces 資料", "json": True}
                if action == "import":
                    values["destination"] = "New import 資料"
                elif action == "export":
                    values["output"] = "New archive 資料.zip"
                elif action == "audit":
                    values.update(finding="both-sides-candidate", lookup_state="active", text="fluid circuit", offset=25, limit=25)
                argv, intent = command.build_argv(values, root=ROOT, execute=True)
                self.assertEqual(["atlas", "scans", action], argv[2:5])
                self.assertEqual("execute", intent)
                parsed = build_parser().parse_args(argv[4:])
                for key, value in values.items():
                    self.assertEqual(Path(value) if key in {"path", "destination", "output"} else value, getattr(parsed, key))
                self.assertEqual("writes-output" if action in {"import", "export"} else "read-only", command.risk)
                self.assertEqual("experimental", command.availability)

    def test_scan_actions_do_not_require_a_pack_profile(self):
        from workbench_api.profiles import profile_scope
        with profile_scope(disabled=("supersymmetry", "cleanroom")):
            catalog = build_catalog(ROOT)
        for action in ("import", "show", "audit", "export"):
            command = catalog.command("atlas.scans-" + action)
            self.assertNotEqual("unavailable", command.availability)
            self.assertNotIn("pack_profile", {field.key for field in command.fields})


def _parser_destinations(
    parser: argparse.ArgumentParser, path: tuple[str, ...]
) -> set[str]:
    destinations: set[str] = set()
    current = parser
    for token in (*path, None):
        subparsers = None
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers = action
            elif action.dest != "help":
                destinations.add(action.dest)
        if token is None:
            break
        if subparsers is None or token not in subparsers.choices:
            raise AssertionError(f"parser has no subcommand path component {token}")
        current = subparsers.choices[token]
    return destinations


if __name__ == "__main__":
    unittest.main()
