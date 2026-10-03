"""Teaching: the apprentice becomes the tutor, then reports what was learned.

Once the Work Map is final, the same conversation switches parts. The system
prompt's role becomes the tutor (`prompts/tutor.md`), the eyes watch again,
and the clock checks every change on the screen against the map at once
(`Initiative.teaching`). When the new hire is done, one model reads the map
and the teaching part of the conversation and writes the report: what was
mastered, which rules were broken or nearly broken — in the expert's words —
and what to practise next.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from voice_agent import judge, prompts, timing
from voice_agent.conversation import Conversation, Message
from voice_agent.errors import SilentReplyError
from voice_agent.llm import LLM
from voice_agent.llm.base import Usage
from voice_agent.workmap import parse, script

logger = logging.getLogger(__name__)

EXPECTED_S = 20
"""What the page's progress bar counts towards while the report is written."""

ATTEMPTS = 2

HANDOVER = (
    "I'm your tutor now. Start a new case — say a different route — share your screen, "
    "and I'll watch the way the expert would."
)


def request(conversation: Conversation) -> str:
    start = conversation.teach_from or 0
    text, _ = script(conversation, start, conversation.teach_seen_from)
    return (
        f"<work_map>\n{judge.quoted(str(conversation.work_map))}\n</work_map>\n\n"
        f"<teaching>\n{text or '(nothing was said or seen)'}\n</teaching>\n\n"
        "Write the report now, as the JSON object you were asked for."
    )


def check(raw: Any) -> dict[str, Any]:
    """The report as the page may show it: every list a list of the right things."""
    if not isinstance(raw, dict):
        raise ValueError("the report is not a JSON object")

    def items(key: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
        value = raw.get(key)
        found: list[Any] = value if isinstance(value, list) else []
        kept = []
        for item in found:
            if isinstance(item, dict) and any(str(item.get(f) or "").strip() for f in fields):
                kept.append({f: str(item.get(f) or "").strip() for f in fields})
        return kept[:8]

    given = raw.get("practice")
    practice: list[Any] = given if isinstance(given, list) else []
    return {
        "headline": str(raw.get("headline") or "").strip(),
        "mastered": items("mastered", ("step", "note")),
        "caught": items("caught", ("step", "what", "rule", "quote")),
        "practice": [str(p).strip() for p in practice if str(p).strip()][:5],
        "spoken": str(raw.get("spoken") or "").strip(),
    }


async def assess(llm: LLM, conversation: Conversation) -> dict[str, Any]:
    """`{"status": "done", "report": …}` or `{"status": "failed", "error": …}`,
    with how long it took. Never raises."""
    started = timing.now()
    ask = [Message("user", request(conversation))]
    error = "no attempt"
    for attempt in range(1, ATTEMPTS + 1):
        reply = ""
        usage = Usage()
        try:
            async for fragment in llm.stream(prompts.load("teach_report"), ask, usage):
                reply += fragment
            report = check(parse(reply))
        except (ValueError, SilentReplyError) as exc:
            logger.warning("report reply %d was unusable: %r", attempt, exc)
            error = str(exc)
            continue
        except Exception as exc:
            logger.warning("the report failed: %r", exc)
            error = str(exc)
            break
        return {"status": "done", "report": report, "ms": round((timing.now() - started) * 1000)}
    return {"status": "failed", "error": error[:300], "ms": round((timing.now() - started) * 1000)}


class Tutor:
    """One conversation's teaching: started once the map is final, finished
    with a report. Knows nothing of `Session`: it is handed how to become the
    tutor, how to say a line, and how to stop the screen being shared."""

    def __init__(
        self,
        conversation: Conversation,
        creator: Callable[[], LLM],
        title: str,
        report: Callable[[dict[str, object]], Awaitable[None]],
        announce: Callable[..., Awaitable[None]],
        become: Callable[[], Awaitable[None]],
        written: Callable[[dict[str, Any]], None] = lambda _: None,
        guard: asyncio.Semaphore | None = None,
    ) -> None:
        self._conversation = conversation
        self._creator = creator
        self._title = title
        self._report = report
        self._announce = announce
        self._become = become
        self._written = written
        self._guard = guard
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """The map is final: from here on, the apprentice teaches it."""
        conversation = self._conversation
        if conversation.phase != "mapped":
            return
        conversation.phase = "teach"
        conversation.teach_from = len(conversation.messages)
        conversation.teach_seen_from = len(conversation.seen)
        await self._become()
        await self._report({"type": "teaching"})
        await self._announce(HANDOVER, interrupt=True)

    async def finish(self) -> None:
        """The new hire is done: write the report, then say it."""
        conversation = self._conversation
        if conversation.phase != "teach":
            return
        conversation.phase = "assessing"
        conversation.screen.sharing = False
        await self._report({"type": "assessing", "expected_s": EXPECTED_S, "creator": self._title})
        await self._announce("Give me a moment — I'm writing up how you did.", interrupt=True)
        self._task = asyncio.create_task(self._assess())

    async def _assess(self) -> None:
        conversation = self._conversation
        async with self._guard or contextlib.nullcontext():
            result = await assess(self._creator(), conversation)
        if result["status"] != "done":
            conversation.phase = "teach"
            await self._report({"type": "assess_failed", "error": result["error"]})
            await self._announce(
                "I couldn't write the report just now. Press the button to try again."
            )
            return
        conversation.mastery = {**result["report"], "ms": result["ms"], "creator": self._title}
        conversation.phase = "taught"
        self._written(conversation.mastery)
        await self._report({"type": "mastery", **conversation.mastery})
        if conversation.mastery["spoken"]:
            await self._announce(conversation.mastery["spoken"])

    async def close(self) -> None:
        if self._conversation.phase == "assessing":
            self._conversation.phase = "teach"  # a reconnect offers Finish teaching again
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
