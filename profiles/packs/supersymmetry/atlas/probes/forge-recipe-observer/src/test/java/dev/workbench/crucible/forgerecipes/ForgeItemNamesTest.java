package dev.workbench.crucible.forgerecipes;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import dev.workbench.crucible.runtimegraph.Hashing;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Synthetic protocol checks only; does not initialize Minecraft or qualify the native resolver. */
public final class ForgeItemNamesTest {
    public static class Resolver {
        private final Map<String,Map<String,ForgeReflectionTest.Stack>> metaItems = new LinkedHashMap<>();
        final Map<String,ForgeReflectionTest.Stack> machines = new LinkedHashMap<>();
        final List<String> invoked = new ArrayList<>();
        void put(String namespace,String name,int damage) {
            if (!metaItems.containsKey(namespace)) metaItems.put(namespace,new LinkedHashMap<>());
            metaItems.get(namespace).put(name,new ForgeReflectionTest.Stack(1,damage,null));
        }
        public String[] splitObjectName(String query) {
            String[] split={"gregtech",query};int colon=query.indexOf(':');
            if (colon>=0) {split[1]=query.substring(colon+1);if(colon>1)split[0]=query.substring(0,colon);}
            return split;
        }
        public ForgeReflectionTest.Stack getMetaTileEntityItem(String[] split) {
            return machines.get(split[0]+":"+split[1]);
        }
        public ForgeReflectionTest.Stack getMetaItem(String query) {
            invoked.add(query);String[] split=splitObjectName(query);
            Map<String,ForgeReflectionTest.Stack> values=metaItems.get(split[0]);
            ForgeReflectionTest.Stack selected=values==null?null:values.get(split[1]);
            if(selected==null)selected=getMetaTileEntityItem(split);
            return selected==null?null:selected.copy();
        }
        public void loadMetaItemBracketHandler(){throw new AssertionError("Cache reload is forbidden");}
    }
    public static final class DriftedResolver extends Resolver {
        @Override public ForgeReflectionTest.Stack getMetaItem(String query) {
            ForgeReflectionTest.Stack stack=super.getMetaItem(query);
            if(stack!=null)stack.damage++;
            return stack;
        }
    }
    private static void check(boolean condition,String label){if(!condition)throw new AssertionError(label);}
    private static void refuse(Runnable action,String label){try{action.run();throw new AssertionError(label);}catch(IllegalStateException expected){}}
    private static Map<String,Object> query(List<Map<String,Object>> rows,String name){
        for(Map<String,Object> row:rows)if(name.equals(row.get("query")))return row;
        throw new AssertionError("Missing actual query "+name);
    }
    public static void main(String[] args)throws Exception {
        Resolver resolver=new Resolver();
        resolver.put("gregtech","circuit.microprocessor",301);
        resolver.put("susy","registered_part",302);
        resolver.put("gregtech","shadow",303);
        resolver.put("x","one_character_namespace",304);
        resolver.machines.put("gregtech:assembler.mv",new ForgeReflectionTest.Stack(1,101,null));
        resolver.machines.put("gregtech:shadow",new ForgeReflectionTest.Stack(1,102,null));
        List<Map<String,Object>> rows=ForgeRecipeSnapshot.itemNameRows(resolver,
            Arrays.asList("gregtech:assembler.mv","gregtech:shadow"),
            Arrays.asList("missing.original","x:circuit.microprocessor","circuit.microprocessor"),"artifact","class");
        check(rows.size()==10 && resolver.invoked.size()==10,"Unique finite query union invoked once per query");
        Map<String,Object> target=query(rows,"circuit.microprocessor");
        check(target.get("resolution").equals("cache") && target.get("namespace").equals("gregtech"),"Unqualified target uses observed cache");
        check(hash(target.get("stack")).equals(hash(query(rows,"gregtech:circuit.microprocessor").get("stack"))),"Qualified and unqualified exact output identity agrees");
        check(query(rows,"susy:registered_part").get("namespace").equals("susy"),"Foreign namespace retained");
        check(query(rows,"assembler.mv").get("resolution").equals("meta-tile-entity"),"Actual machine fallback declared");
        check(query(rows,"shadow").get("resolution").equals("cache") && ((Map<?,?>)query(rows,"shadow").get("stack")).get("item_damage").equals(303),"Observed cache shadows machine fallback");
        check(query(rows,"missing.original").get("resolution").equals("unresolved") && query(rows,"missing.original").get("stack")==null,"Explicit miss remains unresolved");
        check(query(rows,"x:circuit.microprocessor").get("namespace").equals("gregtech"),"Original one-character prefix splitter preserved");
        check(query(rows,"x:one_character_namespace").get("resolution").equals("unresolved"),"Existing key does not invent success when original splitter differs");
        check(resolver.metaItems.get("gregtech").get("circuit.microprocessor").damage==301,"Original cached stack remains unchanged");
        check(target.get("gregtech_sha256").equals("artifact") && target.get("resolver_class_sha256").equals("class"),"Archive provenance retained");
        DriftedResolver drift=new DriftedResolver();drift.put("gregtech","bad",5);
        refuse(()->ForgeRecipeSnapshot.itemNameRows(drift,Collections.emptyList(),Collections.emptyList(),"a","b"),"Changed getter result refused");

        Path input=Paths.get(args[0]).resolve("item-name-input.json");
        byte[] raw="{\"item_name_queries\":[\"circuit.microprocessor\",\"missing.original\"]}".getBytes(StandardCharsets.UTF_8);
        Files.write(input,raw);
        System.setProperty("workbench.runtimeGraph.input_manifest_path",input.toString());
        System.setProperty("workbench.runtimeGraph.input_manifest_sha256",Hashing.sha256(raw));
        check(ForgeRecipeSnapshot.requestedItemNames().equals(Arrays.asList("circuit.microprocessor","missing.original")),"Explicit queries read from exact bound bytes");
        Files.write(input,"{}".getBytes(StandardCharsets.UTF_8));
        refuse(()->ForgeRecipeSnapshot.requestedItemNames(),"Changed manifest refused before query consumption");
        System.setProperty("workbench.runtimeGraph.input_manifest_sha256",Hashing.sha256(Files.readAllBytes(input)));
        check(ForgeRecipeSnapshot.requestedItemNames().isEmpty(),"Absent optional request means enumeration only");
        Files.write(input,"{\"item_name_queries\":[17]}".getBytes(StandardCharsets.UTF_8));
        System.setProperty("workbench.runtimeGraph.input_manifest_sha256",Hashing.sha256(Files.readAllBytes(input)));
        refuse(()->ForgeRecipeSnapshot.requestedItemNames(),"Non-string requested query refused");
        System.out.println("ForgeItemNamesTest: alias observation contract checks passed; no native initialization performed");
    }
}
