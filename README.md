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
docs/vendor/              vendor references: AssemblyAI, ElevenLabs
prompts/                  system prompt, rules, role cards, inner voice, judge
src/voice_agent/          the inherited pipeline: server, session, turn-taking,
                          llm/ stt/ tts/ adapters, web/ (the browser page)
tests/                    pytest; tests/web/ node tests; tests/tapes/ replayed conversations
scripts/                  operational helpers
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
