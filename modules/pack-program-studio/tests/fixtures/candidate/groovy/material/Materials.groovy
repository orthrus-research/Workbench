package material

import classes.Helper

class Materials {
    static void register() {
        Alpha = new Material.Builder(32000, Helper.id('alpha'))
            .dust()
            .build()
        Beta = new Material.Builder(32000, Helper.id('beta'))
            .dust()
            .build()
    }
}
