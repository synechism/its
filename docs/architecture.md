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

No state-changing `CLICK` or `KEY` command is exposed to the model. The benchmark uses semantic
`PLAY`, `END`, `POTION`, `CHOOSE`, `PROCEED`, and `RETURN` commands. `WAIT` appears only when the
game exposes no semantic choice and needs animation frames to advance.

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

## Training deployment

For evaluation and video, a controller and one visible worker are enough. For GRPO, run a pool of
licensed game installations and attach them to local `verifiers` tasks. Each task is still the same
reset/observe/act loop. A future scheduler can lease workers without changing the public state,
action, trajectory, or outcome schemas.
