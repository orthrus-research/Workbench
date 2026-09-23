package dev.workbench.crucible.forgerecipes;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** No native initialization: serializer/reflection contract regressions only. */
public final class ForgeReflectionTest {
    public static class Tag {
        final int id; final Object value;
        Tag(int id,Object value){this.id=id;this.value=value;}
        public byte getId(){return (byte)id;}
        public byte getByte(){return ((Number)value).byteValue();}
        public short getShort(){return ((Number)value).shortValue();}
        public int getInt(){return ((Number)value).intValue();}
        public long getLong(){return ((Number)value).longValue();}
        public float getFloat(){return ((Number)value).floatValue();}
        public double getDouble(){return ((Number)value).doubleValue();}
        public byte[] getByteArray(){return (byte[])value;}
        public int[] getIntArray(){return (int[])value;}
        public long[] getLongArray(){return (long[])value;}
        public String getString(){return (String)value;}
        public int tagCount(){return ((java.util.List<?>)value).size();}
        public int getTagType(){return tagCount()==0?0:((Tag)((java.util.List<?>)value).get(0)).id;}
        public Object get(int i){return ((java.util.List<?>)value).get(i);}
        public java.util.Set<?> getKeySet(){return ((Map<?,?>)value).keySet();}
        public Object getTag(String key){return ((Map<?,?>)value).get(key);}
    }
    public static class Item {
        public String getRegistryName(){return "example:selector";}
    }
    public static class Stack {
        int count,damage; Tag tag;
        Stack(int count,int damage,Tag tag){this.count=count;this.damage=damage;this.tag=tag;}
        public boolean isEmpty(){return count==0;}
        public Item getItem(){return new Item();}
        public int getCount(){return count;}
        public int getItemDamage(){return damage;}
        public int getMetadata(){return damage;}
        public Tag getTagCompound(){return tag;}
        public Stack copy(){return new Stack(count,damage,tag);}
    }
    public static class SrgOnly {
        public String func_77946_l(){return "copied-original";}
    }
    public static class LongTag {
        private final long[] field_193587_b;
        LongTag(long[] values){field_193587_b=values;}
        public byte func_74732_a(){return 12;}
    }
    public static class EmptyTypedList {
        private final int elementType;
        EmptyTypedList(int type){elementType=type;}
        public byte func_74732_a(){return 9;}
        public int func_74745_c(){return 0;}
        public int func_150303_d(){return elementType;}
    }
    public static class SrgRegistry {
        private final Object original = new Object();
        public java.util.Set<String> func_148742_b(){return java.util.Collections.singleton("machine");}
        public Object func_82594_a(Object key){return key.equals("machine") ? original : null;}
        public Object func_177774_c(Object object){return object == original ? "machine" : null;}
        public Object func_148754_a(int id){return id == 5 ? original : null;}
    }
    public static class RegisteredBiome {
        public String getRegistryName(){return "fixture:ocean";}
    }
    public static class BiomeRegistry {
        final RegisteredBiome original=new RegisteredBiome();
        boolean badName,badNumber;
        public Object func_177774_c(Object object){return object==original?"fixture:ocean":null;}
        public int func_148757_b(Object object){return object==original?4:-1;}
        public Object func_82594_a(Object key){return !badName && "fixture:ocean".equals(key)?original:null;}
        public Object func_148754_a(int id){return !badNumber && id==4?original:null;}
    }
    public static class Ambiguous {
        public String choose(CharSequence a){return "a";}
        public String choose(java.io.Serializable a){return "b";}
    }
    public static class CachedReceiver {
        int calls;
        public String first(){return "first:"+(++calls);}
        public String second(){return "second:"+(++calls);}
        public static String staticValue(){return "static";}
        public String typed(String value){return "string:"+value;}
        public String typed(Integer value){return "integer:"+value;}
        public String throwsOriginal(){throw new IllegalArgumentException("exact original cause");}
    }
    public static class Parent { private final int original = 17; }
    public static class Child extends Parent {}
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);}
    private static void refuse(Runnable run,String label){try{run.run();throw new AssertionError(label);}catch(IllegalStateException expected){}}
    public static void main(String[] args) {
        CachedReceiver cached=new CachedReceiver();
        check(callNames(cached,new String[]{"first","second"}).equals("first:1"),"Ordered method preference first");
        check(callNames(cached,new String[]{"first","second"}).equals("first:2"),"Cache still invokes actual method each time");
        check(callNames(cached,new String[]{"second","first"}).equals("second:3"),"Ordered fallback names belong to cache key");
        check(call(cached,"typed","x").equals("string:x") && call(cached,"typed",7).equals("integer:7"),"Runtime argument classes belong to key");
        refuse(()->call(cached,"typed",(Object)null),"Null ambiguity remains refused after nonnull cache entries");
        check(call(CachedReceiver.class,"staticValue").equals("static"),"Static resolution retained");
        refuse(()->call(cached,"staticValue"),"Static and receiver invocation remain separate");
        for(int repeat=0;repeat<2;repeat++)try{call(cached,"throwsOriginal");throw new AssertionError("Original exception missing");}
        catch(IllegalStateException expected){check(expected.getCause() instanceof IllegalArgumentException && expected.getCause().getMessage().equals("exact original cause"),"Cached invocation preserves actual original cause");}
        Map<String,Object> compound = new LinkedHashMap<String,Object>();
        compound.put("other", new Tag(8,"preserved"));
        compound.put("Configuration", new Tag(3,1));
        Object encoded = nbt(new Tag(10,compound));
        Map<String,Object> expected = row("tag_id",10,"value",row("Configuration",row("tag_id",3,"value",1),"other",row("tag_id",8,"value","preserved")));
        check(hash(encoded).equals(hash(expected)),"Typed ordered full NBT data");
        check(hash(nbt(new Tag(5,-1.25f))).equals(hash(row("tag_id",5,"value",row("decimal","-1.25","raw_bits","3214934016","value_kind","float32")))),"Negative float bits exact");
        check(hash(nbt(new Tag(6,-0.0d))).equals(hash(row("tag_id",6,"value",row("decimal","-0.0","raw_bits","9223372036854775808","value_kind","float64")))),"Negative double zero bits exact");
        check(hash(nbt(new Tag(4,4294967297L))).equals(hash(row("tag_id",4,"value",4294967297L))),"Long not narrowed");
        check(hash(nbt(new Tag(7,new byte[]{-128,0,127}))).equals(hash(row("tag_id",7,"value",Arrays.asList(-128,0,127)))),"Signed bytes exact");
        check(hash(nbt(new Tag(11,new int[]{-2,4}))).equals(hash(row("tag_id",11,"value",Arrays.asList(-2,4)))),"Int arrays exact");
        check(hash(nbt(new LongTag(new long[]{Long.MIN_VALUE,Long.MAX_VALUE}))).equals(hash(row("tag_id",12,"value",Arrays.asList(Long.MIN_VALUE,Long.MAX_VALUE)))),"Original1.12 private long array field exact");
        check(hash(nbt(new Tag(9,Arrays.asList(new Tag(8,"a"),new Tag(8,"b"))))).equals(hash(row("tag_id",9,"element_type",8,"value",Arrays.asList(row("tag_id",8,"value","a"),row("tag_id",8,"value","b"))))),"List order and element type exact");
        check(!hash(nbt(new EmptyTypedList(0))).equals(hash(nbt(new EmptyTypedList(3)))),"Emptied typed list remains distinct from new empty list");
        refuse(()->nbt(new Tag(9,Arrays.asList(new Tag(8,"a"),new Tag(3,2)))),"Mixed original list types refused");
        Stack original=new Stack(2,7,new Tag(10,compound));
        Map<String,Object> observed=item(original);
        check(observed.get("count").equals(2) && observed.get("metadata").equals(7) && observed.get("item_damage").equals(7),"Stack quantities and metadata retained separately");
        check(hash(observed.get("tag")).equals(hash(encoded)),"Stack full NBT preserved");
        Stack copy=(Stack)copyItem(original);copy.count=99;copy.tag=null;
        check(original.count==2 && original.tag!=null,"Copy passed to potentially mutating matcher");
        check(copyItem(new SrgOnly()).equals("copied-original"),"Original obfuscated-name alternative");
        SrgRegistry registry = new SrgRegistry();
        Object key = list(callNames(registry,new String[]{"getKeys","func_148742_b"})).get(0);
        Object machine = callNames(registry,new String[]{"getObject","func_82594_a"},key);
        check(key.equals(callNames(registry,new String[]{"getNameForObject","func_177774_c"},machine)),"SRG registry name roundtrip");
        check(machine == callNames(registry,new String[]{"getObjectById","func_148754_a"},5),"SRG registry numeric roundtrip");
        BiomeRegistry biomes=new BiomeRegistry();
        check(hash(registeredReference(biomes.original,biomes,"minecraft-biome")).equals(hash(row(
            "runtime_class",RegisteredBiome.class.getName(),"projection_kind","registry-reference",
            "registry_kind","minecraft-biome","registry_name","fixture:ocean","numeric_id",4))),"Biome registry identity retains exact name/id and explicit reference meaning");
        biomes.badName=true;
        refuse(()->registeredReference(biomes.original,biomes,"minecraft-biome"),"Wrong registry name roundtrip refused");
        biomes.badName=false;biomes.badNumber=true;
        refuse(()->registeredReference(biomes.original,biomes,"minecraft-biome"),"Wrong registry numeric roundtrip refused");
        biomes.badNumber=false;
        refuse(()->registeredReference(new RegisteredBiome(),biomes,"minecraft-biome"),"Unregistered object refused");
        java.util.function.Predicate<Object> executable = candidate -> true;
        Map<?,?> opaque = (Map<?,?>) value(executable);
        check(opaque.get("projection_kind").equals("opaque-executable") && opaque.get("behavior_body_captured").equals(false),"Executable identity does not claim behavior");
        java.util.function.Predicate<Object> anonymous = new java.util.function.Predicate<Object>() {public boolean test(Object value){return false;}};
        check(((Map<?,?>)value(anonymous)).get("behavior_body_captured").equals(false),"Anonymous executable is also explicit opaque identity");
        check(field(new Child(),"original").equals(17),"Private inherited field");
        refuse(()->call(new Ambiguous(),"choose","both"),"Ambiguous overload refused");
        refuse(()->call(original,"missing"),"Missing original method refused");
        refuse(()->nbt(new Tag(127,"unknown")),"Unknown NBT refused");
        refuse(()->item(new Stack(0,0,null)),"Empty item representative refused");
        Map<String,Object> cycle=new LinkedHashMap<String,Object>();Tag cyclic=new Tag(10,cycle);cycle.put("self",cyclic);
        refuse(()->nbt(cyclic),"Cyclic NBT refused");
        check(nbt(null)==null,"Absent NBT distinct from empty compound");
        check(!hash(nbt(null)).equals(hash(nbt(new Tag(10,new LinkedHashMap<String,Object>())))),"Null vs empty NBT identity");
        new ForgeRecipeSnapshot.RecipeFailures().throwIfAny();
        ForgeRecipeSnapshot.RecipeFailures failures=new ForgeRecipeSnapshot.RecipeFailures();
        IllegalStateException originalFailure=new IllegalStateException("Unsupported exact data class");
        failures.add("first_map",0,new Object(),originalFailure);
        failures.add("second_map",7,new Object(),new IllegalStateException("Unsupported exact data class"));
        failures.add("third_map",3,new Object(),new NoClassDefFoundError("original/missing/Type"));
        try {failures.throwIfAny();throw new AssertionError("Partial recipe capture was not refused");}
        catch(IllegalStateException refused) {
            check(refused.getMessage().contains("3 observed recipes in 2 failure shapes"),"Every failed recipe is counted without substituted records");
            Throwable[] shapes=refused.getSuppressed();
            check(shapes.length==2 && shapes[0].getCause()==originalFailure,"Original throwable retained per failure shape");
            check(shapes[0].getMessage().contains("first_map") && shapes[0].getMessage().contains("second_map")
                && shapes[0].getMessage().contains("union_iteration_ordinal\":7"),"All grouped original recipe contexts retained");
            check(shapes[1].getCause() instanceof NoClassDefFoundError && shapes[1].getMessage().contains("third_map"),"Distinct linkage failure context retained");
        }
        System.out.println("ForgeReflectionTest: bounded contract checks passed; no native initialization performed");
    }
}
