# Live acceptance report — 2026-08-12 through 2026-08-14

This report records the first end-to-end smoke test and subsequent terminal determinism proof
against a user-owned Steam installation on macOS.

## Pinned stack

- Slay the Spire: `12-18-2022`, Steam build `10180494`
- ModTheSpire: `3.30.3`
- BaseMod: `5.56.0`
- Communication Mod: `1.2.1`
- Sts Bench Observer: `0.5.0`
- sts-bench bridge: `0.1.0`
- Smoke seed: `STSBENCHSMOKE1` (`7099436505620676093` in the game)
- Acceptance seed: `STSBENCHV1000` (`1783990535053581670` in the game)
- Character/ascension: Ironclad, A0
- Dedicated worker profile: `rl` (slot 2)

## Verified live

- Steam Workshop discovery and initialization of ModTheSpire, BaseMod, and Communication Mod.
- Communication Mod launching the configured `sts-bench bridge` subprocess.
- Authenticated JSONL/TCP connection from the bridge to the controller.
- Exact seeded `START`, map/event choices, card rewards, card plays, end turns, combat rewards,
  potion handling, and subsequent map navigation.
- A scripted first-legal policy cleared multiple combats and reached floor 4 while writing stable
  manifests, prompts, state hashes, exact commands, trajectories, and transcripts.
- The run was deliberately stopped by the operator so the computer could return to normal use.
- A later recorded policy reached a real score screen: defeat on floor 24, score 204, one elite,
  one boss, and Act I cleared.
- Its finalized reference contains 441 policy decisions and 48 recorded maintenance transitions:
  38 master-deck settlement waits and 10 monster-intent initialization waits.
- `verify-determinism` replayed all 489 player-visible state hashes twice in the real game. The same
  seed and exact commands produced identical trajectories on both passes.

## Issues discovered and fixed

Fresh profiles present `FTUE` tutorial screens, unseen-boss sequencing and content locks depend on
profile history, and a windowed macOS game can present `SETTINGS` after focus changes. The observer
normalizes tutorials, unlock pools, and seeded bosses in a dedicated worker profile; the controller
still has guarded overlay recovery. Live replay also exposed frame-timed monster intent, dead-power
cleanup, card render caches, mutable progress aliases, and master-deck settlement. The controller
now canonicalizes non-actionable residue and records explicit waits at actionable timing
boundaries. These transitions consume no model response, token, or decision budget.

## Terminal acceptance

The terminal determinism gate passed on 2026-08-14 with the pinned stack above. New engine/mod
tuples and observation-schema changes still require their own terminal two-pass proof before their
results are mixed into a published leaderboard.

## First model benchmark

After the gate passed, authenticated Codex CLI `gpt-5.6-terra` at low reasoning played
`STSBENCHV1000` through the finalized controller. This is a provisional local-backend result, not a
direct-API leaderboard claim:

- defeat on floor 24, score 225, with Act I, one boss, and one elite cleared;
- 335 model decisions plus 59 audited maintenance transitions (394 unique state hashes);
- zero retries, parse errors, illegal actions, or forced defaults; and
- 6,760,936 input tokens and 65,901 output/reasoning tokens under Codex CLI accounting.

The generated local leaderboard reports one run, 0% win rate, average floor 24, average score 225,
and 0% illegal-action rate. Run artifacts remain ignored by Git because they contain full prompts
and model responses.
