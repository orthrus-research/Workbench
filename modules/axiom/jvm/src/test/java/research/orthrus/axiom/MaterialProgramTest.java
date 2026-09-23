package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialProgramTest {
    @TempDir Path temporary;
    @Test void packCannotFallBackToPreparedBootstrapButBoundedBaseRemainsSeparate() {
        var pack=Map.<String,Object>of("id","supersymmetry:material-authoring-pack");
        var failure=assertThrows(Failure.class,()->MaterialProgram.requireRuntimeRoute(pack,Map.of()));
        assertEquals("requires-context",failure.kind);
        assertEquals("material-program.original-runtime-required",failure.rule);
        assertDoesNotThrow(()->MaterialProgram.requireRuntimeRoute(pack,Map.of("nativeInitialization",Map.of())));
        assertDoesNotThrow(()->MaterialProgram.requireRuntimeRoute(Map.of("id","supersymmetry:material-authoring-gt-base"),Map.of()));
    }
    @Test void nativeHomesReuseVerifiedArtifactBytesWithIndependentSavedState() throws Exception {
        Path runtime = Files.createDirectory(temporary.resolve("runtime"));
        Path original = runtime.resolve("native-home/mods/Original Mod.jar");
        Files.createDirectories(original.getParent());
        Files.writeString(original, "complete original artifact");
        Map<String,Path> verified = Map.of("native-home/mods/Original Mod.jar", original);
        Path first = Files.createDirectory(temporary.resolve("first"));
        Path second = Files.createDirectory(temporary.resolve("second"));
        assertEquals(runtime.resolve("native-home"), MaterialProgram.linkNativeArtifacts(runtime, first, verified));
        MaterialProgram.linkNativeArtifacts(runtime, second, verified);
        for (Path home : List.of(first, second)) {
            Path link = home.resolve("mods/Original Mod.jar");
            assertTrue(Files.isSymbolicLink(link));
            assertTrue(Files.isSameFile(original, link));
            assertEquals("complete original artifact", Files.readString(link));
        }
        Files.createDirectory(first.resolve("config"));
        Files.writeString(first.resolve("config/saved.cfg"), "first worker state");
        assertFalse(Files.exists(second.resolve("config/saved.cfg")));
        assertFalse(Files.exists(runtime.resolve("native-home/config/saved.cfg")));
        assertEquals("complete original artifact", Files.readString(original));
    }
    private final Set<String> roots=new LinkedHashSet<>(List.of("classes/","globals/","material/","preInit/"));
    private final Map<String,Object> context=Map.of("runConfig","groovy/runConfig.json","scriptOwner","supersymmetry","loader","preInit",
            "loaders",Map.of("preInit",new ArrayList<>(roots),"postInit",List.of("prePostInit/","postInit/")));
    private Map<String,String> files() {
        return new LinkedHashMap<>(Map.of("groovy/runConfig.json",Json.write(Map.of("packName","Developer program","packId","supersymmetry",
                "version","1","debug",false,"loaders",Map.of("preInit",new ArrayList<>(roots)))),
                "groovy/material/Example.groovy","package material\nclass Example {}"));
    }
    private MaterialProgram.Program unpack(Map<String,String> files) throws Exception {
        Path zip=Files.createTempFile(temporary,"program-",".zip");
        try(var stream=new ZipOutputStream(Files.newOutputStream(zip))) {
            for(var entry:files.entrySet()) {
                stream.putNextEntry(new ZipEntry(entry.getKey()));stream.write(entry.getValue().getBytes(java.nio.charset.StandardCharsets.UTF_8));stream.closeEntry();
            }
        }
        var available=new LinkedHashSet<>(roots);available.addAll(List.of("prePostInit/","postInit/"));
        return MaterialProgram.unpack(zip,Files.createTempDirectory(temporary,"home-"),context,available);
    }
    @Test void generalCompleteProgramKeepsEverySavedByteAndInventory() throws Exception {
        var files=files();files.put("groovy/classes/nested/Helper.groovy","package classes.nested\nclass Helper {}\n");
        var program=unpack(files);
        assertEquals(3,program.inventory().size());assertEquals(2,program.sources().size());
        assertEquals(files.get("groovy/classes/nested/Helper.groovy"),program.sources().get("groovy/classes/nested/Helper.groovy"));
        assertEquals(Json.digest(program.inventory()),program.digest());
        var reversed=new ArrayList<>(files.entrySet());Collections.reverse(reversed);var reordered=new LinkedHashMap<String,String>();
        reversed.forEach(entry->reordered.put(entry.getKey(),entry.getValue()));
        assertEquals(program.digest(),unpack(reordered).digest());
        files.put("groovy/material/Example.groovy",files.get("groovy/material/Example.groovy")+"\n");
        assertNotEquals(program.digest(),unpack(files).digest());
    }
    @Test void nativeAcknowledgementIsComputedFromAllOriginalEntriesWithExactUnicodeEncoding() throws Exception {
        var files=files();files.put("groovy/material/😀\".groovy","// saved\r\n");
        files.put("groovy/material/\ue000.groovy","// saved\r\n");files.put("config/é.cfg","binary-resource\r\n");
        var program=unpack(files);var ack=MaterialProgram.sourceAcknowledgement(program);
        assertEquals(Set.of("schema","sha256","fileCount","inventoryEncoding","scope"),ack.keySet());
        assertEquals(files.size(),ack.get("fileCount"));assertEquals(program.digest(),ack.get("sha256"));
        assertTrue(MaterialProgram.validSourceAcknowledgement(ack));assertFalse(ack.containsKey("files"));
        var paths=program.inventory().stream().map(row->row.get("path")).toList();
        assertTrue(paths.indexOf("groovy/material/\ue000.groovy")<paths.indexOf("groovy/material/😀\".groovy"));
        var canonical=new ArrayList<Map<String,Object>>();
        for(String path:List.of("groovy/é.groovy","groovy/\ue000.groovy","groovy/😀\".groovy"))
            canonical.add(Map.of("path",path,"sha256","1c3a649ff74e226cf2385db565d6071cb49f42e51c15b45404fb72556b88d74f","size",10));
        assertEquals("ee083647789646428b3ac1ec1fc647ff9d43e40e4158db342b44720cdee44c4d",MaterialProgram.inventoryDigest(canonical));
        files.put("config/é.cfg","changed\r\n");
        assertNotEquals(ack.get("sha256"),MaterialProgram.sourceAcknowledgement(unpack(files)).get("sha256"));
        files.remove("groovy/material/😀\".groovy");
        assertEquals(files.size(),MaterialProgram.sourceAcknowledgement(unpack(files)).get("fileCount"));
    }
    @Test void missingRunConfigAndNonportablePathsRefuse() {
        var files=files();files.remove("groovy/runConfig.json");assertThrows(Failure.class,()->unpack(files));
        for(String path:List.of("groovy/../Outside.groovy","groovy//config.json","../Outside.groovy","groovy/.git/config","groovy/a\\b.groovy")) {
            var candidate=files();candidate.put(path,"class Other {}");assertThrows(Failure.class,()->unpack(candidate));
        }
    }
    @Test void completeWorkspacePreservesMetadataAndDeferredSourcesWithoutSchedulingThem() throws Exception {
        var files=files();var config=Json.object(Json.parse(files.get("groovy/runConfig.json")));
        config.put("loaders",context.get("loaders"));files.put("groovy/runConfig.json",Json.write(config));
        files.put("groovy/groovy.iml","<module />\r\n");files.put("groovy/config.json","uninterpreted metadata");
        files.put("groovy/postInit/Recipe.groovy","log.error('must not run in preInit')");
        var program=unpack(files);
        assertEquals(files.size(),program.inventory().size());assertEquals(2,program.sources().size());
        assertEquals(List.of("postInit"),program.scope().get("deferredLoaders"));
        assertEquals(List.of("groovy/postInit/Recipe.groovy"),program.scope().get("deferredSourceFiles"));
        assertEquals("preInit",program.scope().get("selectedLoader"));
        files.remove("groovy/postInit/Recipe.groovy");
        assertNotEquals(program.digest(),unpack(files).digest());
        assertEquals(List.of(),unpack(files).scope().get("deferredSourceFiles"));
    }
    @Test void unknownSourceRootIsRetainedForExplicitAdmissionFeedback() throws Exception {
        var files=files();files.put("groovy/addon/Other.groovy","class Other {}");
        var program=unpack(files);
        assertTrue(program.sources().containsKey("groovy/addon/Other.groovy"));
        assertFalse(MaterialSourceAdmission.inspect(program.sources(),roots,Set.of()).structurallyAdmitted());
    }
    @Test void savedConfigurationIsBoundButNotCompiledOrClaimedApplied() throws Exception {
        var files=files();files.put("config/supercritical.cfg","B:disableAllMaterials=true\r\n");
        files.put("config/nested/not-a-script.groovy","this is not source to compile");
        var program=unpack(files);
        assertEquals(1,program.sources().size());assertEquals(4,program.inventory().size());
        var configuration=Json.object(program.scope().get("configuration"));
        var configFiles=program.inventory().stream().filter(row->Json.string(row.get("path")).startsWith("config/")).toList();
        assertEquals(configFiles.size(),configuration.get("fileCount"));
        assertFalse(configuration.containsKey("inventoryPointer"));
        assertEquals(Map.of("owner","core-retained-material-program","sourceProgramSha256",program.digest(),"filesPointer","/files"),
                configuration.get("inventoryReference"));
        assertEquals(Json.digest(configFiles),configuration.get("sha256"));
        assertEquals("not-qualified",configuration.get("application"));
        files.put("config/supercritical.cfg","B:disableAllMaterials=false\n");
        var changed=unpack(files);assertNotEquals(program.digest(),changed.digest());
        assertEquals(program.sources(),changed.sources());
        files.remove("config/supercritical.cfg");assertNotEquals(changed.digest(),unpack(files).digest());
    }
    @Test void nativeConfigurationResourcesKeepExactBinaryBytesOutsideSourceAdmission() throws Exception {
        byte[] binary=new byte[(1<<20)+1];binary[0]=(byte)0xff;binary[10]=(byte)0x80;
        Path zip=Files.createTempFile(temporary,"config-",".zip"),home=Files.createTempDirectory(temporary,"config-home-");
        try(var stream=new ZipOutputStream(Files.newOutputStream(zip))) {
            for(var entry:files().entrySet()) {
                stream.putNextEntry(new ZipEntry(entry.getKey()));stream.write(entry.getValue().getBytes(java.nio.charset.StandardCharsets.UTF_8));stream.closeEntry();
            }
            stream.putNextEntry(new ZipEntry("config/native/resource.bin"));stream.write(binary);stream.closeEntry();
        }
        var available=new LinkedHashSet<>(roots);available.addAll(List.of("prePostInit/","postInit/"));
        var program=MaterialProgram.unpack(zip,home,context,available);
        assertArrayEquals(binary,Files.readAllBytes(home.resolve("config/native/resource.bin")));
        assertEquals(1,program.sources().size());
        assertEquals(Json.bytesDigest(binary),program.inventory().stream().filter(row->row.get("path").equals("config/native/resource.bin")).findFirst().orElseThrow().get("sha256"));
    }
    @Test void configurationDoesNotSupplyMissingSourceOrBypassNativeInputBounds() {
        var missing=files();missing.remove("groovy/material/Example.groovy");missing.put("config/Fake.groovy","class Fake {}");
        assertThrows(Failure.class,()->unpack(missing));
        for(String path:List.of("config/../Outside.cfg","config//bad.cfg","config/.git/config","configuration/Outside.cfg")) {
            var candidate=files();candidate.put(path,"x");assertThrows(Failure.class,()->unpack(candidate));
        }
        for(String path:List.of("groovy/too-large.txt","config/too-large.bin")) {
            var candidate=files();candidate.put(path,"x".repeat((path.startsWith("config/")?4<<20:1<<20)+1));
            assertThrows(Failure.class,()->unpack(candidate));
        }
        var candidate=files();for(int i=0;i<6;i++)candidate.put("config/"+i+".bin","x".repeat(4<<20));
        assertThrows(Failure.class,()->unpack(candidate));
        var scripts=files();for(int i=0;i<512;i++)scripts.put("groovy/"+i+".txt","x");
        assertThrows(Failure.class,()->unpack(scripts));
        var configurations=files();for(int i=0;i<4096;i++)configurations.put("config/"+i+".cfg","x");
        assertThrows(Failure.class,()->unpack(configurations));
    }
    @Test void scriptOwnerAndLoaderOrderBelongToTheContext() {
        for(String changed:List.of(files().get("groovy/runConfig.json").replace("supersymmetry","another"),
                files().get("groovy/runConfig.json").replace("classes/","unselected/"))) {
            var files=files();files.put("groovy/runConfig.json",changed);assertThrows(Failure.class,()->unpack(files));
        }
    }

    @Test void observationSelectorsAreOptionalButStillBoundedAndValidated() {
        assertEquals(List.of(),MaterialProgram.requestedMaterials(Map.of(),List.of()));
        assertEquals(List.of(),MaterialProgram.requestedMaterials(Map.of("observeMaterials",List.of()),List.of()));
        var expectation=Map.<String,Object>of("material","supersymmetry:example");
        assertEquals(List.of("supersymmetry:example"),MaterialProgram.requestedMaterials(Map.of(),List.of(expectation)));
        assertEquals(List.of("supersymmetry:example"),MaterialProgram.requestedMaterials(
                Map.of("observeMaterials",List.of("supersymmetry:example")),List.of(expectation)));
        for(var selectors:List.of(List.of("bad"),List.of("supersymmetry:example","supersymmetry:example"),
                java.util.stream.IntStream.range(0,65).mapToObj(i->"supersymmetry:m"+i).toList()))
            assertThrows(Failure.class,()->MaterialProgram.requestedMaterials(Map.of("observeMaterials",selectors),List.of()));
    }
}
