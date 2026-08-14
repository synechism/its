# sts-bench observer mod

This tiny companion mod patches Communication Mod's private card serializer after it has built a
card JSON object. It adds the current, live `raw_description` and dynamic numeric fields from the
real `AbstractCard`. A second patch stamps its version into every Communication Mod envelope so a
controller can verify the observer before starting a seed. A third patch normalizes the dedicated
worker profile before each `START`: it disables first-time tutorials, fully unlocks the game's
card/relic/character pools, and marks the nine seeded bosses as seen. This removes profile history
from seeded content selection. If a tutorial still appears, the patch translates Communication
Mod's `KEY CONFIRM` maintenance command into the native **Got It** button's completed-click state.
It does not implement or simulate any game rules.

Use a dedicated Slay the Spire profile for a benchmark worker. Profile normalization is persistent
and intentionally unlocks content in that profile.

Build it against a user-owned Slay the Spire installation:

```bash
./scripts/build-observer-mod /path/to/SlayTheSpire.app/Contents/Resources
```

The script writes `dist/StsBenchObserver.jar`. Copy that JAR into the game's `mods/` directory and
enable **Sts Bench Observer** alongside BaseMod and Communication Mod. No game or third-party mod
binaries are copied into this repository or the output JAR.
