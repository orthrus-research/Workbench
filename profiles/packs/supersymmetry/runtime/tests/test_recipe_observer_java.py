"""Execute the packaged transformer/collector on explicit synthetic JVM fixtures.

These tests are not GTCEu or GroovyScript qualification. Set WORKBENCH_TEST_JAVA
to an admitted JDK 25 executable to exercise them without downloading tools.
"""
from hashlib import sha256
from base64 import urlsafe_b64encode
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from workbench_core import check_attachments
from workbench_profile_supersymmetry import recipe_lifecycle
from test_recipe_checks import inputs, expectation

FIXTURES = {
    "groovyjarjarantlr4/v4/runtime/CodePointCharStream.java": '''package groovyjarjarantlr4.v4.runtime;
public class CodePointCharStream implements CharStream {
 private final String text; public CodePointCharStream(String text) { this.text=text; }
 public int size() { return text.length(); } public String toString() { return text; }
}''',
    "groovyjarjarantlr4/v4/runtime/CharStream.java": '''package groovyjarjarantlr4.v4.runtime;
public interface CharStream {}''',
    "org/apache/groovy/parser/antlr4/AstBuilder.java": '''package org.apache.groovy.parser.antlr4;
public class AstBuilder {
 private groovyjarjarantlr4.v4.runtime.CharStream createCharStream(org.codehaus.groovy.control.SourceUnit source) { return new groovyjarjarantlr4.v4.runtime.CodePointCharStream(source.text); }
 public void parse(org.codehaus.groovy.control.SourceUnit source) { createCharStream(source); }
}''',
    "net/minecraft/launchwrapper/LaunchClassLoader.java": '''package net.minecraft.launchwrapper;
public class LaunchClassLoader extends java.net.URLClassLoader {
 public LaunchClassLoader(java.net.URL[] urls) { super(urls, ClassLoader.getPlatformClassLoader()); }
 public void addClassLoaderExclusion(String name) {}
}''',
    "gregtech/api/util/EnumValidationResult.java": '''package gregtech.api.util;
public enum EnumValidationResult { VALID, INVALID, SKIP }''',
    "gregtech/api/util/ValidationResult.java": '''package gregtech.api.util;
public class ValidationResult {
 private final Object recipe; private final EnumValidationResult type;
 public ValidationResult(Object recipe, EnumValidationResult type) { this.recipe=recipe; this.type=type; }
 public Object getResult() { return recipe; } public EnumValidationResult getType() { return type; }
}''',
    "gregtech/api/recipes/Recipe.java": '''package gregtech.api.recipes;
public class Recipe {
 public int getDuration() { return 20; } public int getEUt() { return 30; }
 public java.util.List<Object> getInputs() { return java.util.List.of(); }
 public java.util.List<Object> getFluidInputs() { return java.util.List.of(); }
}''',
    "gregtech/api/recipes/RecipeMap.java": '''package gregtech.api.recipes;
import gregtech.api.util.*;
public class RecipeMap {
 public final String unlocalizedName="mixer"; public Recipe current;
 public boolean addRecipe(ValidationResult value) { value=postValidateRecipe(value); return value.getType()==EnumValidationResult.VALID && compileRecipe((Recipe)value.getResult()); }
 protected ValidationResult postValidateRecipe(ValidationResult value) { return value; }
 public boolean compileRecipe(Recipe recipe) { if(current!=null) return false; current=recipe; return true; }
 public boolean removeRecipe(Recipe recipe) { if(current!=recipe) return false; current=null; return true; }
 void removeAllRecipes() { current=null; }
}''',
    "gregtech/api/recipes/RecipeBuilder.java": '''package gregtech.api.recipes;
import gregtech.api.util.*;
public class RecipeBuilder {
 public final RecipeMap recipeMap; public boolean invalid=false, throwing=false;
 public RecipeBuilder(RecipeMap map) { recipeMap=map; }
 public void buildAndRegister() { if(throwing) throw new IllegalStateException("fixture-failure"); recipeMap.addRecipe(build()); }
 public ValidationResult build() { return new ValidationResult(new Recipe(), validate()); }
 protected EnumValidationResult validate() { return invalid ? EnumValidationResult.INVALID : EnumValidationResult.VALID; }
}''',
    "org/codehaus/groovy/control/SourceUnit.java": '''package org.codehaus.groovy.control;
public class SourceUnit {
 public final String text; public SourceUnit(String text) { this.text=text; }
 public Input getSource() { return new Input(); }
 public String getName() { return java.nio.file.Path.of("groovy/postInit/Fixture.groovy").toAbsolutePath().toUri().toString(); }
 public Module getAST() { return new Module(this); }
 public static class Input { public java.net.URI getURI() { return null; } }
 public static class Module {
  final SourceUnit unit; public Module(SourceUnit unit) { this.unit=unit; }
  public Module getUnit() { return this; }
  public SourceUnit getScriptSourceLocation(String name) { return unit; }
 }
}''',
    "FixtureScript.java": '''import gregtech.api.recipes.*;
public class FixtureScript {
 public static void register(RecipeMap map) { new RecipeBuilder(map).buildAndRegister(); }
 public static void registerBuilder(RecipeBuilder builder) { builder.buildAndRegister(); }
}''',
    "com/cleanroommc/groovyscript/sandbox/CustomGroovyScriptEngine.java": '''package com.cleanroommc.groovyscript.sandbox;
public class CustomGroovyScriptEngine {
 public void onCompileClass(org.codehaus.groovy.control.SourceUnit source, String name, Class<?> type, byte[] bytes, boolean inner) {}
}''',
    "Harness.java": '''import java.nio.file.*;
import java.util.*;
import net.minecraft.launchwrapper.LaunchClassLoader;
public class Harness {
 public static void main(String[] args) throws Exception {
  var loader=new LaunchClassLoader(new java.net.URL[]{Path.of(args[0]).toUri().toURL()});
  var mapClass=loader.loadClass("gregtech.api.recipes.RecipeMap");
  var builderClass=loader.loadClass("gregtech.api.recipes.RecipeBuilder");
  loader.loadClass("com.cleanroommc.groovyscript.sandbox.CustomGroovyScriptEngine");
  Class<?> sourceClass=loader.loadClass("org.codehaus.groovy.control.SourceUnit");
  Object unit=sourceClass.getConstructor(String.class).newInstance("fixture parser input\\n");
  Class<?> parser=loader.loadClass("org.apache.groovy.parser.antlr4.AstBuilder");
  parser.getMethod("parse",sourceClass).invoke(parser.getConstructor().newInstance(),unit);
  Class<?> script=loader.loadClass("FixtureScript");
  Class<?> engine=loader.loadClass("com.cleanroommc.groovyscript.sandbox.CustomGroovyScriptEngine");
  engine.getMethod("onCompileClass",sourceClass,String.class,Class.class,byte[].class,boolean.class).invoke(
   engine.getConstructor().newInstance(),unit,"groovy/postInit/Fixture.groovy",script,Files.readAllBytes(Path.of(args[0],"FixtureScript.class")),false);
  Object map=mapClass.getConstructor().newInstance();
  var constructor=builderClass.getConstructor(mapClass); var register=builderClass.getMethod("buildAndRegister");
  var sourceRegister=script.getMethod("registerBuilder",builderClass);
  script.getMethod("register",mapClass).invoke(null,map);
  Object first=mapClass.getField("current").get(map);
  sourceRegister.invoke(null,constructor.newInstance(map));
  if(first!=mapClass.getField("current").get(map)) throw new AssertionError("duplicate changed winner");
  Object invalid=constructor.newInstance(map); builderClass.getField("invalid").set(invalid,true); sourceRegister.invoke(null,invalid);
  Object throwing=constructor.newInstance(map); builderClass.getField("throwing").set(throwing,true);
  try { sourceRegister.invoke(null,throwing); throw new AssertionError("exception swallowed"); }
  catch(java.lang.reflect.InvocationTargetException failure) { if(!failure.getCause().getMessage().equals("fixture-failure")) throw failure; }
  if(!((Boolean)mapClass.getMethod("removeRecipe",first.getClass()).invoke(map,first))) throw new AssertionError("removal failed");
  System.out.println("OUTCOME:success-duplicate-invalid-exception-removal");
  register.invoke(constructor.newInstance(mapClass.getConstructor().newInstance()));
  if(args.length>2 && args[2].equals("overflow")) for(int i=0;i<10000;i++) register.invoke(invalid);
  if(args.length>1) {
   Class<?> trace=Class.forName("dev.workbench.recipe.RecipeTrace",true,null);
   List<?> objects=(List<?>)mapClass.getMethod("workbenchTraceRecipes").invoke(null);
   Set<Object> selected=Collections.newSetFromMap(new IdentityHashMap<>()); selected.add(first);
   Map<?,?> result=(Map<?,?>)mapClass.getMethod("workbenchTraceSnapshot",Map.class,Set.class,String.class).invoke(null,new IdentityHashMap<>(),selected,"groovy/postInit/Fixture.groovy");
   System.out.println("TRACE:"+result);
   if(!result.get("state").equals(args[1])) throw new AssertionError(result);
   if(args[1].equals("complete")) {
    String text=result.toString();
    if(!text.contains("operation=insertion") || !text.contains("returned=false") || !text.contains("validation=INVALID") || !text.contains("outcome=threw") || !text.contains("operation=removal") || !text.contains("class_name=FixtureScript")) throw new AssertionError(text);
    if(((List<?>)result.get("events")).size()!=19) throw new AssertionError("unrelated roots selected: "+text);
   }
  }
 }
}''',
}


class RecipeObserverJavaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("WORKBENCH_TEST_JAVA") or shutil.which("java")
        if not selected:
            raise unittest.SkipTest("requires explicit JDK 25; no tools are downloaded")
        cls.java = Path(selected).resolve()
        version = subprocess.check_output([str(cls.java), "-version"], stderr=subprocess.STDOUT).decode()
        if 'version "25.' not in version:
            raise unittest.SkipTest("requires JDK 25")

    def test_decision_branches_preserve_single_evaluation_return_identity_and_exception(self):
        from workbench_profile_supersymmetry import recipe_decisions
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            specification = recipe_lifecycle.attachment(inputs(), "nonce", expectation=expectation())
            check_attachments.build(root / "observer", self.java, specification)
            payload = root / "observer/payload/workbench-check-attachment"
            classpath = os.pathsep.join(map(str, (payload / "observer.jar", payload / "bridge.jar", root)))
            source = root / "DecisionHarness.java"
            source.write_text('''import java.lang.classfile.*;
import java.lang.classfile.instruction.*;
import java.nio.file.*;
import java.util.*;
import dev.workbench.recipe.*;
public class DecisionHarness {
 public static class Choice {
  public final String unlocalizedName="mixer";
  public int evaluations=0; public boolean fail=false;
  public final RuntimeException failure=new IllegalStateException("same exception");
  public Object choose(Object first,Object second) {
   if(first==second) return first;
   evaluations++; if(fail) throw failure;
   return second;
  }
 }
 static class Loader extends ClassLoader {
  Class<?> define(byte[] raw) { return defineClass(null,raw,0,raw.length); }
 }
 public static void main(String[] args) throws Exception {
  for(var entry:RecipeDecisionHooks.SITES.entrySet()) for(var site:entry.getValue().entrySet())
   System.out.println("SITE:"+entry.getKey()+":"+site.getKey()+"="+site.getValue().taken()+","+site.getValue().fallthrough());
  byte[] original=Files.readAllBytes(Path.of(args[0],"DecisionHarness$Choice.class"));
  ClassFile file=ClassFile.of();
  var model=file.parse(original);
  var changed=file.transformClass(model,(builder,element)-> {
   if(element instanceof MethodModel method && method.methodName().equalsString("choose")) {
    int pc=0; Map<Integer,RecipeDecisionHooks.Site> sites=new HashMap<>();
    for(var instruction:method.code().orElseThrow()) if(instruction instanceof Instruction ins) {
     if(ins instanceof BranchInstruction branch && branch.opcode()==Opcode.IF_ACMPNE)
      sites.put(pc,new RecipeDecisionHooks.Site(Opcode.IF_ACMPNE,"different-recipe-blocks-insertion","same-recipe-leaf",1,-1,-1,-1,true));
     pc+=ins.sizeInBytes();
    }
    if(sites.size()!=1) throw new AssertionError("fixture branch changed");
    builder.transformMethod(method,MethodTransform.transformingCode(new RecipeDecisionHooks.Decisions("fixture",sites)));
   } else builder.with(element);
  });
  Properties config=new Properties(); config.setProperty("nonce","fixture"); config.setProperty("candidate","fixture");
  RecipeTrace.configure(config); RecipeDecisions.installed(RecipeDecisionHooks.required());
  for(boolean on:List.of(false,true)) {
   Class<?> type=new Loader().define(on?changed:original); Object map=type.getConstructor().newInstance();
   var choose=type.getMethod("choose",Object.class,Object.class);
   Object first=new Object(),second=new Object();
   if(on) RecipeDecisions.query("q0");
   Object token=on?RecipeDecisions.enterLookup(map,2147483647L,List.of(),List.of(),false):null;
   Object result=choose.invoke(map,first,second);
   if(result!=second || type.getField("evaluations").getInt(map)!=1) throw new AssertionError("evaluation or selected identity changed");
   if(on) { RecipeDecisions.exitLookup(token,result,false); RecipeDecisions.query(null); }
   if(choose.invoke(map,first,first)!=first || type.getField("evaluations").getInt(map)!=1) throw new AssertionError("short circuit changed");
   type.getField("fail").setBoolean(map,true);
   try { choose.invoke(map,first,second); throw new AssertionError("exception swallowed"); }
   catch(java.lang.reflect.InvocationTargetException failure) {
    if(failure.getCause()!=type.getField("failure").get(map)) throw new AssertionError("exception identity changed");
   }
   if(type.getField("evaluations").getInt(map)!=2) throw new AssertionError("predicate was re-evaluated");
  }
  if(args.length>1) {
   Class<?> type=new Loader().define(changed); Object map=type.getConstructor().newInstance();
   var choose=type.getMethod("choose",Object.class,Object.class); Object first=new Object(),second=new Object();
   RecipeDecisions.query("q1"); Object token=RecipeDecisions.enterLookup(map,2147483647L,List.of(),List.of(),false);
   for(int i=0;i<5000;i++) if(choose.invoke(map,first,second)!=second) throw new AssertionError("bound changed result");
   if(type.getField("evaluations").getInt(map)!=5000) throw new AssertionError("bound changed evaluation count");
   RecipeDecisions.exitLookup(token,second,false); RecipeDecisions.query(null);
  }
  var report=RecipeDecisions.snapshot(new IdentityHashMap<>(),Map.of("events",List.of()));
  if(args.length>1) {
   if(!report.get("state").equals("incomplete") || !report.get("problems").toString().contains("decision-step-bound")) throw new AssertionError(report);
  } else if(!report.get("state").equals("complete") || ((List<?>)report.get("steps")).size()!=1) throw new AssertionError(report);
  System.out.println("PRESERVED:arguments,return-identity,exception-identity,single-evaluation,unscoped-exclusion");
 }
}''')
            subprocess.run([str(self.java.with_name("javac")), "-g", "-cp", classpath, str(source)], check=True, capture_output=True, timeout=60)
            result = subprocess.run([str(self.java), "-cp", classpath, "DecisionHarness", str(root)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PRESERVED:arguments", result.stdout)
            actual = dict(line.removeprefix("SITE:").split("=", 1) for line in result.stdout.splitlines() if line.startswith("SITE:"))
            self.assertEqual(actual, {key: ",".join(value) for key, value in recipe_decisions.SITE_OUTCOMES.items()})
            overflow = subprocess.run([str(self.java), "-cp", classpath, "DecisionHarness", str(root), "overflow"], capture_output=True, text=True, timeout=60)
            self.assertEqual(overflow.returncode, 0, overflow.stdout + overflow.stderr)
            self.assertIn("PRESERVED:arguments", overflow.stdout)

    def test_only_nonexecuting_accessor_session_metadata_is_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            specification = recipe_lifecycle.attachment(inputs(), "nonce", expectation=expectation())
            check_attachments.build(root / "observer", self.java, specification)
            payload = root / "observer/payload/workbench-check-attachment"
            helper = root / "Digest.java"
            helper.write_text('''import java.nio.file.*;
public class Digest {
 public static void main(String[] args) throws Exception {
  try { System.out.println(dev.workbench.recipe.RecipeAgent.definitionDigest(Files.readAllBytes(Path.of(args[0])))); }
  catch(IllegalArgumentException rejected) { System.out.println("REJECTED"); }
 }
}''')
            classpath = os.pathsep.join(map(str, (payload / "observer.jar", payload / "bridge.jar", root)))
            subprocess.run([str(self.java.with_name("javac")), "-cp", classpath, str(helper)], check=True, capture_output=True, timeout=60)
            results = []
            for case, uuid, changed, executable in (
                ("a", "11111111-1111-1111-1111-111111111111", False, False),
                ("b", "22222222-2222-2222-2222-222222222222", False, False),
                ("changed", "11111111-1111-1111-1111-111111111111", True, False),
                ("executable", "11111111-1111-1111-1111-111111111111", False, True),
            ):
                directory = root / case; directory.mkdir()
                annotation = directory / "MixinMerged.java"
                annotation.write_text('package org.spongepowered.asm.mixin.transformer.meta; @java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.RUNTIME) public @interface MixinMerged { String sessionId(); }')
                source = directory / "RecipeBuilder.java"
                source.write_text('package gregtech.api.recipes; import org.spongepowered.asm.mixin.transformer.meta.MixinMerged; public class RecipeBuilder { '
                                  + '@MixinMerged(sessionId="' + uuid + '") public Object getOnBuildAction() { return null; } '
                                  + '@MixinMerged(sessionId="' + uuid + '") public Object getRecipeMap() { return null; } '
                                  + ('public String value() { return "' + uuid + '"; }' if executable else 'public int value() { return ' + ('2' if changed else '1') + '; }') + '}')
                subprocess.run([str(self.java.with_name("javac")), "-g", "-d", str(directory), str(annotation), str(source)], check=True, capture_output=True, timeout=60)
                result = subprocess.check_output([str(self.java), "-cp", classpath, "Digest", str(directory / "gregtech/api/recipes/RecipeBuilder.class")], timeout=60).decode().strip()
                results.append(result)
            self.assertEqual(results[0], results[1])
            self.assertNotEqual(results[0], results[2])
            self.assertEqual(results[3], "REJECTED")

    def test_real_transformer_preserves_results_exceptions_and_exposes_stage_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            classes = root / "fixture-classes"; classes.mkdir()
            paths = []
            for name, text in FIXTURES.items():
                path = root / "fixture-source" / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text); paths.append(str(path))
            subprocess.run([str(self.java.with_name("javac")), "-g", "-d", str(classes), *paths], check=True, capture_output=True, timeout=60)
            specification = recipe_lifecycle.attachment(inputs(), "nonce", expectation=expectation())
            config = "nonce=nonce\ncandidate=synthetic\n" + "".join("target." + name + "=" + sha256((classes/(name+".class")).read_bytes()).hexdigest() + "\n" for name in recipe_lifecycle.TARGETS)
            config += "source." + urlsafe_b64encode(b"groovy/postInit/Fixture.groovy").decode().rstrip("=") + "=" + sha256(b"fixture parser input\n").hexdigest() + "\n"
            specification["configuration"] = config.encode()
            value = check_attachments.build(root / "build", self.java, specification)
            runtime = root / "runtime"; runtime.mkdir()
            check_attachments.materialize(runtime, root / "build", value)
            def run(arguments, expected="complete", extra=()):
                return subprocess.run([str(self.java), *arguments, "-cp", str(classes), "Harness", str(classes), *([expected, *extra] if arguments else [])],
                                      cwd=runtime, capture_output=True, text=True, timeout=60)
            off = run([]); on = run(check_attachments.arguments(runtime, value))
            self.assertEqual(off.returncode, 0, off.stdout + off.stderr)
            self.assertEqual(on.returncode, 0, on.stdout + on.stderr)
            outcome = lambda result: next(line for line in result.stdout.splitlines() if line.startswith("OUTCOME:"))
            self.assertEqual(outcome(off), outcome(on))
            self.assertIn("state=complete", on.stdout)
            overflow = run(check_attachments.arguments(runtime, value), "incomplete", ["overflow"])
            self.assertEqual(overflow.returncode, 0, overflow.stdout[-5000:] + overflow.stderr)
            self.assertIn("event-bound", overflow.stdout)
            for case, changed in (
                ("definition", config.replace("target.gregtech/api/recipes/RecipeBuilder=", "target.gregtech/api/recipes/RecipeBuilder=wrong")),
                ("source", config.replace(sha256(b"fixture parser input\n").hexdigest(), "0"*64)),
            ):
                specification["configuration"] = changed.encode()
                broken = check_attachments.build(root / case, self.java, specification)
                broken_runtime = root / (case + "-runtime"); broken_runtime.mkdir()
                check_attachments.materialize(broken_runtime, root / case, broken)
                result = subprocess.run([str(self.java), *check_attachments.arguments(broken_runtime, broken), "-cp", str(classes), "Harness", str(classes), "incomplete"],
                                        cwd=broken_runtime, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(outcome(off), outcome(result))
