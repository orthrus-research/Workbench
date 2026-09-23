package material

class PetrochemistryMaterials {
    static void register() {
        Existing = new Material.Builder(20007, SuSyUtility.susyId('existing'))
                .liquid()
                .color(0x111111)
                .build()
    }
}
