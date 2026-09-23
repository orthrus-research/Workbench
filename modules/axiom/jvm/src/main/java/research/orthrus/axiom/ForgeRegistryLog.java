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
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
final class ForgeRegistryLog {
    public static final Logger log = LogManager.getLogger("FML");
public static void bigWarning(String format, Object... data)
    {
        StackTraceElement[] trace = Thread.currentThread().getStackTrace();
        log.warn("****************************************");
        log.warn("* "+format, data);
        for (int i = 2; i < 8 && i < trace.length; i++)
        {
            log.warn("*  at {}{}", trace[i].toString(), i == 7 ? "..." : "");
        }
        log.warn("****************************************");
    }
}
