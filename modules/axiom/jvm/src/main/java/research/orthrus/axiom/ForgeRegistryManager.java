// Retained selected Cleanroom source; see spec/native-forge-registries.md.
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

package research.orthrus.axiom;

import java.util.Map;
import java.util.Set;


import com.google.common.collect.BiMap;
import com.google.common.collect.HashBiMap;
import com.google.common.collect.Maps;
import com.google.common.collect.Sets;
import com.google.common.collect.Sets.SetView;

import research.orthrus.axiom.IForgeRegistry.*;

class ForgeRegistryManager
{
    public static final ForgeRegistryManager ACTIVE = new ForgeRegistryManager("ACTIVE");
    public static final ForgeRegistryManager VANILLA = new ForgeRegistryManager("VANILLA");
    public static final ForgeRegistryManager FROZEN = new ForgeRegistryManager("FROZEN");

    BiMap<NativeLocation, ForgeRegistry<? extends IForgeRegistryEntry<?>>> registries = HashBiMap.create();
    private BiMap<Class<? extends IForgeRegistryEntry<?>>, NativeLocation> superTypes = HashBiMap.create();
    private Set<NativeLocation> persisted = Sets.newHashSet();
    private final String name;

    public ForgeRegistryManager(String name)
    {
        this.name = name;
    }

    public String getName()
    {
        return this.name;
    }

    @SuppressWarnings("unchecked")
    public <V extends IForgeRegistryEntry<V>> Class<V> getSuperType(NativeLocation key)
    {
        return (Class<V>)superTypes.inverse().get(key);
    }

    @SuppressWarnings("unchecked")
    public <V extends IForgeRegistryEntry<V>> ForgeRegistry<V> getRegistry(NativeLocation key)
    {
        return (ForgeRegistry<V>)this.registries.get(key);
    }

    public <V extends IForgeRegistryEntry<V>> IForgeRegistry<V> getRegistry(Class<V> cls)
    {
        return getRegistry(superTypes.get(cls));
    }

    public <V extends IForgeRegistryEntry<V>> NativeLocation getName(IForgeRegistry<V> reg)
    {
        return this.registries.inverse().get(reg);
    }

    public <V extends IForgeRegistryEntry<V>> ForgeRegistry<V> getRegistry(NativeLocation key, ForgeRegistryManager other)
    {
        if (!this.registries.containsKey(key))
        {
            ForgeRegistry<V> ot = other.getRegistry(key);
            if (ot == null)
                return null;
            this.registries.put(key, ot.copy(this));
            this.superTypes.put(ot.getRegistrySuperType(), key);
            if (other.persisted.contains(key))
                this.persisted.add(key);
        }
        return getRegistry(key);
    }

    <V extends IForgeRegistryEntry<V>> ForgeRegistry<V> createRegistry(NativeLocation name, Class<V> type, NativeLocation defaultKey, int min, int max,
             AddCallback<V> add,  ClearCallback<V> clear,  CreateCallback<V> create,  ValidateCallback<V> validate,
            boolean persisted, boolean allowOverrides, boolean isModifiable,  DummyFactory<V> dummyFactory,  MissingFactory<V> missing)
    {
        Set<Class<?>> parents = Sets.newHashSet();
        findSuperTypes(type, parents);
        SetView<Class<?>> overlappedTypes = Sets.intersection(parents, superTypes.keySet());
        if (!overlappedTypes.isEmpty())
        {
            Class<?> foundType = overlappedTypes.iterator().next();
            ForgeRegistryLog.log.error("Found existing registry of type {} named {}, you cannot create a new registry ({}) with type {}, as {} has a parent of that type", foundType, superTypes.get(foundType), name, type, type);
            throw new IllegalArgumentException("Duplicate registry parent type found - you can only have one registry for a particular super type");
        }
        ForgeRegistry<V> reg = new ForgeRegistry<V>(type, defaultKey, min, max, create, add, clear, validate, this, allowOverrides, isModifiable, dummyFactory, missing);
        registries.put(name, reg);
        superTypes.put(type, name);
        if (persisted)
            this.persisted.add(name);
        return getRegistry(name);
    }

    private void findSuperTypes(Class<?> type, Set<Class<?>> types)
    {
        if (type == null || type == Object.class)
        {
            return;
        }
        types.add(type);
        for (Class<?> interfac : type.getInterfaces())
        {
            findSuperTypes(interfac, types);
        }
        findSuperTypes(type.getSuperclass(), types);
    }



    //Public for testing only
    public void clean()
    {
        this.persisted.clear();
        this.registries.clear();
        this.superTypes.clear();
    }
}
