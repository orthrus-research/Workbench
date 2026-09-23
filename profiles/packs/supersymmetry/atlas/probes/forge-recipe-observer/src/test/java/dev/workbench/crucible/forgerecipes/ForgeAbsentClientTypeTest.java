package dev.workbench.crucible.forgerecipes;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/** A loadable receiver with an unrelated unavailable client method signature. */
public final class ForgeAbsentClientTypeTest {
    public interface RegistryContract { String getRegistryName(); }
    public static final class ClientOnly {}
    public static class RegistryBase implements RegistryContract {
        public String getRegistryName(){return "base:entry";}
    }
    public static final class RegisteredItem extends RegistryBase {
        @Override public String getRegistryName(){return "original:virtual-override";}
        public void tooltip(ClientOnly ignored){}
    }
    public static class PropertyContract {
        public String getKey(){return "original-key";}
        public boolean isHidden(){return false;}
        public void drawInfo(ClientOnly ignored){}
    }
    public static final class Property extends PropertyContract {
        @Override public boolean isHidden(){return true;}
    }
    public static class MachineContract {
        public final String identity="original:machine-id";
        public static final String REGISTRY="original:registry";
        public ClientOnly unrelatedClientField;
    }
    public static final class Machine extends MachineContract {
        public ClientOnly unrelatedSubclassClientField;
    }
    private static final class DedicatedSideLoader extends ClassLoader {
        DedicatedSideLoader(){super(ForgeAbsentClientTypeTest.class.getClassLoader());}
        @Override protected Class<?> loadClass(String name,boolean resolve)throws ClassNotFoundException {
            if(name.equals(ClientOnly.class.getName()))throw new ClassNotFoundException("Client-only type is absent");
            if(!name.startsWith(ForgeAbsentClientTypeTest.class.getName()+"$") || name.endsWith("DedicatedSideLoader"))return super.loadClass(name,resolve);
            synchronized(getClassLoadingLock(name)) {
                Class<?> loaded=findLoadedClass(name);
                if(loaded==null) {
                    try(InputStream in=getParent().getResourceAsStream(name.replace('.','/')+".class")) {
                        if(in==null)throw new ClassNotFoundException(name);
                        ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buffer=new byte[4096];int count;
                        while((count=in.read(buffer))!=-1)out.write(buffer,0,count);
                        byte[] bytes=out.toByteArray();loaded=defineClass(name,bytes,0,bytes.length);
                    } catch(java.io.IOException failure){throw new ClassNotFoundException(name,failure);}
                }
                if(resolve)resolveClass(loaded);return loaded;
            }
        }
    }
    public static void main(String[] args)throws Exception {
        Class<?> receiverType=new DedicatedSideLoader().loadClass(RegisteredItem.class.getName());
        Object receiver=receiverType.getDeclaredConstructor().newInstance();
        boolean broadEnumerationFailed=false;
        try {receiverType.getMethods();}catch(NoClassDefFoundError expected){broadEnumerationFailed=true;}
        if(!broadEnumerationFailed)throw new AssertionError("Fixture did not reproduce absent-client signature failure");
        Object name=ForgeReflection.call(receiver,"getRegistryName");
        if(!"original:virtual-override".equals(name))throw new AssertionError("Original virtual override was not invoked");
        DedicatedSideLoader loader=new DedicatedSideLoader();
        Class<?> contract=loader.loadClass(PropertyContract.class.getName());
        Class<?> propertyType=loader.loadClass(Property.class.getName());
        Object property=propertyType.getDeclaredConstructor().newInstance();
        boolean baseEnumerationFailed=false;
        try {contract.getMethod("getKey");}catch(NoClassDefFoundError expected){baseEnumerationFailed=true;}
        if(!baseEnumerationFailed)throw new AssertionError("Exact reflective getter did not reproduce base client signature failure");
        if(!"original-key".equals(ForgeReflection.callVirtual(property,contract,"getKey",String.class)))
            throw new AssertionError("Exact original inherited key getter failed");
        if(!Boolean.TRUE.equals(ForgeReflection.callVirtual(property,contract,"isHidden",boolean.class)))
            throw new AssertionError("Exact base contract lost subclass boolean override");
        if(!PropertyContract.class.getName().equals(ForgeReflection.virtualOwner(property,"getKey",String.class)))
            throw new AssertionError("Inherited declaration owner changed");
        if(!Property.class.getName().equals(ForgeReflection.virtualOwner(property,"isHidden",boolean.class)))
            throw new AssertionError("Overridden declaration owner changed");
        for(int repeat=0;repeat<3;repeat++)
            if(!Boolean.TRUE.equals(ForgeReflection.callVirtual(property,contract,"isHidden",boolean.class)))throw new AssertionError("Cached handle changed behavior");
        Class<?> machineContract=loader.loadClass(MachineContract.class.getName());
        Class<?> machineType=loader.loadClass(Machine.class.getName());
        Object machine=machineType.getDeclaredConstructor().newInstance();
        boolean fieldEnumerationFailed=false;
        try {machineContract.getDeclaredField("identity");}catch(NoClassDefFoundError expected){fieldEnumerationFailed=true;}
        if(!fieldEnumerationFailed)throw new AssertionError("Exact reflective field did not reproduce unrelated client field failure");
        for(int repeat=0;repeat<3;repeat++) {
            if(!"original:machine-id".equals(ForgeReflection.readPublicField(machine,machineContract,"identity",String.class)))
                throw new AssertionError("Exact inherited original field descriptor failed");
            if(!"original:registry".equals(ForgeReflection.readPublicField(null,machineContract,"REGISTRY",String.class)))
                throw new AssertionError("Exact static original field descriptor failed");
        }
        boolean wrongDescriptorRefused=false;
        try {ForgeReflection.readPublicField(machine,machineContract,"identity",Object.class);}
        catch(IllegalStateException expected){wrongDescriptorRefused=true;}
        if(!wrongDescriptorRefused)throw new AssertionError("Wrong field descriptor admitted");
        System.out.println("ForgeAbsentClientTypeTest: original contract dispatch survives unrelated missing client signature; no native initialization performed");
    }
}
