"""Teaching: the apprentice becomes the tutor, watches against the map, steps
in at once, and reports what was learned."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeLLM
from tests.test_mic import RecordingChannel
from tests.test_workmap import MAP, captured, receive_until
from voice_agent import initiative as initiative_module
from voice_agent.conversation import Conversation, Glimpse
from voice_agent.initiative import Initiative, marked_step
from voice_agent.server import create_app
from voice_agent.session import Session
from voice_agent.sessions import SessionStore
from voice_agent.teach import HANDOVER, Tutor, assess, check

REPORT: dict[str, Any] = {
    "headline": "Nearly ready: one rule to remember.",
    "mastered": [{"step": "1. Filter to direct", "note": "Went straight to direct flights."}],
    "caught": [
        {
            "step": "2. Pick the flight",
            "what": "Reached for a two-stop fare",
            "rule": "One stop at most",
            "quote": "he hates layovers",
        }
    ],
    "practice": ["Check the journey time before the price."],
    "spoken": "You did well. Watch the stops.",
}


def test_a_step_marker_is_taken_off_and_named() -> None:
    assert marked_step("[step 4] Stop — that's two stops.") == (4, "Stop — that's two stops.")
    assert marked_step("No marker here.") == (None, "No marker here.")


async def test_becoming_the_tutor_changes_every_call_and_stops_the_inner_voice() -> None:
    thinker = FakeLLM()
    llm = FakeLLM(["Sure."])
    session = Session(
        RecordingChannel(),  # type: ignore[arg-type]
        Conversation(id="t"),
        llm,
        None,
        "apprentice prompt",
        None,
        (),
        role=None,
        thinker=thinker,
    )

    await session.become("tutor prompt")
    await session.submit("hello", typed=True)
    await asyncio.sleep(0.05)

    assert llm.systems[-1] == "tutor prompt"
    assert session._speculator._system == "tutor prompt"
    assert session._initiative.teaching is True
    await session.close()


class Clock:
    def __init__(self, quiet: float | None) -> None:
        self.quiet = quiet
        self.spoken: list[str] = []
        self.reports: list[dict[str, object]] = []

    def __call__(self) -> float | None:
        return self.quiet

    async def speak(self, line: str, rung: int) -> None:
        self.spoken.append(line)

    async def report(self, payload: dict[str, object]) -> None:
        self.reports.append(payload)


async def test_the_tutor_steps_in_at_once_and_opens_the_expert_s_step() -> None:
    llm = FakeLLM(["[step 2] Stop — that's two stops. What did the expert say?"])
    clock = Clock(quiet=0.0)  # mid-action: no pause at all
    initiative = Initiative(
        llm, "tutor", Conversation(id="t"), clock, clock.speak, clock.report, ladder=()
    )
    initiative.become("tutor")

    initiative.notice("selected a 2-stop flight, 31 h")
    await initiative.tick()

    assert clock.spoken == ["Stop — that's two stops. What did the expert say?"]
    assert {"type": "map_focus", "n": 2} in clock.reports
    assert "Check it against the Work Map" in llm.seen[0][-1].content


async def test_the_tutor_watches_more_often_than_the_apprentice() -> None:
    assert initiative_module.TEACH_GAP_SECONDS < initiative_module.SCREEN_GAP_SECONDS
    assert initiative_module.TEACH_QUIET_SECONDS < initiative_module.SCREEN_QUIET_SECONDS


def teaching() -> Conversation:
    conversation = captured()
    conversation.work_map = MAP
    conversation.phase = "teach"
    conversation.teach_from = len(conversation.messages)
    conversation.teach_seen_from = len(conversation.seen)
    said = conversation.add_user("Now Tallinn to Berlin, I'll take this two-stop one.")
    conversation.seen.append(Glimpse(said, "3:01", "Flights", ("selected 2-stop fare",), id="g2"))
    return conversation


async def test_the_report_reads_only_the_teaching() -> None:
    llm = FakeLLM([json.dumps(REPORT)])

    result = await assess(llm, teaching())

    assert result["status"] == "done"
    assert result["report"]["caught"][0]["quote"] == "he hates layovers"
    asked = llm.seen[0][0].content
    assert "Tallinn to Berlin" in asked and "selected 2-stop fare" in asked
    assert "I filter to direct" not in asked.split("<teaching>")[1], "capture is not re-judged"


def test_a_report_keeps_only_well_formed_items() -> None:
    report = check({"mastered": [{"step": "1"}, "junk", {}], "practice": ["x", ""], "headline": 3})

    assert report["mastered"] == [{"step": "1", "note": ""}] and report["practice"] == ["x"]


async def test_a_bad_report_fails_cleanly() -> None:
    assert (await assess(FakeLLM(["no report"]), teaching()))["status"] == "failed"


class Page:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.said: list[str] = []
        self.became = 0

    async def report(self, payload: dict[str, object]) -> None:
        self.frames.append(payload)

    async def announce(self, line: str, interrupt: bool = False) -> None:
        self.said.append(line)

    async def become(self) -> None:
        self.became += 1


async def test_teaching_starts_from_a_final_map_and_ends_with_a_report() -> None:
    conversation, page = captured(), Page()
    conversation.work_map, conversation.phase = MAP, "mapped"
    tutor = Tutor(
        conversation,
        lambda: FakeLLM([json.dumps(REPORT)]),
        "Fake",
        page.report,
        page.announce,
        page.become,
    )

    await tutor.start()
    assert conversation.phase == "teach" and page.became == 1 and page.said == [HANDOVER]

    await tutor.finish()
    await asyncio.wait_for(tutor._task, 2)  # type: ignore[arg-type]

    assert conversation.phase == "taught" and conversation.mastery is not None
    assert [f["type"] for f in page.frames] == ["teaching", "assessing", "mastery"]
    assert page.said[-1] == REPORT["spoken"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tests.test_eyes import FakeVision

    creator = FakeLLM([json.dumps(MAP), json.dumps(REPORT)])
    app = create_app(
        llm=FakeLLM(["Sure."]),
        store=SessionStore(),
        voice=False,
        ears=False,
        vision=FakeVision(),
        judge=creator,
        sessions_dir=tmp_path,
        role="ai_apprentice",
    )
    return TestClient(app)


def test_capture_map_teach_report_over_the_socket(client: TestClient) -> None:
    key = client.get("/", follow_redirects=False).headers["location"].removeprefix("/c/")
    with client.websocket_connect(f"/ws/{key}") as socket:
        receive_until(socket, "greeting")
        socket.send_text(json.dumps({"type": "phase", "to": "map"}))
        receive_until(socket, "work_map")
        socket.send_text(json.dumps({"type": "phase", "to": "done"}))
        receive_until(socket, "mapped")

        socket.send_text(json.dumps({"type": "phase", "to": "teach"}))
        receive_until(socket, "teaching")
        assert HANDOVER in str(receive_until(socket, "reply_end").get("text"))

        socket.send_text(json.dumps({"type": "phase", "to": "finish"}))
        assert receive_until(socket, "assessing")["expected_s"] == 20
        report = receive_until(socket, "mastery")
        assert report["caught"][0]["rule"] == "One stop at most"

    with client.websocket_connect(f"/ws/{key}") as socket:
        ready = receive_until(socket, "ready")
        assert ready["phase"] == "taught" and ready["mastery"]["headline"] == REPORT["headline"]


def test_the_tutor_sees_where_the_expert_ends_and_the_new_hire_begins() -> None:
    conversation = teaching()
    conversation.eyes = "sonnet-5-5"

    lines = [m.content for m in conversation.context]
    boundary = next(i for i, line in enumerate(lines) if line.startswith("[teaching starts here"))

    assert lines.index("[screen 0:12 Flights: filter 'Direct']") < boundary, "the expert's moment"
    assert boundary < lines.index("[screen 3:01 Flights: selected 2-stop fare]"), "the new hire's"
    assert boundary < lines.index("Now Tallinn to Berlin, I'll take this two-stop one.")


def test_a_new_hire_s_first_screen_change_is_theirs_even_before_anyone_speaks() -> None:
    conversation = captured()
    conversation.eyes = "sonnet-5-5"
    conversation.phase, conversation.work_map = "teach", MAP
    conversation.teach_from = len(conversation.messages)
    conversation.teach_seen_from = len(conversation.seen)
    last = conversation.messages[-1]  # the expert's last words, before any handover
    conversation.seen.append(Glimpse(last, "3:00", "Flights", ("opened search",), id="g2"))

    lines = [m.content for m in conversation.context]
    boundary = lines.index(next(x for x in lines if x.startswith("[teaching starts here")))

    assert lines.index("[screen 0:12 Flights: filter 'Direct']") < boundary
    assert boundary < lines.index("[screen 3:00 Flights: opened search]")


def test_a_clicked_step_quotes_the_expert_to_the_new_hire() -> None:
    from voice_agent.workmap import spoken_step

    step = {
        "n": 1,
        "title": "Filter",
        "decision": "Direct only",
        "reason": {"quote": "no layovers"},
    }

    assert "The expert said: “no layovers”" in spoken_step(step, teaching=True)
    assert "In your words" in spoken_step(step)
    assert "expert never said why" in spoken_step({**step, "reason": None}, teaching=True)
