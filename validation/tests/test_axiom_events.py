"""Event source custody and honest material bootstrap capability boundaries."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools"))
import axiom_event_sources as events
import axiom_event_conformance as conformance
import axiom_bootstrap_sources as lifecycle


class AxiomEventTests(unittest.TestCase):
    def test_event_relocation_includes_asm_descriptors_and_only_explicit_ports(self):
        source='''package net.minecraftforge.fml.common.eventhandler;
import net.minecraftforge.fml.common.Loader;
ModContainer owner = Loader.instance().activeModContainer();
String type = "Lnet/minecraftforge/fml/common/eventhandler/Cancelable;";
String message = "Cannot apply @SubscribeEvent to private method %s/%s%s";
'''
        result=events.extract(source)
        self.assertIn("EventContext.Owner owner = EventContext.instance().activeModContainer()",result)
        self.assertIn('"Lresearch/orthrus/axiom/nativeevents/Cancelable;"',result)
        self.assertIn('"Cannot apply @SubscribeEvent to private method %s/%s%s"',result)
        self.assertNotIn("net.minecraftforge",result)

    def test_original_source_is_loaded_from_locked_objects_not_worktree(self):
        raw=b"original";lock={"revision":"a"*40,"references":[{"path":"Event.java","sha256":sha256(raw).hexdigest()}]}
        with patch.object(conformance.subprocess,"check_output",return_value=raw) as read:
            self.assertEqual({"Event.java":raw},conformance.originals(Path("input"),lock))
        self.assertIn("a"*40+":Event.java",read.call_args.args[0])
        with patch.object(conformance.subprocess,"check_output",return_value=b"changed"),self.assertRaises(ValueError):
            conformance.originals(Path("input"),lock)

    def test_duplicate_source_identities_are_rejected(self):
        row={"path":"Event.java","sha256":sha256(b"source").hexdigest()}
        with patch.object(conformance.subprocess,"check_output",return_value=b"source"),self.assertRaises(ValueError):
            conformance.originals(Path("input"),{"revision":"a"*40,"references":[row,row]})

    def test_retained_event_changes_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/"Event.java").write_text("wrong")
            with patch.object(events,"OUTPUT",root),patch.object(events,"sources",return_value={"Event.java":"expected"}),self.assertRaises(ValueError):
                events.check(Path("input"))

    def test_lifecycle_cannot_extract_arbitrary_or_ambiguous_blocks(self):
        with self.assertRaises(ValueError):lifecycle.extract("other.java","unrelated")
        with self.assertRaises(ValueError):lifecycle.extract(lifecycle.CORE,"missing block")
        marker="        /* Start Material Registration */\n        /* End Material Registration */"
        with self.assertRaises(ValueError):lifecycle.extract(lifecycle.CORE,marker*2)
        with self.assertRaises(ValueError):lifecycle.extract(lifecycle.CORE,marker)

    def test_selected_native_library_hashes_match_gradle_verification(self):
        import xml.etree.ElementTree as ET
        lock=json.loads(events.LOCK.read_text())
        tree=ET.parse(ROOT/"modules/axiom/jvm/gradle/verification-metadata.xml")
        ns={"v":"https://schema.gradle.org/dependency-verification"}
        for row in lock["oracleLibraries"]:
            artifacts=tree.findall(".//v:artifact[@name='"+Path(row["path"]).name+"']/v:sha256",ns)
            self.assertEqual([row["sha256"]],[a.attrib["value"] for a in artifacts])

    def test_native_event_and_lifecycle_notices_are_packaged(self):
        build=(ROOT/"modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../sources/cleanroom-events.lock.json")',build)
        self.assertIn('from("../sources/material-lifecycle.lock.json")',build)
        self.assertIn('"third-party/$artifact"',build)
        source=(ROOT/"modules/axiom/spec/native-events.md").read_text()
        self.assertIn("vertical remains unfinished",source)
        self.assertIn("not a security boundary",source)
        for name in ("Event.java","EventBus.java","EventSubscriptionTransformer.java"):
            self.assertIn("Copyright (c) 2016-2020.",(events.OUTPUT/name).read_text())

    def test_event_provisioning_never_reuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(conformance.urllib.request,"urlopen") as download:
            with self.assertRaises(ValueError):conformance.provision_libraries(Path(temporary),{"oracleLibraries":[]})
            download.assert_not_called()

    def test_comparison_and_retention_are_in_required_ci_lane(self):
        source=(ROOT/".github/workflows/validate.yml").read_text()
        self.assertIn("python3 tools/axiom_event_conformance.py",source)
        self.assertIn("python3 tools/axiom_bootstrap_sources.py",source)
