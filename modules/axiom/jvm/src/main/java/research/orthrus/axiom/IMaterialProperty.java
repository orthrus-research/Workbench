// Extracted from pinned GTCEu; LGPL-3.0. See sources/NOTICE.md and spec/material-properties.md.
package research.orthrus.axiom;

@FunctionalInterface
interface IMaterialProperty {

    void verifyProperty(MaterialProperties properties);
}
