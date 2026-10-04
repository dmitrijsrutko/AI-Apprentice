# AI-Apprentice

An AI apprentice that learns screen work from an expert while they do it, then
teaches it to the next person. It is built one chapter at a time.

Experienced people carry judgment that was never written down: why an invoice
is moved to another cost center, why one supplier's bill is held, when to stop
and ask someone. A screen recording shows *what* they clicked but not *why*.
The apprentice watches the expert's screen, asks short spoken questions at
natural pauses, runs a debrief until it understands, and turns that into a
workflow a new hire can learn from.

The direction comes from the Hack-Nation × ElevenLabs challenge brief,
[docs/AI-Apprentice/AI-Apprentice.pdf](docs/AI-Apprentice/AI-Apprentice.pdf). It is
the aspiration, not a spec:

1. **Capture**: the expert shares their screen, and a voice agent asks *why* at the right moments.
2. **Map**: a spoken debrief and teach-back produce a clickable Work Map of steps, decisions, reasons and guardrails.
3. **Teach**: a voice tutor coaches a new hire on their own screen and steps in before a guardrail is broken.

## Where this stands today

Built in **chapters**, each one deliberate piece on top of a working, tested
previous one. [CHANGELOG.md](CHANGELOG.md) has the reasoning.

- **0 — The empty page, again.** A new project with a fresh history, standing on an inherited voice pipeline. The brief is added as context, and the working contract ([AGENTS.md](AGENTS.md)) is kept.
- **1 — The apprentice, by ear.** A new default role, **AI apprentice**: the expert talks it through a task; it asks why, finds the limits and exceptions, and explains the process back until they confirm. There are no eyes yet. The default model is DeepSeek V4.1 Flash, smartest.
- **2 — Eyes.** Share a screen, window or tab at any time (🖥 share). Changed frames are read by Claude (Sonnet 5.5 by default, which reads small dates and digits; Haiku 4.5 as the cheaper choice; about 2.5–3.5 s each), and what it sees joins the conversation. The apprentice knows whether it can see, may ask about what you just did at a short pause, and the **eyes** toggle shows every reading. Frames are not stored (until Chapter 4's map screenshots).
- **3 — Live eyes.** The screen is read the moment you start speaking, readings take 1.1–2.4 s (shorter output, 1024 px frames), two run overlapped with the newest result winning, replies stay around 25 words, and an unprompted line about a screen that has since changed is dropped and reconsidered.
- **4 — The Work Map.** A flow bar, Capture ▸ Map ▸ Teach. **Finish capture** draws the map from the conversation (~25–30 s, DeepSeek or Claude Opus as "Map creator"). Its steps link to stored screenshots and quote the expert word for word, with code marking any quote it cannot find. The apprentice explains the map back, says any step you click, asks the open questions, and redraws the map from your corrections.
- **5 — Teach.** **Start teaching** turns the apprentice into the tutor on the same page. A new hire works a new case on a shared screen; the tutor asks them to predict at judgment calls and, the moment it sees one of the expert's guardrails broken, stops them, opens that step with the expert's screenshot, and explains in the expert's words (decided in about 1 s once seen). **Finish teaching** writes a report of what was mastered, what was caught and what to practise, as a card and aloud. Interventions need the microphone listening.
- **6 — Sharper eyes.** A small change is sent twice: the whole screen (1024 px, JPEG 0.85) for context, and a close-up of what changed at the screen's own resolution (PNG), which values are read from (🔍 in the eyes view). On a flight page with an itinerary opened, Haiku went from 2–6 of 10 values with "16 Oct" misread as "18 Oct" to 10 of 10 with no misreads. Sonnet read 10 of 10 either way, at +0–0.5 s and +270–960 tokens per reading ([docs/eyes-eval.md](docs/eyes-eval.md)). A reply to a question waits up to 1.5 s for the reading of the screen as it was when you started speaking, and a guessed reply made before that reading is dropped.

- **7 — The screen overtakes the voice.** If the screen changes while the agent is talking and the rest of its line is now wrong (you switched Tallinn to Riga), it stops itself at the word you last heard and says something fresh about the screen as it is now (~0.8 s to decide, on DeepSeek V4.1 Flash with thinking off; Sonnet 5.5 without a DeepSeek key). A scroll or a page load is talked through. On the live traces half of all lines had a screen change land mid-speech.

**Known limitation.** Screenshots go to Anthropic as they are. The model masks personal data in what it writes, not in the image, and the screenshots kept for the Work Map are not blurred (`--purge-sessions` deletes them).

### The inherited foundation

The code starts from an earlier conversational voice agent. It provides a
streaming, cascaded pipeline in which each vendor sits behind a narrow,
swappable interface. Speech recognition is AssemblyAI or ElevenLabs Scribe v2
Realtime. The reasoning models are Claude or DeepSeek, and the voice is
ElevenLabs. Around that sit a voice detector that tracks who holds the floor,
barge-in, an inner voice that decides whether something is worth saying, and
per-conversation records and traces. It is a starting point. Later chapters
will reshape it.

## Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- Optional: node ≥ 22, to run the page's own tests (`uv run verify` skips them without it, and says so)

## Setup and usage

```bash
uv sync                  # install, including dev tools
cp .env.example .env     # fill in only the keys you need
uv run voice-agent       # serve on :8000 (the command keeps its inherited name for now)
uv run voice-agent --help
```

Open <http://127.0.0.1:8000>, press **start conversation**, and talk or type.

## Development

```bash
uv run verify     # ruff + format check + mypy (strict) + pytest (+ node tests)
```

`uv run verify` is the single gate: run it before committing.

## Project layout

```
AGENTS.md, CHANGELOG.md   the working contract; the chapter log
docs/AI-Apprentice/        the challenge brief (aspirational context)
docs/DEPLOY.md            deployment runbook (inherited; to be adapted)
docs/eyes-eval.md         how well the eyes read small text, per way of sending the screen
docs/vendor/              vendor references: AssemblyAI, ElevenLabs
prompts/                  system prompt, rules, role cards, inner voice, judge
src/voice_agent/          the inherited pipeline: server, session, turn-taking,
                          llm/ stt/ tts/ adapters, web/ (the browser page)
  eyes.py                 the shared screen: one frame read at a time, the newest waiting
  overtaken.py            a change mid-speech: is the rest of the line still right?
  llm/vision.py           Claude reads a frame: what is on screen, what changed
  web/eyes.js             the screen picker and which frames are worth sending
prompts/vision.md, eyes_nudge.md  what the eyes report; when what they saw is worth a word
  workmap.py              the Work Map: drawn from the conversation, checked, redrawn, spoken
  web/workmap.js          the map card and its wait
prompts/workmap.md        what the map creator is asked for
  teach.py                teaching: the tutor's part, starting and finishing, the report
prompts/tutor.md, tutor_nudge.md, teach_report.md  the tutor, what it checks on screen, the report
tests/                    pytest; tests/web/ node tests; tests/tapes/ replayed conversations
scripts/                  operational helpers; eyes_eval.py (+ eyes_eval/) measures the eyes
```

## Deployment

AI-Apprentice runs as its own instance, separate from the earlier project:
**<https://ai-apprentice.fly.dev>**. It is one always-on Fly.io machine in `iad`
with one volume. [docs/DEPLOY.md](docs/DEPLOY.md) is the runbook and `fly.toml`
the configuration.

## Roadmap

Each chapter is decided in conversation. The brief sets the direction: get
screen events and voice into one conversation first, then the debrief and Work
Map, then the tutor.

## License

MIT
