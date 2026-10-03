"""The Work Map: drawn from the conversation, checked in code, redrawn after
corrections, and said step by step."""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeLLM
from voice_agent import workmap
from voice_agent.conversation import Conversation, Glimpse
from voice_agent.server import create_app
from voice_agent.sessions import SessionStore
from voice_agent.workmap import Mapper, check, draw, script, spoken_step

MAP: dict[str, Any] = {
    "title": "Book the flight",
    "summary": "Direct, on time.",
    "teach_back": "First you filter to direct flights. Is that how it works?",
    "steps": [
        {
            "title": "Filter to direct",
            "screen": "g1",
            "decision": "Direct only",
            "reason": {"quote": "he hates layovers", "said": "m2"},
            "guardrails": [
                {
                    "rule": "Over 500 euros: ask him",
                    "kind": "escalate",
                    "quote": "ask him",
                    "said": "m2",
                }
            ],
            "judgment": True,
        },
        {
            "title": "Pick the 9:00",
            "screen": "g9",
            "decision": "The 9:00",
            "reason": {"quote": "words nobody said", "said": "m2"},
            "guardrails": [],
            "judgment": False,
        },
    ],
    "gaps": ["What if no direct flight exists?"],
}


def captured() -> Conversation:
    conversation = Conversation(id="t")
    conversation.add_assistant("Walk me through it.")
    said = conversation.add_user("I filter to direct, he hates layovers. Over 500, I ask him.")
    conversation.seen.append(
        Glimpse(said, "0:12", "Flights", ("filter 'Direct'",), id="g1", frame=True)
    )
    return conversation


def test_the_creator_reads_numbered_messages_and_moments() -> None:
    text, by_id = script(captured())

    assert text.splitlines() == [
        'm1 APPRENTICE: "Walk me through it."',
        'm2 EXPERT: "I filter to direct, he hates layovers. Over 500, I ask him."',
        "[g1 0:12 Flights: filter 'Direct']",
    ]
    assert set(by_id) == {"m1", "m2"}


def test_the_map_is_checked_against_what_was_said_and_seen() -> None:
    conversation = captured()
    _, by_id = script(conversation)

    checked = check(MAP, conversation, by_id)
    first, second = checked["steps"]

    assert first["reason"]["verified"] is True and first["frame"] and first["at"] == "0:12"
    assert first["guardrails"][0]["kind"] == "escalate"
    assert second["reason"]["verified"] is False, "a quote the expert never said is marked"
    assert second["screen"] == "" and not second["frame"], "an unknown moment is dropped"


def test_a_map_with_no_steps_is_not_a_map() -> None:
    with pytest.raises(ValueError):
        check({"title": "x", "steps": []}, captured(), {})


async def test_a_good_reply_becomes_a_map_and_a_bad_one_a_failure() -> None:
    done = await draw(FakeLLM([json.dumps(MAP)]), captured())
    failed = await draw(FakeLLM(["no map here"]), captured())

    assert done["status"] == "done" and len(done["map"]["steps"]) == 2
    assert failed["status"] == "failed"


def test_a_clicked_step_is_said_in_the_expert_s_words() -> None:
    step = check(MAP, captured(), script(captured())[1])["steps"][0]

    assert (
        spoken_step(step)
        == "Step one, Filter to direct: Direct only. In your words: “he hates layovers”"
    )
    assert "don't know why" in spoken_step({**step, "reason": None})


class Page:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.said: list[str] = []
        self.cut: list[str] = []

    async def report(self, payload: dict[str, object]) -> None:
        self.frames.append(payload)

    async def announce(self, line: str, interrupt: bool = False) -> None:
        self.said.append(line)
        if interrupt:
            self.cut.append(line)


def mapper_for(conversation: Conversation, llm: FakeLLM, page: Page) -> Mapper:
    return Mapper(conversation, lambda: llm, "Fake creator", page.report, page.announce)


async def test_finishing_capture_draws_the_map_and_says_it() -> None:
    conversation, page = captured(), Page()
    mapper = mapper_for(conversation, FakeLLM([json.dumps(MAP)]), page)

    await mapper.begin()
    assert conversation.phase == "mapping"
    await asyncio.wait_for(mapper._task, 2)  # type: ignore[arg-type]

    assert page.cut == [page.said[0]], "finishing capture cuts off what it was saying"
    assert conversation.phase == "map" and conversation.map_version == 1
    assert [f["type"] for f in page.frames] == ["mapping", "work_map"]
    assert page.said[-1] == MAP["teach_back"]
    assert conversation.map_note.startswith("[work map v1")
    assert any(m.content.startswith("[work map") for m in conversation.context)


async def test_corrections_redraw_the_map_once_they_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workmap, "REDRAW_AFTER_SECONDS", 0.05)
    conversation, page = captured(), Page()
    llm = FakeLLM([json.dumps(MAP)])
    mapper = mapper_for(conversation, llm, page)
    await mapper.begin()
    await asyncio.wait_for(mapper._task, 2)  # type: ignore[arg-type]

    mapper.corrected()
    mapper.corrected()  # two turns in a row: one redraw
    await asyncio.sleep(0.2)

    assert len(llm.seen) == 2 and conversation.map_version == 2
    assert "Draw it again with their corrections applied" in llm.seen[1][0].content
    assert page.said.count(MAP["teach_back"]) == 1, "a redraw is not said again"


async def test_a_failed_first_map_returns_to_capture() -> None:
    conversation, page = captured(), Page()
    mapper = mapper_for(conversation, FakeLLM(["nope"]), page)

    await mapper.begin()
    await asyncio.wait_for(mapper._task, 2)  # type: ignore[arg-type]

    assert conversation.phase == "capture"
    assert page.frames[-1]["type"] == "map_failed"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tests.test_eyes import FakeVision

    app = create_app(
        llm=FakeLLM(["Sure."]),
        store=SessionStore(),
        voice=False,
        ears=False,
        vision=FakeVision(),
        judge=FakeLLM([json.dumps(MAP)]),
        sessions_dir=tmp_path,
        role="ai_apprentice",
    )
    return TestClient(app)


def receive_until(socket: Any, kind: str) -> dict[str, Any]:
    while (frame := socket.receive_json())["type"] != kind:
        pass
    return dict(frame)


def test_capture_to_map_over_the_socket_with_its_screenshot(client: TestClient) -> None:
    key = client.get("/", follow_redirects=False).headers["location"].removeprefix("/c/")
    with client.websocket_connect(f"/ws/{key}") as socket:
        ready = receive_until(socket, "ready")
        assert ready["mapped"] is True and ready["phase"] == "capture"
        receive_until(socket, "greeting")
        socket.send_text(json.dumps({"type": "share", "active": True, "surface": "browser"}))
        jpeg = base64.b64encode(b"a frame").decode()
        socket.send_text(json.dumps({"type": "frame", "jpeg": jpeg}))
        seen = receive_until(socket, "seen")
        assert seen["id"] == "g1" and seen["frame"] is True

        socket.send_text(json.dumps({"type": "phase", "to": "map"}))
        assert receive_until(socket, "mapping")["expected_s"] == workmap.EXPECTED_S
        drawn = receive_until(socket, "work_map")
        assert drawn["version"] == 1 and drawn["map"]["steps"][0]["frame"] is True

        socket.send_text(json.dumps({"type": "map_step", "n": 1}))
        while "Step one" not in str(receive_until(socket, "reply_end").get("text")):
            pass  # the teach-back may still be ending first

    assert client.get(f"/c/{key}/frames/g1.jpg").content == b"a frame"
    assert client.get(f"/c/{key}/frames/../x.jpg").status_code == 404
    assert client.get(f"/c/{key}/frames/g999.jpg").status_code == 404
    assert client.get("/c/nobody/frames/g1.jpg").status_code == 404


async def test_a_drawing_cut_off_by_the_socket_offers_itself_again() -> None:
    conversation, page = captured(), Page()
    mapper = mapper_for(conversation, FakeLLM([json.dumps(MAP)], delay=1.0), page)
    await mapper.begin()

    await mapper.close()

    assert conversation.phase == "capture", "not stuck at 'drawing the map…'"


async def test_what_is_said_while_the_first_map_is_drawn_is_drawn_too() -> None:
    conversation, page = captured(), Page()
    llm = FakeLLM([json.dumps(MAP)], delay=0.05)
    mapper = mapper_for(conversation, llm, page)
    await mapper.begin()

    mapper.corrected()  # said while the bar fills
    await asyncio.sleep(0.3)

    assert len(llm.seen) == 2 and conversation.map_version == 2


async def test_the_clock_is_quiet_while_the_map_is_drawn() -> None:
    from tests.test_mic import RecordingChannel
    from voice_agent.session import Session

    conversation = captured()
    session = Session(
        RecordingChannel(),  # type: ignore[arg-type]
        conversation,
        FakeLLM(),
        None,
        "system",
        None,
        (),
    )
    conversation.phase = "mapping"

    assert session.quiet_for() is None
    await session.close()


def test_a_clicked_step_says_only_the_first_sentence_of_its_decision() -> None:
    step = {"n": 2, "title": "Pick", "decision": "Take the 9:00. It lands in time.", "reason": None}

    assert spoken_step(step).startswith("Step two, Pick: Take the 9:00.")
    assert "lands in time" not in spoken_step(step)
