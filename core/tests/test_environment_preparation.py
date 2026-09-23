import json
import shutil
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from workbench_api.prism import encode_launch_script
from workbench_core import check_storage as storage
from workbench_core import environment_preparation as prep
from workbench_core.host_services import install_local_host_services
from workbench_core.prism_capture import normalize_handoff
from workbench_core.render import Renderer
from workbench_core.runner import supervise_process
from workbench_core.sessions import RetainedSession


class SilentRenderer(Renderer):
    def consume(self, event):
        pass


class PreparationLifecycleTests(unittest.TestCase):
    def setUp(self):
        install_local_host_services()
        self.temp = tempfile.TemporaryDirectory(prefix="workbench lifecycle ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = storage.initialize(self.base / "private")

    def plan(self):
        java = self.base / "selected-jdk/bin/java"
        java.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(sys.executable).resolve(), java)
        java.chmod(0o755)
        (java.parent.parent / "release").write_text(
            'JAVA_RUNTIME_VERSION="25.0.4+7-LTS"\nIMPLEMENTOR="Eclipse Adoptium"\n'
        )
        accounts = self.base / "accounts.json"
        accounts.write_text('{"accounts":[{"secret":"account-canary"}]}')
        source = {"pack.toml": b"saved bytes"}
        return prep.plan(
            self.root,
            source=source,
            source_rows=[
                {
                    "path": "pack.toml",
                    "size": 11,
                    "sha256": sha256(source["pack.toml"]).hexdigest(),
                    "mode": 0o100644,
                }
            ],
            candidate_id="candidate",
            binding={"pack": "test"},
            requirements={
                "java": {"runtime_version": "25.0.4+7", "vendor": "Eclipse Adoptium"}
            },
            dependencies={"artifacts": []},
            tools={"prism": java, "packwiz": java, "java": java},
            accounts=accounts,
            seeds=[],
            context={
                "selection": {"pack_uri": "file:///selected"},
                "selection_id": "selection",
            },
            provider={"id": "profile"},
            excluded_roots=[],
        )

    def test_plan_captures_source_without_copying_credentials_or_executing(self):
        request = self.plan()
        self.assertEqual(request["state"], "planned-not-run")
        self.assertNotIn("account-canary", json.dumps(request))
        attempt = prep.attempt_path(self.root, request["attempt_id"])
        self.assertEqual((attempt / "source/pack.toml").read_bytes(), b"saved bytes")
        self.assertFalse((attempt / "started.json").exists())
        self.assertFalse(
            (self.root / ".workbench/tmp" / request["attempt_id"]).exists()
        )
        with self.assertRaisesRegex(ValueError, "confirmation"):
            prep.execute(
                self.root,
                request["attempt_id"],
                "wrong",
                validate_image=lambda *_: None,
            )

    def test_changed_tool_rejects_before_execution(self):
        request = self.plan()
        Path(request["tools"]["java"]["path"]).write_bytes(
            b"not the selected executable"
        )
        with self.assertRaises(ValueError):
            prep.execute(
                self.root,
                request["attempt_id"],
                request["id"],
                validate_image=lambda *_: None,
            )
        self.assertFalse(
            (
                prep.attempt_path(self.root, request["attempt_id"]) / "started.json"
            ).exists()
        )

    def test_failed_phase_is_retained_without_exception_secrets(self):
        request = self.plan()
        with patch.object(prep, "_command", side_effect=ValueError("account-canary")):
            result = prep.execute(
                self.root,
                request["attempt_id"],
                request["id"],
                validate_image=lambda *_: None,
            )
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["cleanup"]["state"], "trashed")
        self.assertNotIn("account-canary", json.dumps(result))
        self.assertTrue((self.base / "accounts.json").exists())
        with self.assertRaisesRegex(ValueError, "already started"):
            prep.execute(
                self.root,
                request["attempt_id"],
                request["id"],
                validate_image=lambda *_: None,
            )

    def test_fresh_phases_use_current_progress_contract_without_capturing_secrets(self):
        request = self.plan()
        attempt = prep.attempt_path(self.root, request["attempt_id"])
        work = self.root / ".workbench/tmp" / request["attempt_id"]
        work.mkdir()
        for label in ("refresh", "install", "prism"):
            with self.subTest(label=label):
                result = prep._command(
                    self.root, attempt, work, label,
                    [sys.executable, "-c", "import os; print(os.environ['PREPARATION_CANARY'])"],
                    cwd=work, environment={"PREPARATION_CANARY": "account-canary"},
                    cancelled=lambda: False,
                    capture=False,
                )
                self.assertEqual(result["state"], "closed")
                self.assertEqual(result["stop_reason"], "process-exited")
                self.assertTrue(result["observations"])
                for record in result["observations"]:
                    self.assertEqual(record["request_id"], request["id"])
                    self.assertEqual(record["attempt_id"], label)
                self.assertTrue((attempt / label / "observations/0001.json").is_file())
                console = Path(result["console"]["session"]["retention"]["directory"])
                self.assertNotIn(b"account-canary", (console / "stdout.raw").read_bytes())
        self.assertFalse((attempt / "observations").exists())

    def test_preparation_phase_cancellation_still_closes_owned_process(self):
        request = self.plan()
        attempt = prep.attempt_path(self.root, request["attempt_id"])
        work = self.root / ".workbench/tmp" / request["attempt_id"]
        work.mkdir()
        with self.assertRaisesRegex(ValueError, "phase failed or stopped"):
            prep._command(
                self.root, attempt, work, "refresh",
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=work, environment={}, cancelled=lambda: True,
            )
        execution = storage.read_json(attempt / "refresh/execution.json")
        self.assertEqual(execution["state"], "closed")
        self.assertEqual(execution["stop_reason"], "cancelled")

    def test_verified_image_reuse_runs_no_upstream_processes(self):
        request = self.plan()
        source = self.base / "installed"
        source.mkdir()
        (source / "fixture").write_text("payload")
        java = Path(request["tools"]["java"]["path"])
        image = storage.import_image(
            self.root,
            source,
            java,
            ["fixture"],
            request["binding"],
            excluded_roots=[],
            toolchain_root=java.parent.parent,
        )
        with patch.object(
            prep, "_command", side_effect=AssertionError("must not execute")
        ):
            result = prep.execute(
                self.root,
                request["attempt_id"],
                request["id"],
                validate_image=lambda *_: None,
            )
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["image_id"], image["id"])
        self.assertEqual(set(result["phases"].values()), {"not-started"})

    def test_recovery_refuses_uncertain_custody_before_removing_accounts(self):
        request = self.plan()
        attempt = prep.attempt_path(self.root, request["attempt_id"])
        (attempt / "prism").mkdir()
        work = self.root / ".workbench/tmp" / request["attempt_id"]
        (work / "prism-data").mkdir(parents=True)
        copied = work / "prism-data/accounts.json"
        copied.write_text("account-canary")
        with patch.object(
            prep, "recover_execution", side_effect=ValueError("uncertain custody")
        ):
            with self.assertRaisesRegex(ValueError, "custody"):
                prep.recover(self.root, request["attempt_id"], request["id"])
        self.assertTrue(copied.exists())
        with patch.object(prep, "recover_execution", return_value={"state": "closed"}):
            result = prep.recover(self.root, request["attempt_id"], request["id"])
        self.assertEqual(result["state"], "recovered-incomplete")
        self.assertFalse(copied.exists())


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workbench preparation ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = storage.initialize(self.base / "private")
        self.instance = self.base / "prism-data/instances/test"
        (self.instance / ".minecraft").mkdir(parents=True)
        (self.instance / "natives").mkdir()
        self.launcher = self.base / "NewLaunch.jar"
        self.launcher.write_bytes(b"entry point")
        self.library = self.base / "prism-data/libraries/one.jar"
        self.library.parent.mkdir()
        self.library.write_bytes(b"library")
        self.policy = {
            "main_class": "example.Main",
            "prism_version": "11.1.0",
            "jvm_arguments": [],
            "minecraft_version": "1.12.2",
            "asset_index": "1.12",
            "tweakers": ["example.Tweaker"],
            "prism_entrypoint_sha256": sha256(self.launcher.read_bytes()).hexdigest(),
        }
        self.request = {
            "id": "request",
            "requirements": self.policy,
            "java": "/native/java",
            "instance": str(self.instance),
            "prism_data": str(self.base / "prism-data"),
        }
        self.argv = [
            "/native/java",
            "-Xms512m",
            "-Xmx4096m",
            "-Djava.library.path=" + str(self.instance / "natives"),
            "-cp",
            str(self.launcher) + ":" + str(self.library),
            "org.prismlauncher.EntryPoint",
        ]
        self.fields = {
            "mainClass": "example.Main",
            "launcher": "standard",
            "launcherVersion": "11.1.0",
            "userName": "private-email",
            "sessionId": "private-session",
            "param": [
                "--username",
                "private-name",
                "--uuid",
                "private-uuid",
                "--accessToken",
                "private-token",
                "--userType",
                "msa",
                "--version",
                "1.12.2",
                "--gameDir",
                str(self.instance / ".minecraft"),
                "--assetsDir",
                str(self.base / "prism-data/assets"),
                "--assetIndex",
                "1.12",
                "--tweakClass",
                "example.Tweaker",
            ],
        }

    def test_sanitized_handoff_never_retains_account_values(self):
        value = normalize_handoff(
            self.argv, encode_launch_script(self.fields), self.request
        )
        self.assertNotIn("private-", json.dumps(value))
        self.assertFalse(value["game_executed"])
        self.assertIn("Workbench", value["protocol"]["param"])

    def test_assembly_preserves_launcher_library_paths_and_relative_protocol(self):
        handoff = normalize_handoff(
            self.argv, encode_launch_script(self.fields), self.request
        )
        assets = Path(handoff["assets"])
        assets.mkdir()
        (assets / "index").write_text("assets")
        (Path(handoff["natives"]) / "native.so").write_text("native")
        work = self.base / "assembly"
        (work / "payload").mkdir(parents=True)
        arguments = prep._assemble(work, self.request, handoff)
        self.assertEqual(
            arguments[-2], "libraries/workbench-prism/NewLaunch.jar:libraries/one.jar"
        )
        self.assertEqual((work / "payload/libraries/one.jar").read_bytes(), b"library")
        text = (work / "payload/workbench-launch.txt").read_text()
        self.assertIn("param --gameDir\nparam .\n", text)
        self.assertNotIn("private-", text)
        self.assertNotIn(str(self.base), text)

    def test_unknown_parameters_entrypoint_drift_and_external_libraries_reject(self):
        raw = encode_launch_script(
            {
                **self.fields,
                "param": self.fields["param"] + ["--unknown", "private-token"],
            }
        )
        with self.assertRaises(ValueError) as caught:
            normalize_handoff(self.argv, raw, self.request)
        self.assertNotIn("private-token", str(caught.exception))
        self.launcher.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            normalize_handoff(
                self.argv, encode_launch_script(self.fields), self.request
            )

    def test_contained_jdk_file_links_are_flattened_but_escaping_links_reject(self):
        toolchain = self.base / "jdk"
        toolchain.mkdir()
        (toolchain / "real").write_bytes(b"real")
        (toolchain / "linked").symlink_to("real")
        rows = storage.tree_manifest(toolchain, contained_file_links=True)
        destination = self.base / "copied-jdk"
        storage.copy_manifest(toolchain, destination, rows, contained_file_links=True)
        self.assertFalse((destination / "linked").is_symlink())
        (toolchain / "escape").symlink_to(self.library)
        with self.assertRaises(ValueError):
            storage.tree_manifest(toolchain, contained_file_links=True)

    def test_launcher_output_is_discarded_and_input_survives_custody_gate(self):
        protocol = self.base / "input.txt"
        protocol.write_text("launcher input\n")
        argv = [
            sys.executable,
            "-c",
            "import sys; assert sys.stdin.read() == 'launcher input\\n'; print('private-token'); print('private-email', file=sys.stderr)",
        ]
        session = RetainedSession(
            root=self.root,
            command_id="fixture.input",
            argv=argv,
            cwd=self.base,
            intent="execute",
        )
        result = supervise_process(
            argv,
            cwd=self.base,
            root=self.root,
            session=session,
            renderer=SilentRenderer(),
            source="fixture",
            input_file=protocol,
            capture_output=False,
            require_group_closure=True,
        )
        self.assertEqual(result.effective_exit_code, 0)
        self.assertEqual((session.directory / "stdout.raw").read_bytes(), b"")
        self.assertEqual((session.directory / "stderr.raw").read_bytes(), b"")
        # The test's argv contains the canary; real launcher argv never does.
        self.assertTrue(
            any("discarded" in value for value in session.value["limitations"])
        )

    def test_input_change_during_custody_is_not_executed(self):
        protocol = self.base / "input.txt"
        protocol.write_text("admitted")
        marker = self.base / "executed"
        argv = [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ]
        session = RetainedSession(
            root=self.root,
            command_id="fixture.changed-input",
            argv=argv,
            cwd=self.base,
            intent="execute",
        )
        bind = session.bind_process

        def change(*args):
            protocol.write_text("changed")
            bind(*args)

        with patch.object(session, "bind_process", side_effect=change):
            result = supervise_process(
                argv,
                cwd=self.base,
                root=self.root,
                session=session,
                renderer=SilentRenderer(),
                source="fixture",
                input_file=protocol,
            )
        self.assertNotEqual(result.effective_exit_code, 0)
        self.assertFalse(marker.exists())

    def test_qsettings_config_quotes_spaces_without_shell_expansion(self):
        path = self.base / "instance.cfg"
        prep._config(
            path,
            {
                "WrapperCommand": '"/a path/python" "capture.py" "request.json"',
                "JavaPath": "/jdk path/bin/java",
            },
        )
        self.assertIn('JavaPath="/jdk path/bin/java"', path.read_text())
        self.assertIn('\\"/a path/python\\"', path.read_text())
