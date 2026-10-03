# Changelog

This project is built up in **chapters**, each adding one deliberate piece of
functionality on top of a working, fully-tested previous chapter. At every
point in this history the code runs and its tests pass — it is simply not the
whole apprentice yet.

Guiding idea: **an apprentice, not a recorder.** It watches an expert work,
asks *why* at the right moment, maps the workflow with its guardrails, and
teaches it to the next person. The challenge brief that sets the direction is
[docs/AI-Apprentice/AI-Apprentice.pdf](docs/AI-Apprentice/AI-Apprentice.pdf): it is
the aspiration, not a spec.

Newest chapter first. Each entry says *why* the chapter was the right next
step — the diff already says what changed. The chapter entry format is
specified in [AGENTS.md](AGENTS.md#5-documentation-is-part-of-every-chapter).

## Chapter 1 — The apprentice, by ear: a role that learns how you work

A new role, **AI apprentice**, now the start screen's default. The expert talks
it through a task they do often. It asks *why*, digs for the limits, the
exceptions and the moment to stop and ask someone, and when the expert is done
it explains the process back until they confirm. It has no eyes yet. This is
Capture and Debrief rehearsed by voice alone, so the next chapter only has to
add the screen.

**What changed**
- `prompts/roles/ai_apprentice.md` (new): the card. It is built on Thinking
  partner, with an apprentice's job: reasons and guardrails over steps, never
  correcting the expert, and leading when something is missing (a concrete
  case, the number behind "too expensive").
- `roles.py`: `DEFAULT_ROLE = "ai_apprentice"`. It also sorts first.
- `llm/registry.py`: `DEFAULT_CHOICE = "deepseek-high"`, DeepSeek V4.1 Flash
  at the "smartest" tier.
- `tests/scenarios/walking_through.md` (new): an expert narrating a flight
  booking, labelled where a reason or a limit is missing.
- Tests that pinned the old defaults now pin the new ones.

**Design decisions**
- **A card, not code.** Roles are data: a card reaches the thinker and the
  system prompt with no pipeline change. The turn policy the memo asks for
  (narration is not a request) is code, and waits for the eyes.
- **Built on Thinking partner, not Devil's advocate.** An apprentice is on the
  expert's side and never the expert. It asks what made a decision, not
  whether it was right.
- **`clarify` first among the moves**: it asks before it pushes. `challenge`
  stays for contradictions ("earlier you said…").
- **The explain-back is the one long turn.** Everywhere else, one question in
  two sentences. A teach-back that is cut short proves nothing.
- **Smartest by default.** The quality of the question is what the brief
  judges, and thinking costs time to first token.

**Latency impact** — not measured. Effort `high` thinks before it answers, so
the first token comes later than at `off`. It still needs a measurement.

**Deliberately not done** — eyes (screen frames to events, next chapter), the
Capture turn policy, the Work Map, the tutor.

**Verification** — `uv run verify` (913 passed). The new scenario parses and
passes the fake replay. Not yet exercised in a live conversation.

## Chapter 0 — The empty page, again: a new project on a working voice pipeline

AI-Apprentice starts as a separate project, with its own repository and its own
deployment, forked from an earlier conversational voice agent. That agent's
code comes along as a foundation. It is a streaming STT → LLM → TTS pipeline
with turn-taking, barge-in, an inner voice, session records and a trace, all
working and tested. Its history does not come along. This chapter builds
nothing new. It resets the project's memory and puts the direction on the page.

**What changed**
- `CHANGELOG.md`, `README.md`: the earlier project's chapters are gone. The
  README describes what AI-Apprentice is aiming at and what it inherits.
- `AGENTS.md`: the project overview now describes AI-Apprentice. The method,
  verification, style and security rules are unchanged.
- `docs/AI-Apprentice/AI-Apprentice.pdf`: the challenge brief (Capture → Map →
  Teach), added as context.
- Removed `docs/history.md`, `docs/ROADMAP.md` and
  `prompts/interjecting_agent_prompt.md`, the earlier project's memory and plans.
- `web/index.html`, `web/admin.html`, `server.py`: the page, the admin page and
  the API title show the AI-Apprentice name. The spoken greeting and the
  persona are unchanged until the apprentice's behaviour is designed.
- `fly.toml`, `docs/DEPLOY.md`: a separate Fly app, `ai-apprentice`
  (<https://ai-apprentice.fly.dev>), with its own volume and secrets. The
  earlier app is untouched.
- `pyproject.toml`: the distribution is named `ai-apprentice`. `module-name =
  "voice_agent"` is pinned for the build backend, which otherwise expects a
  module named after the distribution.

**Design decisions**
- **Keep the code, drop the history.** The pipeline works and is tested.
  Throwing it away would cost weeks for nothing, while keeping its 33-chapter
  log would make the new project read as the old one. Each later chapter
  decides what of the foundation stays, changes or goes.
- **No renames below the surface.** The Python package (`voice_agent`), the
  `uv run voice-agent` command and the `VOICE_AGENT_*` variables keep their
  names for now. Renaming them is churn with no behaviour behind it.
- **Architecture is not decided here.** Whether the apprentice runs on this
  pipeline or on ElevenLabs' ElevenAgents, as the brief suggests, is a
  question for a later chapter.

**Latency impact** — none; no code changed.

**Deliberately not done** — no screen capture, no Work Map, no tutor. Those are the chapters to come.

**Verification** — `uv run verify`.
