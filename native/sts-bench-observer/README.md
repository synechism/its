# sts-bench observer mod

This tiny companion mod patches Communication Mod's private card serializer after it has built a
card JSON object. It adds the current, live `raw_description` and dynamic numeric fields from the
real `AbstractCard`. A second patch stamps its version into every Communication Mod envelope so a
controller can verify the observer before starting a seed. It does not implement or simulate any
game rules.

Build it against a user-owned Slay the Spire installation:

```bash
./scripts/build-observer-mod /path/to/SlayTheSpire.app/Contents/Resources
```

The script writes `dist/StsBenchObserver.jar`. Copy that JAR into the game's `mods/` directory and
enable **Sts Bench Observer** alongside BaseMod and Communication Mod. No game or third-party mod
binaries are copied into this repository or the output JAR.
