package research.orthrus.axiom;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.codehaus.groovy.ast.*;
import org.codehaus.groovy.ast.expr.*;
import org.codehaus.groovy.ast.stmt.*;
import org.codehaus.groovy.control.SourceUnit;
import static research.orthrus.axiom.Domain.*;

/**
 * Groovy 4.0.30 syntax with an explicit, fail-closed semantic subset. Only
 * parse/convert runs; no user bytecode, imports, AST transforms, or methods run.
 * This is not an alternative permissive DSL and is not a whole-pack compiler.
 */
public final class GroovyProgram {
    private final Construction construction;
    private Location file;
    private boolean mapsImported, valuesImported;
    private int nodes;
    private static final int[] VA = {7,30,120,480,1920,7680,30720,122880,491520,1966080,7864320,31457280,125829120,503316480,2013265920};
    private static final List<String> TIERS = List.of("ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV", "UEV", "UIV", "UXV", "OpV", "MAX");

    public GroovyProgram(Construction construction) { this.construction = construction; }
    public void read(String path, String text) {
        if (text.length() > 131072) throw new Failure("incomplete", "source.bound", "Source file exceeds 128 KiB");
        if (!path.matches("[A-Za-z0-9_./-]+\\.groovy") || path.startsWith("/") || Arrays.asList(path.split("/")).contains("..") || path.contains("//"))
            throw Failure.request("Expected an ordinary relative .groovy source path");
        file = new Location(path, Json.bytesDigest(text.getBytes(StandardCharsets.UTF_8)), 1, 1);
        nodes = 0; mapsImported = false; valuesImported = false;
        SourceUnit source = SourceUnit.create(path, text);
        try { source.parse(); source.completePhase(); source.nextPhase(); source.convert(); }
        catch (org.codehaus.groovy.control.CompilationFailedException exception) {
            throw new Failure("source-error", "source.syntax", exception.getMessage());
        }
        ModuleNode module = source.getAST();
        if (module.getPackage() != null || !module.getImports().isEmpty() || !module.getStarImports().isEmpty()
                || !module.getStaticImports().isEmpty() || !module.getMethods().isEmpty()) unsupported(module, "Packages, imports and helper methods outside the admitted bindings");
        for (ClassNode type : module.getClasses()) {
            if (!type.isScript() || !type.getAnnotations().isEmpty()) unsupported(type, "User classes or annotations");
        }
        for (ImportNode item : module.getStaticStarImports().values()) {
            if (!item.getAnnotations().isEmpty()) unsupported(item, "Annotated import");
            switch (item.getClassName()) {
                case "prePostInit.Recipemaps" -> mapsImported = true;
                case "gregtech.api.GTValues" -> valuesImported = true;
                default -> unsupported(item, "Import " + item.getClassName());
            }
        }
        for (Statement statement : module.getStatementBlock().getStatements()) {
            if (!(statement instanceof ExpressionStatement expression)) unsupported(statement, "Control flow or declaration");
            Object result = evaluate(((ExpressionStatement)statement).getExpression(), 0);
            if (result != null) unsupported(statement, "Statement without buildAndRegister");
        }
    }

    private Object evaluate(Expression expression, int depth) {
        if (++nodes > 20_000 || depth > 128) throw new Failure("incomplete", "source.bound", "AST evaluation bound exhausted");
        if (expression instanceof ConstantExpression literal) {
            Object value = literal.getValue();
            if (value instanceof String || value instanceof Integer) return value;
            unsupported(expression, "Literal kind " + (value == null ? "null" : value.getClass().getSimpleName()));
        }
        if (expression instanceof UnaryMinusExpression unary) return -Construction.integer(evaluate(unary.getExpression(), depth + 1));
        if (expression instanceof VariableExpression variable) {
            String name = variable.getName();
            if (mapsImported && (name.equals("MIXER") || name.equals("BLENDER"))) return new MapSymbol(name.toLowerCase(Locale.ROOT));
            if (valuesImported && name.equals("VA")) return VA;
            if (valuesImported && TIERS.contains(name)) return TIERS.indexOf(name);
            unsupported(expression, "Unresolved symbol " + name);
        }
        if (expression instanceof BinaryExpression binary) {
            Object left = evaluate(binary.getLeftExpression(), depth + 1);
            Object right = evaluate(binary.getRightExpression(), depth + 1);
            if (binary.getOperation().getText().equals("[") && left == VA) {
                int index = Construction.integer(right);
                if (index < 0 || index >= VA.length) throw new Failure("source-error", "source.array-index", "VA index out of range");
                return VA[index];
            }
            if (binary.getOperation().getText().equals("*") && left instanceof Ingredient input) {
                int amount = Construction.integer(right);
                // IResourceStack.multiply SETS, rather than multiplies, the amount.
                if (input.kind().equals("ore")) amount = construction.registry.ore(input.id()).members().isEmpty() ? 0 : Math.max(0, amount);
                return input.withAmount(amount);
            }
            unsupported(expression, "Operator " + binary.getOperation().getText());
        }
        if (expression instanceof MethodCallExpression call) {
            String name = call.getMethodAsString();
            if (name == null || call.isSafe() || call.isSpreadSafe() || !(call.getArguments() instanceof TupleExpression)) unsupported(call, "Dynamic call or spread");
            Object receiver = call.isImplicitThis() ? null : evaluate(call.getObjectExpression(), depth + 1);
            List<Object> args = new ArrayList<>();
            for (Expression arg : ((TupleExpression)call.getArguments()).getExpressions()) args.add(evaluate(arg, depth + 1));
            if (call.isImplicitThis()) return mapper(name, args, call);
            if (receiver instanceof MapSymbol map && name.equals("recipeBuilder")) {
                Construction.arity(args, 0);
                return construction.builder(map.name, location(call));
            }
            if (receiver instanceof Construction.Builder builder) return builder.call(name, args);
            unsupported(call, "Call " + name);
        }
        unsupported(expression, expression.getClass().getSimpleName());
        throw new AssertionError();
    }
    private Object mapper(String name, List<Object> args, ASTNode node) {
        if (name.equals("ore") || name.equals("fluid")) {
            Construction.arity(args, 1); String id = string(args.get(0), node);
            if (name.equals("ore")) {
                if (id.equals("Unknown")) unsupported(node, "GroovyScript rejects the Unknown ore mapper");
                Ore ore = construction.registry.ore(id);
                return new Ingredient("ore", id, ore.members().isEmpty() ? 0 : 1, false, null, null, 0);
            }
            construction.registry.requireFluid(id);
            return new Ingredient("fluid", id, 1, false, null, null, 0);
        }
        if (name.equals("item")) {
            if (args.size() < 1 || args.size() > 2) unsupported(node, "item overload");
            String id = string(args.get(0), node);
            if (id.chars().filter(c -> c == ':').count() != 1) unsupported(node, "Inline metadata or wildcard item mapper");
            int meta = args.size() == 2 ? Construction.integer(args.get(1)) : 0;
            ItemType type = construction.registry.item(id, meta);
            return new Ingredient("item", id, 1, false, type, null, 0);
        }
        unsupported(node, "Unmodeled mapper/helper " + name); return null;
    }
    private String string(Object value, ASTNode node) { if (value instanceof String text) return text; unsupported(node, "Expected string"); return null; }
    private Location location(ASTNode node) { return new Location(file.path(), file.sha256(), Math.max(1, node.getLineNumber()), Math.max(1, node.getColumnNumber())); }
    private void unsupported(ASTNode node, String message) {
        throw Failure.unsupported("source.ast", file.path() + ":" + Math.max(1, node.getLineNumber()) + ": " + message + " is outside the admitted Groovy subset");
    }
    private record MapSymbol(String name) {}
}
