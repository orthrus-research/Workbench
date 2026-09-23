"""Focused tests for retained-input SUSY server materialization."""

from __future__ import annotations

from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
SHELL_SOURCE = ROOT / "modules/workbench-shell/src"
PROJECT_INTELLIGENCE = ROOT / "modules/project-intelligence/src"
for source in (PROJECT_INTELLIGENCE, SHELL_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.runtime_materialize import (  # noqa: E402
    materialize_packwiz_workspace_v2,
)
from workbench_shell.runtime_plan import plan_project_runtime  # noqa: E402
from workbench_shell.susy_mod_dev import (  # noqa: E402
    RESULT_FORMAT,
    _digest,
    _load_pack,
    _runtime_tree,
)
from workbench_shell.susy_server_materialize import (  # noqa: E402
    SusyServerMaterializationError,
    materialize_susy_server,
    render_susy_server_materialization,
)
import workbench_shell.susy_server_materialize as server_materialize  # noqa: E402


RUN_ID = "susy-mod-20260820T120000000000Z-abcdef123456"
PLAN_ID = "workbench-susy-mod-dev-plan:sha256:" + "2" * 64
BASELINE = b"retained Packwiz baseline\n"
CANDIDATE = b"retained developer candidate\n"
RESTRICTED = b"restricted both-side artifact from the canonical client\n"
CLIENT_ONLY = b"client-only artifact\n"
SERVER_ONLY = b"server-only downloadable artifact\n"
SERVER_OPTIONAL_ON = b"server optional default-on artifact\n"
SERVER_OPTIONAL_OFF = b"server optional default-off artifact\n"
DIRECT = b"shared=true\n"


def _write(
    path: Path,
    data: bytes | str,
    *,
    executable: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_json(path: Path, value: dict) -> None:
    _write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )


def _uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise AssertionError(f"expected file URI, observed {uri!r}")
    return Path(unquote(parsed.path))


def _run(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def _snapshot(root: Path) -> dict[str, object]:
    rows: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            rows[relative] = "link:" + os.readlink(path)
        elif stat.S_ISDIR(info.st_mode):
            rows[relative] = "directory"
        elif stat.S_ISREG(info.st_mode):
            raw = path.read_bytes()
            rows[relative] = (sha256(raw).hexdigest(), len(raw))
        else:
            rows[relative] = "special"
    return rows


def _reseal_stage(stage: dict) -> None:
    material = {key: value for key, value in stage.items() if key != "stage_id"}
    stage["stage_id"] = "workbench-susy-mod-client-stage:" + _digest(material)


def _reseal_result(result: dict) -> None:
    material = {key: value for key, value in result.items() if key != "result_id"}
    result["result_id"] = "workbench-susy-mod-dev-result:" + _digest(material)


def _canonical_digest(value: dict) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    ).hexdigest()


def _fake_packwiz(
    path: Path,
    *,
    counter: Path | None = None,
    mutation_flag: Path | None = None,
    source_side_effect: Path | None = None,
) -> None:
    counter = path.with_suffix(".refresh-count") if counter is None else counter
    mutation_flag_value = None if mutation_flag is None else str(mutation_flag)
    source_side_effect_value = (
        None if source_side_effect is None else str(source_side_effect)
    )
    source = f"""\
        #!/usr/bin/env python3
        from hashlib import sha256
        from pathlib import Path
        import re

        counter = Path({str(counter)!r})
        current = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(current + 1), encoding="utf-8")
        mutation_flag = {mutation_flag_value!r}
        source_side_effect = {source_side_effect_value!r}
        if mutation_flag is not None and Path(mutation_flag).exists():
            Path(source_side_effect).write_text(
                "ignored source mutation\\n",
                encoding="utf-8",
            )
        root = Path.cwd()
        records = [("config/common.cfg", False)]
        records.extend(
            (path.relative_to(root).as_posix(), True)
            for path in sorted((root / "mods").glob("*.pw.toml"))
        )
        parts = ['hash-format = "sha256"', ""]
        for relative, metafile in records:
            raw = (root / relative).read_bytes()
            parts.extend([
                "[[files]]",
                f'file = "{{relative}}"',
                f'hash = "{{sha256(raw).hexdigest()}}"',
            ])
            if metafile:
                parts.append("metafile = true")
            parts.append("")
        index = ("\\n".join(parts) + "\\n").encode("utf-8")
        (root / "index.toml").write_bytes(index)
        manifest = (root / "pack.toml").read_text(encoding="utf-8")
        manifest = re.sub(
            r'(?m)^hash = "[0-9a-f]{{64}}"$',
            f'hash = "{{sha256(index).hexdigest()}}"',
            manifest,
            count=1,
        )
        (root / "pack.toml").write_text(manifest, encoding="utf-8")
        print("refreshed")
    """
    _write(path, textwrap.dedent(source).lstrip(), executable=True)


def _fake_java(
    path: Path,
    failure_flag: Path,
    packwiz_failure_flag: Path,
    *,
    late_child_flag: Path | None = None,
) -> None:
    probe_count = path.with_suffix(".probe-count")
    cleanroom_count = path.with_suffix(".cleanroom-count")
    packwiz_count = path.with_suffix(".packwiz-count")
    source = f"""\
        #!/usr/bin/env python3
        from pathlib import Path
        import hashlib
        import json
        import shutil
        import subprocess
        import sys
        import tomllib
        from urllib.parse import unquote, urlparse

        arguments = sys.argv[1:]

        def count(raw):
            target = Path(raw)
            current = int(target.read_text()) if target.exists() else 0
            target.write_text(str(current + 1), encoding="utf-8")

        if "-XshowSettings:properties" in arguments:
            count({str(probe_count)!r})
            home = Path(sys.argv[0]).resolve().parent.parent
            print("Property settings:", file=sys.stderr)
            print("    java.version = 25.0.4", file=sys.stderr)
            print("    java.runtime.version = 25.0.4+7-LTS", file=sys.stderr)
            print("    java.vendor = Eclipse Adoptium", file=sys.stderr)
            print("    java.vendor.version = Temurin-25.0.4+7", file=sys.stderr)
            print(f"    java.home = {{home}}", file=sys.stderr)
            print("    java.vm.name = OpenJDK 64-Bit Server VM", file=sys.stderr)
            print("    java.vm.version = 25.0.4+7-LTS", file=sys.stderr)
            print("    os.arch = amd64", file=sys.stderr)
            raise SystemExit(0)

        install_server = next(
            (item for item in arguments if item.startswith("--install-server=")),
            None,
        )
        if "--install-server" in arguments or install_server is not None:
            count({str(cleanroom_count)!r})
            if Path({str(failure_flag)!r}).exists():
                print("deliberate Cleanroom installer failure", file=sys.stderr)
                raise SystemExit(17)
            if install_server is not None:
                target = Path(install_server.split("=", 1)[1])
            else:
                position = arguments.index("--install-server")
                target = (
                    Path(arguments[position + 1])
                    if position + 1 < len(arguments)
                    and not arguments[position + 1].startswith("-")
                    else Path.cwd()
                )
            (target / "libraries/example").mkdir(parents=True, exist_ok=True)
            (target / "libraries/example/library.jar").write_bytes(b"library")
            (target / "cleanroom-0.6.8-alpha.jar").write_bytes(b"cleanroom")
            (target / "minecraft_server.1.12.2.jar").write_bytes(b"minecraft")
            print("installed Cleanroom server")
            raise SystemExit(0)

        count({str(packwiz_count)!r})
        if Path({str(packwiz_failure_flag)!r}).exists():
            print("deliberate Packwiz installer failure", file=sys.stderr)
            raise SystemExit(23)
        side = arguments[arguments.index("--side") + 1]
        target = Path(arguments[arguments.index("--pack-folder") + 1])
        late_child_flag = {
            None if late_child_flag is None else str(late_child_flag)
        !r}
        if (
            side == "server"
            and late_child_flag is not None
            and Path(late_child_flag).exists()
        ):
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import time; "
                        "time.sleep(2.0); "
                        "Path('late-child.txt').write_text"
                        "('late child mutation\\\\n', encoding='utf-8')"
                    ),
                ],
                cwd=target,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        pack_uri = arguments[-1]
        source_root = Path(unquote(urlparse(pack_uri).path)).parent
        index = tomllib.loads(
            (source_root / "index.toml").read_text(encoding="utf-8")
        )
        state_path = target / "packwiz.json"
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else None
        )
        cached_files = (
            state.get("cachedFiles", {{}})
            if isinstance(state, dict)
            else {{}}
        )
        for record in index["files"]:
            relative = Path(record["file"])
            if not record.get("metafile"):
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_root / relative, destination)
                continue
            metadata = tomllib.loads(
                (source_root / relative).read_text(encoding="utf-8")
            )
            applicable = metadata.get("side", "both")
            if applicable not in ("both", side):
                continue
            option = metadata.get("option")
            cached = cached_files.get(relative.as_posix())
            if (
                isinstance(option, dict)
                and option.get("optional") is True
                and isinstance(cached, dict)
                and cached.get("optionValue") is False
            ):
                continue
            destination = target / relative.parent / metadata["filename"]
            if destination.exists():
                continue
            url = metadata.get("download", {{}}).get("url")
            if not isinstance(url, str) or not url.startswith("file:"):
                print(f"missing restricted seed: {{destination}}", file=sys.stderr)
                raise SystemExit(19)
            payload = Path(unquote(urlparse(url).path))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload, destination)
            if isinstance(cached, dict):
                cached["cachedLocation"] = (
                    relative.parent / metadata["filename"]
                ).as_posix()
        if isinstance(state, dict):
            pack_path = source_root / "pack.toml"
            pack_record = tomllib.loads(pack_path.read_text(encoding="utf-8"))
            state["packFileHash"] = {{
                "type": "sha256",
                "value": hashlib.sha256(pack_path.read_bytes()).hexdigest(),
            }}
            state["indexFileHash"] = {{
                "type": pack_record["index"]["hash-format"],
                "value": pack_record["index"]["hash"],
            }}
            state_path.write_text(
                json.dumps(state, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
        else:
            state_path.write_text(
                '{{"installed": true}}\\n', encoding="utf-8"
            )
        print("Finished successfully!")
    """
    _write(path, textwrap.dedent(source).lstrip(), executable=True)


def _installer(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr("link/infra/packwiz/installer/Main.class", b"test")


def _component_registry(suite: Path) -> None:
    components = [
        ("atlas", "authority", []),
        ("blueprints", "authority", []),
        ("manuals", "authority", []),
        ("project-intelligence", "foundation", []),
        (
            "workbench-shell",
            "foundation",
            ["project-intelligence", "atlas", "blueprints", "manuals"],
        ),
    ]
    rows = []
    for component_id, kind, dependencies in components:
        path = f"modules/{component_id}"
        (suite / path).mkdir(parents=True)
        rows.append(
            {
                "id": component_id,
                "kind": kind,
                "path": path,
                "state": "active",
                "depends_on": dependencies,
            }
        )
    payload = {
        "format": "workbench-component-registry-v2",
        "schema_version": 2,
        "registry_id": "workbench-components",
        "lifecycle_states": [
            "incomplete",
            "ready",
            "active",
            "review",
            "complete",
            "blocked",
        ],
        "constraints": {
            "shell_component": "workbench-shell",
            "project_context_component": "project-intelligence",
            "authority_components": ["atlas", "blueprints", "manuals"],
            "profile_roots": ["profiles/platforms", "profiles/packs"],
        },
        "components": rows,
    }
    _write_json(
        suite / "modules/workbench-shell/data/component-registry-v2.json",
        payload,
    )


class _MaterializationFixture:
    def __init__(
        self,
        root: Path,
        *,
        server_collision: bool = False,
        server_optionals: bool = False,
    ) -> None:
        self.root = root
        self.suite = root / "suite"
        self.pack = root / "pack"
        self.tools = root / "tools"
        self.java = root / "jdk/bin/java"
        self.failure_flag = root / "fail-cleanroom-installer"
        self.packwiz_failure_flag = root / "fail-packwiz-installer"
        self.late_child_flag = root / "spawn-late-installer-child"
        self.source_mutation_flag = root / "mutate-original-pack"
        self.source_side_effect = self.pack / "ignored-side-effect.count"
        self.server_payload = root / "downloads/server-only.jar"
        self.server_optional_on_payload = (
            root / "downloads/server-optional-on.jar"
        )
        self.server_optional_off_payload = (
            root / "downloads/server-optional-off.jar"
        )
        self.client_seed = root / "client-seed"
        self.run_root = self.suite / ".workbench/dev-runs" / RUN_ID
        self.candidate = self.run_root / "source/build/libs/sample.jar"
        self.stage_path = self.run_root / "runtime/client-stage-v1.json"
        self.result_path = self.run_root / "result.json"
        self.server_collision = server_collision
        self.server_optionals = server_optionals
        self._make_tools()
        self._make_suite()
        self._make_pack()
        self._make_client_materialization()
        self._make_retained_stage()

    def _make_tools(self) -> None:
        self.tools.mkdir(parents=True)
        self.packwiz_installer = self.tools / "packwiz-installer.jar"
        self.cleanroom_client = self.tools / "cleanroom-client.zip"
        self.cleanroom_server = self.tools / "cleanroom-server-installer.jar"
        _installer(self.packwiz_installer)
        _write(self.cleanroom_client, b"client bootstrap")
        _write(self.cleanroom_server, b"server installer")
        _write(self.server_payload, SERVER_ONLY)
        _write(self.server_optional_on_payload, SERVER_OPTIONAL_ON)
        _write(self.server_optional_off_payload, SERVER_OPTIONAL_OFF)
        _fake_java(
            self.java,
            self.failure_flag,
            self.packwiz_failure_flag,
            late_child_flag=self.late_child_flag,
        )
        _write(self.java.with_name("javac"), "#!/bin/sh\nexit 0\n", executable=True)

    def _make_suite(self) -> None:
        _component_registry(self.suite)
        _write(self.suite / "workbench.toml", (ROOT / "workbench.toml").read_bytes())
        pack_profile = ROOT / "profiles/packs/supersymmetry/profile.yaml"
        _write(
            self.suite / "profiles/packs/supersymmetry/profile.yaml",
            pack_profile.read_bytes(),
        )
        artifacts = {
            "packwiz_installer": self.packwiz_installer,
            "cleanroom_client": self.cleanroom_client,
            "cleanroom_server": self.cleanroom_server,
        }
        lines = [
            "schema_version: 1",
            "profile_id: workbench-platform:cleanroom:provisional",
            "kind: cleanroom",
            "status: provisional",
            "minecraft_version: 1.12.2",
            "cleanroom_version: 0.6.8-alpha",
            "java:",
            "  runtime: eclipse-temurin-25.0.4+7",
            "  runtime_provision:",
            "    distribution: eclipse-temurin",
            "    feature_version: 25",
            "    release_name: jdk-25.0.4+7",
            "    release_type: ga",
            "    image_type: jdk",
            "    jvm_impl: hotspot",
            "    heap_size: normal",
            "    project: jdk",
            "    vendor: eclipse",
            "    java_vendor: Eclipse Adoptium",
            "    api_base_url: https://api.adoptium.net/v3",
            "runtime_artifacts:",
        ]
        for artifact_id, path in artifacts.items():
            raw = path.read_bytes()
            lines.extend(
                [
                    f"  {artifact_id}:",
                    f"    url: https://example.invalid/{artifact_id}",
                    f'    source_revision: "{"9" * 40}"',
                    f"    size: {len(raw)}",
                    f"    sha256: {sha256(raw).hexdigest()}",
                ]
            )
        _write(
            self.suite / "profiles/platforms/cleanroom/provisional.yaml",
            "\n".join(lines) + "\n",
        )

    def _metadata(
        self,
        path: Path,
        *,
        name: str,
        filename: str,
        side: str,
        payload: bytes,
        url: str | None = None,
        optional_default: bool | None = None,
    ) -> None:
        lines = [
            f'name = "{name}"',
            f'filename = "{filename}"',
            f'side = "{side}"',
            "",
            "[download]",
            'hash-format = "sha1"',
            f'hash = "{sha1(payload).hexdigest()}"',
        ]
        if url is not None:
            lines.append(f'url = "{url}"')
        if optional_default is not None:
            lines.extend(
                [
                    "",
                    "[option]",
                    "optional = true",
                    "default = " + str(optional_default).lower(),
                ]
            )
        lines.extend(
            [
                "",
                "[update]",
                "[update.curseforge]",
                "project-id = 1",
                "file-id = 2",
                "",
            ]
        )
        _write(path, "\n".join(lines))

    def _make_pack(self) -> None:
        for directory in ("config", "groovy", "mods"):
            (self.pack / directory).mkdir(parents=True, exist_ok=True)
        _write(self.pack / "config/common.cfg", DIRECT)
        _write(self.pack / "index.toml", "\n")
        _write(
            self.pack / "pack.toml",
            "\n".join(
                [
                    'name = "Supersymmetry"',
                    'author = "SymmetricDevs"',
                    'version = "test"',
                    'pack-format = "packwiz:1.1.0"',
                    "",
                    "[index]",
                    'file = "index.toml"',
                    'hash-format = "sha256"',
                    f'hash = "{"0" * 64}"',
                    "",
                    "[versions]",
                    'minecraft = "1.12.2"',
                    'forge = "14.23.5.2860"',
                    "",
                ]
            ),
        )
        self._metadata(
            self.pack / "mods/sample.pw.toml",
            name="Sample",
            filename="sample.jar",
            side="both",
            payload=BASELINE,
        )
        self._metadata(
            self.pack / "mods/restricted.pw.toml",
            name="Restricted",
            filename="restricted.jar",
            side="both",
            payload=RESTRICTED,
        )
        self._metadata(
            self.pack / "mods/client.pw.toml",
            name="Client Only",
            filename="client.jar",
            side="client",
            payload=CLIENT_ONLY,
        )
        self._metadata(
            self.pack / "mods/server.pw.toml",
            name="Server Only",
            filename="server.jar",
            side="server",
            payload=SERVER_ONLY,
            url=self.server_payload.as_uri(),
        )
        if self.server_optionals:
            self._metadata(
                self.pack / "mods/server-optional-on.pw.toml",
                name="Server Optional On",
                filename="server-optional-on.jar",
                side="server",
                payload=SERVER_OPTIONAL_ON,
                url=self.server_optional_on_payload.as_uri(),
                optional_default=True,
            )
            self._metadata(
                self.pack / "mods/server-optional-off.pw.toml",
                name="Server Optional Off",
                filename="server-optional-off.jar",
                side="server",
                payload=SERVER_OPTIONAL_OFF,
                url=self.server_optional_off_payload.as_uri(),
                optional_default=False,
            )
        if self.server_collision:
            self._metadata(
                self.pack / "mods/server-collision.pw.toml",
                name="Server Collision",
                filename="SERVER.jar",
                side="server",
                payload=SERVER_ONLY,
                url=self.server_payload.as_uri(),
            )
        self.packwiz = self.pack / "packwiz"
        _fake_packwiz(
            self.packwiz,
            counter=self.tools / "packwiz.refresh-count",
            mutation_flag=self.source_mutation_flag,
            source_side_effect=self.source_side_effect,
        )
        _write(self.pack / ".gitignore", "*.count\n")
        _run(self.pack, "init", "--quiet")
        _run(self.pack, "config", "user.name", "Workbench Test")
        _run(
            self.pack,
            "config",
            "user.email",
            "workbench@example.invalid",
        )
        _run(self.pack, "add", ".")
        _run(self.pack, "commit", "--quiet", "-m", "fixture")
        _write(self.client_seed / "mods/sample.jar", BASELINE)
        _write(self.client_seed / "mods/restricted.jar", RESTRICTED)
        _write(self.client_seed / "mods/client.jar", CLIENT_ONLY)

    def _make_client_materialization(self) -> None:
        if not hasattr(self, "client_plan"):
            self.client_plan = plan_project_runtime(
                self.suite,
                self.pack,
                side="client",
                launcher="prism",
            )
            fixture = _uri_path(
                self.client_plan["target"]["fixture_root_uri"]
            )
            instance = fixture / "instance"
            (fixture / "receipts").mkdir(parents=True)
            instance.mkdir()
            _write(
                instance / "mmc-pack.json",
                json.dumps(
                    {
                        "formatVersion": 1,
                        "components": [
                            {"uid": "net.minecraft", "version": "1.12.2"},
                            {
                                "uid": "net.minecraftforge",
                                "version": "0.6.8-alpha",
                            },
                        ],
                    },
                    sort_keys=True,
                ),
            )
            _write_json(
                fixture / "receipts/cleanroom-client-bootstrap-v1.json",
                {
                    "format": "workbench-runtime-bootstrap-receipt-v1",
                    "schema_version": 1,
                    "state": "materialized",
                    "plan_id": self.client_plan["plan_id"],
                },
            )
        raw_installer = self.packwiz_installer.read_bytes()
        lock = {
            "id": "packwiz_installer",
            "url": "https://example.invalid/packwiz_installer",
            "source_revision": "9" * 40,
            "sha256": sha256(raw_installer).hexdigest(),
            "size": len(raw_installer),
        }
        self.client_materialization = materialize_packwiz_workspace_v2(
            self.client_plan,
            workspace_root=self.pack,
            state_root=self.suite / ".workbench",
            packwiz_executable=self.packwiz,
            java_executable=self.java,
            java_identity={
                "source": "explicit",
                "runtime_id": "sha256:" + "8" * 64,
                "runtime_identity": "eclipse-temurin-25.0.4+7",
                "runtime_version": "25.0.4+7-LTS",
                "vendor": "Eclipse Adoptium",
            },
            installer_path=self.packwiz_installer,
            installer_lock=lock,
            seed_roots=[self.client_seed],
            refresh_timeout_seconds=10,
            install_timeout_seconds=10,
        )
        self.client_receipt = self.client_materialization["receipt"]
        self.canonical_instance = _uri_path(
            self.client_receipt["target"]["instance_root_uri"]
        )
        self.canonical_receipt = _uri_path(
            self.client_receipt["target"]["receipt_uri"]
        )

    def _make_retained_stage(self) -> None:
        _write(self.candidate, CANDIDATE)
        staged_instance = self.run_root / "runtime/client"
        shutil.copytree(self.canonical_instance, staged_instance)
        _write(staged_instance / ".minecraft/mods/sample.jar", CANDIDATE)
        canonical_summary, canonical_records = _runtime_tree(
            self.canonical_instance / ".minecraft"
        )
        staged_summary, _staged_records = _runtime_tree(
            staged_instance / ".minecraft"
        )
        baseline_record = canonical_records["mods/sample.jar"]
        stage = {
            "format": "workbench-susy-mod-client-stage-v1",
            "schema_version": 1,
            "state": "staged",
            "run_id": RUN_ID,
            "plan_id": PLAN_ID,
            "materialization_id": self.client_receipt["materialization_id"],
            "source": {
                "instance_uri": self.canonical_instance.as_uri(),
                "payload": canonical_summary,
                "receipt_uri": self.canonical_receipt.as_uri(),
            },
            "replacement": {
                "path": "mods/sample.jar",
                "baseline": {
                    "hash_format": "sha1",
                    "hash": sha1(BASELINE).hexdigest(),
                    "sha256": baseline_record["sha256"],
                    "size": baseline_record["size"],
                },
                "candidate": {
                    "sha256": sha256(CANDIDATE).hexdigest(),
                    "size": len(CANDIDATE),
                    "mod_ids": ["sample"],
                },
            },
            "target": {
                "instance_uri": staged_instance.as_uri(),
                "payload": staged_summary,
                "changed_paths": ["mods/sample.jar"],
                "launch_ready": True,
                "launched": False,
            },
            "canonical_materialization_mutated": False,
        }
        _reseal_stage(stage)
        public_pack = {
            key: value for key, value in _load_pack(self.pack).items()
            if key != "entries"
        }
        selected = next(
            row
            for row in _load_pack(self.pack)["entries"]
            if row["metadata_path"] == "mods/sample.pw.toml"
        )
        artifact = {
            "path": str(self.candidate),
            "size": len(CANDIDATE),
            "sha256": sha256(CANDIDATE).hexdigest(),
            "mod_ids": ["sample"],
            "mod_metadata": [],
            "classfile_majors": [52],
            "mixin_configs": [],
            "refmaps": [],
            "manifest": {},
            "selection_score": 100,
        }
        self.result = {
            "format": RESULT_FORMAT,
            "schema_version": 1,
            "run_id": RUN_ID,
            "outcome": "passed",
            "failed_stage": None,
            "plan_id": PLAN_ID,
            "project": {
                "root": str(self.root / "project"),
                "name": "Sample",
                "archive_base": "sample",
                "mod_ids": ["sample"],
                "minecraft_version": "1.12.2",
            },
            "supersymmetry": public_pack,
            "replacement": {
                "match": {
                    "state": "exact",
                    "reason": "explicit-pack-entry",
                    "selected": selected,
                },
                "applicable_sides": ["client", "server"],
            },
            "source_snapshot": {
                "root_uri": (self.run_root / "source").as_uri(),
                "manifest_uri": (
                    self.run_root / "source-manifest.json"
                ).as_uri(),
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
                },
                {
                    "id": "client-materialization-and-replacement",
                    "state": "passed",
                    "exit_code": 0,
                    "timed_out": False,
                },
            ],
            "artifact_set": [artifact],
            "overlay": {
                "root_uri": (
                    self.run_root / "supersymmetry-overlay"
                ).as_uri(),
                "metadata_path": "mods/sample.pw.toml",
                "baseline_filename": "sample.jar",
                "baseline_hash": selected["baseline"],
                "candidate_path": "mods/sample.jar",
                "candidate_sha256": sha256(CANDIDATE).hexdigest(),
                "candidate_size": len(CANDIDATE),
                "applicable_sides": ["client", "server"],
                "runtime_installed_and_loaded": False,
            },
            "runtime": {"client": stage, "server": None},
            "problems": [],
            "cleanup": {
                "owned_processes_running": False,
                "checkout_mutated_by_workbench": False,
                "managed_run_root": str(self.run_root),
            },
            "next_actions": [],
            "limitations": [],
        }
        _write(self.run_root / "source-manifest.json", "{}\n")
        _write(
            self.run_root / "supersymmetry-overlay/mods/sample.jar",
            CANDIDATE,
        )
        _reseal_result(self.result)
        _write_json(self.stage_path, stage)
        _write_json(self.result_path, self.result)
        self.stage = stage

    def reseal(self) -> None:
        _reseal_stage(self.stage)
        self.result["runtime"]["client"] = self.stage
        _reseal_result(self.result)
        _write_json(self.stage_path, self.stage)
        _write_json(self.result_path, self.result)

    def server_plan_target(self) -> Path:
        plan = plan_project_runtime(
            self.suite,
            self.pack,
            side="server",
            launcher="dedicated-server",
        )
        return _uri_path(plan["target"]["fixture_root_uri"])

    def server_target(self) -> Path:
        plan = plan_project_runtime(
            self.suite,
            self.pack,
            side="server",
            launcher="dedicated-server",
        )
        provenance = server_materialize._canonical_client_provenance(
            self.client_receipt,
            self.canonical_receipt,
        )
        variant = server_materialize._server_source_variant(
            plan,
            provenance,
        )
        return (
            self.suite
            / ".workbench/fixtures/supersymmetry/server-v2"
            / variant["variant_id"].removeprefix("sha256:")[:16]
        )

    def materialize(self, **overrides):
        arguments = {
            "server_java": self.java,
            "accept_minecraft_eula": True,
            "refresh_timeout_seconds": 10.0,
            "install_timeout_seconds": 10.0,
        }
        arguments.update(overrides)
        artifact_by_digest = {
            sha256(path.read_bytes()).hexdigest(): path
            for path in (self.packwiz_installer, self.cleanroom_server)
        }

        def fetch(**request):
            path = artifact_by_digest.get(request.get("expected_sha256"))
            if path is None:
                raise AssertionError(f"unexpected artifact request: {request!r}")
            if path.stat().st_size != request.get("expected_size"):
                raise AssertionError(f"artifact size request drifted: {request!r}")
            return path, "fixture"

        with patch.object(
            server_materialize,
            "fetch_verified_artifact",
            side_effect=fetch,
        ):
            return materialize_susy_server(
                self.suite,
                RUN_ID,
                **arguments,
            )


class SusyServerMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = _MaterializationFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v2_reuse_rejects_resealed_forged_canonical_provenance(self) -> None:
        installed = self.fixture.materialize()
        receipt_path = _uri_path(installed["receipt"]["target"]["receipt_uri"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["canonical_client_seed"]["provenance"]["receipt_uri"] = (
            (self.fixture.root / "forged-client-receipt.json").as_uri()
        )
        receipt["materialization_id"] = server_materialize._receipt_id(receipt)
        _write_json(receipt_path, receipt)
        target = _uri_path(receipt["target"]["fixture_root_uri"])
        before = _snapshot(target)

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "provenance|input|invalid|receipt|variant",
        ):
            self.fixture.materialize()

        self.assertEqual(before, _snapshot(target))

    def test_v2_source_variant_target_collision_is_fail_closed(self) -> None:
        installed = self.fixture.materialize()
        target = _uri_path(installed["receipt"]["target"]["fixture_root_uri"])
        displaced = target.with_name("." + target.name + ".displaced")
        target.rename(displaced)
        _write(target / "foreign.txt", "do not replace or adopt\n")
        foreign_before = _snapshot(target)

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "receipt|existing|missing|invalid|unsafe",
        ):
            self.fixture.materialize()

        self.assertEqual(foreign_before, _snapshot(target))
        self.assertEqual(
            installed["receipt"]["materialization_id"],
            json.loads(
                (
                    displaced
                    / "receipts/susy-server-materialization-v2.json"
                ).read_text(encoding="utf-8")
            )["materialization_id"],
        )

    def test_server_v2_applies_packwiz_defaults_and_reuses_exact_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(
                Path(temporary),
                server_optionals=True,
            )
            installed = fixture.materialize()
            receipt = installed["receipt"]
            target = _uri_path(receipt["target"]["fixture_root_uri"])
            template = _uri_path(receipt["target"]["template_uri"])
            options = receipt["server_packwiz_options"]

            self.assertEqual(options["policy"], "pack-declared-defaults")
            self.assertEqual(options["policy_version"], 1)
            self.assertEqual(options["side"], "server")
            self.assertEqual(options["optional_count"], 2)
            self.assertEqual(options["enabled_count"], 1)
            self.assertEqual(options["disabled_count"], 1)
            self.assertEqual(
                options["installer_state"]["cached_side"],
                "server",
            )
            rows = {
                row["metadata_path"]: row
                for row in options["files"]
            }
            enabled = rows["mods/server-optional-on.pw.toml"]
            disabled = rows["mods/server-optional-off.pw.toml"]
            self.assertEqual(
                (enabled["declared_default"], enabled["applied"], enabled["present"]),
                (True, True, True),
            )
            self.assertEqual(enabled["output_path"], "mods/server-optional-on.jar")
            self.assertEqual(
                enabled["output_sha256"],
                sha256(SERVER_OPTIONAL_ON).hexdigest(),
            )
            self.assertEqual(enabled["output_size"], len(SERVER_OPTIONAL_ON))
            self.assertEqual(
                (disabled["declared_default"], disabled["applied"], disabled["present"]),
                (False, False, False),
            )
            self.assertEqual(disabled["output_path"], "mods/server-optional-off.jar")
            self.assertIsNone(disabled["output_sha256"])
            self.assertIsNone(disabled["output_size"])

            decisions = [
                {
                    key: row[key]
                    for key in (
                        "metadata_path",
                        "metafile_sha256",
                        "output_path",
                        "name",
                        "side",
                        "declared_default",
                        "applied",
                    )
                }
                for row in options["files"]
            ]
            initial_bytes = server_materialize._packwiz_initial_state_bytes(
                decisions,
                side="server",
            )
            self.assertEqual(
                options["installer_state"]["initial"],
                {
                    "sha256": sha256(initial_bytes).hexdigest(),
                    "size": len(initial_bytes),
                },
            )
            initial_state = json.loads(initial_bytes)
            self.assertEqual(initial_state["cachedSide"], "server")
            self.assertFalse(
                initial_state["cachedFiles"]
                ["mods/server-optional-off.pw.toml"]["optionValue"]
            )
            self.assertTrue(
                initial_state["cachedFiles"]
                ["mods/server-optional-on.pw.toml"]["optionValue"]
            )

            final_state = json.loads(
                (template / "packwiz.json").read_text(encoding="utf-8")
            )
            self.assertEqual(final_state["cachedSide"], "server")
            self.assertNotIn(
                "cachedLocation",
                final_state["cachedFiles"]
                ["mods/server-optional-off.pw.toml"],
            )
            self.assertEqual(
                final_state["cachedFiles"]
                ["mods/server-optional-on.pw.toml"]["cachedLocation"],
                "mods/server-optional-on.jar",
            )
            self.assertEqual(
                (template / "mods/server-optional-on.jar").read_bytes(),
                SERVER_OPTIONAL_ON,
            )
            self.assertFalse(
                (template / "mods/server-optional-off.jar").exists()
            )
            inventory_names = {
                row["filename"]
                for row in receipt["server_payload"]["mod_inventory"]["entries"]
            }
            self.assertIn("server-optional-on.jar", inventory_names)
            self.assertNotIn("server-optional-off.jar", inventory_names)

            before = _snapshot(target)
            receipt_bytes = _uri_path(
                receipt["target"]["receipt_uri"]
            ).read_bytes()
            reused = fixture.materialize()

            self.assertEqual(reused["outcome"], "reused")
            self.assertEqual(
                reused["receipt"]["materialization_id"],
                receipt["materialization_id"],
            )
            self.assertEqual(before, _snapshot(target))
            self.assertEqual(
                receipt_bytes,
                _uri_path(receipt["target"]["receipt_uri"]).read_bytes(),
            )

            receipt_path = _uri_path(receipt["target"]["receipt_uri"])
            forged = json.loads(receipt_path.read_text(encoding="utf-8"))
            forged_disabled = next(
                row
                for row in forged["server_packwiz_options"]["files"]
                if row["metadata_path"]
                == "mods/server-optional-off.pw.toml"
            )
            forged_disabled["declared_default"] = True
            forged_disabled["applied"] = True
            forged["materialization_id"] = server_materialize._receipt_id(
                forged
            )
            _write_json(receipt_path, forged)
            forged_before = _snapshot(target)

            with self.assertRaisesRegex(
                SusyServerMaterializationError,
                "Packwiz|option|default|state|drift",
            ):
                fixture.materialize()

            self.assertEqual(forged_before, _snapshot(target))

    def test_fresh_atomic_install_and_exact_reuse(self) -> None:
        pack_before = _snapshot(self.fixture.pack)
        canonical_before = _snapshot(self.fixture.canonical_instance)
        run_before = _snapshot(self.fixture.run_root)

        self.assertEqual(
            self.fixture.client_materialization["format"],
            "workbench-packwiz-materialization-result-v2",
        )
        self.assertEqual(
            self.fixture.client_receipt["format"],
            "workbench-packwiz-materialization-receipt-v2",
        )
        self.assertEqual(
            self.fixture.canonical_receipt.name,
            "packwiz-materialization-v2.json",
        )
        installed = self.fixture.materialize()

        self.assertEqual(
            installed["format"],
            "workbench-susy-server-materialization-result-v2",
        )
        self.assertEqual(installed["schema_version"], 2)
        self.assertEqual(installed["outcome"], "installed")
        receipt = installed["receipt"]
        self.assertEqual(
            receipt["format"],
            "workbench-susy-server-materialization-receipt-v2",
        )
        template = _uri_path(receipt["target"]["template_uri"])
        self.assertEqual(template, self.fixture.server_target() / ".minecraft")
        self.assertEqual(
            receipt["target"]["fixture_root_uri"],
            self.fixture.server_target().as_uri(),
        )
        self.assertEqual(
            receipt["canonical_client_seed"]["materialization_id"],
            self.fixture.client_receipt["materialization_id"],
        )
        self.assertEqual(
            receipt["canonical_client_seed"]["receipt_uri"],
            self.fixture.canonical_receipt.as_uri(),
        )
        self.assertEqual(
            receipt["claims"],
            {
                "source_checkout_mutated": False,
                "canonical_client_mutated": False,
                "retained_run_mutated": False,
                "server_side_packwiz_reconciled": True,
                "cleanroom_server_installed": True,
                "minecraft_launched": False,
            },
        )
        self.assertTrue(template.is_dir())
        self.assertEqual("eula=true\n", (template / "eula.txt").read_text())
        self.assertEqual(
            "# Workbench disposable SUSY server template\n"
            "defaultworldgenerator-port=a55790f1-609f-11ee-b9f3-80e82ceaaf53\n"
            "level-type=RTG\n"
            "online-mode=false\n"
            "server-ip=127.0.0.1\n",
            (template / "server.properties").read_text(),
        )
        self.assertEqual(pack_before, _snapshot(self.fixture.pack))
        self.assertEqual(
            canonical_before,
            _snapshot(self.fixture.canonical_instance),
        )
        self.assertEqual(run_before, _snapshot(self.fixture.run_root))
        first_snapshot = _snapshot(template)
        first_id = receipt["materialization_id"]

        counts_before = {
            path.name: path.read_text(encoding="utf-8")
            for path in self.fixture.root.rglob("*.count")
            if path.name != "java.probe-count"
        }
        reused = self.fixture.materialize()

        self.assertEqual(reused["outcome"], "reused")
        reused_receipt = reused["receipt"]
        self.assertEqual(
            reused_receipt["materialization_id"],
            first_id,
        )
        self.assertEqual(first_snapshot, _snapshot(template))
        counts_after = {
            path.name: path.read_text(encoding="utf-8")
            for path in self.fixture.root.rglob("*.count")
            if path.name != "java.probe-count"
        }
        self.assertEqual(counts_before, counts_after)

    def test_filters_exact_server_side_and_keeps_direct_files(self) -> None:
        result = self.fixture.materialize()
        template = _uri_path(result["receipt"]["target"]["template_uri"])

        self.assertEqual((template / "config/common.cfg").read_bytes(), DIRECT)
        self.assertEqual((template / "mods/sample.jar").read_bytes(), BASELINE)
        self.assertEqual(
            (template / "mods/restricted.jar").read_bytes(),
            RESTRICTED,
        )
        self.assertEqual(
            (template / "mods/server.jar").read_bytes(),
            SERVER_ONLY,
        )
        self.assertFalse((template / "mods/client.jar").exists())
        self.assertFalse((template / "world").exists())
        self.assertFalse((template / "logs").exists())
        self.assertFalse((template / "crash-reports").exists())
        inventory = result["receipt"]["server_payload"]["mod_inventory"]
        self.assertEqual(inventory["entry_count"], 3)
        self.assertEqual(
            [
                (entry["filename"], entry["side"])
                for entry in inventory["entries"]
            ],
            [
                ("restricted.jar", "both"),
                ("sample.jar", "both"),
                ("server.jar", "server"),
            ],
        )
        self.assertEqual(
            inventory["inventory_sha256"],
            "sha256:" + _canonical_digest(inventory["entries"]),
        )

    def test_restricted_server_seed_is_derived_from_canonical_payload(self) -> None:
        receipt_before = json.loads(
            self.fixture.canonical_receipt.read_text(encoding="utf-8")
        )
        receipt_before["seeds"]["entries"] = []
        receipt_before["seeds"]["file_count"] = 0
        receipt_before["seeds"]["total_bytes"] = 0
        _write_json(self.fixture.canonical_receipt, receipt_before)

        result = self.fixture.materialize()

        template = _uri_path(result["receipt"]["target"]["template_uri"])
        self.assertEqual(
            (template / "mods/restricted.jar").read_bytes(),
            RESTRICTED,
        )
        seed = result["receipt"]["canonical_client_seed"]
        self.assertEqual(
            seed["selection"],
            "packwiz-declared-server-defaults-and-applicable-mod-seeds",
        )
        self.assertEqual(
            [entry["path"] for entry in seed["seeded_files"]],
            ["mods/restricted.jar", "mods/sample.jar"],
        )

    def test_explicit_minecraft_eula_acceptance_is_required(self) -> None:
        target = self.fixture.server_target()
        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "EULA|eula|accept",
        ):
            self.fixture.materialize(accept_minecraft_eula=False)
        self.assertFalse(target.exists())
        self.assertFalse(target.is_symlink())

        result = self.fixture.materialize(accept_minecraft_eula=True)

        template = _uri_path(result["receipt"]["target"]["template_uri"])
        self.assertIn("eula=true", (template / "eula.txt").read_text())

    def test_rejects_client_stage_and_receipt_drift(self) -> None:
        self.fixture.stage["source"]["payload"]["file_count"] += 1
        self.fixture.reseal()
        with self.assertRaises(SusyServerMaterializationError):
            self.fixture.materialize()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            receipt = json.loads(
                fixture.canonical_receipt.read_text(encoding="utf-8")
            )
            receipt["payload"]["total_bytes"] += 1
            _write_json(fixture.canonical_receipt, receipt)
            with self.assertRaises(SusyServerMaterializationError):
                fixture.materialize()

    def test_rejects_pack_profile_and_tool_drift(self) -> None:
        _write(self.fixture.pack / "config/common.cfg", b"drifted=true\n")
        with self.assertRaises(SusyServerMaterializationError):
            self.fixture.materialize()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            profile = (
                fixture.suite
                / "profiles/platforms/cleanroom/provisional.yaml"
            )
            _write(profile, profile.read_bytes() + b"# drift\n")
            with self.assertRaises(SusyServerMaterializationError):
                fixture.materialize()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            _write(
                fixture.packwiz,
                fixture.packwiz.read_bytes() + b"\n# tool drift\n",
                executable=True,
            )
            with self.assertRaises(SusyServerMaterializationError):
                fixture.materialize()

    def test_rejects_symlink_escape_collision_and_unrecorded_target(self) -> None:
        baseline = self.fixture.canonical_instance / ".minecraft/mods/sample.jar"
        baseline.unlink()
        baseline.symlink_to(self.fixture.root / "outside.jar")
        _write(self.fixture.root / "outside.jar", BASELINE)
        with self.assertRaises(SusyServerMaterializationError):
            self.fixture.materialize()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            escaped = fixture.root / "escaped-client"
            shutil.copytree(fixture.canonical_instance, escaped)
            fixture.stage["source"]["instance_uri"] = escaped.as_uri()
            fixture.reseal()
            with self.assertRaises(SusyServerMaterializationError):
                fixture.materialize()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(
                Path(temporary),
                server_collision=True,
            )
            with self.assertRaisesRegex(
                SusyServerMaterializationError,
                "collid|output",
            ):
                fixture.materialize()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            target = fixture.server_target()
            _write(target / "foreign.txt", "do not overwrite\n")
            before = _snapshot(target)
            with self.assertRaises(SusyServerMaterializationError):
                fixture.materialize()
            self.assertEqual(before, _snapshot(target))

    def test_rejects_symlinked_server_fixture_parent_escape(self) -> None:
        lexical_parent = (
            self.fixture.suite
            / ".workbench/fixtures/supersymmetry/server-v2"
        )
        outside = self.fixture.root / "outside-server-state"
        outside.mkdir()
        lexical_parent.parent.mkdir(parents=True, exist_ok=True)
        lexical_parent.symlink_to(outside, target_is_directory=True)
        outside_before = _snapshot(outside)

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "symlink|symbolic|escape|unsafe|director",
        ):
            self.fixture.materialize()

        self.assertTrue(lexical_parent.is_symlink())
        self.assertEqual(outside_before, _snapshot(outside))

    def test_failed_cleanroom_installer_never_publishes_partial_target(self) -> None:
        target = self.fixture.server_target()
        _write(self.fixture.failure_flag, "fail\n")
        pack_before = _snapshot(self.fixture.pack)
        canonical_before = _snapshot(self.fixture.canonical_instance)

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "17|Cleanroom|installer",
        ):
            self.fixture.materialize()

        self.assertFalse(target.exists())
        self.assertFalse(target.is_symlink())
        self.assertEqual(pack_before, _snapshot(self.fixture.pack))
        self.assertEqual(
            canonical_before,
            _snapshot(self.fixture.canonical_instance),
        )
        staging = self.fixture.suite / ".workbench/staging/susy-server"
        self.assertEqual(list(staging.glob("*")) if staging.exists() else [], [])

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            target = fixture.server_target()
            _write(fixture.packwiz_failure_flag, "fail\n")
            pack_before = _snapshot(fixture.pack)
            canonical_before = _snapshot(fixture.canonical_instance)
            with self.assertRaisesRegex(
                SusyServerMaterializationError,
                "23|Packwiz|installer",
            ):
                fixture.materialize()
            self.assertFalse(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertEqual(pack_before, _snapshot(fixture.pack))
            self.assertEqual(
                canonical_before,
                _snapshot(fixture.canonical_instance),
            )

    def test_reuse_rejects_resealed_forged_semantic_claims(self) -> None:
        installed = self.fixture.materialize()
        receipt_path = _uri_path(installed["receipt"]["target"]["receipt_uri"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["claims"]["minecraft_launched"] = True
        receipt["server_payload"]["mod_inventory"]["entry_count"] = 999
        material = {
            key: value
            for key, value in receipt.items()
            if key != "materialization_id"
        }
        receipt["materialization_id"] = (
            "workbench-susy-server-materialization:"
            + _canonical_digest(material)
        )
        _write_json(receipt_path, receipt)

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "semantic|claim|inventory|invalid",
        ):
            self.fixture.materialize()

    def test_reuse_rejects_symlinked_receipt_parent(self) -> None:
        installed = self.fixture.materialize()
        target = _uri_path(installed["receipt"]["target"]["fixture_root_uri"])
        receipts = target / "receipts"
        outside = self.fixture.root / "outside-receipts"
        receipts.rename(outside)
        receipts.symlink_to(outside, target_is_directory=True)
        outside_before = _snapshot(outside)

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "receipt|unsafe|symbolic|symlink",
        ):
            self.fixture.materialize()

        self.assertTrue(receipts.is_symlink())
        self.assertEqual(outside_before, _snapshot(outside))

    def test_atomic_publish_never_replaces_concurrent_empty_target(self) -> None:
        target = self.fixture.server_target()
        original_publish = server_materialize._rename_no_replace
        injected = False

        def race_with_empty_target(source: Path, destination: Path) -> None:
            nonlocal injected
            self.assertFalse(injected)
            self.assertEqual(destination, target)
            destination.mkdir()
            injected = True
            original_publish(source, destination)

        with patch.object(
            server_materialize,
            "_rename_no_replace",
            side_effect=race_with_empty_target,
        ):
            with self.assertRaisesRegex(
                SusyServerMaterializationError,
                "appeared|atomic|publish",
            ):
                self.fixture.materialize()

        self.assertTrue(injected)
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])

    def test_post_publish_validation_failure_quarantines_target(self) -> None:
        target = self.fixture.server_target()
        fixture_parent = target.parent

        with patch.object(
            server_materialize,
            "_validate_reusable",
            side_effect=SusyServerMaterializationError(
                "deliberate post-publish verification failure"
            ),
        ):
            with self.assertRaisesRegex(
                SusyServerMaterializationError,
                "failed verification|rejected bytes",
            ):
                self.fixture.materialize()

        self.assertFalse(target.exists())
        self.assertFalse(target.is_symlink())
        rejected = sorted(fixture_parent.glob(f".rejected-{target.name}-*"))
        self.assertEqual(len(rejected), 1)
        self.assertTrue((rejected[0] / ".minecraft").is_dir())
        self.assertTrue(
            (
                rejected[0]
                / "receipts/susy-server-materialization-v2.json"
            ).is_file()
        )

    def test_detached_installer_child_cannot_mutate_published_template(self) -> None:
        managed_test_root = ROOT / ".workbench"
        managed_test_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="susy-server-late-child-",
            dir=managed_test_root,
        ) as temporary:
            fixture = _MaterializationFixture(Path(temporary))
            _write(fixture.late_child_flag, "spawn\n")
            target = fixture.server_target()
            error: SusyServerMaterializationError | None = None
            result: dict | None = None
            try:
                result = fixture.materialize()
            except SusyServerMaterializationError as exc:
                error = exc

            deadline = time.monotonic() + 4.0
            late_file = target / ".minecraft/late-child.txt"
            while time.monotonic() < deadline and not late_file.exists():
                time.sleep(0.05)

            self.assertIsNone(
                result,
                "materializer accepted a tool that escaped custody; "
                f"late child wrote published bytes={late_file.exists()}",
            )
            self.assertIsNotNone(error)
            self.assertFalse(target.exists())
            self.assertFalse(target.is_symlink())

    def test_ignored_source_mutation_is_detected_before_publish(self) -> None:
        target = self.fixture.server_target()
        canonical_before = _snapshot(self.fixture.canonical_instance)
        run_before = _snapshot(self.fixture.run_root)
        _write(self.fixture.source_mutation_flag, "mutate\n")

        with self.assertRaisesRegex(
            SusyServerMaterializationError,
            "checkout changed|source|Supersymmetry",
        ):
            self.fixture.materialize()

        self.assertEqual(
            self.fixture.source_side_effect.read_text(encoding="utf-8"),
            "ignored source mutation\n",
        )
        self.assertFalse(target.exists())
        self.assertFalse(target.is_symlink())
        self.assertEqual(
            canonical_before,
            _snapshot(self.fixture.canonical_instance),
        )
        self.assertEqual(run_before, _snapshot(self.fixture.run_root))

    def test_reuse_rejects_selected_java_tool_drift(self) -> None:
        installed = self.fixture.materialize()
        template = _uri_path(
            installed["receipt"]["target"]["template_uri"]
        )
        before = _snapshot(template)
        _write(
            self.fixture.java,
            self.fixture.java.read_bytes() + b"\n# changed selected JDK\n",
            executable=True,
        )

        with self.assertRaises(SusyServerMaterializationError):
            self.fixture.materialize()

        self.assertEqual(before, _snapshot(template))

    def test_receipt_identity_and_rendering_are_stable(self) -> None:
        result = self.fixture.materialize()
        receipt = result["receipt"]
        identity = receipt["materialization_id"]
        self.assertIsInstance(identity, str)
        self.assertRegex(
            identity,
            r"^workbench-susy-server-materialization:[0-9a-f]{64}$",
        )
        material = {
            key: value
            for key, value in receipt.items()
            if key != "materialization_id"
        }
        self.assertTrue(identity.endswith(_canonical_digest(material)))
        self.assertEqual(
            json.loads(
                render_susy_server_materialization(result, json_output=True)
            ),
            result,
        )
        rendered = render_susy_server_materialization(
            result,
            json_output=False,
        )
        self.assertIn(result["outcome"], rendered)
        self.assertIn(receipt["target"]["template_uri"], rendered)


if __name__ == "__main__":
    unittest.main()
