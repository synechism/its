# Live smoke report — 2026-08-12

This report records the first end-to-end test against a user-owned Steam installation on macOS.
It is acceptance evidence for the transport and decision loop, not yet the terminal determinism
proof required for a published benchmark build.

## Pinned stack

- Slay the Spire: `12-18-2022`, Steam build `10180494`
- ModTheSpire: `3.30.3`
- BaseMod: `5.56.0`
- Communication Mod: `1.2.1`
- sts-bench bridge: `0.1.0`
- Requested seed: `STSBENCHSMOKE1`
- Numeric seed reported by the game: `7099436505620676093`

## Verified live

- Steam Workshop discovery and initialization of ModTheSpire, BaseMod, and Communication Mod.
- Communication Mod launching the configured `sts-bench bridge` subprocess.
- Authenticated JSONL/TCP connection from the bridge to the controller.
- Exact seeded `START`, map/event choices, card rewards, card plays, end turns, combat rewards,
  potion handling, and subsequent map navigation.
- A scripted first-legal policy cleared multiple combats and reached floor 4 while writing stable
  manifests, prompts, state hashes, exact commands, trajectories, and transcripts.
- The run was deliberately stopped by the operator so the computer could return to normal use.

## Issues discovered and fixed

Fresh profiles present `FTUE` tutorial screens, and a windowed macOS game can present `SETTINGS`
after focus changes. Communication Mod correctly exposes `key` and `wait`, but neither overlay is a
meaningful model decision. The controller now recognizes a sole maintenance action and executes it
automatically. These transitions stay in replay logs while consuming no model response, token, or
decision budget. Regression tests cover both overlays.

## Remaining live acceptance

Before publishing results, complete a terminal run and run `sts-bench verify-determinism` on it.
That must reproduce every model-visible state hash twice with this exact engine/mod tuple. A real
model evaluation can begin before publication, but its results should remain provisional until
that proof passes.
