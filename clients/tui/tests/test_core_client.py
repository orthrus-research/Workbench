"""Contract tests for the external Workbench Core boundary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from workbench_tui.core_client import CoreClient, CoreClientError, SetupInputs


class CoreClientContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="workbench tui core ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = self.root / "calls.jsonl"
        self.migration_file = self.root / "migration.json"
        self.migration_file.write_text(json.dumps({
            "format": "workbench-user-config-migration-v1",
            "schema_version": 1,
            "source": str(self.root / "earlier"),
            "destination": str(self.root / "stable"),
            "files": [{
                "name": "setup-v1.json", "sha256": "sha256:" + "a" * 64, "state": "copy",
            }],
            "state": "ready",
        }), encoding="utf-8")
        self.launcher = self.root / "fake; workbench.py"
        self.launcher.write_text(
            """import json
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(__file__).with_name('calls.jsonl').open('a', encoding='utf-8') as log:
    log.write(json.dumps(args, ensure_ascii=False) + '\\n')
if args[:3] == ['setup', '--check', '--json']:
    print(json.dumps({'format': 'workbench-setup-check-v2',
                      'dependencies': [{'id': 'git', 'state': 'ready'}]}))
    sys.exit(1)
if args[:3] == ['setup', '--plan', '--json']:
    print(json.dumps({'format': 'workbench-setup-plan-v2',
                      'plan_id': 'workbench-setup-plan-' + 'a' * 64,
                      'actions': [{'id': 'save'}], 'blockers': []}))
    sys.exit(0)
if args[:2] == ['setup', '--apply']:
    print(json.dumps({'applied_plan_id': args[2], 'record': {'selection': {}}}))
    sys.exit(0)
if args[:2] == ['version', '--json']:
    print(json.dumps({'component_id': 'workbench-core', 'version': '0.0.test'}))
    sys.exit(0)
if args[:2] == ['settings', 'migrate']:
    record = json.loads(Path(__file__).with_name('migration.json').read_text(encoding='utf-8'))
    if '--dry-run' not in args and record['state'] == 'ready':
        record['state'] = 'imported'
        record['files'] = [{**row, 'state': 'copied' if row['state'] == 'copy' else row['state']}
                           for row in record['files']]
    print(json.dumps(record))
    sys.exit(1 if record['state'] == 'conflict' else 0)
if args[:2] == ['environment', 'resolve']:
    print(json.dumps({'format': 'workbench-environment-resolution-v1',
                      'resolution_id': 'workbench-environment-resolution:sha256:' + 'a' * 64,
                      'workspace': {'path': args[2] if len(args) > 3 else '/saved/default',
                                    'source': 'argument' if len(args) > 3 else 'user-workspaces'}}))
    sys.exit(0)
if args[:2] == ['console', 'run'] and '--review-json' in args:
    print(json.dumps({'format_version': 'workbench-live-console-command-review-v2',
                      'catalog_digest': 'sha256:' + 'a' * 64,
                      'action_digest': 'sha256:' + 'b' * 64,
                      'command_id': 'workspace.open',
                      'review_digest': 'sha256:' + 'c' * 64,
                      'risk': 'read-only'}))
    sys.exit(0)
if args[:3] == ['console', 'run', 'manuals.overview']:
    print('Workbench manuals document')
    sys.exit(0)
if args[:2] == ['console', 'run'] and '--execute' in args:
    print(json.dumps({'ok': True}))
    sys.exit(0)
print(json.dumps({'error': 'unexpected invocation'}))
sys.exit(2)
""",
            encoding="utf-8",
        )
        self.client = CoreClient((sys.executable, str(self.launcher)))

    def recorded_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    async def test_environment_resolution_preserves_core_workspace_precedence(self) -> None:
        default = await self.client.environment_resolve()
        explicit = str(self.root / "moved 世界")
        selected = await self.client.environment_resolve(explicit)
        self.assertEqual("user-workspaces", default["workspace"]["source"])
        self.assertEqual(explicit, selected["workspace"]["path"])
        self.assertEqual(
            [["environment", "resolve", "--json"],
             ["environment", "resolve", explicit, "--json"]],
            self.recorded_calls(),
        )

    async def test_migration_is_preflighted_again_before_import(self) -> None:
        preview = await self.client.migration_preview()
        self.assertEqual("ready", preview["state"])
        result = await self.client.migration_import(preview)
        self.assertEqual("imported", result["state"])
        self.assertEqual("copied", result["files"][0]["state"])
        self.assertEqual([
            ["settings", "migrate", "--dry-run", "--json"],
            ["settings", "migrate", "--dry-run", "--json"],
            ["settings", "migrate", "--json"],
        ], self.recorded_calls())

    async def test_migration_conflict_and_changed_preview_never_import(self) -> None:
        preview = await self.client.migration_preview()
        changed = {**preview, "files": [
            {**preview["files"][0], "sha256": "sha256:" + "b" * 64},
        ]}
        self.migration_file.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(CoreClientError, "changed before import"):
            await self.client.migration_import(preview)
        self.assertEqual(2, len(self.recorded_calls()))

        conflict = {**changed, "state": "conflict", "files": [
            {**changed["files"][0], "state": "conflict"},
        ]}
        self.migration_file.write_text(json.dumps(conflict), encoding="utf-8")
        observed = await self.client.migration_preview()
        self.assertEqual("conflict", observed["state"])
        with self.assertRaisesRegex(CoreClientError, "requires a ready"):
            await self.client.migration_import(observed)
        self.assertNotIn(["settings", "migrate", "--json"], self.recorded_calls())

    async def test_migration_rejects_incompatible_json(self) -> None:
        record = json.loads(self.migration_file.read_text(encoding="utf-8"))
        record["schema_version"] = 2
        self.migration_file.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(CoreClientError, "unsupported Workbench configuration"):
            await self.client.migration_preview()

    async def test_migration_failure_without_json_reports_core_diagnostic(self) -> None:
        self.migration_file.unlink()
        with self.assertRaises(CoreClientError) as raised:
            await self.client.migration_preview()
        self.assertIn("without JSON", str(raised.exception))
        self.assertIn("migration.json", str(raised.exception))

    async def test_setup_check_exit_one_and_plan_apply_preserve_exact_options(self) -> None:
        workspace = str(self.root / "workspace ; é")
        options = SetupInputs(mode="review", workspace=workspace).option_args()
        self.assertEqual(options, ("--workspace", workspace))

        check = await self.client.setup_check(options)
        plan = await self.client.setup_plan(options)
        applied = await self.client.setup_apply(plan["plan_id"], options)

        self.assertEqual(check["dependencies"][0]["state"], "ready")
        self.assertEqual(applied["applied_plan_id"], plan["plan_id"])
        self.assertEqual(
            self.recorded_calls(),
            [
                ["setup", "--check", "--json", *options],
                ["setup", "--plan", "--json", *options],
                ["setup", "--apply", plan["plan_id"], "--json", *options],
            ],
        )

    async def test_rejects_bad_plan_identity_before_launch(self) -> None:
        with self.assertRaisesRegex(CoreClientError, "invalid setup plan identity"):
            await self.client.setup_apply("not-a-plan", ("--workspace", str(self.root)))
        self.assertFalse(self.calls.exists())

    async def test_review_digest_is_bound_to_the_same_action_and_inputs_on_execute(self) -> None:
        catalog = {"catalog_digest": "sha256:" + "a" * 64}
        action = {
            "command_id": "workspace.open",
            "action_digest": "sha256:" + "b" * 64,
            "risk": "read-only",
            "options": [{"key": "workspace"}],
        }
        values = {"workspace": str(self.root / "space 世界")}
        review = await self.client.command_review(catalog, action, values)
        result = await self.client.run_reviewed_command(catalog, action, values, review)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout), {"ok": True})
        reviewed, executed = self.recorded_calls()
        self.assertEqual(reviewed[-1], "--review-json")
        self.assertEqual(executed[: len(reviewed) - 1], reviewed[:-1])
        self.assertEqual(
            executed[len(reviewed) - 1 :],
            [
                "--expect-review-digest",
                review["review_digest"],
                "--execute",
                "--no-retain",
                "--console",
                "plain",
            ],
        )
        self.assertEqual(
            reviewed[reviewed.index("--set") + 1],
            "workspace:=" + json.dumps(values["workspace"], ensure_ascii=False),
        )

        with self.assertRaisesRegex(CoreClientError, "read-only"):
            await self.client.run_reviewed_command(
                catalog, {**action, "risk": "writes"}, values, review
            )
        self.assertEqual(len(self.recorded_calls()), 2)

    async def test_timeout_terminates_the_selected_process(self) -> None:
        sleeper = self.root / "sleeper.py"
        sleeper.write_text(
            "import time\nfrom pathlib import Path\n"
            "Path(__file__).with_suffix('.started').write_text('started')\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        client = CoreClient((sys.executable, str(sleeper)))
        with self.assertRaisesRegex(CoreClientError, "timed out after 1s"):
            await client.call("version", "--json", timeout=1)
        self.assertTrue(sleeper.with_suffix(".started").exists())

    async def test_output_is_capped_while_streaming(self) -> None:
        noisy = self.root / "noisy.py"
        noisy.write_text(
            "import sys, time\n"
            "sys.stdout.write('x' * 16384)\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        client = CoreClient((sys.executable, str(noisy)))
        with self.assertRaisesRegex(CoreClientError, "display limit"):
            await client.call("version", "--json", timeout=5, max_output=1024)

    async def test_core_does_not_share_terminal_input(self) -> None:
        reader = self.root / "reader.py"
        reader.write_text(
            "import sys\nprint(repr(sys.stdin.read()))\n",
            encoding="utf-8",
        )
        client = CoreClient((sys.executable, str(reader)))
        result = await client.call("version", "--json", timeout=5)
        self.assertEqual(result.stdout.strip(), "''")

    async def test_document_uses_catalog_binding_without_execution(self) -> None:
        catalog = {"catalog_digest": "sha256:" + "a" * 64}
        action = {
            "command_id": "manuals.overview",
            "action_digest": "sha256:" + "b" * 64,
            "risk": "read-only",
            "document": "modules/manuals/README.md",
            "options": [],
        }
        output = await self.client.open_document(catalog, action)
        self.assertIn("manuals document", output.stdout)
        arguments = self.recorded_calls()[0]
        self.assertNotIn("--execute", arguments)
        self.assertNotIn("--review-json", arguments)
        self.assertIn("--expect-catalog-digest", arguments)
        self.assertIn("--expect-action-digest", arguments)


class InputAndDigestTests(unittest.TestCase):
    def test_review_mode_rejects_profile_and_java_selection(self) -> None:
        with self.assertRaises(ValueError):
            SetupInputs(mode="review", workspace="/tmp/example", java_home="/tmp/jdk").option_args()
        with self.assertRaises(ValueError):
            SetupInputs(mode="full", workspace="/tmp/example").option_args()

    def test_bound_command_keeps_unicode_values_as_one_json_argument(self) -> None:
        digest = "sha256:" + "f" * 64
        catalog = {"catalog_digest": digest}
        action = {
            "command_id": "workspace.open",
            "action_digest": digest,
            "options": [{"key": "workspace"}],
        }
        args = CoreClient._bound_command(
            catalog, action, {"workspace": "/tmp/space and/世界; $(whoami)"}
        )
        self.assertIn("workspace:=\"/tmp/space and/世界; $(whoami)\"", args)
        with self.assertRaisesRegex(CoreClientError, "not declared"):
            CoreClient._bound_command(catalog, action, {"secret": "value"})
        with self.assertRaisesRegex(CoreClientError, "digest is invalid"):
            CoreClient._bound_command(
                {"catalog_digest": "wrong"}, action, {"workspace": "/tmp/example"}
            )


if __name__ == "__main__":
    unittest.main()
