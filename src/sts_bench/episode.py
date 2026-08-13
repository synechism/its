from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sts_bench.artifacts import RunArtifacts
from sts_bench.game import GameBackend
from sts_bench.models import ModelReply, Outcome
from sts_bench.text_protocol import (
    PROTOCOL_VERSION,
    ActionParseError,
    parse_action,
    retry_prompt,
    safe_default,
    serialize_state,
)

Responder = Callable[[str, int, int], Awaitable[ModelReply]]


@dataclass(frozen=True, slots=True)
class EpisodeConfig:
    seed: str
    model: str
    character: str = "Ironclad"
    ascension: int = 0
    max_decisions: int = 1_200
    retry_budget: int = 2
    runs_dir: Path | None = None
    benchmark_version: str = "v1"
    model_config: dict[str, Any] = dataclasses.field(default_factory=dict)
    require_observer: bool = False


async def play_episode(config: EpisodeConfig, respond: Responder, *, game: GameBackend) -> Outcome:
    if config.max_decisions < 1:
        raise ValueError("max_decisions must be positive")
    if config.retry_budget < 0:
        raise ValueError("retry_budget cannot be negative")

    artifacts: RunArtifacts | None = None
    tokens_in = tokens_out = illegal_count = forced_count = response_count = 0
    agent_terminated = False
    initial_engine = getattr(game, "engine", {})
    if (
        config.require_observer
        and isinstance(initial_engine, dict)
        and not initial_engine.get("observer_version")
    ):
        raise RuntimeError(
            "worker observations do not include Sts Bench Observer card fields; "
            "install/enable the companion mod before running model evaluations"
        )
    state = game.reset(config.seed, config.character, config.ascension)
    if config.require_observer and not state.engine.get("observer_version"):
        raise RuntimeError(
            "worker observations do not include Sts Bench Observer card fields; "
            "install/enable the companion mod before running model evaluations"
        )

    if config.runs_dir is not None:
        artifacts = RunArtifacts.create(
            config.runs_dir,
            model=config.model,
            seed=config.seed,
            character=config.character,
            ascension=config.ascension,
            manifest={
                "schema_version": 1,
                "model": config.model,
                "seed": config.seed,
                "actual_seed": state.actual_seed,
                "character": config.character,
                "ascension": config.ascension,
                "max_decisions": config.max_decisions,
                "retry_budget": config.retry_budget,
                "benchmark_version": config.benchmark_version,
                "protocol_version": PROTOCOL_VERSION,
                "engine": state.engine,
                "model_config": config.model_config,
                "require_observer": config.require_observer,
            },
        )

    try:
        while not state.terminal and state.decisions < config.max_decisions:
            before = state
            prompt = serialize_state(before)
            current_prompt = prompt
            responses: list[str] = []
            errors: list[str] = []
            action = None
            model_chose_legal = False

            automatic_actions = {
                "dismiss_overlay",
                "dismiss_tutorial",
                "wait",
            }
            if (
                len(before.legal_actions) == 1
                and before.legal_actions[0].kind in automatic_actions
            ):
                action = before.legal_actions[0]

            if action is None:
                for attempt in range(config.retry_budget + 1):
                    reply = await respond(current_prompt, before.decisions, attempt)
                    response_count += 1
                    tokens_in += reply.tokens_in
                    tokens_out += reply.tokens_out
                    responses.append(reply.content)
                    if reply.terminated:
                        agent_terminated = True
                        errors.append("model interaction terminated")
                        break
                    try:
                        action = parse_action(reply.content, before.legal_actions)
                        model_chose_legal = True
                        break
                    except ActionParseError as error:
                        illegal_count += 1
                        errors.append(str(error))
                        if attempt < config.retry_budget:
                            current_prompt = retry_prompt(str(error), before)

            if action is None:
                action = safe_default(before.legal_actions)
                forced_count += 1

            is_automatic = action.kind in automatic_actions
            state = game.step(action, count_decision=not is_automatic)
            if artifacts is not None:
                row = {
                    "decision": before.decisions,
                    "state_hash": before.stable_hash(),
                    "state": before.canonical_dict(),
                    "prompt": prompt,
                    "raw_response": responses[-1] if responses else "",
                    "attempt_responses": responses,
                    "parse_errors": errors,
                    "action": action.to_dict(),
                    "engine_command": action.command,
                    "automatic": is_automatic,
                    "legal": model_chose_legal,
                    "retries": max(0, len(responses) - 1),
                    "forced_default": not is_automatic and not model_chose_legal,
                    "resulting_state_hash": state.stable_hash(),
                    "resulting_outcome_delta": {
                        "hp": state.hp - before.hp,
                        "floor": state.floor_reached - before.floor_reached,
                        "terminal_status": state.status if state.terminal else None,
                    },
                }
                transcript = (
                    f"DECISION {before.decisions}\n{prompt}\n\n"
                    f"MODEL\n{responses[-1] if responses else '<automatic>'}\n\n"
                    f"APPLIED ACTION {action.index}: {action.label}\n"
                    f"ENGINE COMMAND {action.command}"
                    + (
                        " (automatic)"
                        if is_automatic
                        else " (forced default)" if not model_chose_legal else ""
                    )
                )
                artifacts.record(row, transcript)

            if agent_terminated:
                break
    except BaseException as error:
        if artifacts is not None:
            artifacts.mark_interrupted(error)
        raise

    outcome = game.outcome(state, model=config.model)
    outcome.tokens_in = tokens_in
    outcome.tokens_out = tokens_out
    outcome.illegal_action_count = illegal_count
    outcome.forced_default_count = forced_count
    outcome.response_count = response_count
    if agent_terminated:
        outcome.termination_reason = "model_terminated"
    elif not state.terminal:
        outcome.termination_reason = "max_decisions"
    outcome.metadata.update({"benchmark_version": config.benchmark_version})
    if artifacts is not None:
        artifacts.finalize(outcome)
    return outcome
