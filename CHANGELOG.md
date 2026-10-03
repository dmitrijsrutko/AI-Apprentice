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

## Chapter 3 — Live eyes: the screen as it is when you ask

Two live sessions on Skyscanner felt "not truly interactive", and the record
says why. The journey-duration slider went from 47.5 h to 24 h while
"Searching…" covered the page. The last reading of it was 23 s old when the
reply said *"the slider's still at forty-seven and a half"*, and the reading
that showed 17–24 h arrived four seconds into a 10.7 s answer. The lag had
four parts, each measured in the record:
- a reading took 2.0–4.7 s;
- waiting behind the call in flight put readings 5–9 s behind the screen;
- DeepSeek took 1.1–3.6 s to its first word;
- replies ran 10–23 s while the screen kept moving.

This chapter takes on each one.

**What changed**
- **Look on speech** (`session.py`, `eyes.py`, `web/eyes.js`): when the voice
  detector hears the user start speaking, the page is asked for the screen
  *now* (`look`, at most every 3 s), past the change threshold. The reading
  runs while they talk and the recognizer commits, so "what's set now?" is
  answered from the screen as it was when they asked.
- **Faster readings** (`prompts/vision.md`, `llm/vision.py`, `web/eyes.js`):
  events first, a 60-word screen summary that keeps the values in play, a
  400-token ceiling, and 1024 px frames at quality 0.6. The prompt now
  insists on the current value of anything just changed, overlay or not.
- **Two readings in flight** (`eyes.py`): staggered by at least 1 s, newest
  frame waiting. Results are applied in the order the frames were taken, and a
  reading overtaken by a newer one is dropped (`stale`). The cap is 900
  readings (`VOICE_AGENT_EYES_MAX_FRAMES`).
- **Shorter replies** (`ai_apprentice.md`): about 25 words, and what it sees
  named in a phrase, never a tour of the page. The explain-back is still the
  one long turn.
- **A stale unprompted line is not spoken** (`initiative.py`): if the screen
  changes while a screen-prompted line is being decided, the decision is
  `superseded` and made again at once with the newer change.

**Design decisions**
- **Look on speech, not faster sampling.** Sampling was already every second;
  the delay was in reading. Reading the screen at the moment a question
  starts costs about one frame per question, and buys the moment that matters.
- **Two calls, not a queue.** Staggered overlap halves the time between
  readings, and ordering by frame keeps a slow early reading from
  overwriting a fresh one.
- **Answers are never cut off mid-sentence by the screen.** A voice that stops
  itself while you scroll sounds broken. Only unprompted lines, which nobody
  is waiting for, yield to a newer screen.

**Latency impact** — a reading of the same screenshot (Haiku 4.5, local):
**1.1–2.4 s** (77–107 tokens out, about 1,560 in), against 2.3–2.5 s (115+ out,
1,960 in) before; on Fly it was 2.0–4.7 s at 230–376 out. With two in flight
and look on speech, the screen a reply reads should be about 1–3 s old
instead of 5–23 s. Not yet measured live.

**Deliberately not done** — cutting a reply mid-speech when the screen changes;
input-activity detection; a faster default reasoning model (the user chose
DeepSeek's smartest; Haiku 4.5 starts in about 1 s).

**Verification** — `uv run verify` (940 passed). Real readings timed as
above. Look on speech needs a voice, so it is unit-tested only until the
next live session.

**Fixes**
- The change test could not see a character. "1%" meant 1% of a 64x36
  thumbnail, whose cells average about 30x30 screen pixels: one character
  moved its cell by about 8 grey levels against a bar of 24, and a slider and
  its label were 1-3 cells against a bar of 23. Only large changes (a
  "Searching…" overlay) were sent. The page now compares a 256x144 thumbnail
  (one character is 1-2 cells), sends on 2 changed cells
  (`VOICE_AGENT_EYES_MIN_CELLS`, which replaces `…_THRESHOLD`), and skips a
  frame matching any of the last 4 sent, so a blinking caret is sent twice
  and then never again.
- Only a caret-sized change (at most 2x4 cells) may be skipped for matching a
  recent frame. As first written, any change back to a recent screen was
  skipped, so 24 h → 30 h → 24 h stayed "30 h".
- A screen decision is superseded at most once in a row: a ticking timer
  overtook every decision and bought a billed call every couple of seconds.
- A change reported by both overlapping readings is kept once.
- The page is asked to look only when the user speaks, not when the agent
  does.

## Chapter 2 — Eyes: the apprentice sees the screen you share

The apprentice can now see. A **🖥 share** button opens the browser's own
picker: entire screen, a window or a tab. Sharing can start, stop and switch at
any point, as often as you like. Changed frames go to a vision model, and what
it reads becomes part of what the apprentice knows. It knows whether it can see
at all, so "I'm sharing my screen" while nothing is shared gets "I can't see
it". It may speak up about what it just saw at a short pause. A new **eyes**
toggle shows each reading on the page. No image is ever stored.

**What changed**
- `web/eyes.js` (new): `getDisplayMedia`, plus a sampler every 1 s that
  compares a 64x36 grey thumbnail with the last frame *sent*. Only a change of
  at least 1% goes up, as a JPEG up to 1280 px wide at quality 0.7. A still
  screen costs nothing. The browser's own "Stop sharing" bar counts as a stop.
- `eyes.py` (new): **one vision call in flight, the newest frame waiting.**
  A frame arriving mid-call replaces the waiting one, so a fast-changing
  screen is read as it is now, never as a backlog. A result for a share that
  has since stopped is dropped.
- `llm/vision.py` (new) and `prompts/vision.md`: Claude reads a frame against
  its previous description and returns `{app, screen, events, doing}`. That is
  the whole screen now plus what changed. Personal data becomes
  "[personal data]".
- `conversation.py`: what was seen is kept as text, and `context` interleaves
  `[screen m:ss app: events]` notes where they happened, plus an `[eyes: …]`
  status before the latest turn. It is built at call time, so the history,
  the record and the judge hold only what was said.
- `initiative.py` and `prompts/eyes_nudge.md`: a change the eyes saw is
  considered once the user has been quiet for 2 s, at most once every 15 s.
  The model may ask why, confirm in a few words, or stay quiet. Screen
  activity stands the silence ladder down until the user speaks.
- The start screen has an **Eyes** picker: Claude Haiku 4.5 (default) or
  Sonnet 5.5. `system_prompt.md` and the apprentice card say how to use the
  eyes.
- The page has the share chip, the **eyes** toggle, and one line per reading
  that expands, on a click, to the full screen description.

**Design decisions**
- **Latest-wins over a queue.** A queue reads the past; the conversation is
  about now. Two calls in flight would double the cost and return out of
  order.
- **State plus delta.** The state answers "what do you see?" and the events
  are the action log the brief's Work Map will need. Each call is given the
  previous state, so "what changed" is the model's job, not a text diff's.
- **Notes built at call time, as bracketed user-role text.** The rules
  already define brackets as "not the user speaking". Both vendors accept
  consecutive user messages.
- **The page decides which frames, the server decides when.** The pixel diff
  is free in the browser, while the model's pace is only known at the server.
- **Never stored, never traced.** `page.in` keeps a frame's size, not its
  bytes. The record and the trace hold what was *read*, as text.

**Latency impact** — off the voice path: a reply reads the latest notes and
waits for nothing. Measured on one 1280 px screenshot: Haiku 4.5 **2.3–2.5 s**
per frame (about 1,960 tokens in, 115 out), Sonnet 5.5 at effort low **2.6 s**.
So the eyes catch up with a changing screen about every 2.5 s. A typed reply
with screen context on Haiku took 0.9 s end to end.

**Deliberately not done** — storing frames or screen moments (Work Map),
input-activity detection and a smarter "when to ask" (Capture turn policy),
redaction before the model (Presidio), off-the-record, mobile (iOS has no
`getDisplayMedia`).

**Verification** — `uv run verify` (928 passed, plus node). Real models over
the socket: "can you see it?" while not sharing got "I can't see it right now
— can you hit the share button"; after one shared frame (read in 2.3 s), "I can
see your desktop with a forest background and the Terminal window open". The
browser picker and the sampler are not yet exercised in a real browser.

**Fixes**
- Listening stopped after 6 minutes in every role: the microphone's backstop
  (`SESSION_CAP_SECONDS`) had stayed at 360 s when the conversation budget
  went to 30 minutes. It is now 1800 s, and a test holds it to `fly.toml`.
- A change on the shared screen now restarts listening's 30 s idle window, so
  an expert working silently is not taken for one who has left. It is kept
  apart from the silence the clock measures: counted as speech, it made every
  screen-prompted line "yield" to nobody.
- Screen notes seen after a reply that was then cut short jumped to the top of
  the context, because the cut reply is a new message. They are now re-anchored.
- Only the latest 40 screen notes are resent on each call, with older ones
  counted. 30 minutes of sharing was otherwise ~30k tokens on every turn.
- A conversation's screen readings are capped (`VOICE_AGENT_EYES_MAX_FRAMES`,
  600, about $1.50 on Haiku), and the page is told once when the cap is reached.
- The inner voice now reads the latest screen notes too, and stopping a share
  drops a change not yet spoken about.

**Fixes**
- Listening stopped after 6 minutes in every role: the microphone's backstop
  (`SESSION_CAP_SECONDS`) had stayed at 360 s when the conversation budget
  went to 30 minutes. It is now 1800 s, and a test holds it to `fly.toml`.
- A change on the shared screen now restarts listening's 30 s idle window, so
  an expert working silently is not taken for one who has left.

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
