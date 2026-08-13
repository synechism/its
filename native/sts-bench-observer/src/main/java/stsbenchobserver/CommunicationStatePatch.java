package stsbenchobserver;

import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePostfixPatch;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

@SpirePatch(
        cls = "communicationmod.GameStateConverter",
        method = "getCommunicationState",
        requiredModId = "CommunicationMod"
)
public final class CommunicationStatePatch {
    private CommunicationStatePatch() {}

    @SpirePostfixPatch
    public static String Postfix(String __result) {
        JsonObject envelope = new JsonParser().parse(__result).getAsJsonObject();
        envelope.addProperty("sts_bench_observer_version", CardObservationPatch.VERSION);
        return envelope.toString();
    }
}
