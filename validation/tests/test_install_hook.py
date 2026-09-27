"""First-run behavior of the generated Linux hook without a system Python."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import render_install_hook  # noqa: E402


FAKE_PYTHON = b"""#!/bin/sh
set -eu
while [ "$#" -gt 0 ]; do
    case "$1" in
        -I|-E|-s|-B) shift ;;
        *) break ;;
    esac
done
script=$1
printf '%s\\n' "$script" >> "$TEST_PYTHON_LOG"
case "$script" in
    -c)
        exit 0
        ;;
    -)
        exit 0
        ;;
    */verify_install_bundle.py)
        printf 'verified\\n' >> "$TEST_ACTION_LOG"
        exit 0
        ;;
    */wheelhouse/install_workbench.py)
        shift
        destination=
        while [ "$#" -gt 0 ]; do
            if [ "$1" = --destination ]; then
                destination=$2
                break
            fi
            shift
        done
        [ -n "$destination" ] || exit 2
        mkdir -p "$destination/bin"
        if [ "${TEST_INSTALL_FAIL:-}" = 1 ]; then
            printf '{"state": "failed"}\\n' > "$destination/workbench-install.json"
            printf 'install failed\\n' >> "$TEST_ACTION_LOG"
            exit 1
        fi
        printf '{"state": "installed"}\\n' > "$destination/workbench-install.json"
        printf '#!/bin/sh\\n[ "$1" = version ] || exit 2\\nprintf "{}\\\\n"\\n' > "$destination/bin/workbench"
        chmod +x "$destination/bin/workbench"
        printf '#!/bin/sh\\nexit 0\\n' > "$destination/bin/workbench-tui"
        chmod +x "$destination/bin/workbench-tui"
        printf 'installed\\n' >> "$TEST_ACTION_LOG"
        exit 0
        ;;
esac
exit 2
"""


FAKE_CURL = """#!/bin/sh
set -eu
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output=$2; shift 2 ;;
        https://*) url=$1; shift ;;
        *) shift ;;
    esac
done
[ -n "$output" ] && [ -n "$url" ] || exit 2
printf '%s\\n' "$url" >> "$TEST_DOWNLOAD_LOG"
[ "${TEST_CURL_FAIL:-}" != 1 ] || exit 3
case "$url" in
    */astral-sh/python-build-standalone/*) cp "$TEST_RUNTIME_ARCHIVE" "$output" ;;
    */orthrus-research/Workbench/releases/download/*) cp "$TEST_BUNDLE_DOWNLOAD" "$output" ;;
    *) exit 4 ;;
esac
"""


FAKE_UNAME = """#!/bin/sh
case "$1" in
    -s) printf '%s\\n' "${TEST_HOST_OS:-Linux}" ;;
    -m) printf '%s\\n' "${TEST_HOST_MACHINE:-x86_64}" ;;
    *) exit 2 ;;
esac
"""


FAKE_MV = """#!/bin/sh
set -eu
for argument do
    if [ "${TEST_FAIL_BUNDLE_MOVE:-}" = 1 ] \
        && [ "$argument" = "$HOME/.local/share/workbench/bundles/test-v1" ]; then
        printf 'bundle move failed\\n' >> "$TEST_ACTION_LOG"
        exit 7
    fi
done
exec "$TEST_REAL_MV" "$@"
"""


@unittest.skipUnless(sys.platform == "linux" and Path("/bin/sh").is_file(),
                     "the released hook targets Linux /bin/sh")
class InstallHookTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="workbench-hook-test-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.outside_checkout = self.root / "empty-cwd"
        self.outside_checkout.mkdir()
        self.commands = self.root / "commands"
        self.commands.mkdir()
        for name in (
            "sha256sum", "awk", "tar", "gzip", "mktemp", "mkdir", "rmdir",
            "rm", "mv", "grep", "ln", "chmod", "cp", "basename",
        ):
            executable = shutil.which(name)
            self.assertIsNotNone(executable, name)
            (self.commands / name).symlink_to(executable)
        self.real_mv = shutil.which("mv")
        self._command("mv", FAKE_MV)
        self._command("curl", FAKE_CURL)
        self._command("uname", FAKE_UNAME)
        self._command(
            "getconf",
            "#!/bin/sh\nprintf '%s\\n' \"${TEST_HOST_LIBC:-glibc 2.39}\"\n",
        )
        self.assertIsNone(shutil.which("python3", path=str(self.commands)))
        self.assertIsNone(shutil.which("python", path=str(self.commands)))
        self.assertIsNone(shutil.which("pixi", path=str(self.commands)))

        self.runtime_archive = self.root / "runtime.tar.gz"
        self._archive(self.runtime_archive, {"python/bin/python3.14": (FAKE_PYTHON, 0o755)})
        self.bad_runtime = self.root / "bad-runtime.tar.gz"
        self.bad_runtime.write_bytes(self.runtime_archive.read_bytes() + b"changed")
        self.bundle_archive = self.root / "workbench-linux-x64-py314-test.tar.gz"
        self._archive(self.bundle_archive, {
            "workbench-linux-x64-py314/verify_install_bundle.py": (b"# stubbed by runtime fixture\n", 0o644),
            "workbench-linux-x64-py314/wheelhouse/install_workbench.py": (b"# stubbed by runtime fixture\n", 0o644),
            "workbench-linux-x64-py314/axiom/workbench-axiom-engine-0.1.0.zip": (b"fixture archive", 0o644),
        })
        self.bad_bundle = self.root / "bad-bundle.tar.gz"
        self.bad_bundle.write_bytes(self.bundle_archive.read_bytes() + b"changed")
        descriptor = {
            "format": "workbench-install-bundle-descriptor-v1",
            "qualified": False,
            "target": {"python": "3.14", "platform": "linux", "machine": "x86_64"},
            "release_tag": "test-v1",
            "archive_filename": self.bundle_archive.name,
            "bundle_directory": "workbench-linux-x64-py314",
            "archive_sha256": self._sha(self.bundle_archive),
        }
        self.descriptor = self.root / "descriptor.json"
        self.descriptor.write_text(json.dumps(descriptor), encoding="utf-8")
        pin = {
            "format": "workbench-python-bootstrap-v1",
            "target": descriptor["target"],
            "version": "3.14.7",
            "runtime_id": "cpython-3.14.7+20260924",
            "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260924/fake.tar.gz",
            "sha256": self._sha(self.runtime_archive),
        }
        self.pin = self.root / "python-pin.json"
        self.pin.write_text(json.dumps(pin), encoding="utf-8")
        self.hook = self.root / "hook-output" / "workbench-install-linux-x64.sh"
        # The bundle verifier has separate real-archive coverage. This fixture
        # stubs only its admission so the hook can run against tiny local tars.
        with patch("assemble_install_bundle.verify_bundle_archive", return_value={}), \
                patch.object(render_install_hook, "verify_bundle_archive", return_value={}, create=True):
            render_install_hook.render(
                self.descriptor, self.hook, python_pin=self.pin,
                configuration_home=self.root / "config",
            )
        self.download_log = self.root / "downloads.log"
        self.python_log = self.root / "python.log"
        self.action_log = self.root / "actions.log"

    def _command(self, name: str, body: str) -> None:
        command = self.commands / name
        if command.is_symlink():
            command.unlink()
        command.write_text(body, encoding="utf-8")
        command.chmod(0o755)

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _archive(path: Path, files: dict[str, tuple[bytes, int]]) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, (body, mode) in files.items():
                record = tarfile.TarInfo(name)
                record.mode = mode
                record.size = len(body)
                archive.addfile(record, io.BytesIO(body))

    def invoke(self, **settings: str) -> subprocess.CompletedProcess[bytes]:
        environment = {
            "PATH": str(self.commands),
            "HOME": str(self.home),
            "TEST_RUNTIME_ARCHIVE": str(self.runtime_archive),
            "TEST_BUNDLE_DOWNLOAD": str(self.bundle_archive),
            "TEST_DOWNLOAD_LOG": str(self.download_log),
            "TEST_PYTHON_LOG": str(self.python_log),
            "TEST_ACTION_LOG": str(self.action_log),
            "TEST_REAL_MV": str(self.real_mv),
            **settings,
        }
        # The hook receives its whole program on stdin, as with curl | sh.
        # Its cwd and PATH contain no Workbench source or Python executable.
        return subprocess.run(
            ["/bin/sh"], input=self.hook.read_bytes(), cwd=self.outside_checkout,
            env=environment, capture_output=True, check=False,
        )

    @property
    def install_root(self) -> Path:
        return self.home / ".local/share/workbench"

    def test_fresh_host_fetches_managed_python_and_installs_bundle(self) -> None:
        self.assertFalse((self.outside_checkout / "pixi.toml").exists())
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr.decode())
        destination = self.install_root / "installs/test-v1"
        self.assertEqual("installed", json.loads((destination / "workbench-install.json").read_text())["state"])
        hook_receipt = json.loads((destination / "workbench-hook.json").read_text())
        self.assertEqual("installed", hook_receipt["state"])
        self.assertEqual(self._sha(self.bundle_archive), hook_receipt["archive_sha256"])
        self.assertTrue((destination / "bin/workbench").is_file())
        self.assertTrue((destination / "bin/workbench-tui").is_file())
        self.assertEqual(["verified", "installed"], self.action_log.read_text().splitlines())
        self.assertEqual(2, len(self.download_log.read_text().splitlines()))
        self.assertTrue((self.install_root / "runtimes/cpython-3.14.7+20260924/python/bin/python3.14").is_file())
        self.assertTrue((self.install_root / "bundles/test-v1/verify_install_bundle.py").is_file())

    def test_supersymmetry_client_hook_reports_only_shipped_features(self) -> None:
        descriptor = json.loads(self.descriptor.read_text())
        descriptor["format"] = "workbench-install-bundle-descriptor-v2"
        descriptor["edition"] = "supersymmetry-client"
        self.descriptor.write_text(json.dumps(descriptor), encoding="utf-8")
        with patch("assemble_install_bundle.verify_bundle_archive", return_value={}), \
                patch.object(render_install_hook, "verify_bundle_archive", return_value={}, create=True):
            rendered = render_install_hook.render(
                self.descriptor,
                self.root / "client-hook" / "workbench-install-linux-x64.sh",
                python_pin=self.pin, configuration_home=self.root / "config",
            )
        self.hook = Path(rendered["hook"])
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr.decode())
        output = result.stdout.decode()
        self.assertIn("guided Supersymmetry setup", output)
        self.assertNotIn("IDE clients:", output)
        self.assertIn(
            f"Axiom engine ZIP: {self.install_root}/bundles/test-v1/axiom/workbench-axiom-engine-0.1.0.zip",
            output,
        )

    def test_changed_managed_python_archive_is_rejected_before_extraction(self) -> None:
        result = self.invoke(TEST_RUNTIME_ARCHIVE=str(self.bad_runtime))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("differs from the reviewed SHA-256", result.stderr.decode())
        self.assertEqual(1, len(self.download_log.read_text().splitlines()))
        self.assertFalse((self.install_root / "runtimes/cpython-3.14.7+20260924").exists())
        self.assertFalse((self.install_root / "installs/test-v1").exists())
        self.assertFalse(self.python_log.exists())
        self.assertFalse(self.action_log.exists())

    def test_changed_download_is_rejected_before_bundle_verifier_or_install(self) -> None:
        result = self.invoke(TEST_BUNDLE_DOWNLOAD=str(self.bad_bundle))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("differs from the reviewed SHA-256", result.stderr.decode())
        self.assertFalse((self.install_root / "installs/test-v1").exists())
        self.assertFalse(self.action_log.exists())
        self.assertFalse((self.install_root / "bin/workbench").exists())

    def test_unsupported_host_is_rejected_before_download_or_mutation(self) -> None:
        result = self.invoke(TEST_HOST_MACHINE="aarch64")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("requires Linux x64", result.stderr.decode())
        self.assertFalse(self.download_log.exists())
        self.assertFalse(self.install_root.exists())

    def test_older_glibc_is_rejected_before_download_or_mutation(self) -> None:
        result = self.invoke(TEST_HOST_LIBC="glibc 2.27")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("requires GNU libc 2.28 or newer", result.stderr.decode())
        self.assertFalse(self.download_log.exists())
        self.assertFalse(self.install_root.exists())

    def test_linked_data_home_parent_is_rejected_before_download_or_mutation(self) -> None:
        redirected = self.root / "redirected-data"
        redirected.mkdir()
        (self.home / ".local").symlink_to(redirected, target_is_directory=True)
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("install path crosses a link", result.stderr.decode())
        self.assertFalse(self.download_log.exists())
        self.assertFalse(self.action_log.exists())
        self.assertEqual([], list(redirected.iterdir()))
        self.assertFalse(self.install_root.exists())

    def test_repeat_is_noop_without_download_or_receipt_change(self) -> None:
        first = self.invoke()
        self.assertEqual(0, first.returncode, first.stderr.decode())
        destination = self.install_root / "installs/test-v1"
        receipt = (destination / "workbench-install.json").read_bytes()
        hook_receipt = (destination / "workbench-hook.json").read_bytes()
        downloads = self.download_log.read_bytes()
        second = self.invoke(TEST_CURL_FAIL="1")
        self.assertEqual(0, second.returncode, second.stderr.decode())
        self.assertIn("already installed", second.stdout.decode())
        self.assertIn(
            f"Axiom engine ZIP: {self.install_root}/bundles/test-v1/axiom/workbench-axiom-engine-0.1.0.zip",
            second.stdout.decode(),
        )
        self.assertEqual(receipt, (destination / "workbench-install.json").read_bytes())
        self.assertEqual(hook_receipt, (destination / "workbench-hook.json").read_bytes())
        self.assertEqual(downloads, self.download_log.read_bytes())

    def test_failed_install_is_retained_without_active_launcher(self) -> None:
        result = self.invoke(TEST_INSTALL_FAIL="1")
        self.assertNotEqual(0, result.returncode)
        destination = self.install_root / "installs/test-v1"
        self.assertEqual("failed", json.loads((destination / "workbench-install.json").read_text())["state"])
        self.assertFalse((self.install_root / "bin/workbench").exists())
        self.assertIn("install failed", self.action_log.read_text())

    def test_failed_bundle_retention_cannot_be_mistaken_for_completed_install(self) -> None:
        first = self.invoke(TEST_FAIL_BUNDLE_MOVE="1")
        self.assertNotEqual(0, first.returncode)
        destination = self.install_root / "installs/test-v1"
        self.assertEqual("installed", json.loads((destination / "workbench-install.json").read_text())["state"])
        self.assertFalse((destination / "workbench-hook.json").exists())
        self.assertFalse((self.install_root / "bundles/test-v1").exists())
        self.assertFalse((self.install_root / "bin/workbench").exists())
        self.assertEqual(["verified", "installed", "bundle move failed"], self.action_log.read_text().splitlines())

        # The wheelhouse receipt alone cannot certify the outer bundle work.
        repeat = self.invoke(TEST_CURL_FAIL="1")
        self.assertNotEqual(0, repeat.returncode)
        self.assertIn("incomplete", repeat.stderr.decode())
        self.assertNotIn("already installed", repeat.stdout.decode())
        self.assertFalse((destination / "workbench-hook.json").exists())


if __name__ == "__main__":
    unittest.main()
