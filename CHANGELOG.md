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
