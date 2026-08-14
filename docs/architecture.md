# Architecture and benchmark contract

## Trust boundary

Slay the Spire and CommunicationMod are authoritative for state, legal command capabilities,
seed conversion, score, and victory. The model cannot emit an engine command. It emits only
`ACTION <index>`; `sts-bench` resolves that index against the legal list derived from the current
stable game state.

The controller owns model credentials, prompts, retry policy, artifacts, and aggregation. The
bridge owns no game logic. It authenticates to the controller, forwards newline-delimited JSON,
and places only controller-provided single-line commands on stdout.

## Decision loop

1. CommunicationMod waits until the game is stable.
2. It writes an envelope containing `available_commands` and `game_state`.
3. The bridge forwards the envelope to the controller.
4. The controller removes non-semantic UUIDs, hides draw-pile order, and enumerates exact actions.
5. The model chooses an index. Invalid responses retry without advancing the game.
6. The controller sends the corresponding CommunicationMod command.
7. The next stable envelope is normalized, hashed, and recorded.

No state-changing `CLICK` or `KEY` command is exposed for model selection. The benchmark uses
semantic `PLAY`, `END`, `POTION`, `CHOOSE`, `PROCEED`, and `RETURN` commands. When exactly one
engine-maintenance transition is possible, the controller applies it without calling the model or
charging its decision/token budget: `WAIT` advances animations and resolves the game's transient
`DEBUG` monster-intent frame and master-deck mutations from rewards, shops, events, and card grids;
an observer-assisted `KEY CONFIRM` dismisses first-run tutorials through their native **Got It**
button, and
`KEY CANCEL` closes a Settings overlay opened by loss of window focus. Each remains fully recorded
in the trajectory with `automatic=true` so replay sees the exact same command sequence.

## Reproducibility identity

A benchmark build is identified by:

- `sts-bench` and text-protocol versions;
- frozen seed-set version;
- character and ascension;
- Slay the Spire version;
- ModTheSpire, BaseMod, and CommunicationMod versions;
- retry budget and maximum decisions;
- model endpoint and sampling configuration.

The run manifest stores the requested display seed and the numeric seed reported by the game. A
trajectory stores every model-visible state, its SHA-256 hash, all raw model attempts, the selected
action, the exact engine command, and the resulting state hash.

## Hidden information

CommunicationMod includes the draw pile as an ordered Java collection. That is more information
than the game UI gives a player. `sts-bench` converts draw, discard, exhaust, and master-deck
collections into sorted multisets for the model. Hand, enemy, potion-slot, screen-option, and
choice-list order is preserved because those indices are visible and actionable.

Card UUIDs are process-random and removed everywhere from the normalized state. Action selection
uses current hand/choice indices, matching CommunicationMod itself.

The optional Sts Bench Observer companion patch enriches Communication Mod's card JSON with the
live card's rules text and current numeric fields. It reads those fields from the authoritative
`AbstractCard` after Communication Mod serializes it, and stamps the observer version into every
card object so manifests identify the exact observation build. The controller retains live numeric
values for actionable hand cards; cards in the deck and non-actionable piles/screens use their
stable base values because `AbstractCard` leaves transient render calculations cached after a card
moves between zones. Powers are retained for every live or half-dead monster, but discarded once
the engine marks a monster `is_gone`; their later animation-driven cleanup has no gameplay effect
and otherwise creates a sampling race. It also translates the benchmark's
maintenance-only `KEY CONFIRM` command into the tutorial's native completed-click state because
Communication Mod's keyboard injection cannot close that overlay. It owns no content table and
performs no transition or reward logic.

Tutorial flags, content unlocks, and boss-seen flags are mutable Steam-profile state, not seed
state. The observer disables all first-time tutorials, fully unlocks content, and marks the nine
seeded bosses as seen before every `START`. Without that normalization, the same numeric seed can
have a different card/relic pool or can be forced onto the next unseen boss after an earlier run
updates the profile. The `KEY CONFIRM` maintenance hook remains as a guarded fallback for an
overlay opened by another mod or a future game change. Workers must use a dedicated profile because
the unlock normalization is intentionally persistent.

## Training deployment

For evaluation and video, a controller and one visible worker are enough. For GRPO, run a pool of
licensed game installations and attach them to local `verifiers` tasks. Each task is still the same
reset/observe/act loop. A future scheduler can lease workers without changing the public state,
action, trajectory, or outcome schemas.
