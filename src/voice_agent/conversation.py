"""The conversation: the messages exchanged so far, and nothing else.

This is the "context" that is resent to the reasoning engine on every call.
A plain in-memory list: no trimming, no summarization, no persistence.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from voice_agent.language import LanguageMemory
from voice_agent.timeline import Timeline

Role = Literal["user", "assistant"]

TEACHING_STARTS = (
    "[teaching starts here — everything before this was the expert's own session; "
    "what is said and seen after this is the new hire, on their own case]"
)

SCREEN_NOTES = 40
"""How many of the latest screen notes every call carries. Thirty minutes of
sharing is hundreds of them, resent on every turn; older ones are counted, not
repeated. The whole log stays in `Conversation.seen`."""


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(slots=True)
class Glimpse:
    """One thing the eyes saw, kept in the conversation's order: after `after`,
    the message that was last when it was seen (`None`: before any)."""

    after: Message | None
    clock: str
    """When, as m:ss since the eyes opened, for the note the model reads."""
    app: str
    events: tuple[str, ...]
    id: str = ""
    """`g1`, `g2`, …: how the Work Map points at this moment."""
    frame: bool = False
    """Its screenshot is stored (`eyes.py`), so the map can show it."""


@dataclass(slots=True)
class Screen:
    """Whether the user is sharing a screen right now, and what it shows."""

    sharing: bool = False
    surface: str = ""
    """What was shared, as the page names it: a tab, a window, a screen."""
    since: str = ""
    now: str = ""
    """The latest description of what is on it."""


@dataclass(slots=True)
class Conversation:
    """One conversation, identified by the key in its URL."""

    id: str
    messages: list[Message] = field(default_factory=list)
    ended: bool = False
    engine: str | None = None
    ears: str | None = None
    role: str | None = None
    voice: str | None = None
    """Which menu option, which recognizer, which role and which voice model this
    conversation runs on, chosen when it was started. `engine` is a `registry.Choice` name — a
    model and an effort together — and not a provider.

    Pinned rather than re-read on every connection, because resuming a link has
    to resume the same agent: the history below was produced by that engine,
    and the system prompt names the languages *those* ears have. A reconnect
    that quietly swapped either would leave the agent contradicting its own
    transcript.
    """
    opening: Message | None = None
    """The greeting, kept in the history the page replays but out of what the
    model reads: a fixed line in one language anchored replies to it (the system
    prompt says it was said instead)."""
    record: Path | None = None
    """Where this conversation is written down, once it has been."""
    judge: str | None = None
    """Which judge rules on it (`judge.JUDGES`), pinned like the engine."""
    language: LanguageMemory = field(default_factory=LanguageMemory)
    """Which language it is being spoken in, learned from what the recognizer
    committed. **Per conversation, deliberately**: the recognizer adapter is one
    instance shared by every conversation on the server (`Backends.ears`), so
    the hint cannot live there — and this survives a reconnect or a reload,
    since the conversation outlives the socket."""
    started: float | None = None
    """When it was first connected to, on `timing.now()`: a time limit counts
    from here, so reconnecting does not start the clock again."""
    timeline: Timeline | None = None
    """Who said what and when, across reconnects: what the judge reads. Kept
    only for a judged round."""
    judging: "tuple[float, asyncio.Task[None]] | None" = None
    """The ruling in progress: when it began, and the task, held so it is not
    collected mid-call."""
    verdict: dict[str, Any] | None = None
    """The judge's ruling, once there is one; a reload shows it again."""
    eyes: str | None = None
    """Which eyes (`llm/vision.MENU`) this conversation sees with, pinned like the
    engine; `None` when it has none, and then no screen note reaches the model."""
    screen: Screen = field(default_factory=Screen)
    seen: list[Glimpse] = field(default_factory=list)
    """What the eyes saw, in order: what `context` interleaves with the messages."""
    phase: str = "capture"
    """The apprentice's flow: `capture`, `mapping` (the map is being drawn),
    `map` (shown and being corrected), `mapped` (confirmed), `teach` (the
    apprentice tutors a new hire), `assessing` (the report is being written),
    `taught`. Other roles stay in `capture`, which is simply the conversation."""
    mapper: str | None = None
    """Which map creator draws the Work Map (`workmap.CREATORS`), pinned."""
    work_map: dict[str, Any] | None = None
    map_version: int = 0
    teach_from: int | None = None
    """Where teaching began, as an index into `messages`, and into `seen`
    (`teach_seen_from`): the report reads only what came after."""
    teach_seen_from: int = 0
    mastery: dict[str, Any] | None = None
    """The teaching report, once Finish teaching has produced one."""
    map_note: str = ""
    """The current map as the model reads it, in `context`."""
    frames: list[dict[str, Any]] = field(default_factory=list)
    """What was sent to the page, in order (`channel.shown`), plus typed turns
    — kept even once the socket has gone, like the history: a reload redraws
    it with its notes and thoughts, not bare messages."""

    @property
    def context(self) -> list[Message]:
        """What the reasoning engine reads: every message but the greeting.

        With eyes, also what they saw, as bracketed notes where it happened, and
        before the latest user turn whether it can see at all. Built here, at
        call time, so the history itself — the page's replay, the record, the
        judge — holds only what was said."""
        if self.eyes is None:
            said = [m for m in self.messages if m is not self.opening]
            if self.map_note:
                at = len(said) - 1 if said and said[-1].role == "user" else len(said)
                said.insert(at, Message("user", self.map_note))
            return said
        # Notes by the message they follow, the expert's apart from the new
        # hire's: once teaching starts, the tutor must never mistake one for
        # the other, and a new hire's first screen changes can follow the
        # expert's last words before the handover line is said.
        notes: dict[int, list[Message]] = {}
        taught: dict[int, list[Message]] = {}
        kept = self.seen[-SCREEN_NOTES:]
        first = len(self.seen) - len(kept)
        teaching = self.teach_from is not None
        for index, glimpse in enumerate(kept, start=first):
            anchor = id(glimpse.after) if glimpse.after is not None else 0
            held = taught if teaching and index >= self.teach_seen_from else notes
            if index == first and first:
                summary = f"[screen: {first} earlier changes not shown]"
                held.setdefault(anchor, []).append(Message("user", summary))
            held.setdefault(anchor, []).append(Message("user", seen_note(glimpse)))
        known = {id(m) for m in self.messages}
        context: list[Message] = [
            note
            for group in (notes, taught)
            for anchor, held_notes in group.items()
            if anchor not in known
            for note in held_notes
        ]
        boundary = Message("user", TEACHING_STARTS)
        if teaching and self.teach_from == 0:
            context.insert(0, boundary)
        for index, message in enumerate(self.messages):
            if message is not self.opening:
                context.append(message)
            context.extend(notes.get(id(message), ()))
            if teaching and index == (self.teach_from or 0) - 1:
                context.append(boundary)
            context.extend(taught.get(id(message), ()))
        if self.map_note:
            context.append(Message("user", self.map_note))
        status = Message("user", eyes_status(self.screen, self.phase))
        last_user = max(
            (
                i
                for i, m in enumerate(context)
                if m.role == "user" and not m.content.startswith("[")
            ),
            default=None,
        )
        if last_user is not None and last_user == len(context) - 1:
            context.insert(last_user, status)
        else:
            context.append(status)
        return context

    def add_user(self, content: str) -> Message:
        return self._add("user", content)

    def add_assistant(self, content: str) -> Message:
        return self._add("assistant", content)

    def _add(self, role: Role, content: str) -> Message:
        message = Message(role=role, content=content)
        self.messages.append(message)
        return message

    def replace(self, message: Message, content: str | None) -> None:
        """Rewrite a message already recorded, or remove it with `None`.

        By identity, not by position: a reply is recorded when its text is
        written, and learns how much of it was heard only later — after other
        messages may have followed it.
        """
        for index, existing in enumerate(self.messages):
            if existing is message:
                replacement = None if content is None else Message(message.role, content)
                if replacement is None:
                    del self.messages[index]
                else:
                    self.messages[index] = replacement
                # What the eyes saw after it stays where it was seen.
                anchor = replacement or (self.messages[index - 1] if index else None)
                for glimpse in self.seen:
                    if glimpse.after is existing:
                        glimpse.after = anchor
                if existing is self.opening:
                    self.opening = replacement
                return

    def end(self) -> None:
        self.ended = True


def seen_note(glimpse: Glimpse) -> str:
    """What the eyes saw, as the model reads it: in brackets, so it is never
    taken for the user speaking (see the system prompt's "Your eyes")."""
    where = f" {glimpse.app}" if glimpse.app else ""
    return f"[screen {glimpse.clock}{where}: {'; '.join(glimpse.events)}]"


def eyes_status(screen: Screen, phase: str = "capture") -> str:
    """Whether it can see, right before the turn it is about to answer."""
    if phase not in ("capture", "teach"):
        return "[eyes: capture is finished — the screen is no longer shared]"
    if not screen.sharing:
        return "[eyes: not sharing — you cannot see their screen right now]"
    now = f" · on screen now: {screen.now}" if screen.now else " · nothing seen yet"
    return f"[eyes: sharing {screen.surface or 'a screen'} since {screen.since}{now}]"
