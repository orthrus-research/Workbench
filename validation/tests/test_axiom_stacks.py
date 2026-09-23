"""Fluid-stack source custody and native utility/qualification boundaries."""
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_stack_conformance as conformance
import axiom_stack_sources as extraction


class AxiomStackTests(unittest.TestCase):
    def test_exact_source_closure_and_selected_revisions(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        rows = lock["references"]
        self.assertEqual(set(conformance.PATHS.values()), {(r["repository"],r["path"]) for r in rows})
        self.assertEqual(7, len(rows))
        self.assertEqual(json.loads(conformance.EVENT_LOCK.read_bytes())["revision"], lock["revisions"]["cleanroom"])
        target=json.loads(conformance.TARGET_LOCK.read_bytes())
        self.assertEqual(next(r["commit"] for r in target["repositories"] if r["id"]=="gtceu"), lock["revisions"]["gtceu"])
        for row in rows:
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["gitBlob"], r"^[0-9a-f]{40}$")

    def test_mutable_revision_and_changed_inputs_fail_closed(self):
        lock=json.loads(conformance.LOCK.read_bytes()); roots={k:Path(k) for k in lock["revisions"]}
        with patch.object(conformance,"git",return_value=b"changed") as read:
            with self.assertRaisesRegex(ValueError,"identity differs"):
                conformance.verify_references(roots,lock)
        row=lock["references"][0]
        self.assertEqual((roots[row["repository"]],"show",lock["revisions"][row["repository"]]+":"+row["path"]),read.call_args.args)
        lock["revisions"][row["repository"]]="HEAD"
        with self.assertRaisesRegex(ValueError,"revision"):
            conformance.verify_references(roots,lock)

    def test_missing_and_duplicate_source_closure_fail(self):
        raw=b"source"; row={"repository":"cleanroom","path":"fixture.java","sha256":sha256(raw).hexdigest(),
                            "gitBlob":sha1(b"blob 6\0"+raw).hexdigest()}
        lock={"schema":"axiom.native-fluid-stack-source-lock.v1","revisions":{"cleanroom":"a"*40,"gtceu":"b"*40},"references":[row]}
        with patch.object(conformance,"git",return_value=raw):
            with self.assertRaisesRegex(ValueError,"closure"):
                conformance.verify_references({"cleanroom":Path("fixture")},lock)
            with self.assertRaisesRegex(ValueError,"duplicate"):
                conformance.verify_references({"cleanroom":Path("fixture")},{**lock,"references":[row,row]})

    def test_retained_source_changes_cannot_reseal_themselves(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/"Example.java").write_text("changed")
            with patch.object(conformance,"PATHS",{"Example":("cleanroom","fixture")}), patch.object(conformance,"extracted",return_value={"Example":"original"}):
                with self.assertRaisesRegex(ValueError,"differs"):
                    conformance.retained_inputs({},root)

    def test_source_edit_is_one_bounded_assignment(self):
        key=conformance.PATHS["NativeFluidStack"]; original={key:"this.amount = amount;",("gtceu","other"):"unchanged"}
        edited=conformance.edited_source(original)
        self.assertEqual("this.amount = amount + 1;",edited[key]); self.assertEqual("unchanged",edited["gtceu","other"])
        for source in ("",original[key]*2):
            with self.assertRaisesRegex(ValueError,"boundary"):
                conformance.edited_source({key:source})

    def test_relocation_does_not_rewrite_diagnostics(self):
        source='FluidStack value; String message = "FluidStack and NBTTagCompound";'
        self.assertEqual('NativeFluidStack value; String message = "FluidStack and NBTTagCompound";',extraction.relocate(source,{"FluidStack":"NativeFluidStack"}))
        stack=(conformance.MATERIAL_ROOT/"NativeFluidStack.java").read_text()
        self.assertIn("Failed attempt to create a FluidStack",stack)
        self.assertNotIn("isFluidEqual(ItemStack",stack); self.assertNotIn("public String getLocalizedName()",stack)
        self.assertIn("tag = (NativeNbtCompound) nbt.copy()",stack)

    def test_native_delegates_replace_the_old_record(self):
        registry=(conformance.MATERIAL_ROOT/"FluidRegistryState.java").read_text()
        self.assertNotIn("record FluidDelegate",registry)
        self.assertIn("class FluidDelegate implements IRegistryDelegate<NativeFluid>",registry)
        self.assertIn("fluid = fluids.get(name)",registry)
        self.assertIn("maxID = newfluidIDs.size()",registry)
        self.assertIn("currentBucketFluids = null",registry)
        self.assertNotIn("public static final Fluid WATER",registry)

    def test_nbt_is_separately_supplied_native_identity_not_json(self):
        policy=json.loads((ROOT/"profiles/platforms/cleanroom/registry-runtime.json").read_bytes())
        for logical,native in (("NBTTagCompound","fy"),("NBTTagList","ge"),("NBTBase","gn"),("NBTTagString","gm")):
            self.assertEqual(native,policy["classes"][logical]["name"])
            self.assertEqual(64,len(policy["classes"][logical]["sha256"]))
        wrapper=(conformance.MATERIAL_ROOT/"NativeNbtCompound.java").read_text()
        self.assertIn('runtime.utility("fy"',wrapper)
        self.assertNotIn("new HashMap",wrapper); self.assertNotIn("Json.",wrapper)
        self.assertIn("NativeNbtGuard.requireTag(key, value)",wrapper)
        runtime=(conformance.MATERIAL_ROOT/"RegistryRuntime.java").read_text()
        self.assertIn("Map<Object, NativeNbtValue> nbtValues = new IdentityHashMap",runtime)

    def test_build_ci_and_source_notices_admit_the_checkpoint(self):
        build=(ROOT/"modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../sources/native-fluid-stacks.lock.json")',build)
        self.assertIn('from("../tests/oracles/FluidStackConformance.java")',build)
        self.assertIn("python3 tools/axiom_stack_conformance.py",(ROOT/".github/workflows/validate.yml").read_text())
        self.assertIn("native-fluid-stacks.lock.json",(ROOT/"modules/axiom/sources/NOTICE.md").read_text())
        self.assertIn("WorkerIsolation.install()",conformance.DRIVER.read_text())
        with self.assertRaisesRegex(ValueError,"unadmitted"):
            extraction.extract("Unknown","")

    def test_cleanroom_nbt_null_guard_is_retained_and_fail_closed(self):
        patch_source='+        if (value == null) throw new IllegalArgumentException("Invalid null NBT value with key " + key);'
        retained=extraction.extract("NativeNbtGuard",patch_source)
        self.assertIn(patch_source[1:],retained)
        for text in ("",patch_source*2):
            with self.assertRaisesRegex(ValueError,"boundary"):
                extraction.extract("NativeNbtGuard",text)
