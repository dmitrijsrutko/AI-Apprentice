"""The eyes: one frame read at a time, the newest waiting, and what was seen
reaching the conversation's context — never the image itself."""

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeLLM
from voice_agent.conversation import Conversation
from voice_agent.eyes import Eyes
from voice_agent.llm.vision import Seen, parse
from voice_agent.server import create_app
from voice_agent.sessions import SessionStore

WAIT = 2.0


class FakeVision:
    """Reads a frame after `delay`, naming it by its bytes, and remembers what
    it was given."""

    def __init__(self, delay: float = 0.0, events: tuple[str, ...] = ("opened page",)) -> None:
        self.model = "claude-fake"
        self.delay = delay
        self.events = events
        self.read: list[bytes] = []
        self.previous: list[str] = []
        self.started = asyncio.Event()
        self.finished: asyncio.Queue[bytes] = asyncio.Queue()
        self.delays: dict[bytes, float] = {}

    async def look(self, jpeg: bytes, previous: str) -> Seen:
        self.read.append(jpeg)
        self.previous.append(previous)
        self.started.set()
        await asyncio.sleep(self.delays.get(jpeg, self.delay))
        self.finished.put_nowait(jpeg)
        return Seen(
            screen=f"screen {jpeg.decode()}",
            app="Flights",
            events=self.events,
            doing="",
            ms=12,
            model=self.model,
        )


class Page:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []

    async def __call__(self, payload: dict[str, object]) -> None:
        self.frames.append(payload)


async def settle(vision: FakeVision, count: int) -> None:
    """Until `count` frames have been read, then a moment for the result to land."""
    for _ in range(count):
        await asyncio.wait_for(vision.finished.get(), WAIT)
    await asyncio.sleep(0.01)


async def test_a_second_frame_waits_for_the_stagger_and_only_the_newest_waits() -> None:
    vision = FakeVision(delay=0.05)
    eyes = Eyes(vision, Conversation(id="t"), Page())
    await eyes.share(True, "browser", "Skyscanner")

    eyes.frame(b"1")
    await vision.started.wait()
    eyes.frame(b"2")  # waits
    eyes.frame(b"3")  # replaces 2
    await settle(vision, 2)

    assert vision.read == [b"1", b"3"]
    assert eyes.replaced == 1
    assert vision.previous == ["", "screen 1"], "each frame is read against the last screen"
    await eyes.close()


async def test_a_frame_while_not_sharing_is_dropped() -> None:
    vision = FakeVision()
    eyes = Eyes(vision, Conversation(id="t"), Page())

    eyes.frame(b"1")
    await asyncio.sleep(0.02)

    assert vision.read == []


async def test_what_was_seen_reaches_the_conversation_and_the_page() -> None:
    conversation = Conversation(id="t", eyes="haiku-4-5")
    conversation.add_user("let me show you")
    page = Page()
    noticed: list[str | None] = []
    eyes = Eyes(FakeVision(), conversation, page, noticed.append)

    await eyes.share(True, "browser", "Skyscanner")
    eyes.frame(b"1")
    await asyncio.sleep(0.02)

    assert conversation.screen.sharing and conversation.screen.now == "screen 1"
    assert [g.events for g in conversation.seen] == [("opened page",)]
    seen = [f for f in page.frames if f["type"] == "seen"]
    assert seen and seen[0]["events"] == ["opened page"] and "jpeg" not in seen[0]
    assert noticed == ["opened page"]
    assert page.frames[0] == {
        "type": "eyes",
        "sharing": True,
        "surface": 'a browser tab "Skyscanner"',
    }


async def test_a_result_for_a_stopped_share_is_not_kept() -> None:
    conversation = Conversation(id="t", eyes="haiku-4-5")
    vision = FakeVision(delay=0.05)
    eyes = Eyes(vision, conversation, Page())
    await eyes.share(True, "window", "")
    eyes.frame(b"1")
    await vision.started.wait()

    await eyes.share(False)
    await asyncio.sleep(0.1)

    assert conversation.seen == [] and not conversation.screen.sharing


async def test_resharing_starts_afresh_on_the_new_surface() -> None:
    conversation = Conversation(id="t", eyes="haiku-4-5")
    vision = FakeVision()
    eyes = Eyes(vision, conversation, Page())
    await eyes.share(True, "browser", "one")
    eyes.frame(b"1")
    await asyncio.sleep(0.02)
    await eyes.share(False)
    await eyes.share(True, "monitor", "")
    eyes.frame(b"2")
    await asyncio.sleep(0.02)

    assert conversation.screen.surface == "the entire screen"
    assert vision.previous[-1] == "", "a new share is read with no previous screen"


def test_the_context_carries_what_was_seen_and_whether_it_can_see() -> None:
    conversation = Conversation(id="t", eyes="haiku-4-5")
    first = conversation.add_user("I'm opening the search")
    from voice_agent.conversation import Glimpse

    conversation.seen.append(Glimpse(first, "0:05", "Flights", ("filter 'Direct' applied",)))
    conversation.add_assistant("Why direct only?")
    conversation.add_user("He hates layovers")

    context = [m.content for m in conversation.context]

    assert context[0] == "I'm opening the search"
    assert context[1] == "[screen 0:05 Flights: filter 'Direct' applied]"
    assert context[2] == "Why direct only?"
    assert context[3].startswith("[eyes: not sharing")
    assert context[4] == "He hates layovers"
    assert [m.content for m in conversation.messages] == [
        "I'm opening the search",
        "Why direct only?",
        "He hates layovers",
    ], "the history itself holds only what was said"


def test_without_eyes_the_context_is_only_what_was_said() -> None:
    conversation = Conversation(id="t")
    conversation.add_user("hello")

    assert [m.content for m in conversation.context] == ["hello"]


def test_a_reply_that_is_not_quite_json_is_still_read() -> None:
    seen = parse(
        '```json\n{"app": "Mail", "screen": "inbox", "events": ["opened inbox",]}\n```', 5, "m"
    )

    assert (seen.app, seen.screen, seen.events) == ("Mail", "inbox", ("opened inbox",))
    assert parse("just words", 5, "m").screen == "just words"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(
        llm=FakeLLM(), store=SessionStore(), voice=False, ears=False, vision=FakeVision()
    )
    return TestClient(app)


def test_the_page_shares_a_screen_over_the_socket(client: TestClient) -> None:
    key = client.get("/", follow_redirects=False).headers["location"].removeprefix("/c/")
    with client.websocket_connect(f"/ws/{key}?eyes=sonnet-5-5") as socket:
        ready = socket.receive_json()
        assert ready["eyes"] == {"interval": 1.0, "min_cells": 2}
        assert [o["name"] for o in ready["choices"]["eyes"]] == ["sonnet-5-5", "haiku-4-5"]
        while socket.receive_json()["type"] != "greeting":
            pass
        socket.send_text(json.dumps({"type": "share", "active": True, "surface": "browser"}))
        assert socket.receive_json()["type"] == "eyes"
        jpeg = base64.b64encode(b"frame").decode()
        socket.send_text(json.dumps({"type": "frame", "jpeg": jpeg, "changed": 0.2}))
        seen = socket.receive_json()
        assert seen["type"] == "seen" and seen["screen"] == "screen frame"
        # Not base64: refused without a word, and the socket carries on.
        socket.send_text(json.dumps({"type": "frame", "jpeg": "%%%"}))
        socket.send_text(json.dumps({"type": "user_message", "text": "can you see it?"}))
        while socket.receive_json()["type"] != "reply_end":
            pass


async def test_a_screen_change_keeps_listening_open_but_is_not_speech() -> None:
    """Counted as speech, every unprompted line decided while frames arrive
    would yield to nobody."""
    from tests.test_mic import RecordingChannel, running_mic

    mic = await running_mic(RecordingChannel(), idle_timeout=60.0, session_cap=60.0)
    await asyncio.sleep(0.05)
    before = mic.quiet_for
    mic.active()

    assert mic.quiet_for >= before
    await mic.stop()


def test_notes_stay_where_they_were_seen_when_a_reply_is_cut() -> None:
    from voice_agent.conversation import Glimpse

    conversation = Conversation(id="t", eyes="haiku-4-5")
    conversation.add_user("look")
    reply = conversation.add_assistant("Here is a long answer")
    conversation.seen.append(Glimpse(reply, "0:09", "", ("opened invoice 4471",)))
    conversation.replace(reply, "Here is")
    conversation.add_user("wait")

    context = [m.content for m in conversation.context]

    assert context.index("Here is") + 1 == context.index("[screen 0:09: opened invoice 4471]")


def test_only_the_latest_screen_notes_are_resent() -> None:
    from voice_agent.conversation import SCREEN_NOTES, Glimpse

    conversation = Conversation(id="t", eyes="haiku-4-5")
    first = conversation.add_user("go")
    for n in range(100):
        conversation.seen.append(Glimpse(first, "0:01", "", (f"change {n}",)))

    notes = [m.content for m in conversation.context if m.content.startswith("[screen")]

    assert notes[0] == f"[screen: {100 - SCREEN_NOTES} earlier changes not shown]"
    assert len(notes) == SCREEN_NOTES + 1 and notes[-1].endswith("change 99]")


async def test_a_conversation_s_screen_readings_are_capped() -> None:
    vision = FakeVision()
    page = Page()
    eyes = Eyes(vision, Conversation(id="t"), page, max_frames=2)
    await eyes.share(True, "browser", "")

    for n in range(4):
        eyes.frame(str(n).encode())
        await asyncio.sleep(0.02)

    assert len(vision.read) == 2
    limits = [f for f in page.frames if f["type"] == "seen" and "limit" in str(f.get("failed"))]
    assert len(limits) == 1


async def test_stopping_the_share_drops_what_was_not_yet_spoken_about() -> None:
    told: list[str | None] = []
    eyes = Eyes(FakeVision(), Conversation(id="t"), Page(), told.append)
    await eyes.share(True, "browser", "")
    await eyes.share(False)

    assert told == [None]


async def test_two_readings_overlap_once_the_first_has_run_a_while(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_agent import eyes as eyes_module

    monkeypatch.setattr(eyes_module, "STAGGER_SECONDS", 0.05)
    vision = FakeVision(delay=0.3)
    eyes = Eyes(vision, Conversation(id="t"), Page())
    await eyes.share(True, "browser", "")

    eyes.frame(b"1")
    eyes.frame(b"2")  # too soon after the first: waits for the stagger
    await asyncio.sleep(0.1)
    assert vision.read == [b"1", b"2"], "the waiting frame started once the stagger passed"

    eyes.frame(b"3")  # two in flight: waits
    eyes.frame(b"4")  # replaces it
    await settle(vision, 3)
    assert vision.read == [b"1", b"2", b"4"] and eyes.replaced == 1
    await eyes.close()


async def test_a_reading_overtaken_by_a_newer_one_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_agent import eyes as eyes_module

    monkeypatch.setattr(eyes_module, "STAGGER_SECONDS", 0.0)
    conversation = Conversation(id="t", eyes="haiku-4-5")
    vision = FakeVision()
    vision.delays = {b"old": 0.2, b"new": 0.02}
    eyes = Eyes(vision, conversation, Page())
    await eyes.share(True, "browser", "")

    eyes.frame(b"old")
    eyes.frame(b"new")
    await settle(vision, 2)

    assert conversation.screen.now == "screen new", "the older screen came back last"
    assert eyes.stale == 1


async def test_the_page_is_asked_to_look_when_the_user_speaks_but_not_too_often() -> None:
    page = Page()
    eyes = Eyes(FakeVision(), Conversation(id="t"), page)

    await eyes.request()  # not sharing: nothing to look at
    await eyes.share(True, "browser", "")
    await eyes.request()
    await eyes.request()  # within the gap

    assert [f["type"] for f in page.frames] == ["eyes", "look"]


async def test_speech_starting_asks_the_eyes_to_look() -> None:
    from tests.test_mic import RecordingChannel
    from voice_agent.events import FloorChanged
    from voice_agent.session import Session

    session = Session(
        RecordingChannel(),  # type: ignore[arg-type]
        Conversation(id="t"),
        FakeLLM(),
        None,
        "system",
        None,
        (),
    )
    asked: list[bool] = []

    async def looking() -> None:
        asked.append(True)

    session.looking = looking
    await session._handle(FloorChanged("speaking"))
    await session._handle(FloorChanged("micro_pause"))

    assert asked == [True]
    await session.close()


async def test_a_change_reported_by_both_overlapping_readings_is_kept_once() -> None:
    conversation = Conversation(id="t", eyes="haiku-4-5")
    noticed: list[str | None] = []
    eyes = Eyes(FakeVision(), conversation, Page(), noticed.append)
    await eyes.share(True, "browser", "")
    twice = Seen(screen="s", app="", events=("filter 'Direct' applied",), doing="", ms=1, model="m")

    await eyes.saw(twice)
    await eyes.saw(twice)

    assert [g.events for g in conversation.seen] == [("filter 'Direct' applied",)]
    assert noticed == ["filter 'Direct' applied"]


async def test_the_agent_s_own_voice_does_not_make_the_eyes_look() -> None:
    from tests.test_mic import RecordingChannel
    from voice_agent.events import FloorChanged
    from voice_agent.session import Session

    session = Session(
        RecordingChannel(),  # type: ignore[arg-type]
        Conversation(id="t"),
        FakeLLM(["one two three"], pace=0.05),
        None,
        "system",
        None,
        (),
    )
    asked: list[bool] = []

    async def looking() -> None:
        asked.append(True)

    session.looking = looking
    await session.submit("hello")
    await asyncio.sleep(0.02)  # the reply is being written
    await session._handle(FloorChanged("speaking"))

    assert asked == []
    await session.close()
