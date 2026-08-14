package stsbenchobserver;

import com.evacipated.cardcrawl.modthespire.lib.SpireInitializer;

@SpireInitializer
public final class StsBenchObserver {
    private StsBenchObserver() {}

    public static void initialize() {
        // Behavior lives in narrowly scoped Communication Mod command/serialization patches.
        // No BaseMod update subscriber or independent simulation loop is installed here.
    }
}
