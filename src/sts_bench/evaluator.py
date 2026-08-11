from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.game import LiveGame
from sts_bench.models import ModelReply, Outcome
from sts_bench.text_protocol import SYSTEM_PROMPT


@dataclass(frozen=True, slots=True)
class ModelConfig:
    model: str
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 768
    reasoning_effort: str | None = None
    history_turns: int = 2


class OpenAICompatibleModel:
    def __init__(self, config: ModelConfig) -> None:
        api_key = config.api_key or os.environ.get("OPENAI_API_KEY") or "not-needed"
        self.config = config
        self.client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)
        self.messages: list[dict[str, str]] = []
        self.reset_conversation()

    def reset_conversation(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    async def respond(self, prompt: str, _decision: int, _attempt: int) -> ModelReply:
        self.messages.append({"role": "user", "content": prompt})
        history_messages = max(0, self.config.history_turns) * 2
        request_messages = [self.messages[0]]
        if history_messages:
            request_messages.extend(self.messages[1:][-history_messages - 1 :])
        else:
            request_messages.append(self.messages[-1])
        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": request_messages,
            "temperature": self.config.temperature,
            "max_completion_tokens": self.config.max_tokens,
        }
        if self.config.reasoning_effort:
            request["reasoning_effort"] = self.config.reasoning_effort
        response = await self.client.chat.completions.create(**request)
        choice = response.choices[0]
        content = choice.message.content or ""
        self.messages.append({"role": "assistant", "content": content})
        usage = response.usage
        return ModelReply(
            content=content,
            tokens_in=int(usage.prompt_tokens if usage else 0),
            tokens_out=int(usage.completion_tokens if usage else 0),
            terminated=choice.finish_reason == "content_filter",
        )


async def evaluate_model(
    model_config: ModelConfig,
    seeds: list[str],
    *,
    game: LiveGame,
    character: str = "Ironclad",
    ascension: int = 0,
    max_decisions: int = 1_200,
    retry_budget: int = 2,
    runs_dir: Path = Path("runs"),
    benchmark_version: str = "v1",
) -> list[Outcome]:
    model = OpenAICompatibleModel(model_config)
    outcomes: list[Outcome] = []
    for index, seed in enumerate(seeds):
        model.reset_conversation()
        outcome = await play_episode(
            EpisodeConfig(
                seed=seed,
                model=model_config.model,
                character=character,
                ascension=ascension,
                max_decisions=max_decisions,
                retry_budget=retry_budget,
                runs_dir=runs_dir,
                benchmark_version=benchmark_version,
                model_config={
                    "base_url": model_config.base_url,
                    "temperature": model_config.temperature,
                    "max_tokens": model_config.max_tokens,
                    "reasoning_effort": model_config.reasoning_effort,
                    "history_turns": model_config.history_turns,
                },
            ),
            model.respond,
            game=game,
        )
        outcomes.append(outcome)
        if index < len(seeds) - 1:
            if not game.state.terminal:
                raise RuntimeError(
                    "a truncated run cannot be abandoned safely through CommunicationMod; "
                    "return the game to its main menu and resume with the remaining seeds"
                )
            game.return_to_menu()
    return outcomes


class FirstLegalPolicy:
    """Dependency-free scripted policy for wiring and replay smoke tests."""

    async def respond(self, prompt: str, _decision: int, _attempt: int) -> ModelReply:
        in_actions = False
        for line in prompt.splitlines():
            if line == "LEGAL ACTIONS":
                in_actions = True
                continue
            if in_actions:
                stripped = line.strip()
                if stripped.startswith("[") and "]" in stripped:
                    index = stripped[1 : stripped.index("]")]
                    if index.isdigit():
                        return ModelReply(f"ACTION {index}")
        return ModelReply("ACTION 0")
