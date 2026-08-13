package stsbenchobserver;

import com.evacipated.cardcrawl.modthespire.lib.SpireInitializer;

@SpireInitializer
public final class StsBenchObserver {
    private StsBenchObserver() {}

    public static void initialize() {
        // All behavior lives in the serializer patch. Keeping initialization empty makes the
        // observer incapable of changing game state through a subscriber callback.
    }
}
