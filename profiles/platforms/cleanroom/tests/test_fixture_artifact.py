"""The Cleanroom owner validates exact remapped JAR content from retained bytes."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
import stat
from zipfile import ZIP_STORED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[4]
PROFILE = ROOT / "profiles/platforms/cleanroom"
sys.path.insert(0, str(ROOT / "api/src"))
sys.path.insert(0, str(PROFILE / "src"))
from workbench_profile_cleanroom import fixture_home as owner  # noqa: E402


def _members() -> dict[str, bytes]:
    return {
        "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\r\nMixinConfigs: mixins.workbench_daily_loop.json\r\n\r\n",
        "mcmod.info": b'[{"modid":"workbench_daily_loop","version":"1.0.0"}]',
        "pack.mcmeta": b"{}",
        "mixins.workbench_daily_loop.json": b"{}",
        "mixins.workbench_daily_loop.refmap.json": b'{"mappings":{"probe":"target"}}',
        "assets/workbench_daily_loop/lang/en_us.lang": b"PROBE-CONTENT",
        "assets/workbench_daily_loop/blockstates/probe_block.json": b"{}",
        "assets/workbench_daily_loop/models/block/probe_block.json": b"{}",
        "assets/workbench_daily_loop/models/item/probe_block.json": b"{}",
        "dev/workbench/dailyloop/DailyLoopMod.class": b"\xca\xfe\xba\xbeclass",
        "dev/workbench/dailyloop/DailyLoopContent.class": b"\xca\xfe\xba\xbeclass",
        "dev/workbench/dailyloop/DailyLoopProbe.class": b"\xca\xfe\xba\xbeclass",
        "dev/workbench/dailyloop/mixin/MixinBlock.class": b"\xca\xfe\xba\xbeclass",
    }


def _jar(members: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return output.getvalue()


class FixtureArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = json.loads((PROFILE / "fixtures/generic-mod-daily-loop/fixture-lock-v1.json").read_text())
        self.policy = json.loads((PROFILE / "policies/generic-mod-fixture-execution-v1.json").read_text())
        self.properties = (PROFILE / "fixtures/generic-mod-daily-loop/gradle.properties").read_bytes()
        self.spec = owner.portable_artifact_spec(
            fixture_lock=self.lock, execution_policy=self.policy,
            gradle_properties=self.properties,
        )

    def test_locked_properties_select_exact_remapped_jar(self) -> None:
        self.assertEqual(
            ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop/"
            "libs/workbench-daily-loop-1.0.0.jar",
            self.spec["relative_path"],
        )
        self.assertEqual("owner-content-validated", owner.inspect_portable_artifact(
            _jar(_members()), spec=self.spec,
        )["state"])
        with self.assertRaisesRegex(ValueError, "owner identity"):
            owner.portable_artifact_spec(
                fixture_lock=self.lock, execution_policy=self.policy,
                gradle_properties=self.properties + b"\n",
            )

    def test_missing_or_foreign_jar_content_refuses(self) -> None:
        mutations = {}
        missing = _members()
        del missing["mcmod.info"]
        mutations["missing mcmod"] = missing
        foreign = _members()
        foreign["mcmod.info"] = b'[{"modid":"another","version":"1.0.0"}]'
        mutations["foreign mod"] = foreign
        refmap = _members()
        refmap["mixins.workbench_daily_loop.refmap.json"] = b'{"mappings":{}}'
        mutations["empty refmap"] = refmap
        manifest = _members()
        manifest["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\r\n"
        mutations["wrong manifest"] = manifest
        class_bytes = _members()
        class_bytes["dev/workbench/dailyloop/DailyLoopMod.class"] = b"invalid"
        mutations["wrong class"] = class_bytes
        other_class = _members()
        other_class["dev/workbench/dailyloop/DailyLoopProbe.class"] = b"invalid"
        mutations["wrong required class"] = other_class
        mixin = _members()
        mixin["org/spongepowered/asm/mixin/Foreign.class"] = b"foreign"
        mutations["embedded mixin"] = mixin
        unsafe = _members()
        unsafe["../escape"] = b"escape"
        mutations["unsafe path"] = unsafe
        for label, members in mutations.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                owner.inspect_portable_artifact(_jar(members), spec=self.spec)

    def test_zip_crc_and_duplicate_paths_refuse(self) -> None:
        raw = _jar(_members())
        self.assertIn(b"PROBE-CONTENT", raw)
        corrupt = raw.replace(b"PROBE-CONTENT", b"PROBE-CONTENX", 1)
        with self.assertRaisesRegex(ValueError, "cannot be read exactly"):
            owner.inspect_portable_artifact(corrupt, spec=self.spec)
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_STORED) as archive:
            for name, content in _members().items():
                archive.writestr(name, content)
            archive.writestr("MCMod.info", b"duplicate")
        with self.assertRaisesRegex(ValueError, "duplicate member"):
            owner.inspect_portable_artifact(output.getvalue(), spec=self.spec)

    def test_special_zip_member_refuses(self) -> None:
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_STORED) as archive:
            for name, content in _members().items():
                archive.writestr(name, content)
            fifo = ZipInfo("special-fifo")
            fifo.create_system = 3
            fifo.external_attr = (stat.S_IFIFO | 0o600) << 16
            archive.writestr(fifo, b"")
        with self.assertRaisesRegex(ValueError, "special or mismatched"):
            owner.inspect_portable_artifact(output.getvalue(), spec=self.spec)


if __name__ == "__main__":
    unittest.main()
