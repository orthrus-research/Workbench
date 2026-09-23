package dev.workbench.crucible.forgerecipes;

import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import java.lang.invoke.MethodHandle;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Archive-origin and exact original virtual call mechanics, no game initialization. */
public final class ForgeArtifactOriginTest {
    interface Work {void run()throws Exception;}
    static void refused(Work work)throws Exception {
        try {work.run();throw new AssertionError("Unsafe origin admitted");}
        catch(IllegalStateException expected) {/* Refusal is the contract. */}
    }
    public static final class Stack {
        final int ordinal;int copies;
        Stack(int ordinal){this.ordinal=ordinal;}
        public Stack copy(){copies++;return new Stack(ordinal);}
    }
    public static class Input {
        int calls;public boolean acceptsStack(Stack stack){throw new AssertionError("Base implementation dispatched");}
    }
    public static final class OriginalOverride extends Input {
        final Stack original;OriginalOverride(Stack original){this.original=original;}
        @Override public boolean acceptsStack(Stack stack){calls++;if(stack==original)throw new AssertionError("Original passed instead of fresh copy");return stack.ordinal%2==0;}
    }
    public static void main(String[] args)throws Exception {
        Path root=Paths.get(args[0]);Files.createDirectories(root);Path jar=root.resolve("original archive.jar");
        String member="original/Selected.class";
        try(ZipOutputStream out=new ZipOutputStream(Files.newOutputStream(jar))) {
            out.putNextEntry(new ZipEntry(member));out.write(new byte[]{1,2,3});out.closeEntry();
        }
        if(!ForgeRecipeSnapshot.originalMemberHash(jar,member).equals("039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81"))
            throw new AssertionError("Named original member hash changed or attempted class loading");
        refused(()->ForgeRecipeSnapshot.originalMemberHash(jar,"missing/Mixin.class"));
        URL file=jar.toUri().toURL();
        if(!jar.equals(ForgeRecipeSnapshot.localArtifact(file,member)))throw new AssertionError("file origin changed");
        if(!jar.equals(ForgeRecipeSnapshot.localArtifact(new URL("jar:"+file+"!/"),member)))throw new AssertionError("jar root origin changed");
        if(!jar.equals(ForgeRecipeSnapshot.localArtifact(new URL("jar:"+file+"!/"+member),member)))throw new AssertionError("jar member origin changed");
        refused(()->ForgeRecipeSnapshot.localArtifact(new URL("https://invalid.example/archive.jar"),member));
        refused(()->ForgeRecipeSnapshot.localArtifact(new URL("jar:https://invalid.example/archive.jar!/"+member),member));
        refused(()->ForgeRecipeSnapshot.localArtifact(new URL("jar:"+file+"!/inner.jar!/"+member),member));
        refused(()->ForgeRecipeSnapshot.localArtifact(new URL("file://remote.example/archive.jar"),member));
        refused(()->ForgeRecipeSnapshot.localArtifact(file,"original/Missing.class"));
        refused(()->ForgeRecipeSnapshot.localArtifact(root.toUri().toURL(),member));
        refused(()->ForgeRecipeSnapshot.localArtifact(new URL(file+"?ambiguous=true"),member));
        Path text=root.resolve("not-an-archive.jar");Files.write(text,new byte[]{0});
        refused(()->ForgeRecipeSnapshot.localArtifact(text.toUri().toURL(),member));
        MethodHandle accepts=virtualContract(Input.class,"acceptsStack",boolean.class,Stack.class);
        MethodHandle copy=virtualContract(Stack.class,"copy",Stack.class);
        for(int ordinal=0;ordinal<3;ordinal++) {
            Stack stack=new Stack(ordinal);OriginalOverride input=new OriginalOverride(stack);
            for(int repeat=0;repeat<4;repeat++)if(ForgeRecipeSnapshot.acceptsCopied(accepts,copy,input,stack)!=(ordinal%2==0))throw new AssertionError("Override outcome changed");
            if(input.calls!=4 || stack.copies!=4)throw new AssertionError("Pair execution skipped or cached");
        }
        System.out.println("Exact local archive origin and full-pair copied virtual dispatch contracts passed; no native initialization performed");
    }
}
