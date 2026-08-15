# sts-bench

`sts-bench` is a reproducible LLM benchmark and local RL environment for **the real Slay the
Spire 1 game**. Models receive a stable text observation, choose from a game-authoritative action
list, and produce trajectories that can be replayed in the visible game and recorded as video.

The game—not an LLM judge and not a reimplementation—owns every transition, seed, victory, and
score. `sts-bench` does not include Slay the Spire, its assets, or any mod binaries.

> This benchmark measures long-horizon decision-making in one specific game. It is not a claim
> about general reasoning ability.

## What works

- Full runs: combat, map choices, rewards, events, shops, rest sites, potions, and bosses.
- Exact `START <character> <ascension> <seed>` control through CommunicationMod.
- Stable model observations with random card UUIDs removed and draw-pile order hidden.
- Explicit, validated `ACTION <index>` responses with retries and logged forced defaults.
- Automatic tutorial, animation, and focus-overlay recovery that does not consume model budget.
- Game-over-derived win/loss and score; no model-authored or rubric reward.
- Per-run manifests, JSONL trajectories, outcomes, and readable transcripts.
- Real-game replay, two-pass determinism checking, and ffmpeg-compatible video capture.
- Frozen 100-seed v1 set and JSON/Markdown leaderboard aggregation.
- An optional local `verifiers` adapter for future self-hosted GRPO work.

## Architecture

```text
model / verifiers
       │ text state + indexed action
       ▼
sts-bench controller ── authenticated JSONL/TCP ── bridge subprocess
                                                     │ stdin/stdout
                                                     ▼
                                          CommunicationMod + BaseMod
                                                     │
                                                     ▼
                                      visible Slay the Spire 1 game
```

CommunicationMod launches the bridge as a child process. The bridge is deliberately dumb: it
forwards game JSON to the controller and returns one validated engine command. This keeps model
credentials and training code outside the game process and lets several user-owned game workers
serve a later rollout pool.

See [docs/architecture.md](docs/architecture.md) for the trust boundary and replay contract.

## Install

You need:

1. A user-owned PC copy of Slay the Spire 1.
2. [ModTheSpire](https://github.com/kiooeht/ModTheSpire).
3. [BaseMod](https://github.com/daviscook477/BaseMod).
4. [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod).
5. Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:synechism/its.git
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

## First real-game smoke test

At the game's main menu, run:

```bash
uv run sts-bench smoke --seed STSBENCHSMOKE1 --runs-dir runs
```

When it prints `Listening`, use CommunicationMod's **Start external process** button. A simple
first-legal policy will play one visible run and write its artifacts under `runs/`. It is only a
wiring test, not a competent policy.

On macOS, Slay the Spire may open its Settings overlay after losing focus. The controller closes
that overlay automatically when CommunicationMod reports it, and likewise dismisses first-profile
tutorial popups. It also waits through the transient `DEBUG` monster intent that the game can emit
before a combat's real intent is initialized and through master-deck settlement after rewards,
shops, events, and card grids. These engine-maintenance transitions are replayed and audited but do
not count as model decisions or API calls.

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

For another provider, add `--base-url` and, if needed, `--api-key`. Every model should play the
same seed-set version, character, ascension, retry budget, and max-decision limit. Full v1 is 100
seeds; use a small `--limit` only for development.

`eval` requires the Sts Bench Observer fields by default and checks them immediately after the
seeded reset, before making the first model API call. Use `--no-require-observer` only for wiring
experiments whose results will not enter the benchmark leaderboard.

For a provisional local run without an API key, an authenticated Codex installation can be used:

```bash
uv run sts-bench eval --backend codex-cli --model gpt-5.6-terra \
  --reasoning-effort low --limit 1
```

Each decision uses an ephemeral, read-only Codex process in an empty temporary directory. Results
are labeled `codex-cli/<model>` because the Codex agent has additional system context; do not rank
them as direct-API model results in a canonical leaderboard.

For unattended runs, use the supervisor instead of `eval` directly:

```bash
uv run sts-bench overnight \
  --backend codex-cli \
  --model gpt-5.6-terra \
  --reasoning-effort low \
  --seed-set v1 \
  --runs-dir runs/benchmark-v1
```

On macOS with the conventional Steam Workshop installation, `overnight` finds the game,
ModTheSpire, and CommunicationMod config automatically. It enables bridge autostart only while it
is running, launches a fresh game process for each seed, applies a four-hour per-episode timeout,
retries crashes up to three times, prevents system sleep, and restores the exact original config
on exit. A rerun resumes only finalized artifacts matching the exact model, character, ascension,
and seed-set version. Durable progress and per-attempt logs live in
`<runs-dir>/overnight-status.json` and `<runs-dir>/_overnight/`.

Use `--dry-run` to inspect what would be skipped or launched. On another platform or a nonstandard
install, provide `--game-command`, `--game-cwd`, and `--communication-config` explicitly. API keys
and bridge tokens inherited from the environment never enter the status file; explicit secret
arguments are redacted there.

Build leaderboard artifacts afterward:

```bash
uv run sts-bench aggregate --runs-dir runs
```

This creates `leaderboard.json` with raw per-seed outcomes and `leaderboard.md` with the compact
ranking. Ranking uses win rate, average floor, and illegal-action rate, while score and act clears
remain visible sub-metrics.

## Replay and record a model playing

The macOS one-command demo path launches the game, records the main display, checks every replayed
state, and burns the model's selected action plus current act, floor, and HP into the output:

```bash
uv run sts-bench replay runs/<run-id> \
  --launch-game \
  --record-display 1 \
  --step-delay 0.15 \
  --video-output demo.mp4
```

Grant the terminal or host app macOS **Screen & System Audio Recording** permission and relaunch it
before recording. This path uses the system `screencapture` utility and requires `ffmpeg`; the
project's Pillow dependency renders the timestamped action card before FFmpeg composites a broadly
playable H.264 MP4. The raw `.mov`, recorder log, and `.srt` remain beside the final video for
debugging and re-editing. A `.replay.json` sidecar records the hash-verification result before
presentation rendering begins, so a codec failure cannot discard the authoritative replay result.

The replay checks the recorded state hash before and after every command. Without `--launch-game`,
start the bridge from CommunicationMod's mod panel after the listener appears. For another capture
backend, supply a recorder command containing `{output}`:

```bash
uv run sts-bench replay runs/<run-id> \
  --recorder-command 'ffmpeg -y -f avfoundation -framerate 30 -i <screen-device>:none -c:v libx264 -pix_fmt yuv420p {output}' \
  --video-output demo.mp4
```

On Linux, use an ffmpeg `x11grab`/PipeWire input; on Windows, use `gdigrab` or an OBS command-line
workflow. The recorder is started without a shell, kept alive with an open input pipe, and stopped
with `q` (then `SIGINT` as a fallback) so the container is finalized cleanly.

## Determinism proof

Once a recorded trajectory reaches a score screen:

```bash
uv run sts-bench verify-determinism runs/<run-id>
```

This replays the same seed and engine-command sequence twice in the actual game and compares every
player-visible state hash. UUIDs are excluded because Slay the Spire creates card UUIDs from a
process-random source; UUIDs never select actions. Draw-pile contents are represented as a multiset
because CommunicationMod exposes the internal order even though the human UI does not.

Pin and report the Slay the Spire, BaseMod, ModTheSpire, and CommunicationMod versions for any
published leaderboard. A different engine/mod tuple is a different benchmark build.

## Local `verifiers` / GRPO path

Hub publication is optional. Install the adapter locally:

```bash
uv sync --extra training
```

[`sts_bench.environment`](src/sts_bench/environment.py) composes a `verifiers.v1` Taskset and
Harness around a concurrent worker pool. Start the environment, then point any number of bridge
processes at its single `worker_host:worker_port`; concurrent rollouts lease and reuse licensed game
instances. The commercial game is never placed in an environment package or generic hosted worker.
The scalar training reward is deliberately sparse win/loss; the raw outcome also carries floor,
elites, bosses, acts, and score so reward shaping can be introduced later without changing
benchmark grading.

The environment config in [`configs/eval/sts-bench.toml`](configs/eval/sts-bench.toml) can be
loaded with `StsBenchEnvConfig.from_toml(...)`. Model and trainer sampling settings remain owned by
the verifiers/prime-rl runner. For GRPO group size `G`, attach at least `G` game workers to avoid
rollouts waiting for a lease.

## Known limitations

- A live integration test cannot run without a user-owned game installation; CI tests the bridge,
  normalization, action authority, hidden-information policy, artifacts, and deterministic hashes.
- Upstream CommunicationMod lists edge cases around Match and Keep, full potion inventories, and
  certain manual interactions. Benchmark runs should not accept manual input.
- Published runs should enable the source-only Sts Bench Observer companion mod; standard
  Communication Mod omits card rules text and live damage/block/magic values. Runs without it are
  useful for transport testing but do not meet the benchmark's observation-completeness bar.
- `overnight` supervises one visible local worker at a time. The `verifiers.v1` adapter has a
  concurrent worker pool, but provisioning and supervising multiple graphical game processes is
  still a self-hosted operational responsibility.

See [LEGAL.md](LEGAL.md) before publishing results or distributing modifications.

## Development

```bash
uv run ruff check .
uv run pytest
uv build
```

The most important non-live test verifies that random UUIDs and hidden draw order cannot alter a
model-visible hash. The authoritative live acceptance test is `verify-determinism` on a terminal
run with a pinned engine/mod tuple.
