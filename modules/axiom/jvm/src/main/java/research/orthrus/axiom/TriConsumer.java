// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;

@FunctionalInterface
interface TriConsumer<T, U, S> {

    void accept(T t, U u, S s);
}
