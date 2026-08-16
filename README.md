# sts-bench

This is a benchmark and RL-env for one of my favorite roguelikes in the last few years, Slay the Spire. I had intended to do a GRPO run after benchmarking a couple of the frontier models, but it turns out current models can already beat the game (only at A0 though)! There is a fair amount of variance depending on your seed, i.e. on one seed 5.6 Luna failed on floor 14, on another it reached floor 50 and failed on the final boss. 5.6 Sol seems to beat the game quite reliably though. Next order of business will be to up the difficulty to A10 and see what happens.






## Install

You need:

1. A user-owned PC copy of Slay the Spire 1.
2. [ModTheSpire](https://github.com/kiooeht/ModTheSpire).
3. [BaseMod](https://github.com/daviscook477/BaseMod).
4. [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod).
5. Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:synechism/sts.git
cd its
uv sync --extra dev
```

For complete card observations, build the repository's source-only observer mod against your own
installation and copy the resulting JAR into the game's `mods/` directory:

```bash
./scripts/build-observer-mod /path/to/SlayTheSpire.app/Contents/Resources
```

Enable **Sts Bench Observer** with BaseMod and Communication Mod. It patches only Communication
Mod's card-to-JSON conversion, adding live rules text and dynamic damage/block/magic values from
the authoritative `AbstractCard`. Live values are retained for actionable hand cards; stable base
values are used for cards outside the hand so frame-timed render caches cannot change a replay
hash. It also normalizes tutorials, unlock pools, and seen-boss flags before each seeded run. Use a
dedicated benchmark profile: these unlocks persist in that profile. The observer contains no game
assets, content database, or simulated rules.

Launch the game with ModTheSpire, BaseMod, and CommunicationMod enabled once so the mod creates
its config. Set its `command` to the **absolute** bridge executable, for example:

```properties
command=/absolute/path/to/its/.venv/bin/sts-bench bridge --host 127.0.0.1 --port 17851 --game-version VERSION --mod-the-spire-version VERSION --base-mod-version VERSION --communication-mod-version 1.2.1
runAtGameStart=false
verbose=false
maxInitializationTimeout=30
```

The CommunicationMod README explains where its generated `SpireConfig` lives on each platform.
With `runAtGameStart=false`, start the external process from CommunicationMod's in-game mod panel
after the controller says it is listening. Alternatively, enable it and start the controller
before launching the game. On Windows, follow Java properties escaping rules for backslashes and
spaces.

For a bridge reachable beyond localhost, set the same strong token on both sides:

```properties
command=/absolute/path/to/sts-bench bridge --host 10.0.0.5 --port 17851 --token LONG_RANDOM_TOKEN
```

```bash
uv run sts-bench smoke --host 0.0.0.0 --token LONG_RANDOM_TOKEN
```

The controller refuses a non-loopback listener without a token. Do not expose the port directly
to the public internet.



## Evaluate a model

Any Chat Completions-compatible endpoint works:

```bash
export OPENAI_API_KEY=...
uv run sts-bench eval \
  --model gpt-5-mini \
  --seed-set v1 \
  --limit 5 \
  --character Ironclad \
  --ascension 0
```


### Result


| Metric | `gpt-5.6-sol` high | `gpt-5.6-terra` low |
| --- | ---: | ---: |
| Result | **Victory** | Defeat |
| Floor reached | **51** | 50 |
| Score | **713** | 576 |
| Acts / bosses cleared | **3 / 3** | 2 / 2 |
| Elites killed | 4 | 5 |
| Model decisions | 896 | 838 |
| Illegal actions / forced defaults | **0 / 0** | 1 / 0 |
| Episode runtime | **2h 26m** | approximately 1h 55m |
| Recorded input / output tokens | 21,144,518 / 225,546 | 19,238,005 / 194,977 |

The winning deck used a `Corruption+` / `Dark Embrace+` / `Dead Branch` exhaust engine, backed by
`Power Through+`, `Second Wind+`, `Disarm+`, two copies of `Reaper`, and upgraded attacks.



