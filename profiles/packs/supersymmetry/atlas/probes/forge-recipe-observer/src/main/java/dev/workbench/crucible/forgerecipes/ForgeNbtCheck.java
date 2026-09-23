package dev.workbench.crucible.forgerecipes;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Precompiled comparison to captured typed NBT, not Minecraft's weaker NBT equality. */
final class ForgeNbtCheck {
    interface Check {boolean matches(Object actual);}
    private ForgeNbtCheck() {}
    private static Object read(Object value,String mcp,String srg,Object... arguments) {return callNames(value,new String[]{mcp,srg},arguments);}
    static Check compile(Object expected) {
        if(expected==null)return actual->actual==null;
        Map<?,?> encoded=(Map<?,?>)expected;int id=((Number)encoded.get("tag_id")).intValue();Object value=encoded.get("value");
        Check contents;
        switch(id) {
            case 1:{Object number=value;contents=actual->number.equals(read(actual,"getByte","func_150290_f"));break;}
            case 2:{Object number=value;contents=actual->number.equals(read(actual,"getShort","func_150289_e"));break;}
            case 3:{Object number=value;contents=actual->number.equals(read(actual,"getInt","func_150287_d"));break;}
            case 4:{Object number=value;contents=actual->number.equals(read(actual,"getLong","func_150291_c"));break;}
            case 5:{int bits=Integer.parseUnsignedInt((String)((Map<?,?>)value).get("raw_bits"));contents=actual->Float.floatToRawIntBits((Float)read(actual,"getFloat","func_150288_h"))==bits;break;}
            case 6:{long bits=Long.parseUnsignedLong((String)((Map<?,?>)value).get("raw_bits"));contents=actual->Double.doubleToRawLongBits((Double)read(actual,"getDouble","func_150286_g"))==bits;break;}
            case 7:{List<?> values=(List<?>)value;byte[] array=new byte[values.size()];for(int i=0;i<array.length;i++)array[i]=((Number)values.get(i)).byteValue();contents=actual->Arrays.equals(array,(byte[])read(actual,"getByteArray","func_150292_c"));break;}
            case 8:{String text=(String)value;contents=actual->text.equals(read(actual,"getString","func_150285_a_"));break;}
            case 9:{
                int element=((Number)encoded.get("element_type")).intValue();List<Check> children=new ArrayList<Check>();
                for(Object child:(List<?>)value)children.add(compile(child));
                contents=actual->{
                    if(((Number)read(actual,"getTagType","func_150303_d")).intValue()!=element
                        || ((Number)read(actual,"tagCount","func_74745_c")).intValue()!=children.size())return false;
                    for(int i=0;i<children.size();i++)if(!children.get(i).matches(read(actual,"get","func_179238_g",i)))return false;
                    return true;
                };break;
            }
            case 10:{
                Map<String,Check> children=new LinkedHashMap<String,Check>();
                for(Map.Entry<?,?> child:((Map<?,?>)value).entrySet())children.put((String)child.getKey(),compile(child.getValue()));
                contents=actual->{
                    Object keys=read(actual,"getKeySet","func_150296_c");
                    if(!(keys instanceof Set<?>) || !children.keySet().equals(keys))return false;
                    for(Map.Entry<String,Check> child:children.entrySet())if(!child.getValue().matches(read(actual,"getTag","func_74781_a",child.getKey())))return false;
                    return true;
                };break;
            }
            case 11:{List<?> values=(List<?>)value;int[] array=new int[values.size()];for(int i=0;i<array.length;i++)array[i]=((Number)values.get(i)).intValue();contents=actual->Arrays.equals(array,(int[])read(actual,"getIntArray","func_150302_c"));break;}
            case 12:{List<?> values=(List<?>)value;long[] array=new long[values.size()];for(int i=0;i<array.length;i++)array[i]=((Number)values.get(i)).longValue();contents=actual->Arrays.equals(array,(long[])fieldNames(actual,new String[]{"data","field_193587_b"}));break;}
            default:throw new IllegalStateException("Unsupported captured NBT tag "+id);
        }
        return actual->actual!=null && ((Number)read(actual,"getId","func_74732_a")).intValue()==id && contents.matches(actual);
    }
}
