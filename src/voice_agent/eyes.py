"""The eyes: the screen the user shares, read as it changes.

The page decides *which* frames are worth reading (it compares each with the
last one it sent, `web/eyes.js`); this decides *when*: **at most two vision
calls in flight, a second only once the first has run a while, and the newest
frame waiting**. A frame that arrives while no call may start replaces the one
waiting, so a screen that changes faster than the model reads is seen as it is
now, never as a backlog of how it was. Two calls overlapping halve the time
between readings; their results are applied in the order the frames were
taken, and one overtaken by a newer result is dropped.

A frame lives for the length of its call; only the one behind a reading that
saw a change is kept, as the Work Map's screenshot (`_keep`). What was seen is
kept as text, in the conversation (`Conversation.seen`), where every call to
the reasoning engine reads it.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from voice_agent import timing, trace
from voice_agent.conversation import Conversation, Glimpse
from voice_agent.errors import VoiceAgentError
from voice_agent.llm.vision import Seen, Vision

logger = logging.getLogger(__name__)

SURFACES = {"browser": "a browser tab", "window": "a window", "monitor": "the entire screen"}
"""The page's `displaySurface`, as the model and the record word it."""

LABEL_CHARS = 80

IN_FLIGHT = 2
"""Vision calls at once. One read lags the screen by up to two calls (the one
in flight, then its own); two, staggered, by about one and a half."""

STAGGER_SECONDS = 1.0
"""A second call starts only this long after the newest one began: two calls
on the same instant would read the same screen twice."""

MAX_STORED_FRAMES = 200
"""Screenshots kept per conversation, for the Work Map: the frame behind each
reading that saw a change. 1024 px JPEGs of 60-120 KB, so about 20 MB at most."""

LOOK_GAP_SECONDS = 3.0
"""At most one "look now" per this long: a turn's speech can start and stop
several times before it is committed."""

LOOK_ANSWER_SECONDS = 1.0
"""How soon after a "look" the page's frame is taken as its answer. The page
answers at once, or not at all when nothing changed."""

FRESH_WAIT_SECONDS = 1.5
"""How long a reply waits for the reading of the look its speaker started: the
question is most likely about that screen. A reading takes ~3 s and starts as
they begin to speak, so it is usually done or nearly done by the commit."""


@dataclass(slots=True)
class Frame:
    jpeg: bytes
    crop: bytes | None
    """The changed area at the screen's own resolution (PNG), when small."""
    at: float
    """When it arrived: the moment a glimpse is placed at, not when it was read."""
    read: asyncio.Event | None = None
    """Set once its reading is applied or given up: a look's answer, which the
    reply to the speaker who started it waits on (`Eyes.fresh`)."""

    def done(self) -> None:
        if self.read is not None:
            self.read.set()


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
        frames_dir: Path | None = None,
    ) -> None:
        """`frames_dir`: where the screenshots behind what was seen are kept
        for the Work Map; `None` keeps none (recording is off)."""
        self._vision = vision
        self._conversation = conversation
        self._report = report
        self._noticed = noticed
        self._max_frames = max_frames
        self._frames_dir = frames_dir
        self.stored = sum(1 for g in conversation.seen if g.frame)
        self._reads = 0
        """Frames sent to the model, read or still being read: what the cap counts."""
        self._capped = False
        self._limit: asyncio.Task[None] | None = None
        self._opened = timing.now()
        self._waiting: Frame | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._newest_at: float | None = None
        """When the newest call in flight began, for the stagger."""
        self._retry: asyncio.TimerHandle | None = None
        self._taken = 0
        """Frames sent to the model so far, numbered in the order taken."""
        self._applied = 0
        """The newest frame whose reading has been kept."""
        self._looked_at: float | None = None
        self._answer_due: float | None = None
        """Until when a frame from the page answers the last look."""
        self._fresh: asyncio.Event | None = None
        """The last look's answer: set once it has been read (`Frame.read`)."""
        self._share = 0
        """Which share this is: a result for an earlier one is not reported."""
        self.looked = 0
        self.replaced = 0
        """Frames that waited and were overtaken by a newer one, never read."""
        self.stale = 0
        """Readings that came back after a newer one had been kept: dropped."""

    @property
    def sharing(self) -> bool:
        return self._conversation.screen.sharing

    async def share(self, active: bool, surface: str = "", label: str = "") -> None:
        """Sharing started (or switched to another surface), or stopped."""
        screen = self._conversation.screen
        self._share += 1
        self._drop_waiting()
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

    def frame(self, jpeg: bytes, crop: bytes | None = None) -> None:
        """A changed frame from the page, with its close-up when the change was
        small. Read now if nothing is being read, else it waits — replacing
        whatever was waiting."""
        if not self.sharing or self._conversation.phase not in ("capture", "teach"):
            return  # sent after the share stopped, or between capture and teaching
        if self._max_frames is not None and self._reads >= self._max_frames:
            if not self._capped:
                self._capped = True
                # Held, so the notice is not collected before it is sent.
                self._limit = asyncio.create_task(self._say_limit())
            return
        now = timing.now()
        frame = Frame(jpeg, crop, now)
        if self._answer_due is not None and now <= self._answer_due:
            self._answer_due = None
            frame.read = self._fresh = asyncio.Event()
        if self._may_start():
            self._start(frame)
            return
        if self._waiting is not None:
            self.replaced += 1
            # A newer frame answers the look as well as the one it replaces.
            frame.read = frame.read or self._waiting.read
        self._waiting = frame
        self._later()

    async def request(self) -> None:
        """Ask the page for the screen as it is now, whether or not it changed
        much: the user has started speaking, and their question will most
        likely be about what is on it. Read while they talk, it is ready by
        the time the turn is committed."""
        now = timing.now()
        if not self.sharing:
            return
        if self._looked_at is not None and now - self._looked_at < LOOK_GAP_SECONDS:
            return
        self._looked_at = now
        self._answer_due = now + LOOK_ANSWER_SECONDS
        await self._report({"type": "look"})

    async def fresh(self) -> int:
        """Wait, briefly, for the reading of the screen as it was when the
        user started speaking; ms waited. At once when there is none pending."""
        pending = self._fresh
        if pending is None or pending.is_set():
            return 0
        started = timing.now()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(pending.wait(), FRESH_WAIT_SECONDS)
        return round((timing.now() - started) * 1000)

    def _may_start(self) -> bool:
        if len(self._tasks) >= IN_FLIGHT:
            return False
        newest = self._newest_at
        return newest is None or not self._tasks or timing.now() - newest >= STAGGER_SECONDS

    def _start(self, frame: Frame) -> None:
        self._taken += 1
        self._newest_at = timing.now()
        task = asyncio.create_task(self._look(frame, self._share, self._taken))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._next()

    def _next(self) -> None:
        """Start the waiting frame, if one may start now; else try again once
        the stagger allows it."""
        self._retry = None
        if self._waiting is None or not self.sharing:
            return
        if self._max_frames is not None and self._reads >= self._max_frames:
            self._drop_waiting()
            return
        if self._may_start():
            frame, self._waiting = self._waiting, None
            self._start(frame)
        else:
            self._later()

    def _later(self) -> None:
        """A frame waits on the stagger alone: start it when that has passed,
        not only when a call finishes."""
        if self._retry is not None or len(self._tasks) >= IN_FLIGHT or self._newest_at is None:
            return
        due = STAGGER_SECONDS - (timing.now() - self._newest_at)
        self._retry = asyncio.get_running_loop().call_later(max(0.0, due), self._next)

    def _drop_waiting(self) -> None:
        if self._waiting is not None:
            self._waiting.done()
        self._waiting = None

    async def _say_limit(self) -> None:
        await self._report(
            {
                "type": "seen",
                "failed": f"this conversation's limit of {self._max_frames} "
                "screen readings is reached — the apprentice keeps what it saw",
            }
        )

    async def _look(self, frame: Frame, share: int, taken: int) -> None:
        try:
            await self._read(frame, share, taken)
        finally:
            frame.done()

    async def _read(self, frame: Frame, share: int, taken: int) -> None:
        screen = self._conversation.screen
        if self._max_frames is not None and self._reads >= self._max_frames:
            return  # waited while the cap was reached
        self._reads += 1
        try:
            with trace.span(
                "eyes.look",
                # Its size, never the image.
                {
                    "bytes": len(frame.jpeg),
                    "crop_bytes": len(frame.crop or b""),
                    "model": self._vision.model,
                    "previous": screen.now,
                },
                trace_id=self._conversation.id,
            ):
                seen = await self._vision.look(frame.jpeg, screen.now, frame.crop)
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
        if taken < self._applied:
            self.stale += 1
            return  # a newer frame's reading came back first
        self._applied = taken
        await self.saw(seen, frame.jpeg, at=frame.at, crop=frame.crop is not None)

    async def saw(
        self, seen: Seen, jpeg: bytes | None = None, at: float | None = None, crop: bool = False
    ) -> None:
        """Keep what a frame showed, and tell the page and the session. `at`:
        when the frame arrived, which is when the glimpse happened."""
        conversation = self._conversation
        conversation.screen.now = seen.screen
        when = clock((timing.now() if at is None else at) - self._opened)
        # Two readings in flight compare their frames with the same earlier
        # screen, so the second can report again what the first just did.
        before = (
            {e.casefold() for e in conversation.seen[-1].events} if conversation.seen else set()
        )
        fresh = tuple(e for e in seen.events if e.casefold() not in before)
        seen = Seen(
            screen=seen.screen,
            app=seen.app,
            events=fresh,
            doing=seen.doing,
            ms=seen.ms,
            model=seen.model,
            input_tokens=seen.input_tokens,
            output_tokens=seen.output_tokens,
        )
        glimpse: Glimpse | None = None
        if seen.events:
            last = conversation.messages[-1] if conversation.messages else None
            glimpse = Glimpse(
                last, when, seen.app, seen.events, id=f"g{len(conversation.seen) + 1}"
            )
            glimpse.frame = self._keep(glimpse.id, jpeg)
            conversation.seen.append(glimpse)
        await self._report(
            {
                "type": "seen",
                "id": glimpse.id if glimpse is not None else "",
                "frame": bool(glimpse and glimpse.frame),
                "crop": crop,
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

    def _keep(self, gid: str, jpeg: bytes | None) -> bool:
        """Store the screenshot behind a glimpse, for the map. Whether it was."""
        if self._frames_dir is None or jpeg is None or self.stored >= MAX_STORED_FRAMES:
            return False
        try:
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            (self._frames_dir / f"{gid}.jpg").write_bytes(jpeg)
        except OSError as exc:
            logger.warning("could not keep a screenshot: %s", exc)
            return False
        self.stored += 1
        return True

    async def close(self) -> None:
        self._drop_waiting()
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        tasks, self._tasks = list(self._tasks), set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
