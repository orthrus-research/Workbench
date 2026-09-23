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

import java.util.Collection;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;


import org.apache.commons.lang3.Validate;

import com.google.common.collect.Maps;


class ForgeDefaultedRegistry<V extends IForgeRegistryEntry<V>> extends NativeDefaultedRegistry<NativeLocation, V> implements ILockableRegistry
{
    private boolean locked = false;
    private ForgeRegistry<V> delegate;

    private ForgeDefaultedRegistry(ForgeRegistry<V> owner)
    {
        super(FluidEnvironment.current().runtime(), null);
        this.delegate = owner;
    }


    public void register(int id, NativeLocation key, V value)
    {
        if (locked)
            throw new IllegalStateException("Can not register to a locked registry. Modder should use Forge Register methods.");
        Validate.notNull(value);

        if (value.getRegistryName() == null)
            value.setRegistryName(key);

        int realId = this.delegate.add(id, value);
        if (realId != id && id != -1)
            ForgeRegistryLog.log.warn("Registered object did not get ID it asked for. Name: {} Type: {} Expected: {} Got: {}", key, value.getRegistryType().getName(), id, realId);
    }


    public void putObject(NativeLocation key, V value)
    {
        register(-1, key, value);
    }


    public void validateKey()
    {
        this.delegate.validateKey();
    }

    // Reading Functions


    public V getObject( NativeLocation name)
    {
        return this.delegate.getValue(name);
    }



    public NativeLocation getNameForObject(V value)
    {
        return this.delegate.getKey(value);
    }


    public boolean containsKey(NativeLocation key)
    {
        return this.delegate.containsKey(key);
    }


    public int getIDForObject( V value)
    {
        return this.delegate.getID(value);
    }



    public V getObjectById(int id)
    {
        return this.delegate.getValue(id);
    }


    public Iterator<V> iterator()
    {
        return this.delegate.iterator();
    }


    public Set<NativeLocation> getKeys()
    {
        return this.delegate.getKeys();
    }



    public V getRandomObject(Random random)
    {
        Collection<V> values = this.delegate.getValuesCollection();
        return values.stream().skip(random.nextInt(values.size())).findFirst().orElse(this.delegate.getDefault());
    }

    //internal

    public void lock(){ this.locked = true; }

    public static class Factory<V extends IForgeRegistryEntry<V>> implements IForgeRegistry.CreateCallback<V>
    {
        public static final NativeLocation ID = new NativeLocation("forge", "registry_defaulted_wrapper");

        public void onCreate(IForgeRegistryInternal<V> owner, ForgeRegistryManager stage)
        {
            owner.setSlaveMap(ID, new ForgeDefaultedRegistry<V>((ForgeRegistry<V>)owner));
        }
    }
}
