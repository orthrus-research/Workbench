"""Focused tests for the direct dedicated-server SUSY developer slice."""

from __future__ import annotations

import base64
from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/workbench-shell/src"
PROJECT_INTELLIGENCE = ROOT / "modules/project-intelligence/src"
ATLAS = ROOT / "modules/atlas/src"
BLUEPRINTS = ROOT / "modules/blueprints/src"
TESTS = Path(__file__).resolve().parent

for path in (TESTS, ATLAS, BLUEPRINTS, PROJECT_INTELLIGENCE, SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from workbench_shell.susy_mod_dev import RESULT_FORMAT, _digest, _load_pack  # noqa: E402
from workbench_shell.susy_mod_server import (  # noqa: E402
    SusyModServerError,
    launch_susy_mod_server,
    render_susy_mod_server,
)
import workbench_shell.susy_mod_server as susy_mod_server  # noqa: E402
import test_susy_server_materialize as materialize_fixture  # noqa: E402


RUN_ID = "susy-mod-20260820T120000000000Z-abcdef123456"
PLAN_ID = "workbench-susy-mod-dev-plan:" + "2" * 64
BASELINE = b"retained Packwiz baseline"


def _write(path: Path, data: bytes | str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_json(path: Path, value: dict) -> None:
    _write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _candidate_jar() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "mcmod.info",
            json.dumps(
                [
                    {
                        "modid": "sample",
                        "name": "Sample",
                        "version": "1.0",
                        "mcversion": "1.12.2",
                    }
                ]
            ),
        )
        archive.writestr("sample/Main.class", bytes.fromhex("cafebabe00000034"))
    return output.getvalue()


CANDIDATE = _candidate_jar()


def _probe_only_java() -> str:
    return """\
#!/bin/sh
java_home_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cat >&2 <<EOF
Property settings:
    java.version = 25.0.4
    java.runtime.version = 25.0.4+7-LTS
    java.vendor = Eclipse Adoptium
    java.vendor.version = Temurin-25.0.4+7
    java.home = $java_home_dir
    java.vm.name = OpenJDK 64-Bit Server VM
    java.vm.version = 25.0.4+7-LTS
    os.arch = amd64
openjdk version "25.0.4"
EOF
exit 0
"""


def _fake_server_java() -> str:
    """A directly executed JVM stand-in with real stdin and process custody."""

    return r'''#!/bin/sh
set -eu
mode=${WORKBENCH_FAKE_SERVER_MODE:-pass}
probe_metadata=
for argument in "$@"; do
  case "$argument" in
    -javaagent:*=*) probe_metadata=${argument#*=} ;;
  esac
done
printf '%s\n' "$@" > fake-argv.txt
pwd > fake-cwd.txt
printf 'HOME=%s\nTMPDIR=%s\n' "$HOME" "$TMPDIR" > fake-environment.txt

if [ "$mode" = nonzero ]; then
  exit 37
fi
if [ "$mode" = timeout ]; then
  child_pid=
  cleanup_timeout() {
    if [ -n "$child_pid" ]; then
      kill "$child_pid" 2>/dev/null || true
      wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
  }
  trap cleanup_timeout TERM INT
  sleep 60 &
  child_pid=$!
  printf '%s\n' "$child_pid" > fake-child.pid
  wait "$child_pid"
  exit 0
fi

mkdir -p logs
if [ "$mode" = fatal ]; then
  printf '%s\n' '[12:00:00] [main/FATAL] [Foundation]: Unable to launch because startup failed' > logs/latest.log
  exit 42
fi
if [ "$mode" = crash ]; then
  mkdir -p crash-reports
  printf '%s\n' '---- Minecraft Crash Report ----' > crash-reports/crash.txt
  exit 1
fi

if [ "$mode" != missing-proof ]; then
  WORKBENCH_FAKE_PROBE="$probe_metadata" WORKBENCH_FAKE_PID="$$" \
    /usr/bin/python3 - <<'PY'
import base64
import json
import os
from pathlib import Path

metadata = json.loads(base64.urlsafe_b64decode(os.environ["WORKBENCH_FAKE_PROBE"]))
mode = os.environ.get("WORKBENCH_FAKE_SERVER_MODE", "pass")
pid = int(os.environ["WORKBENCH_FAKE_PID"])
nonce = metadata["nonce"]
if mode == "wrong-proof":
    nonce += "-replayed"
if mode == "wrong-pid":
    pid += 1
source = str((Path.cwd() / "mods" / metadata["filename"]).resolve())
proof = {
    "format": "workbench-forge-loaded-source-probe-v1",
    "nonce": nonce,
    "process": f"{pid}@fake-server",
    "pid": pid,
    "mods": [
        {
            "mod_id": mod_id,
            "version": "1.0",
            "source_path": source,
            "sha256": metadata["sha256"],
            "size": metadata["size"],
        }
        for mod_id in metadata["mod_ids"]
    ],
}
target = Path(metadata["output"])
target.parent.mkdir(parents=True, exist_ok=True)
temporary = target.with_name(target.name + ".tmp")
temporary.write_text(json.dumps(proof, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(target)
PY
fi

ready_logger=minecraft/DedicatedServer
if [ "$mode" = current-ready ]; then
  ready_logger=net.minecraft.server.dedicated.DedicatedServer
elif [ "$mode" = near-miss-ready ]; then
  ready_logger=net.minecraft.server.dedicated.DedicatedServerWrapper
fi
if [ "$mode" = gregtech-fatal ]; then
  printf '%s\n' '[12:00:00] [Server thread/FATAL] [GregTech Core]: Seems like invalid recipe, retaining diagnostics' > logs/latest.log
else
  : > logs/latest.log
fi
cat >> logs/latest.log <<EOF
[12:00:00] [Server thread/INFO] [FML]: Forge Mod Loader has successfully loaded 2 mods
[12:00:00] [Server thread/INFO] [$ready_logger]: Done (1.000s)! For help, type "help" or "?"
EOF
if [ "$mode" != missing-pack-ready ]; then
  printf '%s\n' '[12:00:00] [Server thread/INFO] [FTB Library]: Reloaded server in 3ms' >> logs/latest.log
fi
if [ "$mode" = missing-groovy-evidence ]; then
  rm -f logs/groovy_server.log
elif [ "$mode" = groovy-script-failure ]; then
  printf '%s\n' 'An exception occurred while running scripts.' > logs/groovy_server.log
else
  printf '%s\n' 'GroovyScript server scripts completed.' > logs/groovy_server.log
fi

if [ "$mode" = spontaneous-zero ]; then
  exit 0
fi
if IFS= read -r command; then
  printf '%s\n' "$command" > fake-stop-command.txt
  shutdown_server=minecraft/MinecraftServer
  if [ "$mode" = current-ready ]; then
    shutdown_server=net.minecraft.server.MinecraftServer
  fi
  cat >> logs/latest.log <<EOF
[12:00:01] [Server thread/INFO] [$ready_logger]: Stopping the server
[12:00:01] [Server thread/INFO] [$shutdown_server]: Stopping server
[12:00:01] [Server thread/INFO] [$shutdown_server]: Saving players
[12:00:01] [Server thread/INFO] [$shutdown_server]: Saving worlds
EOF
fi
if [ "$mode" = post-ready-crash ]; then
  mkdir -p crash-reports
  printf '%s\n' '---- Minecraft Crash Report ----' > crash-reports/after-ready.txt
  exit 0
fi
if [ "$mode" = post-ready-fatal ]; then
  printf '%s\n' '[12:00:02] [Server thread/ERROR] [net.minecraft.server.MinecraftServer]: Encountered an unexpected exception' >> logs/latest.log
  exit 0
fi
if [ "$mode" = post-ready-shutdown-exception ]; then
  cat >> logs/latest.log <<'EOF'
[12:00:02] [Server thread/ERROR] [minecraft/MinecraftServer]: Exception stopping the server
java.lang.NullPointerException: ForgeChunkManager.requestTicket
EOF
  exit 0
fi
if [ "$mode" = shutdown-nonzero ]; then
  exit 23
fi
if [ "$mode" = shutdown-timeout ]; then
  sleep 60
fi
exit 0
'''


def _uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise AssertionError(f"expected file URI, observed {uri!r}")
    return Path(unquote(parsed.path))


def _snapshot(root: Path) -> dict[str, tuple[str, int] | str]:
    rows: dict[str, tuple[str, int] | str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            rows[relative] = "link:" + os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            raw = path.read_bytes()
            rows[relative] = (sha256(raw).hexdigest(), len(raw))
        elif stat.S_ISDIR(info.st_mode):
            rows[relative] = "directory"
        else:
            rows[relative] = "special"
    return rows


def _reseal_result(result: dict) -> None:
    material = {key: value for key, value in result.items() if key != "result_id"}
    result["result_id"] = "workbench-susy-mod-dev-result:" + _digest(material)


class _RetainedBuild:
    """A passed build plus a distinct reusable dedicated-server template."""

    def __init__(self, root: Path, *, side: str = "both") -> None:
        self.root = root
        self.suite = root / "suite"
        self.run_root = self.suite / ".workbench/dev-runs" / RUN_ID
        self.candidate = self.run_root / "source/build/libs/sample-1.0.jar"
        self.overlay_candidate = (
            self.run_root / "supersymmetry-overlay/mods/sample-1.0.jar"
        )
        self.pack = root / "pack"
        self.project = root / "project"
        self.template = root / "server-template"
        self.suite.mkdir(parents=True)
        self.project.mkdir()
        self.pack.mkdir()
        index_bytes = b""
        _write(self.pack / "index.toml", index_bytes)
        _write(
            self.pack / "pack.toml",
            "\n".join(
                (
                    'name = "Supersymmetry"',
                    'author = "SymmetricDevs"',
                    'version = "test"',
                    'pack-format = "packwiz:1.1.0"',
                    "[index]",
                    'file = "index.toml"',
                    'hash-format = "sha256"',
                    f'hash = "{sha256(index_bytes).hexdigest()}"',
                    "[versions]",
                    'minecraft = "1.12.2"',
                    'forge = "14.23.5.2860"',
                    "",
                )
            ),
        )
        _write(
            self.pack / "mods/sample.pw.toml",
            "\n".join(
                (
                    'name = "Sample"',
                    'filename = "sample-1.0.jar"',
                    f'side = "{side}"',
                    "[download]",
                    'hash-format = "sha1"',
                    f'hash = "{sha1(BASELINE).hexdigest()}"',
                    "",
                )
            ),
        )
        _write(
            self.pack / "mods/required-server-mod.pw.toml",
            "\n".join(
                (
                    'name = "Required Server Mod"',
                    'filename = "required-server-mod.jar"',
                    'side = "server"',
                    "[download]",
                    'hash-format = "sha1"',
                    f'hash = "{sha1(b"required mod").hexdigest()}"',
                    "",
                )
            ),
        )
        _write(self.root / "jdk/bin/java", _probe_only_java(), executable=True)
        _write(self.root / "jdk/bin/javac", "#!/bin/sh\nexit 0\n", executable=True)
        _write(self.candidate, CANDIDATE)
        _write(self.overlay_candidate, CANDIDATE)
        _write(
            self.run_root / "source-manifest.json",
            '{"format":"fixture-source-manifest"}\n',
        )
        _write(self.template / "cleanroom-0.6.8-alpha.jar", b"cleanroom server")
        _write(self.template / "minecraft_server.1.12.2.jar", b"minecraft server")
        _write(self.template / "libraries/example/library.jar", b"library")
        _write(self.template / "mods/sample-1.0.jar", BASELINE)
        _write(self.template / "mods/required-server-mod.jar", b"required mod")
        _write(self.template / "config/pack.cfg", "enabled=true\n")
        _write(self.template / "eula.txt", "eula=true\n")
        _write(
            self.template / "server.properties",
            "online-mode=false\nserver-ip=127.0.0.1\nserver-port=25565\n",
        )

        applicable_sides = ["client", "server"] if side == "both" else [side]
        artifact = {
            "path": str(self.candidate),
            "size": len(CANDIDATE),
            "sha256": sha256(CANDIDATE).hexdigest(),
            "mod_ids": ["sample"],
            "mod_metadata": [
                {
                    "modid": "sample",
                    "name": "Sample",
                    "version": "1.0",
                    "mcversion": "1.12.2",
                }
            ],
            "classfile_majors": [52],
            "mixin_configs": [],
            "refmaps": [],
            "manifest": {},
            "selection_score": 135,
        }
        self.result = {
            "format": RESULT_FORMAT,
            "schema_version": 1,
            "run_id": RUN_ID,
            "outcome": "passed",
            "failed_stage": None,
            "plan_id": PLAN_ID,
            "project": {
                "root": str(self.project),
                "name": "Sample",
                "archive_base": "sample",
                "mod_ids": ["sample"],
                "minecraft_version": "1.12.2",
            },
            "supersymmetry": {
                key: value
                for key, value in _load_pack(self.pack).items()
                if key != "entries"
            },
            "replacement": {
                "match": {
                    "state": "exact",
                    "reason": "source-identity",
                    "selected": {
                        "metadata_path": "mods/sample.pw.toml",
                        "filename": "sample-1.0.jar",
                        "side": side,
                        "baseline": {
                            "hash_format": "sha1",
                            "hash": sha1(BASELINE).hexdigest(),
                        },
                    },
                },
                "applicable_sides": applicable_sides,
            },
            "source_snapshot": {
                "root_uri": (self.run_root / "source").as_uri(),
                "manifest_uri": (self.run_root / "source-manifest.json").as_uri(),
                "source_digest": "sha256:" + "4" * 64,
                "file_count": 1,
                "total_bytes": len(CANDIDATE),
                "managed_build_overlays": [],
            },
            "stages": [
                {
                    "id": "project-build",
                    "state": "passed",
                    "exit_code": 0,
                    "timed_out": False,
                }
            ],
            "artifact_set": [artifact],
            "overlay": {
                "root_uri": (self.run_root / "supersymmetry-overlay").as_uri(),
                "metadata_path": "mods/sample.pw.toml",
                "baseline_filename": "sample-1.0.jar",
                "baseline_hash": {
                    "hash_format": "sha1",
                    "hash": sha1(BASELINE).hexdigest(),
                },
                "candidate_path": "mods/sample-1.0.jar",
                "candidate_sha256": sha256(CANDIDATE).hexdigest(),
                "candidate_size": len(CANDIDATE),
                "applicable_sides": applicable_sides,
                "runtime_installed_and_loaded": False,
            },
            "runtime": {"client": None, "server": None},
            "problems": [],
            "cleanup": {
                "owned_processes_running": False,
                "checkout_mutated_by_workbench": False,
                "managed_run_root": str(self.run_root),
            },
            "next_actions": [],
            "limitations": [],
        }
        _reseal_result(self.result)
        self.write_result()

    def write_result(self) -> None:
        _write_json(self.run_root / "result.json", self.result)

    def reseal(self) -> None:
        _reseal_result(self.result)
        self.write_result()


class SusyModServerPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = _RetainedBuild(Path(self.temporary.name))
        self.template_before = _snapshot(self.fixture.template)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def launch(self, **kwargs):
        return launch_susy_mod_server(
            self.fixture.suite,
            RUN_ID,
            server_template=self.fixture.template,
            server_java=self.fixture.root / "jdk/bin/java",
            timeout_seconds=0.1,
            shutdown_timeout_seconds=0.1,
            poll_interval_seconds=0.001,
            **kwargs,
        )

    def assert_rejected_before_process(self) -> None:
        with patch.object(susy_mod_server.subprocess, "Popen") as popen:
            with self.assertRaises(SusyModServerError):
                self.launch()
        popen.assert_not_called()
        self.assertEqual(self.template_before, _snapshot(self.fixture.template))

    def test_client_only_candidate_is_rejected_before_server_or_template_mutation(
        self,
    ) -> None:
        fixture = _RetainedBuild(self.fixture.root / "client-only", side="client")
        before = _snapshot(fixture.template)
        with patch.object(susy_mod_server.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(SusyModServerError, "server|side"):
                launch_susy_mod_server(
                    fixture.suite,
                    RUN_ID,
                    server_template=fixture.template,
                    server_java=fixture.root / "jdk/bin/java",
                )
        popen.assert_not_called()
        self.assertEqual(before, _snapshot(fixture.template))

    def test_candidate_drift_is_rejected_before_process(self) -> None:
        _write(self.fixture.candidate, b"candidate changed after build")
        self.assert_rejected_before_process()

    def test_packwiz_baseline_drift_is_rejected_before_process(self) -> None:
        _write(self.fixture.template / "mods/sample-1.0.jar", b"wrong baseline")
        self.template_before = _snapshot(self.fixture.template)
        self.assert_rejected_before_process()

    def test_unrelated_server_mod_drift_is_rejected_before_process(self) -> None:
        _write(
            self.fixture.template / "mods/required-server-mod.jar",
            b"unrelated server mod drift",
        )
        self.template_before = _snapshot(self.fixture.template)
        self.assert_rejected_before_process()

    def test_template_symlink_is_rejected_before_process(self) -> None:
        outside = self.fixture.root / "outside.cfg"
        _write(outside, "outside=true\n")
        link = self.fixture.template / "config/linked.cfg"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.template_before = _snapshot(self.fixture.template)
        self.assert_rejected_before_process()

    def test_runtime_parent_symlink_cannot_redirect_server_evidence(self) -> None:
        outside = self.fixture.root / "outside-runtime"
        outside.mkdir()
        runtime = self.fixture.run_root / "runtime"
        try:
            runtime.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assert_rejected_before_process()
        self.assertEqual([], list(outside.iterdir()))

    def test_inverse_susycore_overlay_rejects_conflicting_reccomplex_experiment(
        self,
    ) -> None:
        output = b"inverse SusyCore overlay output"
        _write(self.fixture.template / "mods/required-server-mod.jar", output)
        _write_json(
            self.fixture.template
            / "workbench-inputs/susy-reccomplex-overlay-receipt-v1.json",
            {
                "format": "workbench-cleanroom-compatibility-overlay-receipt-v1",
                "schema_version": 1,
                "overlay_id": "susy-reccomplex-modify-variable-name-v1",
                "profile_state": "provisional",
                "input_artifact_sha256": "1" * 64,
                "input_entry_sha256": "2" * 64,
                "output_artifact_sha256": sha256(output).hexdigest(),
                "output_entry_sha256": "3" * 64,
                "overlay_manifest_sha256": "4" * 64,
                "target_entry": "fixture/StructureSpawnContextMixin.class",
            },
        )
        before = _snapshot(self.fixture.template)
        with patch.object(susy_mod_server.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(SusyModServerError, "conflicts"):
                self.launch(
                    compatibility_experiments=("susy-reccomplex-arg3",)
                )
        popen.assert_not_called()
        self.assertEqual(before, _snapshot(self.fixture.template))


class SusyModServerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._bind_fixture(_RetainedBuild(self.root / "default"))

    def _bind_fixture(self, fixture: _RetainedBuild) -> None:
        self.fixture = fixture
        self.java = self.fixture.root / "jdk/bin/java"
        _write(self.java, _fake_server_java(), executable=True)
        self.template_before = _snapshot(self.fixture.template)
        self.result_before = (self.fixture.run_root / "result.json").read_bytes()
        self.candidate_before = self.fixture.candidate.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _compile_probe(self, mutation=None):
        def compile_probe(
            build_root: Path,
            *,
            projected_instance: Path,
            java_executable: Path,
            launcher_host: dict,
            nonce: str,
            expected_mod_ids: list[str],
        ):
            self.assertEqual(java_executable, self.java)
            self.assertEqual(launcher_host["os"], "linux")
            if mutation is not None:
                mutation()
            candidate_filename = Path(
                self.fixture.result["overlay"]["candidate_path"]
            ).name
            build_root.mkdir(parents=True)
            jar = build_root / "workbench-candidate-loaded-agent.jar"
            _write(jar, b"fixture java agent")
            proof = (
                projected_instance
                / ".workbench/candidate-loaded-probe/loaded-source-v1.json"
            )
            metadata = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "filename": candidate_filename,
                        "mod_ids": sorted(expected_mod_ids),
                        "nonce": nonce,
                        "output": str(proof),
                        "sha256": sha256(
                            (
                                projected_instance
                                / ".minecraft/mods"
                                / candidate_filename
                            ).read_bytes()
                        ).hexdigest(),
                        "size": (
                            projected_instance
                            / ".minecraft/mods"
                            / candidate_filename
                        ).stat().st_size,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).decode("ascii")
            projected_jar = (
                projected_instance
                / ".workbench/candidate-loaded-probe/"
                "workbench-candidate-loaded-agent.jar"
            )
            agent_path = str(projected_jar)
            javaagent = (
                f'-javaagent:"{agent_path}"={metadata}'
                if any(character.isspace() for character in agent_path)
                else f"-javaagent:{agent_path}={metadata}"
            )
            return (
                {
                    "format": "fixture-loaded-source-probe-build-v1",
                    "jar_sha256": sha256(jar.read_bytes()).hexdigest(),
                    "jar_size": jar.stat().st_size,
                },
                proof,
                javaagent,
                jar,
            )

        return compile_probe

    def launch(self, mode: str = "pass", *, mutation=None, **kwargs):
        with (
            patch.object(susy_mod_server, "ensure_java_runtime", return_value={}),
            patch.object(
                susy_mod_server,
                "_selected_java",
                return_value=(self.java, {"probe": {"runtime_version": "25.0.4"}}),
            ),
            patch.object(
                susy_mod_server,
                "_compile_probe_agent",
                side_effect=self._compile_probe(mutation),
            ),
            patch.dict(os.environ, {"WORKBENCH_FAKE_SERVER_MODE": mode}),
        ):
            return launch_susy_mod_server(
                self.fixture.suite,
                RUN_ID,
                server_template=self.fixture.template,
                server_java=self.java,
                timeout_seconds=kwargs.pop("timeout_seconds", 2.0),
                shutdown_timeout_seconds=kwargs.pop(
                    "shutdown_timeout_seconds", 1.0
                ),
                poll_interval_seconds=kwargs.pop("poll_interval_seconds", 0.01),
                **kwargs,
            )

    def assert_inputs_unchanged(self) -> None:
        self.assertEqual(self.template_before, _snapshot(self.fixture.template))
        self.assertEqual(
            self.result_before,
            (self.fixture.run_root / "result.json").read_bytes(),
        )
        self.assertEqual(self.candidate_before, self.fixture.candidate.read_bytes())

    def test_direct_owned_server_ready_exact_proof_and_clean_stop_pass(self) -> None:
        real_popen = susy_mod_server.subprocess.Popen
        calls: list[tuple[tuple, dict]] = []

        def owned_popen(*args, **kwargs):
            calls.append((args, dict(kwargs)))
            return real_popen(*args, **kwargs)

        with patch.object(
            susy_mod_server.subprocess, "Popen", side_effect=owned_popen
        ):
            result = self.launch()

        self.assertEqual("passed", result["outcome"])
        receipt = result["receipt"]
        self.assertEqual("passed", receipt["outcome"])
        self.assertEqual(receipt["process"]["pid"], receipt["loaded_source_proof"]["pid"])
        self.assertEqual(0, receipt["process"]["exit_code"])
        self.assertTrue(receipt["process"]["clean_stop"])
        self.assertTrue(receipt["process"]["stop_command_attempted"])
        self.assertTrue(receipt["process"]["stop_command_sent"])
        self.assertTrue(
            receipt["process"]["shutdown_acknowledgment"]["complete"]
        )
        self.assertTrue(receipt["claims"]["exact_candidate_loaded"])
        self.assertTrue(receipt["claims"]["dedicated_server_ready"])
        self.assertTrue(receipt["claims"]["clean_shutdown"])
        self.assertFalse(receipt["claims"]["shutdown_bridge_observed"])
        self.assertFalse(receipt["claims"]["client_server_parity"])
        self.assertTrue(receipt["claims"]["runtime_health_clean"])
        self.assertFalse(receipt["cleanup"]["owned_processes_running"])
        self.assertEqual([], receipt["cleanup"]["errors"])
        self.assertTrue(all(receipt["immutable_inputs"].values()))
        projection = _uri_path(receipt["projection"]["root_uri"])
        server = projection / ".minecraft"
        self.assertEqual("stop\n", (server / "fake-stop-command.txt").read_text())
        self.assertEqual(str(server.resolve()), (server / "fake-cwd.txt").read_text().strip())
        self.assertEqual(CANDIDATE, (server / "mods/sample-1.0.jar").read_bytes())
        self.assertEqual(1, len(calls))
        argv = calls[0][0][0]
        options = calls[0][1]
        self.assertIsInstance(argv, list)
        self.assertNotIn("shell", options)
        self.assertTrue(options["start_new_session"])
        self.assertIs(options["stdin"], susy_mod_server.subprocess.PIPE)
        self.assertEqual(server, options["cwd"])
        self.assertNotIn("JAVA_TOOL_OPTIONS", options["env"])
        self.assertNotIn("_JAVA_OPTIONS", options["env"])
        self.assertNotIn("JDK_JAVA_OPTIONS", options["env"])
        self.assert_inputs_unchanged()
        rendered = render_susy_mod_server(result)
        self.assertIn("SUSY dedicated-server load/stop smoke: passed", rendered)
        self.assertIn("Loaded proof: exact Forge source JAR", rendered)
        self.assertIn("Template pack: 2 server entries", rendered)
        self.assertIn("index matched", rendered)
        self.assertIn("Pack diagnostics: no-error-or-fatal-observed", rendered)

        receipt["template"]["pack_binding"]["index"][
            "matches_declared_hash"
        ] = False
        receipt["template"]["pack_binding"]["materialized_index"] = {
            "actual_sha256": "a" * 64,
            "declared_hash": "a" * 64,
        }
        managed_rendered = render_susy_mod_server(result)
        self.assertIn("source index required refresh", managed_rendered)
        self.assertIn("refreshed index verified", managed_rendered)
        self.assertNotIn("index drifted", managed_rendered)

    def test_managed_v2_launch_preserves_default_off_server_option(self) -> None:
        fixture = materialize_fixture._MaterializationFixture(
            self.root / "managed-v2",
            server_optionals=True,
        )
        materialized = fixture.materialize()
        materialization_receipt = materialized["receipt"]
        fixture.template = _uri_path(
            materialization_receipt["target"]["template_uri"]
        )
        self._bind_fixture(fixture)

        self.assertFalse(
            (self.fixture.template / "mods/server-optional-off.jar").exists()
        )
        self.assertTrue(
            (self.fixture.template / "mods/server-optional-on.jar").is_file()
        )

        result = self.launch(
            _managed_materialization=materialization_receipt,
        )

        self.assertEqual(result["outcome"], "passed")
        receipt = result["receipt"]
        provenance = receipt["template"]["provenance"]
        self.assertEqual(
            provenance["state"],
            "managed-susy-server-materialization",
        )
        self.assertEqual(provenance["schema_version"], 2)
        self.assertEqual(
            provenance["materialization_id"],
            materialization_receipt["materialization_id"],
        )
        self.assertEqual(
            provenance["receipt_uri"],
            materialization_receipt["target"]["receipt_uri"],
        )
        self.assertEqual(
            receipt["template"]["pack_binding"]["server_entry_count"],
            4,
        )
        projection = _uri_path(receipt["projection"]["root_uri"])
        projected_server = projection / ".minecraft"
        self.assertFalse(
            (projected_server / "mods/server-optional-off.jar").exists()
        )
        self.assertTrue(
            (projected_server / "mods/server-optional-on.jar").is_file()
        )
        self.assertEqual(
            (projected_server / "mods/sample.jar").read_bytes(),
            materialize_fixture.CANDIDATE,
        )
        self.assertTrue(receipt["claims"]["exact_candidate_loaded"])
        self.assertTrue(receipt["claims"]["dedicated_server_ready"])
        self.assert_inputs_unchanged()

    def test_pack_baseline_subject_runs_without_substituting_candidate(self) -> None:
        result = self.launch(_subject="pack-baseline")

        self.assertEqual("passed", result["outcome"])
        receipt = result["receipt"]
        self.assertEqual("pack-baseline", receipt["runtime_subject"])
        self.assertEqual("pack-baseline-control", receipt["candidate"]["role"])
        self.assertEqual(sha256(BASELINE).hexdigest(), receipt["candidate"]["sha256"])
        self.assertTrue(receipt["claims"]["exact_runtime_subject_loaded"])
        self.assertFalse(receipt["claims"]["exact_candidate_loaded"])
        projected = _uri_path(receipt["projection"]["root_uri"]) / ".minecraft"
        self.assertEqual(BASELINE, (projected / "mods/sample-1.0.jar").read_bytes())
        self.assert_inputs_unchanged()

    def test_missing_or_wrong_loaded_source_proof_cannot_pass(self) -> None:
        for mode, expected_kind in (
            ("missing-proof", "candidate-source-proof-timeout"),
            ("wrong-proof", "candidate-source-proof-invalid"),
            ("wrong-pid", "candidate-source-proof-wrong-process"),
        ):
            with self.subTest(mode=mode):
                self._bind_fixture(_RetainedBuild(self.root / ("proof-" + mode)))
                result = self.launch(mode, timeout_seconds=0.15)
                self.assertEqual("failed", result["outcome"])
                self.assertEqual(expected_kind, result["receipt"]["failure_kind"])
                self.assertIsNone(result["receipt"]["loaded_source_proof"])
                self.assertFalse(
                    result["receipt"]["cleanup"]["owned_processes_running"]
                )
                self.assert_inputs_unchanged()

    def test_crash_nonzero_and_timeout_are_retained_failures_with_cleanup(self) -> None:
        cases = (
            ("crash", "minecraft-crash-report"),
            ("nonzero", "server-exited-before-ready"),
            ("fatal", "fatal-startup-log"),
            ("timeout", "server-ready-timeout"),
        )
        for mode, expected_kind in cases:
            with self.subTest(mode=mode):
                self._bind_fixture(_RetainedBuild(self.root / ("failure-" + mode)))
                result = self.launch(
                    mode,
                    timeout_seconds=0.15,
                    shutdown_timeout_seconds=0.1,
                )
                receipt = result["receipt"]
                self.assertEqual("failed", result["outcome"])
                self.assertEqual(expected_kind, receipt["failure_kind"])
                self.assertFalse(receipt["cleanup"]["owned_processes_running"])
                if mode == "timeout":
                    projection = _uri_path(receipt["projection"]["root_uri"])
                    child_pid = int(
                        (projection / ".minecraft/fake-child.pid").read_text()
                    )
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                self.assert_inputs_unchanged()

    def test_nonzero_after_ready_is_not_a_clean_pass(self) -> None:
        result = self.launch("shutdown-nonzero")
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("server-clean-stop-failed", result["receipt"]["failure_kind"])
        self.assertEqual(23, result["receipt"]["process"]["exit_code"])
        self.assertFalse(result["receipt"]["process"]["clean_stop"])
        self.assert_inputs_unchanged()

    def test_zero_exit_without_stop_acceptance_or_lifecycle_ack_cannot_pass(
        self,
    ) -> None:
        result = self.launch("spontaneous-zero")
        receipt = result["receipt"]
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("server-clean-stop-failed", receipt["failure_kind"])
        self.assertFalse(receipt["process"]["stop_command_sent"])
        self.assertFalse(receipt["process"]["clean_stop"])
        self.assertFalse(
            receipt["process"]["shutdown_acknowledgment"]["complete"]
        )
        self.assert_inputs_unchanged()

    def test_post_ready_crash_or_terminal_log_cannot_pass_even_with_zero_exit(
        self,
    ) -> None:
        for mode, failure_kind in (
            ("post-ready-crash", "minecraft-crash-report-after-ready"),
            ("post-ready-fatal", "fatal-log-after-ready"),
            ("post-ready-shutdown-exception", "fatal-log-after-ready"),
            ("groovy-script-failure", "groovy-script-failure"),
        ):
            with self.subTest(mode=mode):
                self._bind_fixture(_RetainedBuild(self.root / mode))
                result = self.launch(mode)
                receipt = result["receipt"]
                self.assertEqual("failed", result["outcome"])
                self.assertEqual(failure_kind, receipt["failure_kind"])
                if mode == "groovy-script-failure":
                    self.assertTrue(receipt["process"]["clean_stop"])
                    self.assertTrue(
                        receipt["diagnostics"]["groovy_server"][
                            "script_failure_observed"
                        ]
                    )
                else:
                    self.assertFalse(receipt["process"]["clean_stop"])
                self.assertTrue(receipt["diagnostics"]["smoke_outcome_affected"])
                if mode == "post-ready-shutdown-exception":
                    self.assertEqual(
                        "try-susy-server-shutdown-bridge",
                        result["next_actions"][0]["id"],
                    )
                self.assert_inputs_unchanged()

    def test_susy_pack_ready_marker_is_required_after_minecraft_ready(self) -> None:
        result = self.launch("missing-pack-ready", timeout_seconds=0.15)
        receipt = result["receipt"]
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("susy-pack-ready-timeout", receipt["failure_kind"])
        self.assertIsNone(receipt["checkpoint"])
        self.assertFalse(receipt["claims"]["supersymmetry_pack_ready"])
        self.assert_inputs_unchanged()

    def test_susy_groovy_script_evidence_is_required(self) -> None:
        result = self.launch("missing-groovy-evidence")
        receipt = result["receipt"]
        self.assertEqual("failed", result["outcome"])
        self.assertEqual(
            "groovy-script-evidence-missing", receipt["failure_kind"]
        )
        self.assertFalse(receipt["diagnostics"]["groovy_server"]["available"])
        self.assertTrue(receipt["process"]["clean_stop"])
        self.assert_inputs_unchanged()

    def test_keyboard_interrupt_is_retained_and_owned_group_is_cleaned(self) -> None:
        original_latest = susy_mod_server._latest_text
        calls = 0

        def interrupt_once(path: Path) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            return original_latest(path)

        with patch.object(
            susy_mod_server, "_latest_text", side_effect=interrupt_once
        ):
            result = self.launch(
                "timeout", timeout_seconds=1.0, shutdown_timeout_seconds=0.1
            )

        receipt = result["receipt"]
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("server-launch-cancelled", receipt["failure_kind"])
        self.assertFalse(receipt["process"]["stop_command_sent"])
        self.assertFalse(receipt["cleanup"]["owned_processes_running"])
        self.assertFalse(
            (self.fixture.run_root / "runtime/server-launch.lock").exists()
        )
        self.assert_inputs_unchanged()

    def test_sigterm_is_converted_to_retained_cancellation_and_cleanup(self) -> None:
        original_latest = susy_mod_server._latest_text
        installed: dict[int, object] = {}
        fired = False

        def remember_handler(signum, handler):
            installed[signum] = handler

        def terminate_once(path: Path) -> str:
            nonlocal fired
            if not fired:
                fired = True
                installed[signal.SIGTERM](signal.SIGTERM, None)
            return original_latest(path)

        with (
            patch.object(signal, "signal", side_effect=remember_handler),
            patch.object(
                susy_mod_server, "_latest_text", side_effect=terminate_once
            ),
        ):
            result = self.launch(
                "timeout", timeout_seconds=1.0, shutdown_timeout_seconds=0.1
            )

        receipt = result["receipt"]
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("server-launch-cancelled", receipt["failure_kind"])
        self.assertIn("SIGTERM", receipt["detail"])
        self.assertFalse(receipt["cleanup"]["owned_processes_running"])
        self.assertFalse(
            (self.fixture.run_root / "runtime/server-launch.lock").exists()
        )
        self.assert_inputs_unchanged()

    def test_gregtech_recipe_fatal_diagnostic_does_not_mask_ready_and_proof(
        self,
    ) -> None:
        result = self.launch("gregtech-fatal")
        self.assertEqual("passed", result["outcome"])
        receipt = result["receipt"]
        self.assertIsNotNone(receipt["checkpoint"])
        self.assertIsNotNone(receipt["loaded_source_proof"])
        self.assertEqual(0, receipt["process"]["exit_code"])
        self.assertTrue(receipt["process"]["clean_stop"])
        self.assertEqual("issues-observed", receipt["diagnostics"]["state"])
        self.assertEqual(1, receipt["diagnostics"]["entry_counts"]["fatal"])
        self.assertFalse(receipt["claims"]["runtime_health_clean"])
        self.assert_inputs_unchanged()

    def test_direct_javaagent_path_with_spaces_is_one_unquoted_argv_item(self) -> None:
        self._bind_fixture(_RetainedBuild(self.root / "workspace with spaces"))
        result = self.launch()
        self.assertEqual("passed", result["outcome"])
        agent_arguments = [
            argument
            for argument in result["receipt"]["command"]
            if argument.startswith("-javaagent:")
        ]
        self.assertEqual(1, len(agent_arguments))
        self.assertNotIn('"', agent_arguments[0])
        self.assertIn("workspace with spaces", agent_arguments[0])
        self.assert_inputs_unchanged()

    def test_only_historical_and_current_cleanroom_ready_loggers_are_accepted(
        self,
    ) -> None:
        for mode, logger in (
            ("pass", "minecraft/DedicatedServer"),
            ("current-ready", "net.minecraft.server.dedicated.DedicatedServer"),
        ):
            with self.subTest(mode=mode):
                self._bind_fixture(_RetainedBuild(self.root / ("ready-" + mode)))
                result = self.launch(mode)
                self.assertEqual("passed", result["outcome"])
                self.assertIn(logger, result["receipt"]["checkpoint"]["marker"])
                self.assert_inputs_unchanged()

        self._bind_fixture(_RetainedBuild(self.root / "ready-near-miss"))
        result = self.launch("near-miss-ready", timeout_seconds=0.15)
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("server-ready-timeout", result["receipt"]["failure_kind"])
        self.assertIsNone(result["receipt"]["checkpoint"])
        self.assertIsNone(result["receipt"]["loaded_source_proof"])
        self.assert_inputs_unchanged()

    def test_template_drift_during_attempt_is_detected_and_never_mutates_projection_source(
        self,
    ) -> None:
        def mutate_template() -> None:
            _write(self.fixture.template / "config/pack.cfg", "changed-during-launch\n")

        result = self.launch(mutation=mutate_template)
        self.assertEqual("failed", result["outcome"])
        self.assertEqual(
            "input-or-cleanup-integrity-failed", result["receipt"]["failure_kind"]
        )
        self.assertFalse(
            result["receipt"]["immutable_inputs"]["server_template_unchanged"]
        )
        self.assertEqual(self.result_before, (self.fixture.run_root / "result.json").read_bytes())
        self.assertEqual(self.candidate_before, self.fixture.candidate.read_bytes())

if __name__ == "__main__":
    unittest.main()
