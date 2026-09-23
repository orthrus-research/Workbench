package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Parser/structure tests only; hostile execution witnesses run isolated. */
class MaterialSourceAdmissionTest {
    private static final Set<String> ROOTS=Set.of("classes/","globals/","material/","preInit/");
    private static final Set<String> TRANSFORMS=Set.of("groovy.transform.TupleConstructor");
    private MaterialSourceAdmission.Result inspect(String source) {
        return MaterialSourceAdmission.inspect(Map.of("groovy/material/Test.groovy",source),ROOTS,TRANSFORMS);
    }
    @Test void traitKeywordIsDistinctFromExplicitAnnotationsAndDefaultInterfaces() {
        var transforms=Set.of("groovy.transform.Trait");
        for(String source:List.of("trait Marker {}", "class Test { trait Base { int amount }; trait Child extends Base {} }")) {
            var result=MaterialSourceAdmission.inspect(Map.of("groovy/material/Test.groovy",source),ROOTS,transforms);
            assertTrue(result.structurallyAdmitted(),result.toString());
            assertEquals(false,result.json().get("candidateCodeGenerated"));
            assertEquals(source.startsWith("trait")?Set.of("material.Marker"):Set.of("material.Test$Base","material.Test$Child"),result.traits());
        }
        for(String source:List.of("@groovy.transform.Trait class Test {}", "interface Test { default int value() { 1 } }",
                "trait Test { @Deprecated int amount }"))
            assertFalse(MaterialSourceAdmission.inspect(Map.of("groovy/material/Test.groovy",source),ROOTS,transforms).structurallyAdmitted(),source);
    }
    @Test void completeOriginalCorpusIsUnchangedAndStructurallyAdmitted() throws Exception {
        var files=new LinkedHashMap<String,String>();
        Path root=Path.of("../tests/fixtures/material-program");
        try(var paths=Files.walk(root.resolve("groovy"))) {
            for(Path path:paths.filter(p->p.toString().endsWith(".groovy")).toList())files.put(root.relativize(path).toString(),Files.readString(path));
        }
        var original=new LinkedHashMap<>(files);
        var result=MaterialSourceAdmission.inspect(files,ROOTS,TRANSFORMS);
        assertTrue(result.structurallyAdmitted(),result.toString());
        assertEquals(3,result.classes().size());
        assertEquals(original,files);
        assertEquals(false,result.json().get("runtimeAdmissionQualified"));
    }
    @Test void unknownImportsAreNotPretendedResolvedByStructuralParsing() {
        var result=inspect("package material\nimport unavailable.addon.Materials\nclass Test {}\n");
        assertTrue(result.structurallyAdmitted(),result.toString());
    }
    @Test void unqualifiedAnnotationsAreRejectedIncludingAliasAndLocations() {
        for(String source:List.of("@groovy.transform.CompileStatic\nclass Test {}",
                "import groovy.transform.CompileStatic as Alias\n@Alias class Test {}",
                "class Test { @Deprecated String value; void f(@Deprecated String value) {} }",
                "class Test { def callback = { @Deprecated Object value -> value } }",
                "@Deprecated package material\nclass Test {}")) {
            var result=inspect(source);
            assertFalse(result.structurallyAdmitted(),source);
            assertTrue(result.findings().stream().anyMatch(f->f.code().equals("admission.annotation")&&f.line()>0&&f.column()>0),result.toString());
        }
    }
    @Test void trustedPackageAndConflictingClassDeclarationsRefuse() {
        assertTrue(inspect("package research.orthrus.axiom\nclass Test {}\n").findings().stream().anyMatch(f->f.code().equals("admission.package")));
        var result=MaterialSourceAdmission.inspect(Map.of("groovy/material/One.groovy","package material\nclass Duplicate {}",
                "groovy/material/Two.groovy","package material\nclass Duplicate {}"),ROOTS,TRANSFORMS);
        assertTrue(result.findings().stream().anyMatch(f->f.code().equals("admission.class-conflict")),result.toString());
    }
    @Test void absentPackageUsesDeclaredNativeSourcePathWithoutChangingSource() {
        var result=inspect("class Test {}\n");
        assertTrue(result.structurallyAdmitted(),result.toString());
        assertEquals(Map.of("material.Test","groovy/material/Test.groovy"),result.classes());
    }
    @Test void sourcePathsAreContextBoundNotCandidateSelectable() {
        for(String path:List.of("../Test.groovy","groovy/../Test.groovy","groovy/material/../Test.groovy",
                "groovy/research/orthrus/axiom/Test.groovy","groovy/material\\Test.groovy",
                "groovy/material/Test.groovy/../Other.groovy","groovy/material//Test.groovy"))
            assertFalse(MaterialSourceAdmission.inspect(Map.of(path,"class Test {}"),ROOTS,TRANSFORMS).structurallyAdmitted(),path);
    }
    @Test void nativeSyntaxErrorRetainsSourceLocationAndCannotBecomeAdmission() {
        var result=inspect("class Test { static broken( }\n");
        assertFalse(result.structurallyAdmitted());
        assertTrue(result.findings().stream().anyMatch(f->f.code().equals("source.syntax")&&f.path().equals("groovy/material/Test.groovy")&&f.line()>0),result.toString());
    }
    @Test void resultsCannotBeMutatedByCallers() {
        var result=inspect("class Test {}\n");
        assertThrows(UnsupportedOperationException.class,()->result.classes().clear());
        assertThrows(UnsupportedOperationException.class,()->result.findings().clear());
    }
    @Test void candidateCannotReplaceCompilerMetaclassProtocol() {
        for(String code:List.of("class Test { Object getMetaClass() { null } }",
                "class Test { Object $getLookup() { null } }","class Test { Object metaClass }",
                "class Test { Object this$dist$invoke$1(String name, Object args) { null } }"))
            assertTrue(inspect(code).findings().stream().anyMatch(f->f.code().equals("admission.meta-protocol")),code);
    }
    @Test void nativeParserRejectsEscapedIdentifiersRatherThanHostRepairingThem() {
        String escaped="package research."+"\\"+"u006frthrus.axiom\nclass Test {}";
        assertTrue(inspect(escaped).findings().stream().anyMatch(f->f.code().equals("source.syntax")));
        escaped="@groovy.trans"+"\\"+"u0066orm.CompileStatic\nclass Test {}";
        assertTrue(inspect(escaped).findings().stream().anyMatch(f->f.code().equals("source.syntax")));
    }
    @Test void commentsAndStringDataAreNotMistakenForDeclarations() {
        var result=inspect("// @groovy.transform.CompileStatic\nclass Test { static String text = '@package research.orthrus.axiom' }");
        assertTrue(result.structurallyAdmitted(),result.toString());
    }
    @Test void parsedAttributeSyntaxIsNotAnAnnotationOrRuntimePermission() {
        for(String text:List.of("delegate.@\"max$field\" = it", "delegate. /* field */ @maxInputs = 2")) {
            var result=inspect("class Test { void edit() { " + text + " } }");
            assertTrue(result.structurallyAdmitted(),result.toString());
            assertEquals(false,result.json().get("runtimeAdmissionQualified"));
        }
        assertFalse(inspect("class Test { @Deprecated int x; void edit() { delegate.@maxInputs = 2 } }").structurallyAdmitted());
    }
    @Test void tupleConstructorAdmissionIsNativePolicyBoundAndDoesNotRunTheTransform() {
        for(String source:List.of("@groovy.transform.TupleConstructor class Test { String name; int amount }",
                "import groovy.transform.TupleConstructor\n@TupleConstructor class Test { String name; int amount }",
                "import groovy.transform.TupleConstructor as Tuple\n@Tuple class Test { String name; int amount }")) {
            var result=inspect(source);assertTrue(result.structurallyAdmitted(),result.toString());
            assertEquals(false,result.json().get("candidateCodeGenerated"));
            assertFalse(MaterialSourceAdmission.inspect(Map.of("groovy/material/Test.groovy",source),ROOTS,Set.of()).structurallyAdmitted());
        }
        for(String source:List.of("@TupleConstructor class Test {}",
                "import unknown.TupleConstructor\n@TupleConstructor class Test {}",
                "import groovy.transform.TupleConstructor\n@TupleConstructor(force=true) class Test {}"))
            assertFalse(inspect(source).structurallyAdmitted(),source);
    }
    @Test void constructorTransformDoesNotImplicitlyAdmitTraitsOrRecords() {
        for(String source:List.of("trait Test {}","record Test(String name) {}"))
            assertTrue(inspect(source).findings().stream().anyMatch(f->f.code().equals("admission.annotation")),source);
        assertThrows(IllegalArgumentException.class,()->MaterialSourceAdmission.inspect(
            Map.of("groovy/material/Test.groovy","class Test {}"),ROOTS,Set.of("groovy.transform.CompileStatic")));
    }
    @Test void nativeRecordKeywordIsDistinctFromExplicitTransformAnnotations() {
        Set<String> records=Set.of("groovy.transform.RecordType");
        for(String source:List.of("record Test(String name, int amount) {}",
                "class Test { record Reagent(String name, boolean liquid, double duration) {} }")) {
            var result=MaterialSourceAdmission.inspect(Map.of("groovy/material/Test.groovy",source),ROOTS,records);
            assertTrue(result.structurallyAdmitted(),result.toString());
            assertEquals(false,result.json().get("candidateCodeGenerated"));
        }
        for(String source:List.of("@groovy.transform.RecordType class Test { String name }",
                "@groovy.transform.RecordType record Test(String name) {}",
                "@groovy.transform.RecordOptions(mode=groovy.transform.RecordTypeMode.EMULATE) record Test(String name) {}",
                "record Test(@Deprecated String name) {}","trait Test {}"))
            assertFalse(MaterialSourceAdmission.inspect(Map.of("groovy/material/Test.groovy",source),ROOTS,records).structurallyAdmitted(),source);
    }
}
