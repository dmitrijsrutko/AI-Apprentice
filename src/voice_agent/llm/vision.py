"""The eyes' model: one screen frame in, what is on it and what changed out.

Not an `LLM`: that protocol streams text from text, and a frame is neither. One
call per frame, drained rather than streamed, since nobody is waiting on its
first token: the conversation reads the result once it is whole.

Only Claude is offered: DeepSeek has no vision. The image never leaves this
module except in the request itself; nothing here keeps it.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import AnthropicError, AsyncAnthropic, DefaultAsyncHttpxClient
from json_repair import repair_json

from voice_agent import prompts, timing
from voice_agent.config import require_env
from voice_agent.errors import ProviderError
from voice_agent.llm.anthropic_provider import ANTHROPIC_API_KEY
from voice_agent.llm.http import http_client

MAX_OUTPUT_TOKENS = 400
"""A short screen summary and a few events. Output is most of a reading's time
(Haiku, live: 230-376 tokens took 2.0-4.7 s), so the prompt asks for 60 words
and this stops a reading that ignores it, before it costs the conversation."""


@dataclass(frozen=True, slots=True)
class Option:
    """One choice of eyes on the start screen."""

    name: str
    model: str
    title: str
    hint: str
    effort: str | None = None
    """Sent as `output_config.effort` when set. Sonnet thinks by default, and
    thinking before describing a screen is time the conversation waits for."""


MENU: tuple[Option, ...] = (
    Option("haiku-4-5", "claude-haiku-4-5", "Haiku 4.5", "fastest"),
    Option("sonnet-5-5", "claude-sonnet-5-5", "Sonnet 5.5", "sharper", effort="low"),
)
"""Fastest first: the first is the default."""

BY_NAME = {option.name: option for option in MENU}


@dataclass(frozen=True, slots=True)
class Seen:
    """What one frame showed. `screen` is the whole picture now; `events` only
    what changed since the previous frame."""

    screen: str
    app: str
    events: tuple[str, ...]
    doing: str
    ms: int
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class Vision(Protocol):
    model: str

    async def look(self, jpeg: bytes, previous: str) -> Seen:
        """Describe `jpeg` against `previous`, the last `Seen.screen` (empty
        when sharing has just started). Raises `ProviderError`."""
        ...


def offered() -> tuple[Option, ...]:
    """The eyes this deployment can run: all of them with a Claude key, none without."""
    return MENU if require_env_or_none(ANTHROPIC_API_KEY) else ()


def require_env_or_none(name: str) -> str | None:
    try:
        return require_env(name)
    except Exception:
        return None


def parse(text: str, ms: int, model: str) -> Seen:
    """The model's JSON as a `Seen`, tolerating a slip; never raises.

    A reply that is not JSON at all becomes a screen description of its own
    words, so a bad call still says something rather than nothing."""
    body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        raw: Any = json.loads(body)
    except ValueError:
        raw = repair_json(body, return_objects=True, skip_json_loads=True)
    if not isinstance(raw, dict):
        return Seen(screen=text.strip()[:600], app="", events=(), doing="", ms=ms, model=model)
    events = raw.get("events")
    listed = events if isinstance(events, list) else []
    return Seen(
        screen=str(raw.get("screen") or "").strip(),
        app=str(raw.get("app") or "").strip(),
        events=tuple(str(event).strip() for event in listed if str(event).strip())[:8],
        doing=str(raw.get("doing") or "").strip(),
        ms=ms,
        model=model,
    )


class AnthropicVision:
    def __init__(self, option: Option, client: AsyncAnthropic | None = None) -> None:
        self.provider = "anthropic"
        self.model = option.model
        self._effort = option.effort
        self._client = client or AsyncAnthropic(
            api_key=require_env(ANTHROPIC_API_KEY),
            http_client=http_client(DefaultAsyncHttpxClient),
        )

    async def look(self, jpeg: bytes, previous: str) -> Seen:
        started = timing.now()
        before = previous.strip() or "nothing: sharing has just started, this is the first frame"
        extra: dict[str, Any] = {}
        if self._effort is not None:
            extra["output_config"] = {"effort": self._effort}
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=prompts.load("vision"),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": base64.b64encode(jpeg).decode("ascii"),
                                },
                            },
                            {"type": "text", "text": f"The previous screen was: {before}"},
                        ],
                    }
                ],
                **extra,
            )
        except AnthropicError as exc:
            raise ProviderError(f"vision request failed: {exc}") from exc
        text = "".join(block.text for block in message.content if block.type == "text")
        seen = parse(text, round((timing.now() - started) * 1000), self.model)
        return Seen(
            screen=seen.screen,
            app=seen.app,
            events=seen.events,
            doing=seen.doing,
            ms=seen.ms,
            model=seen.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )


def create_vision(name: str) -> Vision:
    return AnthropicVision(BY_NAME[name])
