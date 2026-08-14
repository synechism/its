package stsbenchobserver;

import basemod.ReflectionHacks;
import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePrefixPatch;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.helpers.TipTracker;
import com.megacrit.cardcrawl.ui.FtueTip;
import com.megacrit.cardcrawl.ui.buttons.GotItButton;
import com.megacrit.cardcrawl.unlock.UnlockTracker;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.Set;

@SpirePatch(
        cls = "communicationmod.CommandExecutor",
        method = "executeCommand",
        paramtypez = {String.class},
        requiredModId = "CommunicationMod"
)
public final class TutorialCommandPatch {
    private static final String[] BOSS_IDS = {
        "GUARDIAN", "GHOST", "SLIME",
        "CHAMP", "AUTOMATON", "COLLECTOR",
        "CROW", "DONUT", "WIZARD"
    };

    private TutorialCommandPatch() {}

    @SpirePrefixPatch
    public static void Prefix(String command) {
        String normalized = command.trim();
        if (normalized.regionMatches(true, 0, "START ", 0, 6)) {
            normalizeProfile();
            return;
        }
        if (!"KEY CONFIRM".equalsIgnoreCase(normalized)
                || AbstractDungeon.screen != AbstractDungeon.CurrentScreen.FTUE
                || AbstractDungeon.ftue == null) {
            return;
        }

        // FtueTip ignores the keyboard InputAction used by Communication Mod's
        // KEY command. Its own update loop closes only after the Got It hitbox
        // reports a completed click (or a controller action). Mark that native
        // click state so the game closes the overlay on its next normal update.
        GotItButton button = ReflectionHacks.getPrivate(
                AbstractDungeon.ftue,
                FtueTip.class,
                "button"
        );
        if (button != null && button.hb != null) {
            button.hb.clicked = true;
        }
    }

    private static void normalizeProfile() {
        // Tutorials, unlock pools, and the unseen-boss sequence live in the
        // mutable Steam profile rather than the seeded run. Normalize them
        // before CommandExecutor creates the dungeon so a seed means the same
        // thing before and after an earlier benchmark episode.
        TipTracker.disableAllFtues();

        Set<String> locked = new LinkedHashSet<String>();
        locked.addAll(new ArrayList<String>(UnlockTracker.lockedCards));
        locked.addAll(new ArrayList<String>(UnlockTracker.lockedRelics));
        locked.addAll(new ArrayList<String>(UnlockTracker.lockedCharacters));
        locked.addAll(new ArrayList<String>(UnlockTracker.lockedLoadouts));
        for (String id : locked) {
            UnlockTracker.hardUnlockOverride(id);
        }
        if (!locked.isEmpty()) {
            UnlockTracker.refresh();
        }

        for (String bossId : BOSS_IDS) {
            UnlockTracker.markBossAsSeen(bossId);
        }
    }
}
