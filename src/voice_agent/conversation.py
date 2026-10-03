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
            return [m for m in self.messages if m is not self.opening]
        notes: dict[int, list[Message]] = {}
        kept = self.seen[-SCREEN_NOTES:]
        earlier = len(self.seen) - len(kept)
        for glimpse in kept:
            anchor = id(glimpse.after) if glimpse.after is not None else 0
            if earlier:
                summary = f"[screen: {earlier} earlier changes not shown]"
                notes.setdefault(anchor, []).append(Message("user", summary))
                earlier = 0
            notes.setdefault(anchor, []).append(Message("user", seen_note(glimpse)))
        known = {id(m) for m in self.messages}
        context: list[Message] = [
            note for anchor, held in notes.items() if anchor not in known for note in held
        ]
        for message in self.messages:
            if message is not self.opening:
                context.append(message)
            context.extend(notes.get(id(message), ()))
        status = Message("user", eyes_status(self.screen))
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


def eyes_status(screen: Screen) -> str:
    """Whether it can see, right before the turn it is about to answer."""
    if not screen.sharing:
        return "[eyes: not sharing — you cannot see their screen right now]"
    now = f" · on screen now: {screen.now}" if screen.now else " · nothing seen yet"
    return f"[eyes: sharing {screen.surface or 'a screen'} since {screen.since}{now}]"
