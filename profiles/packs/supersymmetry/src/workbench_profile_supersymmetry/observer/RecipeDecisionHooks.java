package dev.workbench.recipe;

import java.lang.classfile.*;
import java.lang.classfile.instruction.*;
import java.lang.constant.*;
import java.util.*;

/** Audited decision sites in the admitted RecipeMap definition, not a second lookup algorithm. */
public final class RecipeDecisionHooks {
    private static final ClassDesc TRACE = ClassDesc.of("dev.workbench.recipe.RecipeDecisions");
    private static final ClassDesc OBJECT = ConstantDescs.CD_Object;
    private static final String RECIPE = "Lgregtech/api/recipes/Recipe;";
    private static final String LIST = "Ljava/util/List;", BRANCH = "Lgregtech/api/recipes/map/Branch;";
    private static final String PREDICATE = "Ljava/util/function/Predicate;";
    public static final String LOOKUP = "findRecipe(J" + LIST + LIST + "Z)" + RECIPE;

    // Registers are from the exact admitted method, and never guessed from debug names.
    public record Site(Opcode opcode, String taken, String fallthrough, int recipe, int key,
                       int depth, int branch, boolean competingOperands) {}
    private static Site s(Opcode op, String yes, String no, int recipe, int key, int depth, int branch) {
        return new Site(op, yes, no, recipe, key, depth, branch, false);
    }
    public static final Map<String, Map<Integer, Site>> SITES = Map.of(
        "compileRecipe(" + RECIPE + ")Z", Map.of(
            1, s(Opcode.IFNONNULL, "recipe-present", "null-recipe", 1,-1,-1,-1),
            24, s(Opcode.IFEQ, "insertion-failed", "insertion-succeeded", 1,-1,-1,-1)),
        "recurseIngredientTreeAdd(" + RECIPE + LIST + BRANCH + "II)Z", Map.of(
            130, s(Opcode.IFEQ, "insertion-subtree", "insertion-leaf", 1,10,5,3),
            142, new Site(Opcode.IF_ACMPNE, "different-recipe-blocks-insertion", "same-recipe-leaf", 1,10,5,3,true),
            177, s(Opcode.IFNE, "child-insertion-succeeded", "child-insertion-failed", 1,10,5,3),
            190, s(Opcode.IF_ICMPNE, "inspect-failed-child", "remove-failed-terminal", 1,10,5,3),
            251, s(Opcode.IFEQ, "retain-nonempty-child", "remove-empty-child", 1,10,5,3)),
        "lambda$recurseIngredientTreeAdd$9(I" + LIST + RECIPE + BRANCH + "Lgregtech/api/recipes/map/AbstractMapIngredient;Lgregtech/api/recipes/map/Either;)Lgregtech/api/recipes/map/Either;", Map.of(
            14, s(Opcode.IFNULL, "terminal-slot-empty", "terminal-slot-occupied", 3,5,1,-1),
            25, s(Opcode.IFEQ, "terminal-occupied-subtree", "terminal-occupied-recipe", 3,5,1,-1),
            37, new Site(Opcode.IF_ACMPEQ, "terminal-same-recipe", "terminal-different-recipe", 3,5,1,-1,true)),
        "recurseIngredientTreeFindRecipe(" + LIST + BRANCH + PREDICATE + "IIJ)" + RECIPE, Map.of(
            8, s(Opcode.IF_ICMPNE, "lookup-depth-continues", "lookup-depth-exhausted", -1,-1,5,2),
            38, s(Opcode.IFEQ, "lookup-alternatives-exhausted", "lookup-next-alternative", -1,-1,5,2),
            77, s(Opcode.IFNULL, "lookup-key-missing", "lookup-key-present", -1,9,5,2),
            112, s(Opcode.IFNULL, "lookup-branch-no-selection", "lookup-branch-selected", 12,9,5,2)),
        "lambda$recurseIngredientTreeFindRecipe$5(" + PREDICATE + RECIPE + ")" + RECIPE, Map.of(
            7, s(Opcode.IFEQ, "candidate-predicate-failed", "candidate-predicate-passed", 1,-1,-1,-1)),
        "lambda$findRecipe$4(ZJ" + LIST + LIST + RECIPE + ")Z", Map.of(
            12, s(Opcode.IFEQ, "exact-voltage-passed", "exact-voltage-failed", 5,-1,-1,-1),
            25, s(Opcode.IFLE, "voltage-limit-passed", "voltage-limit-failed", 5,-1,-1,-1))
    );

    public static Set<String> required() {
        Set<String> names = new TreeSet<>(SITES.keySet()); names.add(LOOKUP); return names;
    }

    /** Refuse the entire decision layer if any required site or descriptor differs. */
    public static void audit(ClassModel model) {
        Set<String> found = new HashSet<>();
        for (var method : model.methods()) {
            String name = method.methodName().stringValue() + method.methodType().stringValue();
            if (name.equals(LOOKUP)) found.add(name);
            var sites = SITES.get(name);
            if (sites == null) continue;
            int pc = 0; Set<Integer> matched = new HashSet<>();
            for (var element : method.code().orElseThrow()) if (element instanceof Instruction instruction) {
                Site site = sites.get(pc);
                if (site != null) {
                    if (!(instruction instanceof BranchInstruction) || instruction.opcode() != site.opcode())
                        throw new IllegalArgumentException("decision-site-mismatch:" + name + ":" + pc);
                    matched.add(pc);
                }
                pc += instruction.sizeInBytes();
            }
            if (!matched.equals(sites.keySet())) throw new IllegalArgumentException("missing-decision-sites:" + name);
            found.add(name);
        }
        if (!found.equals(required())) throw new IllegalArgumentException("missing-decision-methods");
    }

    public static CodeTransform transform(String method) {
        if (method.equals(LOOKUP)) return new Lookup();
        var sites = SITES.get(method);
        return sites == null ? null : new Decisions(method, sites);
    }

    public static final class Decisions implements CodeTransform {
        private final String method;
        private final Map<Integer, Site> sites;
        private int pc, existing, attempted;
        public Decisions(String method, Map<Integer, Site> sites) { this.method = method; this.sites = sites; }
        @Override public void atStart(CodeBuilder code) {
            existing = code.allocateLocal(TypeKind.REFERENCE); attempted = code.allocateLocal(TypeKind.REFERENCE);
        }
        @Override public void accept(CodeBuilder code, CodeElement element) {
            Site site = sites.get(pc);
            if (element instanceof Instruction instruction) {
                int offset = pc; pc += instruction.sizeInBytes();
                if (site != null) {
                    BranchInstruction branch = (BranchInstruction) instruction;
                    if (site.competingOperands()) code.dup2().astore(attempted).astore(existing);
                    Label taken = code.newLabel(), next = code.newLabel();
                    // The original conditional consumes the original operands exactly once.
                    code.with(BranchInstruction.of(branch.opcode(), taken));
                    emit(code, site, site.fallthrough(), offset); code.goto_(next);
                    code.labelBinding(taken); emit(code, site, site.taken(), offset); code.goto_(branch.target());
                    code.labelBinding(next); return;
                }
            }
            code.with(element);
        }
        private void emit(CodeBuilder code, Site site, String result, int offset) {
            code.ldc(method + ":" + offset).ldc(result);
            if (site.recipe() < 0) code.aconst_null(); else code.aload(site.recipe());
            if (site.competingOperands()) code.aload(existing); else code.aconst_null();
            if (site.key() < 0) code.aconst_null(); else code.aload(site.key());
            if (site.depth() < 0) code.iconst_m1(); else code.iload(site.depth());
            if (site.branch() < 0) code.aconst_null(); else code.aload(site.branch());
            code.invokestatic(TRACE, "step", MethodTypeDesc.of(ConstantDescs.CD_void,
                ConstantDescs.CD_String, ConstantDescs.CD_String, OBJECT, OBJECT, OBJECT, ConstantDescs.CD_int, OBJECT));
        }
    }

    private static final class Lookup implements CodeTransform {
        int token, result; Label start;
        @Override public void atStart(CodeBuilder code) {
            token = code.allocateLocal(TypeKind.REFERENCE); result = code.allocateLocal(TypeKind.REFERENCE);
            code.aload(0).lload(1).aload(3).aload(4).iload(5).invokestatic(TRACE, "enterLookup",
                MethodTypeDesc.of(OBJECT, OBJECT, ConstantDescs.CD_long, OBJECT, OBJECT, ConstantDescs.CD_boolean)).astore(token);
            start = code.newBoundLabel();
        }
        @Override public void accept(CodeBuilder code, CodeElement instruction) {
            if (instruction instanceof ReturnInstruction) {
                code.dup().astore(result).aload(token).aload(result).iconst_0().invokestatic(TRACE, "exitLookup",
                    MethodTypeDesc.of(ConstantDescs.CD_void, OBJECT, OBJECT, ConstantDescs.CD_boolean));
            }
            code.with(instruction);
        }
        @Override public void atEnd(CodeBuilder code) {
            Label end = code.newBoundLabel(), handler = code.newLabel();
            code.exceptionCatchAll(start, end, handler).labelBinding(handler).astore(result);
            code.aload(token).aload(result).iconst_1().invokestatic(TRACE, "exitLookup",
                MethodTypeDesc.of(ConstantDescs.CD_void, OBJECT, OBJECT, ConstantDescs.CD_boolean));
            code.aload(result).athrow();
        }
    }
}
