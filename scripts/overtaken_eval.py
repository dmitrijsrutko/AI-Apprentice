"""How well the self-cut check tells a line the screen made wrong from one it
did not.

    uv run --env-file .env python scripts/overtaken_eval.py [--runs 3] [--engine PROVIDER:MODEL]

Paid: cases x runs tiny calls on the production engine (`server.overtaken_engine`,
DeepSeek V4.1 Flash with thinking off; Sonnet 5.5 without a DeepSeek key).
The cases are taken from live sessions, anonymised: what the agent had said,
what it was still to say, what the screen changed to, and — for a reply —
what the user had just said. A wrong STOP cuts a true line mid-sentence, which
is the costlier mistake. Writes `docs/overtaken-eval.md` for the production
engine; `--engine` tries another (thinking off, as production runs) and only
prints, so a candidate never overwrites the record of what ships.
"""

import argparse
import asyncio
import inspect
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from voice_agent import overtaken
from voice_agent.llm import create_llm
from voice_agent.llm.base import Usage
from voice_agent.server import overtaken_engine


@dataclass(frozen=True)
class Case:
    name: str
    want: str  # "STOP" or "GO"
    said: str
    rest: str
    seen: str
    screen: str
    asked: str = ""


CASES = (
    Case(
        "From changed to Riga, line says Tallinn",
        "STOP",
        "Got it — though the From field",
        "still says Tallinn, with a list of nearby airports open under it. Do you want to fly "
        "from Tallinn?",
        "From field changed from 'Tallinn (TLL)' to 'Riga International (RIX)'; dropdown closed",
        "Flights search: From Riga (RIX), To empty",
    ),
    Case(
        "Filter re-ticked, line asks to put it back",
        "STOP",
        "That's the one. But the breakfast filter",
        "got closed in the process — do you want it back on before sorting?",
        "Breakfast included filter ticked again",
        "Hotels in Hong Kong, filters: Breakfast included, Very good 8+",
    ),
    Case(
        "Date changed, line states the old date",
        "STOP",
        "Riga to Hong Kong, departing the sixteenth",
        "of October, and the return on the first of November. What decided those dates?",
        "Departure date changed from 16 Oct to 23 Oct",
        "Flights RIX-HKG, 23 Oct - 1 Nov, 1 adult",
    ),
    Case(
        "Scroll in the airline list",
        "GO",
        "Yes — outbound through Helsinki, and the return",
        "overnight into Riga at 08:35. Was Finnair via Helsinki what you were after?",
        "Scrolled down airline list; new options visible: Tickets.ae 1,700€ at bottom",
        "Finnair itinerary RIX-HEL-HKG open, return HKG-HEL-RIX 08:35",
    ),
    Case(
        "Page finished loading",
        "GO",
        "Flights it is — I can see Tallinn",
        "in the From field and the destination still empty. Where are you headed?",
        "Page fully loaded; flight search form visible with all fields populated as before",
        "Flights search: From Tallinn, To empty",
    ),
    Case(
        "Skyscanner opened while asking about Skyscanner",
        "GO",
        "Skyscanner for",
        "the flights — is that your usual place to check, or just this once?",
        "Opened skyscanner.net (page still loading) from search results; Skyscanner flights "
        "home page shown",
        "Skyscanner flights home page: Return, From Tallinn (TLL), To empty",
    ),
    Case(
        "Read-back of the dates the user just said, return date set",
        "GO",
        "Got",
        "it — out on the sixteenth, back on the first. Was that sixteenth fixed for you, or did "
        "you pick it off the price colours?",
        "Calendar closed; Return date now 01/11/2026; Travellers and cabin dropdown opened, "
        "Apply button shown",
        "Flights RIX-HKG, Depart 16/10/2026, Return 01/11/2026, travellers dropdown open",
        asked="On 16th of October, going back on November 1st.",
    ),
    Case(
        "Why-question about Riga while typing the destination",
        "GO",
        "You switched the departure to Riga —",
        "was it cheaper from there, or is Tallinn not working for these dates?",
        "To field focused; typed 'hong'; suggestions show Hong Kong (HKG)",
        "Flights: From Riga (RIX), To 'hong' with suggestions, dates not set",
    ),
    Case(
        "Hover over a date in the calendar",
        "GO",
        "The sixteenth is in red there —",
        "the most expensive day that week. Is the date fixed, or could you go a day later?",
        "Mouse pointer now hovering over Oct 17 in the calendar; no date selected yet",
        "Calendar open, October and November; 16 Oct red, 17 Oct green",
    ),
    Case(
        "Dropdown opened after choosing one stop",
        "GO",
        "One stop only, then —",
        "is that about the length of the journey, or have two-stop routes gone wrong before?",
        "Sort dropdown opened (Best, Cheapest, Fastest)",
        "Results RIX-HKG, filter: 1 stop, 63 of 745 results, sort dropdown open",
    ),
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--label", default="")
    parser.add_argument(
        "--engine",
        help="PROVIDER:MODEL[:EFFORT] instead of the production engine (effort: off)",
    )
    args = parser.parse_args()
    if args.engine:
        provider, _, rest = args.engine.partition(":")
        model, _, effort = rest.partition(":")
        engine = create_llm(
            provider, model or None, effort or "off", max_tokens=overtaken.MAX_TOKENS
        )
    else:
        engine = overtaken_engine()
    assert engine is not None, "no key for any check engine"
    await engine.connect()
    takes_asked = "asked" in inspect.signature(overtaken.question).parameters
    rows: list[str] = []
    ms: list[float] = []
    tokens_out: list[int] = []
    right = total = wrong_stop = wrong_go = 0
    for case in CASES:
        got: list[str] = []
        for _ in range(args.runs):
            extra = {"asked": case.asked} if takes_asked else {}
            ask = overtaken.question(case.said, case.rest, case.seen, case.screen, **extra)
            usage = Usage()
            started = time.monotonic()
            verdict = await overtaken.stale(engine, ask, usage)
            ms.append((time.monotonic() - started) * 1000)
            tokens_out.append(usage.output_tokens)
            stop = verdict[0] if isinstance(verdict, tuple) else verdict
            got.append("STOP" if stop else "GO")
        hits = got.count(case.want)
        right += hits
        total += len(got)
        if case.want == "GO":
            wrong_stop += len(got) - hits
        else:
            wrong_go += len(got) - hits
        rows.append(f"| {case.name} | {case.want} | {' '.join(got)} | {hits}/{len(got)} |")
    summary = (
        f"{right}/{total} right · wrong STOP {wrong_stop} · wrong GO {wrong_go} · "
        f"median {statistics.median(ms):.0f} ms · p90 {statistics.quantiles(ms, n=10)[-1]:.0f} ms"
        f" · max {max(tokens_out)} tokens out of {overtaken.MAX_TOKENS} · {engine.model}"
    )
    table = "\n".join(["| Case | Want | Got | Right |", "|---|---|---|---|", *rows])
    print(table)
    print(summary)
    if args.engine:
        return
    write(
        Path(__file__).parent.parent / "docs" / "overtaken-eval.md",
        "# The self-cut check's eval\n\n"
        "`uv run --env-file .env python scripts/overtaken_eval.py`: anonymised cases from live "
        "sessions, each run through the real check on the production engine. A wrong STOP "
        f"cuts a true line mid-sentence.\n\n{args.label}\n\n{table}\n\n**{summary}**\n",
    )


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
