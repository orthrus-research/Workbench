package dev.workbench.crucible.forgerecipes;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;
import dev.workbench.crucible.forgerecipes.ForgeReflectionTest.Tag;

/** Exhaustive pairwise parity with the retained typed serializer on independent values. */
public final class ForgeNbtCheckTest {
    static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    static boolean same(Object a,Object b){return a==null?b==null:a.equals(b);}
    public static final class WrongNumericGetter {
        final byte id;final Object result;
        WrongNumericGetter(int id,Object result){this.id=(byte)id;this.result=result;}
        public byte getId(){return id;}
        public Object getByte(){return result;}
        public Object getShort(){return result;}
        public Object getInt(){return result;}
        public Object getLong(){return result;}
    }
    public static void main(String[] args){
        List<Object> values=new ArrayList<Object>();
        values.add(null);values.add(new Tag(10,Collections.emptyMap()));
        for(int type=1;type<=4;type++){values.add(new Tag(type,-1));values.add(new Tag(type,0));values.add(new Tag(type,127));}
        values.add(new Tag(4,Long.MIN_VALUE));values.add(new Tag(4,Long.MAX_VALUE));
        for(float value:new float[]{-0.0f,0.0f,-1.25f,Float.POSITIVE_INFINITY,Float.intBitsToFloat(0x7fc00001),Float.intBitsToFloat(0x7fc00002)})values.add(new Tag(5,value));
        for(double value:new double[]{-0.0d,0.0d,-1.25d,Double.NEGATIVE_INFINITY,Double.longBitsToDouble(0x7ff8000000000001L),Double.longBitsToDouble(0x7ff8000000000002L)})values.add(new Tag(6,value));
        values.add(new Tag(7,new byte[]{}));values.add(new Tag(7,new byte[]{-128,127}));values.add(new Tag(7,new byte[]{127,-128}));
        values.add(new Tag(8,""));values.add(new Tag(8,"exact"));
        values.add(new Tag(9,Arrays.asList(new Tag(8,"a"),new Tag(8,"b"))));values.add(new Tag(9,Arrays.asList(new Tag(8,"b"),new Tag(8,"a"))));
        values.add(new ForgeReflectionTest.EmptyTypedList(0));values.add(new ForgeReflectionTest.EmptyTypedList(3));
        values.add(new Tag(11,new int[]{Integer.MIN_VALUE,2}));values.add(new Tag(11,new int[]{2,Integer.MIN_VALUE}));
        values.add(new ForgeReflectionTest.LongTag(new long[]{Long.MIN_VALUE,Long.MAX_VALUE}));values.add(new ForgeReflectionTest.LongTag(new long[]{Long.MAX_VALUE,Long.MIN_VALUE}));
        Map<String,Object> first=new LinkedHashMap<String,Object>();first.put("z",new Tag(3,1));first.put("a",new Tag(9,Arrays.asList(new Tag(8,"x"))));
        Map<String,Object> reordered=new LinkedHashMap<String,Object>();reordered.put("a",first.get("a"));reordered.put("z",first.get("z"));
        Map<String,Object> extra=new LinkedHashMap<String,Object>(first);extra.put("new",new Tag(3,2));
        values.add(new Tag(10,first));values.add(new Tag(10,reordered));values.add(new Tag(10,extra));
        values.add(new Tag(10,Collections.singletonMap("a",first.get("a"))));
        List<Object> serialized=new ArrayList<Object>();for(Object value:values)serialized.add(nbt(value));
        int comparisons=0;
        for(int i=0;i<values.size();i++) {
            ForgeNbtCheck.Check compiled=ForgeNbtCheck.compile(serialized.get(i));
            Map<String,Object> expected=row("count",2,"item_damage",7,"metadata",9,"registry_name","fixture:item","tag",serialized.get(i));
            ForgeOrdinaryItemMatching.CopyFields fields=new ForgeOrdinaryItemMatching.CopyFields(expected);
            for(int j=0;j<values.size();j++) {
                boolean baseline=same(serialized.get(i),serialized.get(j));
                check(compiled.matches(values.get(j))==baseline,"Typed NBT parity "+i+"/"+j);
                check(fields.matches(2,7,9,"fixture:item",values.get(j))==baseline,"Copy state parity "+i+"/"+j);comparisons++;
            }
            check(!fields.matches(64,7,9,"fixture:item",values.get(i)),"Count changes remain visible");
            check(!fields.matches(2,8,9,"fixture:item",values.get(i)),"Damage changes remain visible");
            check(!fields.matches(2,7,8,"fixture:item",values.get(i)),"Metadata changes remain visible");
            check(!fields.matches(2,7,9,"fixture:other",values.get(i)),"Registry changes remain visible");
            check(!fields.matches(0,7,9,"fixture:item",values.get(i)),"Empty count refused");
        }
        Object[] wrongNumbers={Integer.valueOf(266),Integer.valueOf(65546),Long.valueOf(4294967306L),Integer.valueOf(10)};
        for(int type=1;type<=4;type++) {
            Object expectedNumeric=nbt(new Tag(type,10));Object wrong=new WrongNumericGetter(type,wrongNumbers[type-1]);
            check(!expectedNumeric.equals(nbt(wrong)),"Baseline numeric wrapper differs "+type);
            check(!ForgeNbtCheck.compile(expectedNumeric).matches(wrong),"Numeric conversion must not hide wrapper/overflow difference "+type);
        }
        byte[] mutable={1,2};Tag mutableTag=new Tag(7,mutable);ForgeNbtCheck.Check retained=ForgeNbtCheck.compile(nbt(mutableTag));
        mutable[0]=3;check(!retained.matches(mutableTag),"Compiled expected byte array must not alias live array");mutable[0]=1;check(retained.matches(mutableTag),"Original array restored");
        Map<String,Object> expectedCompound=(Map<String,Object>)nbt(new Tag(10,first));
        ForgeNbtCheck.Check detachedCompound=ForgeNbtCheck.compile(expectedCompound);
        ((Map<?,?>)expectedCompound.get("value")).clear();expectedCompound.put("tag_id",8);
        check(detachedCompound.matches(new Tag(10,first)),"Compiled compound children and tag ID must not alias expected maps");
        Map<String,Object> expectedArray=(Map<String,Object>)nbt(new Tag(7,new byte[]{1,2}));
        ForgeNbtCheck.Check detachedArray=ForgeNbtCheck.compile(expectedArray);
        ((List<Object>)expectedArray.get("value")).set(0,(byte)9);
        check(detachedArray.matches(new Tag(7,new byte[]{1,2})) && !detachedArray.matches(new Tag(7,new byte[]{9,2})),"Compiled array must not alias expected array list");
        ForgeNbtCheck.Check list=ForgeNbtCheck.compile(nbt(new Tag(9,Arrays.asList(new Tag(3,1),new Tag(3,2)))));
        check(!list.matches(new Tag(9,Arrays.asList(new Tag(3,1),new Tag(8,"2")))),"Mixed child type cannot equal homogeneous captured list");
        check(!list.matches(new Tag(9,Arrays.asList(new Tag(3,1)))) ,"List cardinality checked");
        try{ForgeNbtCheck.compile(row("tag_id",127,"value",1));throw new AssertionError("Unknown expected tag admitted");}catch(IllegalStateException expected){}
        System.out.println("Compiled typed NBT and copy checks: "+comparisons+" exact pairwise comparisons passed; no native initialization performed");
    }
}
