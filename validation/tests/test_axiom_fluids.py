"""Native-fluid extraction/receipt boundaries without downloading upstream inputs."""
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "tools"))
import axiom_fluid_sources as extraction
import axiom_fluid_conformance as conformance


class AxiomFluidTests(unittest.TestCase):
    def row(self,raw=b"source"):
        return {"repository":"gtceu","path":"source.java","gitBlob":sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest(),
                "sha256":sha256(raw).hexdigest()}

    def test_member_boundaries_ignore_literal_and_comment_braces(self):
        source = 'prefix void run() { String s = "}"; /* } */ if (true) { } // }\n } tail'
        self.assertEqual('void run() { String s = "}"; /* } */ if (true) { } // }\n }',extraction.member(source,"void run()"))
        with self.assertRaises(ValueError):
            extraction.member(source+source,"void run()")
        with self.assertRaises(ValueError):
            extraction.member("void run() {","void run()")

    def test_relocation_preserves_every_diagnostic_literal(self):
        source = 'Material material; Fluid fluid; ResourceLocation location; String s = "Material requires Fluid and ResourceLocation";'
        result = extraction.clean(source)
        self.assertIn("FluidMaterial material; NativeFluid fluid; NativeLocation location;",result)
        self.assertIn('"Material requires Fluid and ResourceLocation"',result)

    def test_annotation_and_property_relocation_use_complete_identifiers(self):
        result = extraction.clean('@UnmodifiableView Collection<@NotNull Fluid> values; PropertyKey.FLUID_PIPE; PropertyKey.FLUID;')
        self.assertNotIn("View", result)
        self.assertIn("PropertyKey.FLUID_PIPE", result)
        self.assertIn("FluidDomain.FLUID;", result)

    def test_immutable_blob_read_does_not_select_checkout_head(self):
        lock={"schema":"axiom.native-fluid-source-lock.v1","revisions":{"gtceu":"1"*40},"references":[self.row()]}
        with patch.object(conformance,"git",return_value=b"source") as git:
            self.assertEqual({("gtceu","source.java"):"source"},conformance.verify_references({"gtceu":Path("fixture")},lock))
        self.assertEqual(("show","1"*40+":source.java"),git.call_args.args[1:])
        with patch.object(conformance,"git",return_value=b"changed"),self.assertRaisesRegex(ValueError,"identity"):
            conformance.verify_references({"gtceu":Path("fixture")},lock)

    def test_wrong_blob_duplicate_and_unsafe_paths_are_rejected(self):
        row=self.row()
        for rows in ([{**row,"gitBlob":"0"*40}],[row,row],[{**row,"path":"../source.java"}]):
            lock={"schema":"axiom.native-fluid-source-lock.v1","revisions":{"gtceu":"1"*40},"references":rows}
            with patch.object(conformance,"git",return_value=b"source"),self.assertRaises(ValueError):
                conformance.verify_references({"gtceu":Path("fixture")},lock)

    def test_retained_source_mutation_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/"Example.java").write_text("expected")
            with patch.object(extraction,"PATHS",{"Example":"source.java"}),patch.object(extraction,"FORGE",{}),patch.object(extraction,"extract",return_value="expected"):
                self.assertEqual({"Example":"expected"},conformance.retained_inputs({("gtceu","source.java"):"upstream"},root))
                (root/"Example.java").write_text("mutated")
                with self.assertRaisesRegex(ValueError,"differs"):
                    conformance.retained_inputs({("gtceu","source.java"):"upstream"},root)

    def test_whole_producer_boundary_is_not_silently_filtered(self):
        body="public static void init() {\n"+"\n".join(
            f'Value{i} = new Material.Builder({i}, SuSyUtility.susyId("value_{i}")).liquid().build();' for i in range(10))+"\n}"
        originals={("susy-core",conformance.PRODUCER):body,
                   ("susy-core",conformance.UTILITY):"public static ResourceLocation susyId(String path) { return new ResourceLocation(Supersymmetry.MODID, path); }",
                   ("susy-core","gradle.properties"):"modId = susy\n"}
        result=conformance.producer_source(originals)
        self.assertEqual(10,result.count("new FluidMaterial.Builder("))
        self.assertIn('new NativeLocation("susy", path)',result)
        originals["susy-core",conformance.PRODUCER]=body.replace("\n}",'\nExtra = new Material.Builder(99, null).build();\n}')
        with self.assertRaisesRegex(ValueError,"boundary"):
            conformance.producer_source(originals)

    def test_forge_license_and_locked_native_resource_are_packaged(self):
        lock=json.loads(conformance.LOCK.read_bytes())
        row=next(row for row in lock["references"] if row["repository"]=="cleanroom" and row["path"]=="LICENSE")
        self.assertEqual(row["sha256"],sha256((conformance.LOCK.parent/"licenses/forge/LGPL-2.1.txt").read_bytes()).hexdigest())
        for name in extraction.FORGE:
            self.assertIn("Copyright (c) 2016-2020.",(conformance.MATERIAL_ROOT/(name+".java")).read_text())
        build=(ROOT/"modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../sources/native-fluids.lock.json")',build)
        policy=json.loads((ROOT/"profiles/platforms/cleanroom/registry-runtime.json").read_bytes())
        self.assertEqual("nf",policy["classes"]["ResourceLocation"]["name"])
        self.assertEqual(64,len(policy["classes"]["ResourceLocation"]["sha256"]))

    def test_cleanroom_provision_does_not_reuse_an_existing_tree(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(conformance,"git") as git:
            with self.assertRaisesRegex(ValueError,"must be new"):
                conformance.provision_cleanroom(Path(temporary),"1"*40)
            git.assert_not_called()

    def test_required_ci_includes_fluid_comparison(self):
        workflow=(ROOT/".github/workflows/validate.yml").read_text()
        self.assertIn("python3 tools/axiom_fluid_conformance.py",workflow)
        self.assertIn("--provision-cleanroom .workbench/axiom-ci-fluid-cleanroom",workflow)
        self.assertNotIn("git clone --branch master",workflow)
