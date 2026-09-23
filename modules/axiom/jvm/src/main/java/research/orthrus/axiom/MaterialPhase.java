package research.orthrus.axiom;

/** Extracted GT material-manager phase predicate, not a registry/event dispatcher. */
enum MaterialPhase {
    PRE, OPEN, CLOSED, FROZEN;

    // IMaterialRegistryManager.canModifyMaterials; getPhase() replaced by this.
    boolean canModifyMaterials() {
        return this != MaterialPhase.FROZEN && this != MaterialPhase.PRE;
    }
}
