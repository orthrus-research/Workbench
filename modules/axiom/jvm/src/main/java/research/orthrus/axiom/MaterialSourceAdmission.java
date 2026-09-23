package research.orthrus.axiom;

import org.codehaus.groovy.ast.*;
import org.codehaus.groovy.control.*;
import org.codehaus.groovy.control.messages.SyntaxErrorMessage;
import org.apache.groovy.parser.antlr4.GroovyLangLexer;
import org.apache.groovy.parser.antlr4.GroovyLexer;
import groovyjarjarantlr4.v4.runtime.CharStreams;
import java.util.*;

/**
 * Structural admission before native compilation. This only parses complete
 * source; it does not resolve imports, run transforms, generate or define classes.
 * A clear result is NOT runtime/API admission or a material-validity result.
 */
final class MaterialSourceAdmission {
    private MaterialSourceAdmission() {}
    private static final Set<String> RESERVED_METHODS=Set.of("getMetaClass","setMetaClass","invokeMethod",
            "getProperty","setProperty","propertyMissing","methodMissing","finalize");

    record Finding(String path, String code, String message, int line, int column) {
        Map<String,Object> json() {
            var value=new LinkedHashMap<String,Object>();
            value.put("path",path);value.put("code",code);value.put("message",message);
            if(line>0)value.put("line",line);
            if(column>0)value.put("column",column);
            return Collections.unmodifiableMap(value);
        }
    }
    record Result(Map<String,String> classes,Set<String> traits,List<Finding> findings) {
        Result { classes=Collections.unmodifiableMap(new LinkedHashMap<>(classes));traits=Set.copyOf(traits);findings=List.copyOf(findings); }
        boolean structurallyAdmitted() { return findings.isEmpty(); }
        Map<String,Object> json() {
            return Map.of("stage","source-structure","structurallyAdmitted",structurallyAdmitted(),
                    "classes",classes,"traitClasses",traits.stream().sorted().toList(),"findings",findings.stream().map(Finding::json).toList(),
                    "candidateCodeGenerated",false,"runtimeAdmissionQualified",false);
        }
    }

    /** Source roots are trusted context input, not a choice supplied by source. */
    static Result inspect(Map<String,String> files,Set<String> sourceRoots,Set<String> transforms) {
        if(sourceRoots.isEmpty()||sourceRoots.stream().anyMatch(root->!root.matches("[A-Za-z][A-Za-z0-9_]*/")))
            throw new IllegalArgumentException("Invalid trusted material source roots");
        if(!Set.of("groovy.transform.TupleConstructor","groovy.transform.RecordType","groovy.transform.Trait").containsAll(transforms))
            throw new IllegalArgumentException("Unknown native source transform in material policy");
        var classes=new LinkedHashMap<String,String>();
        var traits=new LinkedHashSet<String>();
        var findings=new ArrayList<Finding>();
        for(var entry:files.entrySet()) {
            String path=entry.getKey();
            if(!ordinarySourcePath(path,sourceRoots)) {
                findings.add(new Finding(path,"admission.source-path","Source path is outside the declared material loader roots",0,0));
                continue;
            }
            String relative=path.substring("groovy/".length());
            String expectedPackage=relative.substring(0,relative.lastIndexOf('/')).replace('/','.');
            SourceUnit source=SourceUnit.create(path,entry.getValue());
            try {
                // Deliberately no CompilationUnit, parseClass, semantic analysis,
                // class resolution or phase runner. Annotation nodes remain data.
                source.parse();source.completePhase();source.nextPhase();source.convert();
            } catch(CompilationFailedException failure) {
                boolean located=false;
                if(source.getErrorCollector().getErrors()!=null)for(Object error:source.getErrorCollector().getErrors()) {
                    if(error instanceof SyntaxErrorMessage syntax) {
                        var cause=syntax.getCause();
                        findings.add(new Finding(path,"source.syntax",cause.getOriginalMessage(),cause.getStartLine(),cause.getStartColumn()));
                        located=true;
                    }
                }
                if(!located)findings.add(new Finding(path,"source.parse",failure.getMessage(),0,0));
                continue;
            }
            ModuleNode module=source.getAST();
            var admittedAnnotationSites=new HashSet<String>();
            String declared=module.getPackageName();
            if(declared!=null&&!declared.equals(expectedPackage+".")) {
                ASTNode location=module.getPackage()==null?module:module.getPackage();
                findings.add(at(path,"admission.package","Declared package differs from native source-path package "+expectedPackage,location));
            }
            var visitor=new ClassCodeVisitorSupport() {
                @Override protected SourceUnit getSourceUnit() { return source; }
                @Override public void visitAnnotations(AnnotatedNode node) {
                    for(AnnotationNode annotation:node.getAnnotations()) {
                        if(admittedTuple(node,annotation,module,transforms)||admittedRecord(node,annotation,transforms)
                                ||admittedTrait(node,annotation,transforms))
                            admittedAnnotationSites.add(annotation.getLineNumber()+":"+annotation.getColumnNumber());
                        else findings.add(at(path,"admission.annotation","Compile-time annotations/transforms are not admitted: "+annotation.getClassNode().getName(),annotation));
                    }
                    super.visitAnnotations(node);
                }
                @Override public void visitClosureExpression(org.codehaus.groovy.ast.expr.ClosureExpression closure) {
                    if(closure.getParameters()!=null)for(Parameter parameter:closure.getParameters())visitAnnotations(parameter);
                    super.visitClosureExpression(closure);
                }
            };
            if(module.getPackage()!=null)visitor.visitAnnotations(module.getPackage());
            var imports=new ArrayList<ImportNode>();
            imports.addAll(module.getImports());imports.addAll(module.getStarImports());
            imports.addAll(module.getStaticImports().values());imports.addAll(module.getStaticStarImports().values());
            for(ImportNode imported:imports)visitor.visitAnnotations(imported);
            for(ClassNode type:module.getClasses()) {
                String name=type.getName();
                if(declared==null)name=expectedPackage+"."+name;
                if(!name.startsWith(expectedPackage+"."))
                    findings.add(at(path,"admission.class-owner","Class escaped its source-path package: "+name,type));
                String previous=classes.putIfAbsent(name,path);
                if(type.getAnnotations().stream().anyMatch(annotation->admittedTrait(type,annotation,transforms)))traits.add(name);
                if(previous!=null)
                    findings.add(at(path,"admission.class-conflict","Class "+name+" is also declared by "+previous,type));
                if(type.isAnnotationDefinition())
                    findings.add(at(path,"admission.annotation-definition","Candidate annotation definitions are not admitted",type));
                for(MethodNode method:type.getMethods())if(method.getName().startsWith("$")||method.getName().startsWith("this$dist$")||RESERVED_METHODS.contains(method.getName()))
                    findings.add(at(path,"admission.meta-protocol","Candidate cannot redefine the compiler's metaclass protocol: "+method.getName(),method));
                for(FieldNode field:type.getFields())if(field.getName().startsWith("$")||field.getName().equals("metaClass"))
                    findings.add(at(path,"admission.meta-protocol","Candidate cannot redefine compiler-owned state: "+field.getName(),field));
                visitor.visitClass(type);
                // Parameters have annotations but are not uniformly visited by
                // ClassCodeVisitorSupport across method/closure/default variants.
                for(MethodNode method:type.getMethods())for(Parameter parameter:method.getParameters())visitor.visitAnnotations(parameter);
                for(ConstructorNode constructor:type.getDeclaredConstructors())for(Parameter parameter:constructor.getParameters())visitor.visitAnnotations(parameter);
            }
            // Independent token-level closure for annotations at sites that an
            // AST visitor may not expose (e.g. type-use annotations). This is the
            // selected compiler's lexer, not a text/regex ban: comments and string
            // contents are not AT tokens. A parsed DOT AT is direct attribute
            // syntax, not an annotation. Runtime admission still checks its
            // actual receiver and member. Never unescape/repair code
            // that the native parser has already rejected.
            var lexer=new GroovyLangLexer(CharStreams.fromString(entry.getValue(),path));
            int previous=-1;
            for(var token:lexer.getAllTokens()) {
                if(token.getType()==GroovyLexer.AT&&previous!=GroovyLexer.DOT
                    &&!admittedAnnotationSites.contains(token.getLine()+":"+(token.getCharPositionInLine()+1))
                    &&findings.stream().noneMatch(finding->finding.path().equals(path)&&finding.line()==token.getLine()
                            &&finding.code().startsWith("admission.annotation")))
                findings.add(new Finding(path,"admission.annotation-syntax","Annotation syntax is not admitted",
                        token.getLine(),token.getCharPositionInLine()+1));
                if(token.getChannel()==0)previous=token.getType();
            }
        }
        // Repeated traversal of a property/field or nested class must not yield
        // repeated findings. Preserve source traversal order, not lexical sorting.
        return new Result(classes,traits,new ArrayList<>(new LinkedHashSet<>(findings)));
    }

    private static boolean admittedTuple(AnnotatedNode node,AnnotationNode annotation,ModuleNode module,Set<String> transforms) {
        // Admit the exact native transform, not an annotation with a matching
        // short name. Parsing never resolves/loads candidate annotations.
        String name=annotation.getClassNode().getName();
        ImportNode imported=module.getImport(name);
        if(imported!=null)name=imported.getClassName();
        return node instanceof ClassNode type&&!type.isInterface()
                &&name.equals("groovy.transform.TupleConstructor")&&transforms.contains(name)
                &&annotation.getMembers().isEmpty();
    }
    private static boolean admittedRecord(AnnotatedNode node,AnnotationNode annotation,Set<String> transforms) {
        // Groovy 4 AstBuilder inserts this unlocated annotation for the keyword,
        // and records the actual header parameters before any transform runs.
        // An explicit annotation (even on a record) is not this parser route.
        return node instanceof ClassNode type&&type.getNodeMetaData("_RECORD_HEADER") instanceof Parameter[]
                &&annotation.getLineNumber()<0&&annotation.getColumnNumber()<0
                &&annotation.getClassNode().getName().equals("groovy.transform.RecordType")
                &&transforms.contains("groovy.transform.RecordType")&&annotation.getMembers().isEmpty();
    }

    private static boolean admittedTrait(AnnotatedNode node,AnnotationNode annotation,Set<String> transforms) {
        // AstBuilder inserts Trait for the keyword and for interfaces with
        // default methods. Only the former class-shaped parser route is selected.
        return node instanceof ClassNode type&&!type.isInterface()
                &&!Boolean.TRUE.equals(type.getNodeMetaData("_IS_INTERFACE_WITH_DEFAULT_METHODS"))
                &&annotation.getLineNumber()<0&&annotation.getColumnNumber()<0
                &&annotation.getClassNode().getName().equals("groovy.transform.Trait")
                &&transforms.contains("groovy.transform.Trait")&&annotation.getMembers().isEmpty();
    }

    private static boolean ordinarySourcePath(String path,Set<String> roots) {
        if(!path.startsWith("groovy/")||!path.endsWith(".groovy")||path.indexOf('\\')>=0)return false;
        String relative=path.substring(7,path.length()-7);
        String[] parts=relative.split("/",-1);
        if(parts.length<2||!roots.contains(parts[0]+"/"))return false;
        for(String part:parts)if(!part.matches("[A-Za-z_][A-Za-z0-9_]*"))return false;
        return true;
    }
    private static Finding at(String path,String code,String message,ASTNode node) {
        return new Finding(path,code,message,node.getLineNumber(),node.getColumnNumber());
    }
}
