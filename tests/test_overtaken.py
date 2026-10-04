"""The screen overtakes the voice: a reading that lands while the agent is
talking can make the rest of what it is saying wrong. It then stops itself at
the word last heard and says something fresh — and otherwise keeps talking.

Driven by tapes (`tests/tapes/harness.py`), whose browser plays audio at a
speaking pace: only there is "mid-sentence" a real place."""

import asyncio
import re
from pathlib import Path

import pytest

from tests.conftest import FakeLLM
from tests.tapes.harness import Tape, load, parse, run
from voice_agent.conversation import Conversation
from voice_agent.initiative import Initiative

TAPES = Path(__file__).parent / "tapes"


def at(log: str, pattern: str) -> float | None:
    """When the first log line matching `pattern` happened, in tape seconds."""
    for line in log.splitlines():
        if re.search(pattern, line):
            return float(line.split()[0])
    return None


def said(log: str) -> list[str]:
    return [line.split(": ", 1)[1] for line in log.splitlines() if line.startswith("assistant: ")]


def test_a_line_the_screen_made_wrong_stops_where_it_was_heard_and_starts_afresh() -> None:
    log = run(load(TAPES / "screen_overtakes.tape"))

    seen, cut, fresh = at(log, "< +seen"), at(log, "> +interrupt"), at(log, "Riga now")
    assert seen == 6.0
    assert cut is not None and cut < 8.0, "still talking about Tallinn after the screen said Riga"
    assert fresh is not None and fresh < 10.0, "the new line came only after the stale one ended"
    stale = said(log)[1]
    assert "Tallinn" in stale and "before eleven" not in stale, "keeps only what was heard"


def test_a_change_that_leaves_the_rest_true_is_talked_through() -> None:
    log = run(load(TAPES / "screen_keeps.tape"))

    assert at(log, "> +overtaken") is not None, "the change was never weighed"
    assert at(log, "> +interrupt") is None
    assert said(log)[1].endswith("before eleven.")


def test_the_last_words_of_a_line_are_not_weighed() -> None:
    tape: Tape = parse(
        (TAPES / "screen_overtakes.tape").read_text().replace("6.0 seen", "11.2 seen"), "late"
    )

    log = run(tape)

    assert at(log, "> +overtaken") is None, "a line about to end is not worth a call"
    assert at(log, "> +interrupt") is None


async def test_after_stopping_itself_it_considers_the_screen_at_once() -> None:
    spoken: list[str] = []

    async def speak(line: str, rung: int) -> None:
        spoken.append(line)

    async def report(payload: dict[str, object]) -> None:
        return None

    llm = FakeLLM(["You chose the Riga one.", "Riga now — why Riga?"])
    initiative = Initiative(
        llm, "system", Conversation(id="t"), lambda: 10.0, speak, report, ladder=()
    )
    initiative.notice("opened the Tallinn flight")
    await initiative.tick()  # a screen line now; the next waits out the gap

    initiative.overtaken("From field changed from Tallinn to Riga")
    await initiative.tick()

    assert spoken == ["You chose the Riga one.", "Riga now — why Riga?"]
    assert "stopped yourself" in llm.seen[-1][-1].content


async def test_the_check_runs_on_its_own_engine_not_the_inner_voice() -> None:
    from tests.test_mic import RecordingChannel
    from voice_agent.heard import Spoken
    from voice_agent.session import Session

    checker, thinker = FakeLLM(["STOP"]), FakeLLM()
    session = Session(
        RecordingChannel(),  # type: ignore[arg-type]
        Conversation(id="t"),
        FakeLLM(),
        None,
        "system",
        None,
        (),
        thinker=thinker,
        checker=checker,
    )
    voice = Spoken()
    voice.chars = list("You are flying from Tallinn, so the morning airBaltic is the pick.")
    await session._weigh_voice("From field changed from Tallinn to Riga", voice)

    assert "Still to say" in checker.seen[0][-1].content
    assert thinker.seen == []
    await session.close()


def test_the_check_runs_on_deepseek_with_thinking_off_and_on_sonnet_without_its_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_agent.server import overtaken_engine

    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    engine = overtaken_engine()
    assert engine is not None and (engine.provider, engine.model) == ("deepseek", "deepseek-flash")

    monkeypatch.delenv("DEEPSEEK_API_KEY")
    engine = overtaken_engine()
    assert engine is not None and engine.model == "claude-sonnet-5-5"

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert overtaken_engine() is None


async def test_a_stop_that_was_never_spoken_about_does_not_rush_the_next_change() -> None:
    spoken: list[str] = []

    async def speak(line: str, rung: int) -> None:
        spoken.append(line)

    async def report(payload: dict[str, object]) -> None:
        return None

    initiative = Initiative(
        FakeLLM(["Riga now?"]),
        "system",
        Conversation(id="t"),
        lambda: 1.0,
        speak,
        report,
        ladder=(),
    )
    initiative.overtaken("From field changed from Tallinn to Riga")
    initiative.reset()  # they spoke before it could be said
    initiative.notice("typed in the To field")  # an ordinary change, mid-task

    await initiative.tick()  # 1 s of quiet: short of the pause a screen line waits for

    assert spoken == []


async def test_closing_while_weighing_starts_nothing_more() -> None:
    from tests.test_mic import RecordingChannel
    from voice_agent import timing
    from voice_agent.heard import Spoken
    from voice_agent.session import Session

    checker = FakeLLM(["GO"], delay=0.2)
    session = Session(
        RecordingChannel(),  # type: ignore[arg-type]
        Conversation(id="t"),
        FakeLLM(),
        None,
        "system",
        None,
        (),
        checker=checker,
    )
    voice = Spoken(started_at=timing.now(), sent_bytes=48_000 * 30)
    voice.chars = list("You are flying from Tallinn, so the morning airBaltic is the pick.")
    session._voice = voice
    session._weigh("From field changed")
    session._weigh("Dropdown closed")  # waits for the first

    await asyncio.sleep(0.05)
    await session.close()
    await asyncio.sleep(0.4)

    assert len(checker.seen) == 1, "a check started after the session closed"
