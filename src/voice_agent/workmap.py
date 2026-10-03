"""The Work Map: what the apprentice learned, drawn as steps you can click.

Once the expert finishes capturing, one model reads the whole conversation —
what was said, and what the eyes saw, each moment numbered — and draws the
map: steps, the decision at each, the reason in the expert's own words, the
guardrails around it, the screen moment it happened at, and what is still
unclear. Code then checks what a model cannot be trusted to: that every
screen moment exists, and that every quote is something the expert said.

The map is redrawn from the conversation after the expert corrects it: a few
seconds after their last turn, one drawing at a time.
"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from json_repair import repair_json

from voice_agent import judge, prompts, timing
from voice_agent.conversation import Conversation, Message
from voice_agent.errors import SilentReplyError
from voice_agent.llm import LLM
from voice_agent.llm.base import Usage

logger = logging.getLogger(__name__)

CREATORS = judge.JUDGES
"""Who may draw the map: the menu's two smartest, as for the judge, built the
same way (`judge.build`) and with the same room to think."""

EXPECTED_S = 30
"""What the page's progress bar counts towards."""

REDRAW_AFTER_SECONDS = 6.0
"""How long after the expert's last turn the map is redrawn: long enough for a
correction that takes two sentences, short enough to see it land."""

ATTEMPTS = 2

KINDS = ("limit", "exception", "escalate", "never")

ORDINALS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]


def script(
    conversation: Conversation, messages_from: int = 0, seen_from: int = 0
) -> tuple[str, dict[str, str]]:
    """The conversation as the map creator reads it — numbered messages and
    numbered screen moments, in order — and the messages by number, for
    checking quotes. `messages_from` and `seen_from` start it later: the
    teaching report reads only the teaching."""
    by_id: dict[str, str] = {}
    later: dict[int, list[str]] = {}
    for glimpse in conversation.seen[seen_from:]:
        anchor = id(glimpse.after) if glimpse.after is not None else 0
        shot = "" if glimpse.frame else " (no screenshot)"
        where = f" {glimpse.app}" if glimpse.app else ""
        line = f"[{glimpse.id} {glimpse.clock}{where}{shot}: {'; '.join(glimpse.events)}]"
        later.setdefault(anchor, []).append(line)
    known = {id(m) for m in conversation.messages[messages_from:]}
    lines = [line for anchor, held in later.items() if anchor not in known for line in held]
    for number, message in enumerate(conversation.messages, start=1):
        if number <= messages_from:
            continue
        mid = f"m{number}"
        by_id[mid] = message.content
        who = "EXPERT" if message.role == "user" else "APPRENTICE"
        lines.append(f'{mid} {who}: "{judge.quoted(message.content)}"')
        lines.extend(later.get(id(message), []))
    return "\n".join(lines), by_id


def request(conversation: Conversation) -> str:
    text, _ = script(conversation)
    previous = conversation.work_map
    redraw = (
        "\n\nThe map you drew before, which the expert has been correcting since:\n"
        f"{judge.quoted(str(previous))}\n\nDraw it again with their corrections applied."
        if previous
        else ""
    )
    return (
        f"<conversation>\n{text}\n</conversation>{redraw}\n\n"
        "Draw the Work Map now, as the JSON object you were asked for."
    )


def parse(text: str) -> Any:
    """The JSON object in a reply, repaired if it slipped. `ValueError` when
    there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the reply holds no JSON object")
    body = text[start : end + 1]
    try:
        return json.loads(body)
    except ValueError:
        return repair_json(body, return_objects=True, skip_json_loads=True)


def normal(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def check(raw: Any, conversation: Conversation, by_id: dict[str, str]) -> dict[str, Any]:
    """The map as the page may show it: every field in its place, screen
    moments that exist, and each quote marked by whether the expert said it."""
    if not isinstance(raw, dict):
        raise ValueError("the map is not a JSON object")
    moments = {g.id: g for g in conversation.seen}

    def quote(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or not str(item.get("quote") or "").strip():
            return None
        said = str(item.get("said") or "")
        words = str(item["quote"]).strip()
        heard = by_id.get(said, "")
        return {
            "quote": words,
            "said": said,
            "verified": bool(heard) and normal(words) in normal(heard),
        }

    steps = []
    for n, step in enumerate(raw.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        screen = str(step.get("screen") or "")
        moment = moments.get(screen)
        guardrails = []
        for rail in step.get("guardrails") or []:
            if not isinstance(rail, dict) or not str(rail.get("rule") or "").strip():
                continue
            kind = str(rail.get("kind") or "limit")
            guardrails.append(
                {
                    "rule": str(rail["rule"]).strip(),
                    "kind": kind if kind in KINDS else "limit",
                    "words": quote(rail),
                }
            )
        steps.append(
            {
                "n": n,
                "title": str(step.get("title") or f"Step {n}").strip(),
                "decision": str(step.get("decision") or "").strip(),
                "reason": quote(step.get("reason")),
                "guardrails": guardrails,
                "judgment": step.get("judgment") is True,
                "screen": screen if moment is not None else "",
                "frame": bool(moment and moment.frame),
                "at": moment.clock if moment is not None else "",
            }
        )
    if not steps:
        raise ValueError("the map has no steps")
    gaps = [str(g).strip() for g in raw.get("gaps") or [] if str(g).strip()]
    return {
        "title": str(raw.get("title") or "Work Map").strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "teach_back": str(raw.get("teach_back") or "").strip(),
        "steps": steps,
        "gaps": gaps[:8],
    }


async def draw(llm: LLM, conversation: Conversation) -> dict[str, Any]:
    """`{"status": "done", "map": …}` or `{"status": "failed", "error": …}`,
    with how long it took. Never raises."""
    started = timing.now()
    _, by_id = script(conversation)
    ask = [Message("user", request(conversation))]
    error = "no attempt"
    for attempt in range(1, ATTEMPTS + 1):
        reply = ""
        usage = Usage()
        try:
            async for fragment in llm.stream(prompts.load("workmap"), ask, usage):
                reply += fragment
            if usage.finish_reason in judge.CUT_OFF:
                raise RuntimeError("the map was cut off before it was finished")
            work_map = check(parse(reply), conversation, by_id)
        except (ValueError, SilentReplyError) as exc:
            logger.warning("map reply %d was unusable: %r", attempt, exc)
            error = str(exc)
            continue
        except Exception as exc:
            logger.warning("the map creator failed: %r", exc)
            error = str(exc)
            break
        return {
            "status": "done",
            "map": work_map,
            "attempts": attempt,
            "ms": round((timing.now() - started) * 1000),
        }
    return {"status": "failed", "error": error[:300], "ms": round((timing.now() - started) * 1000)}


def note(work_map: dict[str, Any], version: int) -> str:
    """The map as the conversation model reads it: in brackets, compact."""
    steps = "; ".join(
        f"{s['n']}. {s['title']} — {s['decision']}"
        + (f' (why: "{s["reason"]["quote"]}")' if s.get("reason") else " (why: unknown)")
        + "".join(f" [guardrail: {g['rule']}]" for g in s["guardrails"])
        for s in work_map["steps"]
    )
    gaps = " | ".join(work_map["gaps"]) or "none"
    return f"[work map v{version}, on their page: {steps}. Still unclear: {gaps}]"


def spoken_step(step: dict[str, Any], teaching: bool = False) -> str:
    """What the apprentice says when a step is clicked: built, not generated,
    so it is instant and says exactly what the map says. To the expert the
    reason is "your words"; to a new hire being taught, the expert's."""
    n = step["n"]
    number = ORDINALS[n] if n < len(ORDINALS) else str(n)
    # The first sentence: a clicked step is a reminder, not a reading.
    decision = re.split(r"(?<=[.!?;])\s", step["decision"].strip(), maxsplit=1)[0].rstrip(".;")
    line = (
        f"Step {number}, {step['title']}: {decision}."
        if decision
        else f"Step {number}, {step['title']}."
    )
    reason = step.get("reason")
    if reason:
        whose = "The expert said" if teaching else "In your words"
        line += f" {whose}: “{reason['quote']}”"
    elif teaching:
        line += " The expert never said why — what do you think?"
    else:
        line += " I still don't know why — can you tell me?"
    return line


class Mapper:
    """One conversation's map: drawn on request, redrawn after corrections.

    Knows nothing of `Session`: it reports to the page, says things through
    `announce`, and builds its creator on first use."""

    def __init__(
        self,
        conversation: Conversation,
        creator: Callable[[], LLM],
        title: str,
        report: Callable[[dict[str, object]], Awaitable[None]],
        announce: Callable[..., Awaitable[None]],
        written: Callable[[dict[str, Any]], None] = lambda _: None,
        guard: asyncio.Semaphore | None = None,
    ) -> None:
        self._conversation = conversation
        self._creator = creator
        self._title = title
        self._report = report
        self._announce = announce
        self._written = written
        self._guard = guard
        self._task: asyncio.Task[None] | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._dirty = False

    @property
    def drawing(self) -> bool:
        return self._task is not None and not self._task.done()

    async def begin(self) -> None:
        """Capture is finished: draw the first map, then say it."""
        conversation = self._conversation
        if conversation.phase not in ("capture", "mapping") or self.drawing:
            return
        conversation.phase = "mapping"
        await self._report(
            {"type": "mapping", "expected_s": EXPECTED_S, "creator": self._title, "redraw": False}
        )
        # Cutting off whatever it was saying: capture is over, and talking on
        # over "drawing the map" reads as not having heard the button.
        await self._announce(
            "Give me half a minute — I'm drawing the map of what you showed me.", interrupt=True
        )
        self._task = asyncio.create_task(self._draw(first=True))

    def corrected(self) -> None:
        """The expert said something while the map is up: redraw it soon. Said
        while the first map is being drawn, it is drawn again once that ends:
        the drawing read the conversation as it stood when it began."""
        phase = self._conversation.phase
        if phase == "mapping":
            self._dirty = True
            return
        if phase != "map":
            return
        self._dirty = True
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(REDRAW_AFTER_SECONDS, self._redraw)

    def _redraw(self) -> None:
        self._timer = None
        if self.drawing or not self._dirty:
            return  # a drawing in progress picks the correction up when it ends
        self._dirty = False
        self._task = asyncio.create_task(self._draw(first=False))

    async def _draw(self, first: bool) -> None:
        conversation = self._conversation
        if not first:
            await self._report(
                {
                    "type": "mapping",
                    "expected_s": EXPECTED_S,
                    "creator": self._title,
                    "redraw": True,
                }
            )
        async with self._guard or contextlib.nullcontext():
            result = await draw(self._creator(), conversation)
        if result["status"] != "done":
            if first:
                conversation.phase = "capture"
            await self._report({"type": "map_failed", "error": result["error"], "first": first})
            if first:
                await self._announce(
                    "I couldn't draw the map just now. Press the button to try again."
                )
            return
        conversation.map_version += 1
        conversation.work_map = result["map"]
        conversation.map_note = note(result["map"], conversation.map_version)
        if conversation.phase == "mapping":
            conversation.phase = "map"
        self._written({**result, "version": conversation.map_version})
        await self._report(
            {
                "type": "work_map",
                "map": result["map"],
                "version": conversation.map_version,
                "ms": result["ms"],
                "creator": self._title,
            }
        )
        if first and result["map"]["teach_back"]:
            await self._announce(result["map"]["teach_back"])
        if self._dirty and self._timer is None and (current := asyncio.current_task()):
            # Corrected while it drew: drawn again once this drawing has
            # finished — from inside it, `_redraw` would see it still running.
            current.add_done_callback(lambda _: self._redraw())

    def step(self, n: int) -> str | None:
        """What to say for a clicked step, if the map has it."""
        work_map = self._conversation.work_map
        if work_map is None:
            return None
        teaching = self._conversation.phase in ("teach", "assessing", "taught")
        return next((spoken_step(s, teaching) for s in work_map["steps"] if s["n"] == n), None)

    async def finish(self) -> None:
        """The expert confirmed the map: it is the one teaching starts from."""
        conversation = self._conversation
        if conversation.phase != "map":
            return
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._dirty and not self.drawing:
            self._dirty = False
            self._task = asyncio.create_task(self._draw(first=False))
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task
        conversation.phase = "mapped"
        await self._report({"type": "mapped", "version": conversation.map_version})
        await self._announce("The map is final — it's ready to teach from.")

    async def close(self) -> None:
        if self._conversation.phase == "mapping":
            # The drawing goes with the socket: a reconnect offers it again
            # rather than waiting for a map nothing is drawing.
            self._conversation.phase = "capture"
        if self._timer is not None:
            self._timer.cancel()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
