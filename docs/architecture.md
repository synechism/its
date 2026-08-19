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
button, and `KEY CANCEL` closes a Settings overlay opened by loss of window focus. Each remains
fully recorded
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

## Local process supervision

The unattended supervisor treats each seed as an isolated process attempt. It starts a fresh
controller, waits for the listener-ready signal, launches a fresh modded game process, enforces an
episode deadline, and terminates the whole game process group before the next attempt. Finalized
`manifest.json` plus `outcome.json` pairs are the resume authority; the mutable status file is only
an operational view. Resume matching includes model identity, character, ascension, and seed-set
version so an artifact from a different benchmark configuration cannot silently skip work.

CommunicationMod's `runAtGameStart` property is changed atomically around the supervised session.
The original config bytes and file mode are restored on normal completion, failure, or interrupt.
Per-attempt controller and game logs, retry history, current seed, and finalized run path are
written atomically to the status tree. Model credentials remain in the controller environment;
explicit secret command arguments are redacted from durable status.

The `overnight --detach` mode re-executes the same parsed command in a new process session and
writes its PID, redacted command, log path, and status path under the run directory. This keeps the
supervisor alive when the invoking terminal or desktop client closes. It does not daemonize the
controller or game independently: the detached supervisor remains their owner and tears down their
process groups together.

## Visible replay and video

Replay reissues the recorded engine-command sequence against a newly seeded real game and verifies
the state hash on both sides of every transition. Video capture is observational: recorder timing
callbacks never enter the normalized game state or action loop. The generated SRT timeline records
the wall-clock start of each verified action. Pillow renders transparent caption cards, and FFmpeg
composites those cards over the system capture after the authoritative replay has finished. The raw
capture and SRT remain available independently of the presentation render.

Presentation speed is applied only during the final FFmpeg render. The real-game replay, hash
verification, raw capture, and action timestamps are produced at normal execution speed; caption
durations and video timestamps are scaled together afterward.

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
moves between zones. Powers and move/intent fields are retained for every live or half-dead
monster, but discarded once the engine marks a monster `is_gone`; their later animation-driven
cleanup has no gameplay effect and otherwise creates a sampling race. Legacy trajectories are
replayed through the same semantic normalizer so old hashes remain verifiable without treating
dead-monster render residue as game state. The same rule removes only The Library's randomized
post-choice book-summary flavor after its sole remaining action is already `Leave`; initial event
text, option text, rewards, and every other event field remain hashed. It also omits the personal
and Steam-global lifetime damage counters from the victorious Heart epilogue once `Sleep` is the
only action. Those counters are profile/network statistics, not seeded-run state. The observer also
translates the benchmark's maintenance-only `KEY CONFIRM` command into the tutorial's native
completed-click state because Communication Mod's keyboard injection cannot close that overlay. It
owns no content table and performs no transition or reward logic.

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
