"""The eyes: the screen the user shares, read one changed frame at a time.

The page decides *which* frames are worth reading (it compares each with the
last one it sent, `web/eyes.js`); this decides *when*: **one vision call in
flight, the newest frame waiting**. A frame that arrives during a call replaces
the one waiting, so a screen that changes faster than the model reads is seen
as it is now, never as a backlog of how it was.

Frames are never kept: each lives for the length of its call. What was seen is
kept as text, in the conversation (`Conversation.seen`), where every call to
the reasoning engine reads it.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from voice_agent import timing, trace
from voice_agent.conversation import Conversation, Glimpse
from voice_agent.errors import VoiceAgentError
from voice_agent.llm.vision import Seen, Vision

logger = logging.getLogger(__name__)

SURFACES = {"browser": "a browser tab", "window": "a window", "monitor": "the entire screen"}
"""The page's `displaySurface`, as the model and the record word it."""

LABEL_CHARS = 80


def clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


class Eyes:
    """One conversation's eyes. Knows nothing of `Session`: it reports what it
    saw, and `noticed` is told when something changed."""

    def __init__(
        self,
        vision: Vision,
        conversation: Conversation,
        report: Callable[[dict[str, object]], Awaitable[None]],
        noticed: Callable[[str | None], None] = lambda _: None,
        max_frames: int | None = None,
    ) -> None:
        self._vision = vision
        self._conversation = conversation
        self._report = report
        self._noticed = noticed
        self._max_frames = max_frames
        self._reads = 0
        """Frames sent to the model, read or still being read: what the cap counts."""
        self._capped = False
        self._limit: asyncio.Task[None] | None = None
        self._opened = timing.now()
        self._waiting: bytes | None = None
        self._task: asyncio.Task[None] | None = None
        self._share = 0
        """Which share this is: a result for an earlier one is not reported."""
        self.looked = 0
        self.replaced = 0
        """Frames that waited and were overtaken by a newer one, never read."""

    @property
    def sharing(self) -> bool:
        return self._conversation.screen.sharing

    async def share(self, active: bool, surface: str = "", label: str = "") -> None:
        """Sharing started (or switched to another surface), or stopped."""
        screen = self._conversation.screen
        self._share += 1
        self._waiting = None
        if active:
            kind = SURFACES.get(surface, "a screen")
            named = label.strip()[:LABEL_CHARS]
            screen.sharing = True
            screen.surface = f'{kind} "{named}"' if named else kind
            screen.since = clock(timing.now() - self._opened)
            screen.now = ""
        else:
            screen.sharing = False
            screen.now = ""
            self._noticed(None)  # nothing seen before the stop is still news
        await self._report(
            {"type": "eyes", "sharing": screen.sharing, "surface": screen.surface if active else ""}
        )

    def frame(self, jpeg: bytes) -> None:
        """A changed frame from the page. Read now if nothing is being read,
        else it waits — replacing whatever was waiting."""
        if not self.sharing:
            return  # sent after the share stopped: nothing to see
        if self._max_frames is not None and self._reads >= self._max_frames:
            if not self._capped:
                self._capped = True
                # Held, so the notice is not collected before it is sent.
                self._limit = asyncio.create_task(self._say_limit())
            return
        if self._task is not None and not self._task.done():
            if self._waiting is not None:
                self.replaced += 1
            self._waiting = jpeg
            return
        self._task = asyncio.create_task(self._read(jpeg, self._share))

    async def _say_limit(self) -> None:
        await self._report(
            {
                "type": "seen",
                "failed": f"this conversation's limit of {self._max_frames} "
                "screen readings is reached — the apprentice keeps what it saw",
            }
        )

    async def _read(self, jpeg: bytes | None, share: int) -> None:
        while jpeg is not None:
            await self._look(jpeg, share)
            jpeg, self._waiting = self._waiting, None
            share = self._share

    async def _look(self, jpeg: bytes, share: int) -> None:
        screen = self._conversation.screen
        if self._max_frames is not None and self._reads >= self._max_frames:
            return  # waited while the cap was reached
        self._reads += 1
        try:
            with trace.span(
                "eyes.look",
                # Its size, never the image: frames are not kept anywhere.
                {"bytes": len(jpeg), "model": self._vision.model, "previous": screen.now},
                trace_id=self._conversation.id,
            ):
                seen = await self._vision.look(jpeg, screen.now)
                trace.event(
                    "eyes.seen",
                    {
                        "app": seen.app,
                        "screen": seen.screen,
                        "events": list(seen.events),
                        "doing": seen.doing,
                        "input_tokens": seen.input_tokens,
                        "output_tokens": seen.output_tokens,
                    },
                )
        except VoiceAgentError as exc:
            logger.warning("the eyes could not read a frame: %s", exc)
            await self._report({"type": "seen", "failed": str(exc)[:200]})
            return
        except Exception:
            # A defect must not stop the next frame being read.
            logger.exception("the eyes failed")
            return
        self.looked += 1
        if share != self._share or not self.sharing:
            return  # the share stopped or changed while it was being read
        await self.saw(seen)

    async def saw(self, seen: Seen) -> None:
        """Keep what a frame showed, and tell the page and the session."""
        conversation = self._conversation
        conversation.screen.now = seen.screen
        when = clock(timing.now() - self._opened)
        if seen.events:
            last = conversation.messages[-1] if conversation.messages else None
            conversation.seen.append(Glimpse(last, when, seen.app, seen.events))
        await self._report(
            {
                "type": "seen",
                "at": when,
                "app": seen.app,
                "events": list(seen.events),
                "screen": seen.screen,
                "doing": seen.doing,
                "ms": seen.ms,
                "model": seen.model,
                "input_tokens": seen.input_tokens,
                "output_tokens": seen.output_tokens,
            }
        )
        if seen.events:
            self._noticed("; ".join(seen.events))

    async def close(self) -> None:
        task, self._task = self._task, None
        self._waiting = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
