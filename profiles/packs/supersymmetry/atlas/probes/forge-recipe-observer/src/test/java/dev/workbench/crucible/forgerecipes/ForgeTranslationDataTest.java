package dev.workbench.crucible.forgerecipes;

import java.lang.reflect.Constructor;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Arrays;
import java.util.Map;
import dev.workbench.crucible.runtimegraph.Hashing;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Instantiates only the original passive data record; no mod/game lifecycle. */
public final class ForgeTranslationDataTest {
    private static final String NAME="dev.tianmi.sussypatches.api.recipe.property.InfoProperty$TranslationData";
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);}
    private static void refuse(Runnable run,String label){try{run.run();throw new AssertionError(label);}catch(IllegalStateException expected){}}
    public static void main(String[] args)throws Exception {
        byte[] artifact=Files.readAllBytes(Paths.get(args[0]));
        check(Hashing.sha256(artifact).equals("c6ac9b9a9f920d1c21559cf0e414308d1d2ea1b5ae9efc5788125297d54b9224"),"Original SussyPatches fixture differs");
        try(URLClassLoader loader=new URLClassLoader(new URL[]{Paths.get(args[0]).toUri().toURL()},ForgeTranslationDataTest.class.getClassLoader())) {
            Class<?> record=Class.forName(NAME,true,loader);
            Constructor<?> constructor=record.getConstructor(String.class,Object[].class);
            Object nested=constructor.newInstance("nested.translation",new Object[]{7L});
            Object original=constructor.newInstance("exact.translation",new Object[]{"untranslated",null,2,-1.25f,new Object[]{true,"ordered"},nested});
            Object nestedExpected=row("runtime_class",NAME,"fields",row(NAME+".args",Arrays.asList(7L),NAME+".translationKey","nested.translation"));
            Object expected=row("runtime_class",NAME,"fields",row(NAME+".args",Arrays.asList("untranslated",null,2,
                row("decimal","-1.25","raw_bits","3214934016","value_kind","float32"),Arrays.asList(true,"ordered"),nestedExpected),NAME+".translationKey","exact.translation"));
            check(hash(value(original)).equals(hash(expected)),"Original key, ordered nested values, nulls and numeric types retained");
            Object nullArgs=constructor.newInstance(null,null);
            Object emptyArgs=constructor.newInstance(null,new Object[0]);
            check(!hash(value(nullArgs)).equals(hash(value(emptyArgs))),"Null args remain distinct from empty array");
            Object[] self=new Object[1];Object cycle=constructor.newInstance("cyclic",self);self[0]=cycle;
            refuse(()->value(cycle),"Cyclic original argument graph refused");
            Object unknown=constructor.newInstance("unknown",new Object[]{new Object()});
            refuse(()->value(unknown),"Unknown nested argument remains refused");
            Map<?,?> fields=(Map<?,?>)((Map<?,?>)value(original)).get("fields");
            check(fields.size()==2,"Original record has exactly two preserved data fields");
        }
        System.out.println("ForgeTranslationDataTest: original record fields preserved; no native initialization performed");
    }
}
