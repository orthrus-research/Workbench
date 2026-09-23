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

import java.util.List;

import com.google.common.collect.Lists;

import research.orthrus.axiom.IForgeRegistry.*;


class ForgeRegistryBuilder<T extends IForgeRegistryEntry<T>>
{
    private NativeLocation registryName;
    private Class<T> registryType;
    private NativeLocation optionalDefaultKey;
    private int minId = 0;
    private int maxId = Integer.MAX_VALUE - 1;
    private List<AddCallback<T>> addCallback = Lists.newArrayList();
    private List<ClearCallback<T>> clearCallback = Lists.newArrayList();
    private List<CreateCallback<T>> createCallback = Lists.newArrayList();
    private List<ValidateCallback<T>> validateCallback = Lists.newArrayList();
    private boolean saveToDisc = true;
    private boolean allowOverrides = true;
    private boolean allowModifications = false;
    private DummyFactory<T> dummyFactory;
    private MissingFactory<T> missingFactory;

    public ForgeRegistryBuilder<T> setName(NativeLocation name)
    {
        this.registryName = name;
        return this;
    }

    public ForgeRegistryBuilder<T> setType(Class<T> type)
    {
        this.registryType = type;
        return this;
    }

    public ForgeRegistryBuilder<T> setIDRange(int min, int max)
    {
        this.minId = min;
        this.maxId = max;
        return this;
    }

    public ForgeRegistryBuilder<T> setMaxID(int max)
    {
        return this.setIDRange(0, max);
    }

    public ForgeRegistryBuilder<T> setDefaultKey(NativeLocation key)
    {
        this.optionalDefaultKey = key;
        return this;
    }

    @SuppressWarnings("unchecked")
    public ForgeRegistryBuilder<T> addCallback(Object inst)
    {
        if (inst instanceof AddCallback)
            this.add((AddCallback<T>)inst);
        if (inst instanceof ClearCallback)
            this.add((ClearCallback<T>)inst);
        if (inst instanceof CreateCallback)
            this.add((CreateCallback<T>)inst);
        if (inst instanceof ValidateCallback)
            this.add((ValidateCallback<T>)inst);
        if (inst instanceof DummyFactory)
            this.set((DummyFactory<T>)inst);
        if (inst instanceof MissingFactory)
            this.set((MissingFactory<T>)inst);
        return this;
    }

    public ForgeRegistryBuilder<T> add(AddCallback<T> add)
    {
        this.addCallback.add(add);
        return this;
    }

    public ForgeRegistryBuilder<T> add(ClearCallback<T> clear)
    {
        this.clearCallback.add(clear);
        return this;
    }

    public ForgeRegistryBuilder<T> add(CreateCallback<T> create)
    {
        this.createCallback.add(create);
        return this;
    }

    public ForgeRegistryBuilder<T> add(ValidateCallback<T> validate)
    {
        this.validateCallback.add(validate);
        return this;
    }

    public ForgeRegistryBuilder<T> set(DummyFactory<T> factory)
    {
        this.dummyFactory = factory;
        return this;
    }

    public ForgeRegistryBuilder<T> set(MissingFactory<T> missing)
    {
        this.missingFactory = missing;
        return this;
    }

    public ForgeRegistryBuilder<T> disableSaving()
    {
        this.saveToDisc = false;
        return this;
    }

    public ForgeRegistryBuilder<T> disableOverrides()
    {
        this.allowOverrides = false;
        return this;
    }

    public ForgeRegistryBuilder<T> allowModification()
    {
        this.allowModifications = true;
        return this;
    }

    public IForgeRegistry<T> create()
    {
        return ForgeRegistryManager.ACTIVE.createRegistry(registryName, registryType, optionalDefaultKey, minId, maxId,
                getAdd(), getClear(), getCreate(), getValidate(), saveToDisc, allowOverrides, allowModifications, dummyFactory, missingFactory);
    }


    private AddCallback<T> getAdd()
    {
        if (addCallback.isEmpty())
            return null;
        if (addCallback.size() == 1)
            return addCallback.get(0);

        return (owner, stage, id, obj, old) ->
        {
            for (AddCallback<T> cb : this.addCallback)
                cb.onAdd(owner, stage, id, obj, old);
        };
    }


    private ClearCallback<T> getClear()
    {
        if (clearCallback.isEmpty())
            return null;
        if (clearCallback.size() == 1)
            return clearCallback.get(0);

        return (owner, stage) ->
        {
            for (ClearCallback<T> cb : this.clearCallback)
                cb.onClear(owner, stage);
        };
    }


    private CreateCallback<T> getCreate()
    {
        if (createCallback.isEmpty())
            return null;
        if (createCallback.size() == 1)
            return createCallback.get(0);

        return (owner, stage) ->
        {
            for (CreateCallback<T> cb : this.createCallback)
                cb.onCreate(owner, stage);
        };
    }


    private ValidateCallback<T> getValidate()
    {
        if (validateCallback.isEmpty())
            return null;
        if (validateCallback.size() == 1)
            return validateCallback.get(0);

        return (owner, stage, id, key, obj) ->
        {
            for (ValidateCallback<T> cb : this.validateCallback)
                cb.onValidate(owner, stage, id, key, obj);
        };
    }
}
