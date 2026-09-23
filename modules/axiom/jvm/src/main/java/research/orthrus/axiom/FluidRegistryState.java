/*
 * Minecraft Forge
 * Copyright (c) 2016-2020.
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation version 2.1
 * of the License.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
 */

// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;
import java.util.*;
import java.util.Map.Entry;
final class FluidRegistryState {
    private int maxID;
    NativeBiMap<String, NativeFluid> fluids;
    final NativeBiMap<String, NativeFluid> masterFluidReference;
    NativeBiMap<NativeFluid, Integer> fluidIDs;
    NativeBiMap<Integer, String> fluidNames;
    final NativeBiMap<String,String> defaultFluidName;
    final Map<NativeFluid, FluidDelegate> delegates = new HashMap<>();
    private final Set<String> bucketFluids = new HashSet<>();
    private Set<NativeFluid> currentBucketFluids;
    private final FluidEnvironment environment;
    FluidRegistryState(FluidEnvironment environment) {
        this.environment = environment;
        var runtime = environment.runtime();
        fluids = new NativeBiMap<>(runtime); masterFluidReference = new NativeBiMap<>(runtime);
        fluidIDs = new NativeBiMap<>(runtime); fluidNames = new NativeBiMap<>(runtime);
        defaultFluidName = new NativeBiMap<>(runtime);
    }
public boolean registerFluid(NativeFluid fluid)
    {
        masterFluidReference.put(uniqueName(fluid), fluid);
        delegates.put(fluid, new FluidDelegate(fluid, fluid.getName()));
        if (fluids.containsKey(fluid.getName()))
        {
            return false;
        }
        fluids.put(fluid.getName(), fluid);
        maxID++;
        fluidIDs.put(fluid, maxID);
        fluidNames.put(maxID, fluid.getName());
        defaultFluidName.put(fluid.getName(), uniqueName(fluid));

        environment.postRegistration(fluid.getName(), maxID);
        return true;
    }
private String uniqueName(NativeFluid fluid)
    {
        RegistryRuntime.ActiveMod activeModContainer = environment.runtime().activeModContainer();
        String activeModContainerName = activeModContainer == null ? "minecraft" : activeModContainer.getModId();
        return activeModContainerName+":"+fluid.getName();
    }
public boolean isFluidDefault(NativeFluid fluid)
    {
        return fluids.containsValue(fluid);
    }
public boolean isFluidRegistered(NativeFluid fluid)
    {
        return fluid != null && fluids.containsKey(fluid.getName());
    }
public boolean isFluidRegistered(String fluidName)
    {
        return fluids.containsKey(fluidName);
    }
public NativeFluid getFluid(String fluidName)
    {
        return fluids.get(fluidName);
    }
public String getFluidName(NativeFluid fluid)
    {
        return fluids.inverse().get(fluid);
    }
public String getFluidName(NativeFluidStack stack)
    {
        return getFluidName(stack.getFluid());
    }
public NativeFluidStack getFluidStack(String fluidName, int amount)
    {
        if (!fluids.containsKey(fluidName))
        {
            return null;
        }
        return new NativeFluidStack(getFluid(fluidName), amount);
    }
public Map<String, NativeFluid> getRegisteredFluids()
    {
        return fluids.unmodifiableView();
    }
public Map<NativeFluid, Integer> getRegisteredFluidIDs()
    {
        return fluidIDs.unmodifiableView();
    }
public boolean addBucketForFluid(NativeFluid fluid)
    {
        if(fluid == null) {
            return false;
        }
        // register unregistered fluids
        if (!isFluidRegistered(fluid))
        {
            registerFluid(fluid);
        }
        return bucketFluids.add(fluid.getName());
    }
public Set<NativeFluid> getBucketFluids()
    {
        if (currentBucketFluids == null)
        {
            Set<NativeFluid> tmp = new HashSet<>();
            for (String fluidName : bucketFluids)
            {
                tmp.add(getFluid(fluidName));
            }
            currentBucketFluids = Collections.unmodifiableSet(tmp);
        }
        return currentBucketFluids;
    }
public boolean hasBucket(NativeFluid fluid)
    {
        return bucketFluids.contains(fluid.getName());
    }
public int getMaxID()
    {
        return maxID;
    }
public String getDefaultFluidName(NativeFluid key)
    {
        String name = masterFluidReference.inverse().get(key);
        if ((name == null || name.isEmpty())) {
            environment.logger().error("The fluid registry is corrupted. A fluid {} {} is not properly registered. The mod that registered this is broken", key.getClass().getName(), key.getName());
            throw new IllegalStateException("The fluid registry is corrupted");
        }
        return name;
    }
public void initFluidIDs(NativeBiMap<NativeFluid, Integer> newfluidIDs, Set<String> defaultNames)
    {
        maxID = newfluidIDs.size();
        loadFluidDefaults(newfluidIDs, defaultNames);
    }
private void loadFluidDefaults(NativeBiMap<NativeFluid, Integer> localFluidIDs, Set<String> defaultNames)
    {
        // If there's an empty set of default names, use the defaults as defined locally
        if (defaultNames.isEmpty()) {
            defaultNames.addAll(defaultFluidName.values());
        }
        NativeBiMap<String, NativeFluid> localFluids = new NativeBiMap<>(environment.runtime(), fluids);
        for (String defaultName : defaultNames)
        {
            NativeFluid fluid = masterFluidReference.get(defaultName);
            if (fluid == null) {
                String derivedName = defaultName.split(":",2)[1];
                String localDefault = defaultFluidName.get(derivedName);
                if (localDefault == null) {
                    ForgeRegistryLog.log.error("The fluid {} (specified as {}) is missing from this instance - it will be removed", derivedName, defaultName);
                    continue;
                }
                fluid = masterFluidReference.get(localDefault);
                ForgeRegistryLog.log.error("The fluid {} specified as default is not present - it will be reverted to default {}", defaultName, localDefault);
            }
            ForgeRegistryLog.log.debug("The fluid {} has been selected as the default fluid for {}", defaultName, fluid.getName());
            NativeFluid oldFluid = localFluids.put(fluid.getName(), fluid);
            Integer id = localFluidIDs.remove(oldFluid);
            localFluidIDs.put(fluid, id);
        }
        NativeBiMap<Integer, String> localFluidNames = new NativeBiMap<>(environment.runtime());
        for (Entry<NativeFluid, Integer> e : localFluidIDs.entrySet()) {
            localFluidNames.put(e.getValue(), e.getKey().getName());
        }
        fluidIDs = localFluidIDs;
        fluids = localFluids;
        fluidNames = localFluidNames;
        // Block lookup is not exposed; its cache is not instantiated in this domain.
        currentBucketFluids = null;
        for (FluidDelegate fd : delegates.values())
        {
            fd.rebind();
        }
    }
public void loadFluidDefaults(NativeNbtCompound tag)
    {
        Set<String> defaults = new HashSet<>();
        if (tag.hasKey("DefaultFluidList",9))
        {
            ForgeRegistryLog.log.debug("Loading persistent fluid defaults from world");
            NativeNbtList tl = tag.getTagList("DefaultFluidList", 8);
            for (int i = 0; i < tl.tagCount(); i++)
            {
                defaults.add(tl.getStringTagAt(i));
            }
        }
        else
        {
            ForgeRegistryLog.log.debug("World is missing persistent fluid defaults - using local defaults");
        }
        loadFluidDefaults(new NativeBiMap<>(environment.runtime(), fluidIDs), defaults);
    }
public void writeDefaultFluidList(NativeNbtCompound forgeData)
    {
        NativeNbtList tagList = environment.runtime().newNbtList();

        for (Entry<String, NativeFluid> def : fluids.entrySet())
        {
            tagList.appendTag(environment.runtime().nbtString(getDefaultFluidName(def.getValue())));
        }

        forgeData.setTag("DefaultFluidList", tagList);
    }
public void validateFluidRegistry()
    {
        Set<NativeFluid> illegalFluids = new HashSet<>();
        for (NativeFluid f : fluids.values())
        {
            if (!masterFluidReference.containsValue(f))
            {
                illegalFluids.add(f);
            }
        }

        if (!illegalFluids.isEmpty())
        {
            ForgeRegistryLog.log.fatal("The fluid registry is corrupted. Something has inserted a fluid without registering it");
            ForgeRegistryLog.log.fatal("There is {} unregistered fluids", illegalFluids.size());
            for (NativeFluid f: illegalFluids)
            {
                ForgeRegistryLog.log.fatal("  Fluid name : {}, type: {}", f.getName(), f.getClass().getName());
            }
            ForgeRegistryLog.log.fatal("The mods that own these fluids need to register them properly");
            throw new IllegalStateException("The fluid map contains fluids unknown to the master fluid registry");
        }
    }
IRegistryDelegate<NativeFluid> makeDelegate(NativeFluid fl)
    {
        return delegates.get(fl);
    }
public String getModId( NativeFluidStack fluidStack)
    {
        if (fluidStack != null)
        {
            String defaultFluidName = getDefaultFluidName(fluidStack.getFluid());
            if (defaultFluidName != null)
            {
                NativeLocation fluidResourceName = new NativeLocation(defaultFluidName);
                return fluidResourceName.getNamespace();
            }
        }
        return null;
    }
private class FluidDelegate implements IRegistryDelegate<NativeFluid>
    {
        private String name;
        private NativeFluid fluid;

        FluidDelegate(NativeFluid fluid, String name)
        {
            this.fluid = fluid;
            this.name = name;
        }

        @Override
        public NativeFluid get()
        {
            return fluid;
        }

        @Override
        public NativeLocation name() {
            return new NativeLocation(name);
        }

        @Override
        public Class<NativeFluid> type()
        {
            return NativeFluid.class;
        }

        void rebind()
        {
            fluid = fluids.get(name);
        }
    }
}
