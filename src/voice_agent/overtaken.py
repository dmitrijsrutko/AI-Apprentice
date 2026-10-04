"""The screen overtakes the voice: a reading that lands while the agent is
talking can make the rest of what it is saying wrong ("the From field says
Tallinn" as it changes to Riga). One quick call weighs the rest against the
change; on STOP the session cuts itself off at the last word heard, exactly
as a barge-in would, and the clock says something fresh at once.

Pure but for the call: the session decides when to ask and what to do."""

from voice_agent import prompts
from voice_agent.conversation import Message
from voice_agent.heard import Spoken
from voice_agent.llm import LLM
from voice_agent.llm.base import Usage
from voice_agent.streams import closing

ENGINES: tuple[tuple[str, str], ...] = (
    ("deepseek", "deepseek-flash"),
    ("anthropic", "claude-sonnet-5-5"),
)
"""Who weighs it, first one whose key is set, always with thinking off.
Measured on five cases from the live traces, three runs each, from the Fly
machine: DeepSeek V4.1 Flash 15/15 at a 0.82 s median, Sonnet 5.5 15/15 at
1.14 s, Haiku 4.5 14-15/15 at 0.49-0.58 s. Over every run made, DeepSeek was
wrong once in 55 and Sonnet never in 70: the faster one at about the same
certainty first, Sonnet behind it for a deployment without a DeepSeek key."""

MAX_TOKENS = 48
"""GO, or STOP with the claim that is now false: naming it is what keeps the
model from stopping on a change that leaves the line true."""

SYSTEM = "You decide one thing for a voice assistant and answer with one word."

TAIL_CHARS = 25
"""Under this much left to say (~1.5 s of speech) a line is let finish: the
call would take most of that, and a cut that close to the end is noise."""

SAID_CHARS = 300
"""How much of what was already said the check reads: the end of it."""


def split(voice: Spoken) -> tuple[str, str]:
    """What the user has heard of a voice so far, and what is still to come of
    what it has voiced (the reply may still be being written past that)."""
    voiced = "".join(voice.chars) or voice.text
    heard = voice.heard(voice.estimate_played_ms())
    rest = voiced[len(heard) :] if voiced.startswith(heard) else voiced
    return heard, rest.strip()


def question(said: str, rest: str, seen: str, screen: str, asked: str = "") -> str:
    """`asked`: what the user had just said, when the line is a reply to it —
    a line reading their own words back is not made wrong by the screen."""
    answering = f'You are answering what they just said: "{asked}"\n' if asked.strip() else ""
    return (
        prompts.load("overtaken")
        .replace("{said}", said[-SAID_CHARS:])
        .replace("{rest}", rest)
        .replace("{asked}\n", answering)
        .replace("{seen}", seen)
        .replace("{screen}", screen or "(no description)")
    )


def verdict(reply: str) -> tuple[bool, str]:
    """The model's STOP and the false claim it named, or GO. Anything but a
    leading STOP is GO — finishing a line is the safe default."""
    text = reply.strip()
    if not text.upper().startswith("STOP"):
        return False, ""
    return True, text[4:].lstrip(" :—-").strip()


async def stale(engine: LLM, ask: str, usage: Usage | None = None) -> tuple[bool, str]:
    """Whether the rest should not be said, and why. A failure raises: the
    caller lets the line finish."""
    fragments: list[str] = []
    async with closing(engine.stream(SYSTEM, [Message("user", ask)], usage)) as stream:
        async for fragment in stream:
            fragments.append(fragment)
    return verdict("".join(fragments))
