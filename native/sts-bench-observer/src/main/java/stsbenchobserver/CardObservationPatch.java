package stsbenchobserver;

import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePostfixPatch;
import com.megacrit.cardcrawl.cards.AbstractCard;

import java.util.ArrayList;
import java.util.HashMap;

@SpirePatch(
        cls = "communicationmod.GameStateConverter",
        method = "convertCardToJson",
        paramtypez = {AbstractCard.class},
        requiredModId = "CommunicationMod"
)
public final class CardObservationPatch {
    public static final String VERSION = "0.5.0";

    private CardObservationPatch() {}

    @SpirePostfixPatch
    public static HashMap<String, Object> Postfix(
            HashMap<String, Object> __result,
            AbstractCard card
    ) {
        __result.put("sts_bench_observer_version", VERSION);
        __result.put("raw_description", card.rawDescription);
        __result.put("base_cost", card.cost);
        __result.put("damage", card.damage);
        __result.put("block", card.block);
        __result.put("magic_number", card.magicNumber);
        __result.put("base_damage", card.baseDamage);
        __result.put("base_block", card.baseBlock);
        __result.put("base_magic_number", card.baseMagicNumber);
        __result.put("is_cost_modified", card.isCostModifiedForTurn);
        __result.put("is_damage_modified", card.isDamageModified);
        __result.put("is_block_modified", card.isBlockModified);
        __result.put("is_magic_number_modified", card.isMagicNumberModified);
        __result.put("keywords", new ArrayList<String>(card.keywords));
        return __result;
    }
}
