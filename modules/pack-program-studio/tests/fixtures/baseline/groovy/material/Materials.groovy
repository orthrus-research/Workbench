package material

import classes.Helper

class Materials {
    static void register() {
        Alpha = new Material.Builder(32000, Helper.id('alpha'))
            .dust()
            .build()

        /* This commented declaration must never become a candidate.
        Commented = new Material.Builder(32000, Helper.id('commented'))
            .dust()
            .build()
        */

        def dynamicName = new Material.Builder(32010, Helper.id('dynamic_' + sourceName))
            .dust()
            .build()
    }
}
