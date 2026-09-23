// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;
import com.google.common.collect.HashBiMap;




class MarkerMaterials {

    @SuppressWarnings("ResultOfMethodCallIgnored")
    public static void register() {
        Color.Colorless.toString();
        Tier.ULV.toString();
        Empty.toString();
    }

    /**
     * Marker materials without category
     */
    public static final MarkerMaterial Empty = MarkerMaterial.create("empty");

    /**
     * Color materials
     */
    public static class Color {

        /**
         * Can be used only by direct specifying
         * Means absence of color on OrePrefix
         * Often a default value for color prefixes
         */
        public static final MarkerMaterial Colorless = MarkerMaterial.create("colorless");

        public static final MarkerMaterial White = MarkerMaterial.create("white");
        public static final MarkerMaterial Orange = MarkerMaterial.create("orange");
        public static final MarkerMaterial Magenta = MarkerMaterial.create("magenta");
        public static final MarkerMaterial LightBlue = MarkerMaterial.create("light_blue");
        public static final MarkerMaterial Yellow = MarkerMaterial.create("yellow");
        public static final MarkerMaterial Lime = MarkerMaterial.create("lime");
        public static final MarkerMaterial Pink = MarkerMaterial.create("pink");
        public static final MarkerMaterial Gray = MarkerMaterial.create("gray");
        public static final MarkerMaterial LightGray = MarkerMaterial.create("light_gray");
        public static final MarkerMaterial Cyan = MarkerMaterial.create("cyan");
        public static final MarkerMaterial Purple = MarkerMaterial.create("purple");
        public static final MarkerMaterial Blue = MarkerMaterial.create("blue");
        public static final MarkerMaterial Brown = MarkerMaterial.create("brown");
        public static final MarkerMaterial Green = MarkerMaterial.create("green");
        public static final MarkerMaterial Red = MarkerMaterial.create("red");
        public static final MarkerMaterial Black = MarkerMaterial.create("black");

        /**
         * Arrays containing all possible color values (without Colorless!)
         */
        public static final MarkerMaterial[] VALUES = {
                White, Orange, Magenta, LightBlue, Yellow, Lime, Pink, Gray, LightGray, Cyan, Purple, Blue, Brown,
                Green, Red, Black
        };

        /**
         * Gets color by it's name
         * Name format is equal to NativeDyeColor
         */
        public static MarkerMaterial valueOf(String string) {
            for (MarkerMaterial color : VALUES) {
                if (color.toString().equals(string)) {
                    return color;
                }
            }
            return null;
        }

        /**
         * Contains associations between MC NativeDyeColor and Color MarkerMaterial
         */
        public static final HashBiMap<NativeDyeColor, MarkerMaterial> COLORS = HashBiMap.create();

        static {
            for (NativeDyeColor color : NativeDyeColor.values()) {
                COLORS.put(color, Color.valueOf(color.getName()));
            }
        }
    }

    /**
     * Circuitry, batteries and other technical things
     */
    public static class Tier {

        public static final FluidMaterial ULV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.ULV].toLowerCase());
        public static final FluidMaterial LV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.LV].toLowerCase());
        public static final FluidMaterial MV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.MV].toLowerCase());
        public static final FluidMaterial HV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.HV].toLowerCase());
        public static final FluidMaterial EV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.EV].toLowerCase());
        public static final FluidMaterial IV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.IV].toLowerCase());
        public static final FluidMaterial LuV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.LuV].toLowerCase());
        public static final FluidMaterial ZPM = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.ZPM].toLowerCase());
        public static final FluidMaterial UV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.UV].toLowerCase());
        public static final FluidMaterial UHV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.UHV].toLowerCase());

        public static final FluidMaterial UEV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.UEV].toLowerCase());
        public static final FluidMaterial UIV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.UIV].toLowerCase());
        public static final FluidMaterial UXV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.UXV].toLowerCase());
        public static final FluidMaterial OpV = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.OpV].toLowerCase());
        public static final FluidMaterial MAX = MarkerMaterial.create(MaterialVoltages.VN[MaterialVoltages.MAX].toLowerCase());
    }

    public static class Component {

        public static final FluidMaterial Resistor = MarkerMaterial.create("resistor");
        public static final FluidMaterial Transistor = MarkerMaterial.create("transistor");
        public static final FluidMaterial Capacitor = MarkerMaterial.create("capacitor");
        public static final FluidMaterial Diode = MarkerMaterial.create("diode");
        public static final FluidMaterial Inductor = MarkerMaterial.create("inductor");
    }
}
