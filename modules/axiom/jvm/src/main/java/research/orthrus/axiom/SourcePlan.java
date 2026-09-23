package research.orthrus.axiom;

import java.io.IOException;
import java.util.*;
import org.codehaus.groovy.ast.*;
import org.codehaus.groovy.ast.expr.*;
import org.codehaus.groovy.control.SourceUnit;

/** Source-bound loader membership plus explicitly non-executing syntax/dependency inventory. */
final class SourcePlan {
    private static final List<String> STAGES = List.of("preInit", "init", "postInit");
    private static final List<String> SUFFIXES = List.of(".groovy", ".gvy", ".gy", ".gsh");
    private SourcePlan() {}
    static boolean script(String name) { return SUFFIXES.stream().anyMatch(name::endsWith); }

    /** SandboxData.getSortedFilesOf: ordered groups, native lexical sort, specificity moves. */
    static List<String> ordered(Collection<String> names, List<String> paths) {
        Map<String, Integer> selected = new LinkedHashMap<>();
        for (String path : paths) {
            int specificity = (int)path.chars().filter(ch -> ch == '/').count();
            names.stream().filter(SourcePlan::script).filter(name -> path.isEmpty() || name.equals(path) || name.startsWith(path + "/"))
                    .sorted().forEach(name -> {
                        Integer previous = selected.get(name);
                        if (previous == null) selected.put(name, specificity);
                        else if (specificity > previous) { selected.remove(name); selected.put(name, specificity); }
                    });
        }
        return List.copyOf(selected.keySet());
    }

    /** RunConfig.reload path admission. Unsupported policies never become an execution claim. */
    static Map<String, List<String>> loaders(Map<String, Object> config, List<Map<String, Object>> diagnostics) {
        if (config.containsKey("classes")) throw Failure.unsupported("loader.legacy-classes", "Legacy classes migration is not admitted");
        Object raw = config.get("loaders");
        Map<String, Object> declared;
        if (!(raw instanceof Map<?, ?>)) {
            diagnostics.add(Map.of("rule", "loader.no-loaders", "message", "Native loader schedules no scripts without a loaders object"));
            declared = Map.of();
        } else declared = Json.object(raw);
        if (config.containsKey("packmode")) diagnostics.add(Map.of("rule", "loader.packmode", "message", "Packmode configuration is captured, not evaluated"));
        Map<String, List<String>> result = new LinkedHashMap<>();
        List<String> prior = new ArrayList<>();
        for (var entry : declared.entrySet()) {
            List<String> admitted = new ArrayList<>();
            for (Object item : Json.array(entry.getValue())) {
                String path = Json.string(item).replace('\\', '/');
                while (path.endsWith("/")) path = path.substring(0, path.length() - 1);
                if (!path.isEmpty()) SourceTarget.path(path);
                if (admitted.contains(path)) continue;
                boolean valid = true;
                for (String other : prior) {
                    // Literal prefix comparison, not directory-aware overlap.
                    if (other.startsWith(path) || path.startsWith(other)) valid = false;
                }
                if (!valid) diagnostics.add(Map.of("rule", "loader.cross-stage-path", "loader", entry.getKey(), "path", path,
                        "message", "Native run-config removes the later overlapping loader path"));
                else admitted.add(path);
            }
            result.put(entry.getKey(), List.copyOf(admitted)); prior.addAll(admitted);
            if (!STAGES.contains(entry.getKey())) diagnostics.add(Map.of("rule", "loader.unknown-stage", "loader", entry.getKey(),
                    "message", "Not a pinned LoadStage enum member; not scheduled by this phase plan"));
        }
        return result;
    }

    static Map<String, Object> inspect(SourceTarget target, Map<String, SourceTarget.SourceFile> files,
                                       Map<String, Object> policy, boolean includeFiles, Set<String> sourcePaths) throws IOException {
        String scriptRoot = SourceTarget.path(Json.string(policy.get("scriptRoot"))) + "/";
        String configPath = SourceTarget.path(Json.string(policy.get("runConfig")));
        SourceTarget.SourceFile configFile = files.get("supersymmetry:" + configPath);
        if (configFile == null) throw new Failure("requires-context", "loader.run-config", "Source candidate has no selected runConfig.json");
        Map<String, Object> config = Json.object(Json.parse(Main.utf8(target.read(configFile))));
        List<Map<String, Object>> diagnostics = new ArrayList<>();
        Map<String, List<String>> loaders = loaders(config, diagnostics);
        Map<String, SourceTarget.SourceFile> scripts = new TreeMap<>();
        for (SourceTarget.SourceFile file : files.values()) if (file.repository().equals("supersymmetry")
                && file.path().startsWith(scriptRoot) && script(file.path())) scripts.put(file.path().substring(scriptRoot.length()), file);
        List<Map<String, Object>> phases = new ArrayList<>();
        Set<String> scheduled = new HashSet<>();
        Map<String, Long> methods = new TreeMap<>();
        Set<String> imports = new TreeSet<>(), classes = new TreeSet<>();
        List<Map<String, Object>> inventory = new ArrayList<>();
        int syntaxErrors = 0, dynamicReferences = 0, inspectedFiles = 0;
        Set<String> registryReferences = new TreeSet<>();
        for (String stage : STAGES) {
            List<String> order = ordered(scripts.keySet(), loaders.getOrDefault(stage, List.of()));
            phases.add(Map.of("stage", stage, "reloadable", stage.equals("postInit"), "paths", loaders.getOrDefault(stage, List.of()),
                    "files", order.stream().map(path -> scriptRoot + path).toList()));
            for (String name : order) {
                scheduled.add(name);
                SourceTarget.SourceFile file = scripts.get(name);
                if (sourcePaths != null && !sourcePaths.contains(file.path())) continue;
                inspectedFiles++;
                Syntax facts = syntax(file.path(), Main.utf8(target.read(file)));
                if (facts.error != null) {
                    syntaxErrors++;
                    diagnostics.add(Map.of("rule", "source.syntax", "path", file.path(), "message", facts.error));
                }
                facts.methods.forEach((method, count) -> methods.merge(method, count, Long::sum));
                imports.addAll(facts.imports); classes.addAll(facts.classes);
                registryReferences.addAll(facts.registryReferences); dynamicReferences += facts.dynamicReferences;
                if (includeFiles) {
                    Map<String, Object> row = new LinkedHashMap<>(file.identity());
                    row.put("stage", stage); row.put("syntax", facts.error == null ? "parsed" : "error");
                    row.put("imports", facts.imports); row.put("classes", facts.classes); row.put("methodNames", facts.methods);
                    row.put("literalRegistryReferences", facts.registryReferences); row.put("dynamicRegistryReferences", facts.dynamicReferences);
                    inventory.add(row);
                }
            }
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("scope", "loader-membership-and-syntax-inventory; not construction execution or resolved call graph");
        result.put("runConfigSha256", configFile.sha256()); result.put("phases", phases);
        result.put("sourceFiles", scripts.size()); result.put("scheduledFiles", scheduled.size());
        result.put("inspectedFiles", inspectedFiles); result.put("inventoryScope", sourcePaths == null ? "all-scheduled-scripts" : "selected-scheduled-scripts");
        result.put("unscheduledFiles", scripts.keySet().stream().filter(path -> !scheduled.contains(path)).map(path -> scriptRoot + path).toList());
        result.put("syntaxErrors", syntaxErrors); result.put("diagnostics", diagnostics);
        result.put("syntacticImports", imports); result.put("declaredClasses", classes); result.put("methodNames", methods);
        result.put("literalRegistryReferenceCount", registryReferences.size()); result.put("dynamicRegistryReferences", dynamicReferences);
        if (includeFiles) result.put("literalRegistryReferences", registryReferences);
        result.put("registryReferencesResolved", false); result.put("preprocessorsEvaluated", false); result.put("definitionsExecuted", false);
        if (includeFiles) result.put("files", inventory);
        return result;
    }

    static final class Syntax {
        String error;
        final Set<String> imports = new TreeSet<>(), classes = new TreeSet<>(), registryReferences = new TreeSet<>();
        final Map<String, Long> methods = new TreeMap<>();
        int dynamicReferences;
    }
    static Syntax syntax(String path, String text) {
        Syntax result = new Syntax();
        if (text.length() > 1_048_576) throw new Failure("incomplete", "source.inventory-bound", "Source syntax inventory exceeds per-file bound");
        SourceUnit source = SourceUnit.create(path, text);
        try { source.parse(); source.completePhase(); source.nextPhase(); source.convert(); }
        catch (org.codehaus.groovy.control.CompilationFailedException failure) {
            result.error = failure.getMessage(); return result;
        }
        ModuleNode module = source.getAST();
        for (ImportNode imported : module.getImports()) result.imports.add(imported.getClassName());
        for (ImportNode imported : module.getStarImports()) result.imports.add(imported.getPackageName() + "*");
        for (ImportNode imported : module.getStaticImports().values()) result.imports.add(imported.getClassName() + "." + imported.getFieldName());
        for (ImportNode imported : module.getStaticStarImports().values()) result.imports.add(imported.getClassName() + ".*");
        ClassCodeVisitorSupport visitor = new ClassCodeVisitorSupport() {
            int nodes;
            @Override protected SourceUnit getSourceUnit() { return source; }
            @Override public void visitMethodCallExpression(MethodCallExpression call) {
                if (++nodes > 50000) throw new Failure("incomplete", "source.inventory-bound", "Syntax call inventory exceeds bound");
                String name = call.getMethodAsString();
                result.methods.merge(name == null ? "<dynamic>" : name, 1L, Long::sum);
                if (Set.of("ore", "item", "fluid", "metaitem").contains(name == null ? "" : name)) {
                    if (call.getArguments() instanceof TupleExpression args && !args.getExpressions().isEmpty()
                            && args.getExpression(0) instanceof ConstantExpression constant && constant.getValue() instanceof String value)
                        result.registryReferences.add(name + ":" + value);
                    else result.dynamicReferences++;
                }
                super.visitMethodCallExpression(call);
            }
        };
        for (ClassNode type : module.getClasses()) {
            if (!type.isScript()) result.classes.add(type.getName());
            visitor.visitClass(type);
        }
        return result;
    }
}
