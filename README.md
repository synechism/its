# sts-bench

`sts-bench` is a reproducible LLM benchmark and RL environment for a user-owned copy of
Slay the Spire 1. Models play the authoritative game through semantic actions exposed by
CommunicationMod; the benchmark does not reimplement cards, combat, maps, rewards, or scoring.

The first calibration found a useful frontier. On the same Ironclad seed and agent settings,
GPT-5.6 Sol cleared A15 while GPT-5.6 Terra died to the Act 2 boss. Sol then lost at A16, A17,
and A20. These are single-run capability pilots, not population win-rate estimates.

| Model | Ascension | Result | Floor | Score | Acts | Elites | Decisions |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| GPT-5.6 Sol high | A10 | **Victory** | 51 | 1,221 | 3 | 8 | 860 |
| GPT-5.6 Sol high | A15 | **Victory** | 51 | 1,378 | 3 | 8 | 921 |
| GPT-5.6 Terra high | A15 | Defeat | 33 | 495 | 1 | 3 | 632 |
| GPT-5.6 Sol high | A16 | Defeat | 38 | 767 | 2 | 3 | 640 |
| GPT-5.6 Sol high | A17 | Defeat | 16 | 218 | 0 | 2 | 257 |
| GPT-5.6 Sol high | A20 | Defeat | 16 | 236 | 0 | 2 | 295 |

The A15 victory took 2h 36m and used 21,014,427 recorded input tokens and 248,011 output
tokens. Sol finished at 57/75 HP with a 38-card strength/self-damage deck built around two
`Limit Break`s, `Rupture`, `J.A.X.`, `Reaper`, `Feed`, and `Blood for Blood+`, plus 17 relics.
The trajectory contains 921 model decisions and 1,064 total engine transitions once automatic
maintenance actions are included.

All six calibration rows use `STSBENCHV1005`, Ironclad, temperature 0, two history turns, and
the real game. Every selected action was legal and none used a forced default. The frozen protocol
is in [`configs/eval/benchmark-v1.toml`](configs/eval/benchmark-v1.toml), and machine-readable
results are in [`results/benchmark-v1.json`](results/benchmark-v1.json).

An earlier A0 pilot provides context: Terra went 0/5 on seeds `STSBENCHV1000`–`1004`, ranging from
floor 8 to floor 50, while Sol cleared `STSBENCHV1002`. Benchmark v1 therefore treats A15 as the
standard frontier tier, A16 as a challenge tier, and A20 as a ceiling probe. Ten untouched v2
seeds are frozen for future or community-funded evaluations; no claim is made about performance
on them.

## Install

You need:

1. A user-owned PC copy of Slay the Spire 1.
2. [ModTheSpire](https://github.com/kiooeht/ModTheSpire).
3. [BaseMod](https://github.com/daviscook477/BaseMod).
4. [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod).
5. Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/).
6. [FFmpeg](https://ffmpeg.org/) for optional screen recording and overlays.

```bash
git clone git@github.com:synechism/sts.git
cd sts
uv sync --extra dev
```

Build the repository's source-only observer mod against your installation and copy the resulting
JAR into the game's `mods/` directory:

```bash
./scripts/build-observer-mod /path/to/SlayTheSpire.app/Contents/Resources
```

Enable **Sts Bench Observer** with BaseMod and CommunicationMod. The observer adds authoritative
card text and live numeric values to CommunicationMod observations and normalizes tutorials,
unlocks, and boss-seen flags before seeded runs. Use a dedicated benchmark profile because content
unlocks persist in that profile. The repository contains no game assets or simulated rules.

Launch the game with the three mods once so CommunicationMod creates its config. Point `command`
at the absolute bridge executable:

```properties
command=/absolute/path/to/sts/.venv/bin/sts-bench bridge --host 127.0.0.1 --port 17851 --game-version VERSION --mod-the-spire-version VERSION --base-mod-version VERSION --communication-mod-version 1.2.1
runAtGameStart=false
verbose=false
maxInitializationTimeout=30
```

The unattended supervisor temporarily enables `runAtGameStart`, launches a fresh game per attempt,
and restores the exact original config afterward. For a bridge reachable beyond localhost, set the
same strong token on both sides; the controller refuses an unauthenticated non-loopback listener.

Before a smoke test or overnight run, inspect the installation without launching the game:

```bash
uv run sts-bench doctor
```

`doctor` checks the supported Python version, game paths, all four required mod JARs, the
CommunicationMod bridge command, idle config state, and optional video tooling. It is read-only.
Use `--json` for automation and `--require-video` when FFmpeg is required for the planned run.

## Evaluate a model

Any Chat Completions-compatible endpoint works:

```bash
export OPENAI_API_KEY=...
uv run sts-bench eval \
  --model gpt-5-mini \
  --seed-set v2 \
  --limit 1 \
  --character Ironclad \
  --ascension 15
```

Authenticated Codex CLI runs use `--backend codex-cli`. For a long local run, `--detach` starts the
supervisor in a new process session so closing the terminal or client does not orphan the game:

```bash
uv run sts-bench overnight \
  --backend codex-cli \
  --model gpt-5.6-sol \
  --reasoning-effort high \
  --seed-set v2 \
  --limit 1 \
  --character Ironclad \
  --ascension 15 \
  --runs-dir runs/sol-a15-v2 \
  --detach
```

`overnight-status.json` records the current seed, retry reason, attempt logs, finalized run path,
and completion state. A transport timeout is retried with a fresh game and classified separately;
only directories containing both `manifest.json` and `outcome.json` count as completed episodes.

## Artifacts and reporting

Each run records:

- a versioned manifest with model, engine, seed, character, Ascension, and sampling settings;
- every visible state and SHA-256 replay hash;
- raw model responses, legal actions, chosen semantic command, and resulting state hash;
- terminal outcome, score, progress, token counts, and action-integrity metrics;
- a readable transcript.

Aggregation is recursive and never combines different characters or Ascension levels. Generate the
checked Benchmark v1 report from local artifacts with:

```bash
uv run sts-bench aggregate \
  --runs-dir runs \
  --seed STSBENCHV1005 \
  --model codex-cli/gpt-5.6-sol \
  --model codex-cli/gpt-5.6-terra \
  --ascension 10 --ascension 15 --ascension 16 --ascension 17 --ascension 20 \
  --character Ironclad \
  --output results/benchmark-v1.json \
  --markdown results/benchmark-v1.md
```

Ranks reset within each exact character/Ascension tier. Report tiny pilots as raw records such as
`1/1`, never as estimated population win rates.

Validate a completed run before using it in a report:

```bash
uv run sts-bench submission validate /path/to/run
```

To share a result, create a small privacy-scrubbed summary bundle:

```bash
uv run sts-bench submission export /path/to/run --output result.submission.tar.gz
uv run sts-bench submission validate result.submission.tar.gz
```

The default bundle excludes the trajectory and removes local worker IDs, PIDs, private endpoint
URLs, and secret-like fields. `--include-trajectory` creates an independently hash-auditable bundle,
but also includes raw model responses, prompts, and visible game text; use it only deliberately.
Structural validation detects corruption and internal inconsistencies. It cannot prove which model
ran or that the game/mod installation was unmodified. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for
the frozen evaluation matrix and result-submission checklist.

## Real-game replay and video

A recorded trajectory can be replayed without another model call. Replay sends the original engine
commands to a freshly seeded real game and verifies the visible state hash before and after every
transition:

[Watch a 25-second verified real-game replay clip](https://github.com/synechism/sts/releases/download/v0.1.0/sts-bench-sol-a15-demo-25s.mp4)
from GPT-5.6 Sol's A15 victory. It uses the recorded model actions and makes no new model calls.

```bash
uv run sts-bench replay /path/to/run \
  --launch-game \
  --record-display 1 \
  --video-output artifacts/sol-a15-win.mp4 \
  --video-speed 8
```

The recorder writes a raw capture, an SRT action timeline, a replay-verification JSON file, and an
MP4 with a model/action/progress overlay. `--video-speed` changes only the presentation render, not
the authoritative replay.

For a stronger determinism check, replay a terminal trajectory twice:

```bash
uv run sts-bench verify-determinism /path/to/run --launch-game
```

## RL environment

Install the optional training adapter with `uv sync --extra training`. The `verifiers.v1`
environment in `sts_bench.environment` exposes sparse victory reward plus floor, boss, and legality
metrics while leasing authoritative game workers from a local pool. Evaluation and video need one
visible worker; large-scale RL requires a pool of licensed installations or a separately validated
fast simulator. The actual game remains the gold-standard evaluator.

See [`docs/architecture.md`](docs/architecture.md) for the trust boundary, hidden-information
policy, replay contract, process supervision, and training topology. See [`LEGAL.md`](LEGAL.md) for
the project's clean-room and user-owned-copy requirements.
