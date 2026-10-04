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
from typing import Any, Literal, Protocol

from anthropic import AnthropicError, AsyncAnthropic, DefaultAsyncHttpxClient
from anthropic.types import ImageBlockParam, TextBlockParam
from json_repair import repair_json

from voice_agent import prompts, timing
from voice_agent.config import require_env
from voice_agent.errors import ProviderError
from voice_agent.llm.anthropic_provider import ANTHROPIC_API_KEY
from voice_agent.llm.http import http_client

MAX_OUTPUT_TOKENS = 400
"""A short screen summary and a few events, for a model that does not think
first (Haiku). Output is most of a reading's time (Haiku, live: 230-376
tokens took 2.0-4.7 s), so the prompt asks for 60 words and this stops a
reading that ignores it, before it costs the conversation."""


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
    max_tokens: int = MAX_OUTPUT_TOKENS
    """Thinking counts against it: a model that thinks first needs room for
    that as well as the JSON, or the reading is cut off mid-object."""


MENU: tuple[Option, ...] = (
    Option(
        "sonnet-5-5", "claude-sonnet-5-5", "Sonnet 5.5", "balanced", effort="low", max_tokens=1200
    ),
    Option("haiku-4-5", "claude-haiku-4-5", "Haiku 4.5", "legacy, fastest"),
)
"""The first is the default. Sonnet, because a shared window shrunk to 1024 px
leaves a date about 7 px tall: on the frames of a live session Haiku read
"Fri, 16 Oct" as 10 and 18, Sonnet as 16 every time, and no slower."""

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

    async def look(self, jpeg: bytes, previous: str, crop: bytes | None = None) -> Seen:
        """Describe `jpeg` against `previous`, the last `Seen.screen` (empty
        when sharing has just started). `crop`: the area that changed, at the
        screen's own resolution, to read values from. Raises `ProviderError`."""
        ...


def offered() -> tuple[Option, ...]:
    """The eyes this deployment can run: all of them with a Claude key, none without."""
    return MENU if require_env_or_none(ANTHROPIC_API_KEY) else ()


def require_env_or_none(name: str) -> str | None:
    try:
        return require_env(name)
    except Exception:
        return None


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

CLOSE_UP = (
    "The second image is a close-up, at the screen's full resolution, of the area that "
    "changed: read every value in that area from it."
)


def image(data: bytes) -> ImageBlockParam:
    """One image block. The page sends the whole screen as JPEG and a close-up
    as PNG: lossless, because JPEG smudges the edges of small digits."""
    kind: Literal["image/png", "image/jpeg"] = (
        "image/png" if data.startswith(PNG_SIGNATURE) else "image/jpeg"
    )
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": kind, "data": base64.b64encode(data).decode()},
    }


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
        self._max_tokens = option.max_tokens
        self._client = client or AsyncAnthropic(
            api_key=require_env(ANTHROPIC_API_KEY),
            http_client=http_client(DefaultAsyncHttpxClient),
        )

    async def look(self, jpeg: bytes, previous: str, crop: bytes | None = None) -> Seen:
        started = timing.now()
        before = (
            f"{previous.strip()} (it may contain misreadings — read every value again "
            "from the image)"
            if previous.strip()
            else "nothing: sharing has just started, this is the first frame"
        )
        content: list[ImageBlockParam | TextBlockParam] = [image(jpeg)]
        if crop:
            content += [image(crop), {"type": "text", "text": CLOSE_UP}]
        content.append({"type": "text", "text": f"The previous screen was: {before}"})
        extra: dict[str, Any] = {}
        if self._effort is not None:
            extra["output_config"] = {"effort": self._effort}
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                system=prompts.load("vision"),
                messages=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                **extra,
            )
        except AnthropicError as exc:
            raise ProviderError(f"vision request failed: {exc}") from exc
        if message.stop_reason == "max_tokens":
            # A reading cut off mid-JSON would be "repaired" into a screen with
            # half its values and none of its events: no reading at all is safer.
            raise ProviderError(f"vision reading cut off at {self._max_tokens} tokens")
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
