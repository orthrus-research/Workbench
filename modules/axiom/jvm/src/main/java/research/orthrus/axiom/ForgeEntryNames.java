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
import java.util.Locale;
import org.apache.commons.lang3.Validate;
final class ForgeEntryNames {
public static NativeLocation checkPrefix(String name, boolean warnOverrides)
    {
        int index = name.lastIndexOf(':');
        String oldPrefix = index == -1 ? "" : name.substring(0, index).toLowerCase(Locale.ROOT);
        name = index == -1 ? name : name.substring(index + 1);
        RegistryRuntime.ActiveMod mc = FluidEnvironment.current().runtime().activeModContainer();
        String prefix = mc == null || mc.injectedFmlContainer() ? "minecraft" : mc.getModId().toLowerCase(Locale.ROOT);
        if (warnOverrides && !oldPrefix.equals(prefix) && oldPrefix.length() > 0)
        {
            ForgeRegistryLog.log.warn("Potentially Dangerous alternative prefix `{}` for name `{}`, expected `{}`. This could be a intended override, but in most cases indicates a broken mod.", oldPrefix, name, prefix);
            prefix = oldPrefix;
        }
        return new NativeLocation(prefix, name);
    }
static <T extends IForgeRegistryEntry<T>> ForgeRegistryBuilder<T> makeRegistry(NativeLocation name, Class<T> type, int min, int max)
    {
        return new ForgeRegistryBuilder<T>().setName(name).setType(type).setIDRange(min, max).addCallback(new ForgeNamespacedRegistry.Factory<T>());
    }
static <T extends IForgeRegistryEntry<T>> ForgeRegistryBuilder<T> makeRegistry(NativeLocation name, Class<T> type, int max)
    {
        return new ForgeRegistryBuilder<T>().setName(name).setType(type).setMaxID(max).addCallback(new ForgeNamespacedRegistry.Factory<T>());
    }
static <T extends IForgeRegistryEntry<T>> ForgeRegistryBuilder<T> makeRegistry(NativeLocation name, Class<T> type, int max, NativeLocation _default)
    {
        return new ForgeRegistryBuilder<T>().setName(name).setType(type).setMaxID(max).addCallback(new ForgeDefaultedRegistry.Factory<T>()).setDefaultKey(_default);
    }
public static <V extends IForgeRegistryEntry<V>> NativeDefaultedRegistry<NativeLocation, V> getWrapperDefaulted(Class<V> cls)
    {
        IForgeRegistry<V> reg = ForgeRegistryLookup.findRegistry(cls);
        Validate.notNull(reg, "Attempted to get vanilla wrapper for unknown registry: " + cls.toString());
        @SuppressWarnings("unchecked")
        NativeDefaultedRegistry<NativeLocation, V> ret = reg.getSlaveMap(ForgeDefaultedRegistry.Factory.ID, ForgeDefaultedRegistry.class);
        Validate.notNull(ret, "Attempted to get vanilla wrapper for registry created incorrectly: " + cls.toString());
        return ret;
    }
public static <V extends IForgeRegistryEntry<V>> NativeNamedRegistry<NativeLocation, V> getWrapper(Class<V> cls)
    {
        IForgeRegistry<V> reg = ForgeRegistryLookup.findRegistry(cls);
        Validate.notNull(reg, "Attempted to get vanilla wrapper for unknown registry: " + cls.toString());
        @SuppressWarnings("unchecked")
        NativeNamedRegistry<NativeLocation, V> ret = reg.getSlaveMap(ForgeNamespacedRegistry.Factory.ID, ForgeNamespacedRegistry.class);
        Validate.notNull(ret, "Attempted to get vanilla wrapper for registry created incorrectly: " + cls.toString());
        return ret;
    }
}
