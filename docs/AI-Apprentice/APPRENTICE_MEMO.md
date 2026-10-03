# AI Apprentice — build memo

Context for Claude Code. This is a summary of decisions and suggestions, not a spec to follow blindly. Push back where the code or the brief says otherwise.

## The event

Hack-Nation 7th Global AI Hackathon, Challenge 01 "The AI Apprentice", sponsored by ElevenLabs. Full brief: `docs/challenge.pdf` (add it to the repo). Submission deadline **Sunday 4 October, 15:00 CEST**. The submission needs a GitHub repo, a link to a running version, and three videos.

The brief in one line: an AI voice apprentice that watches an expert do real screen work, asks *why* at natural pauses, runs a debrief until it understands, turns that into a clickable **Work Map**, and then **teaches** a new hire on their own screen. All three modules (Capture, Map, Teach) are required.

Required, verbatim in spirit:
- **Capture:** at least 3 live questions, each at a natural pause, each about something visible on screen, at least one about a guardrail.
- **Map:** the debrief asks at least 3 follow-ups that weren't answered during the task, and ends with a teach-back the expert confirms. Every step and guardrail links to a screen moment and the expert's own words.
- **Teach:** a judge plays a new hire on a case the expert never showed. The tutor catches at least one wrong decision before it is committed and explains it using the expert's reasoning.

The demo must answer five questions: when to ask, what to ask, when it has understood, whether the new hire learned, and trust (off the record, personal data on screen).

## Use case

An executive assistant books flights for their boss on **Skyscanner**. The expert is the experienced assistant (Dmitrijs plays this role). The new hire is their replacement. It's real, short, and has hidden judgment calls, and every judge can play the new hire without training.

The real session to capture is a booking from Tallinn to Stockholm, with rules that were never written down. Examples of what came out of it (illustrative only; the system must **learn** these from the session, never hardcode them):
- Direct flights only on short-haul.
- Arrive with a buffer of about two hours before the commitment, but don't waste a morning arriving too early.
- The return goes after commitments end.
- Pay a little more (€30) for a better-timed flight.
- Stop and ask the boss above a price threshold.

The teach case the expert never showed is Tallinn to Berlin. The cheapest result is a self-transfer via Riga with a short connection. The tutor must catch the new hire opening or selecting it.

**Reliability:** use live Skyscanner for capture (realism is the point). Also build a small **mock flight-results page** with fixed data (generic look, no Skyscanner branding) as the fallback for live judging. On the mock page the "Select" button can actually be intercepted by the tutor.

## Starting point: VoiceAgent

New repo, built on code copied from `github.com/dmitrijsrutko/VoiceAgent` (MIT, the author's own). State this clearly in the README as prior work, and keep the new hackathon work visibly separate, because judging considers what was built during the event.

**Reuse as-is or nearly:**
- The WebSocket server, session and turn machinery, and channel framing.
- Scribe realtime STT (ElevenLabs) with VAD endpointing.
- ElevenLabs streaming TTS.
- The LLM provider interface, particularly Anthropic.
- Barge-in and heard-text tracking.
- The browser client modules: capture worklet, playback worklet, player.
- The **unprompted speaking** mechanism, where a bracketed pause note lets the agent decide whether to speak into a silence. This is the foundation of "when to ask".

**What changes:**
1. **Eyes.** The current prompt says "you cannot see anything", and that has to flip. Screen events become context.
2. **Turn policy.** Today every committed user turn gets a reply. In Capture mode, the expert's speech is mostly **narration, not a request**. It goes into the ledger and the agent stays quiet. The agent answers only when directly addressed, and otherwise speaks only through pause notes. This mode-dependent turn policy is the core change.
3. **Modes and state.** Add a session state machine: `capture` → `debrief` → `map_ready` → `tutor`.
4. **Vision.** The Anthropic provider needs image input for the frame-to-events call.

**On ElevenAgents:** the brief suggests ElevenAgents and Scribe. We use ElevenLabs for both ears (Scribe) and voice (TTS), but keep our own orchestration, because timing is where the brief says most voice agents fail, and owning turn-taking is our edge. We say this explicitly in the pitch. This is a conscious trade-off: sponsor judges may favour ElevenAgents. Revisit only if our own pipeline blocks progress.

## Architecture

```
browser: getDisplayMedia ─ frame every ~1.5 s ─ local pixel diff ─▶ changed frames only
         mic (PCM16) ───────────────────────────────────────────▶ same WebSocket
         input activity (keys/mouse, no content) ───────────────▶ same WebSocket

server:  frames ─▶ redact ─▶ vision LLM ─▶ screen events  ┐
         audio ─▶ Scribe ─▶ transcript (timestamped)      ├─▶ session ledger
         activity ─▶ busy / paused detector               ┘
         ledger ─▶ gap scorer ─▶ candidate question ─▶ pause note ─▶ LLM ─▶ TTS (or decline)
         end of task ─▶ merge LLM ─▶ Work Map JSON + open gaps ─▶ debrief ─▶ teach-back ─▶ confirmed map
         tutor: same eyes on new hire's screen ─▶ guardrail check ─▶ preemptive spoken stop
```

**Screen events.** Each event looks like `{t, frame_id, kind, summary, details}`, for example `{t: 162.4, kind: "filter_applied", summary: "Stops: direct only"}` or `{kind: "result_opened", summary: "SAS 14:20 TLL→ARN, €110, direct"}`. Store the frames, because they are the Work Map's screen moments. Downscale the frames and send only the ones that changed.

**When to ask.** The expert counts as busy if any of these is true: input activity in the last ~2 s, speech in the last ~2 s, or frames still changing. A pause is ~3 s of none of those, right after a meaningful event. Only then is a pause note sent. The budget is 3–5 live questions per 10 minutes; everything else waits for the debrief.

**What to ask.** Keep a gap ledger. Score each event higher when its reason **isn't visible on screen**: skipping the cheapest or fastest option, applying a filter immediately, reversing a choice, hesitating, abandoning something. Score lower when the screen already explains it. Prefer guardrail questions (limits, exceptions, when to stop and ask) until at least one has been asked. Send the top 1–3 candidates in the pause note and let the LLM pick one or decline.

**When it has understood.** Debrief ends when every step has a decision and a reason, every guardrail is linked to a moment, open gaps are empty or explicitly marked "expert doesn't know", and the expert has confirmed the teach-back. Corrections patch the JSON.

**Work Map JSON (sketch):**

```json
{
  "task": "Book flights for the boss",
  "expert": "Dmitrijs",
  "steps": [{
    "id": 3, "title": "Choose outbound flight",
    "screen_moment": {"t": 162.4, "frame_id": "f0412"},
    "decision": "Picked 14:20 direct over the cheaper 07:00",
    "reason": {"quote": "...", "t": 171.0, "source": "live_question"},
    "guardrails": [{"rule": "...", "kind": "limit|exception|escalate",
                    "quote": "...", "t": 0, "check": {"field": "stops", "op": "==", "value": 0}}]
  }],
  "preferences": {"hard": [], "soft": [{"rule": "...", "tradeoff": "..."}], "exceptions": []},
  "open_gaps": []
}
```

`check` is optional and structured where possible, so the tutor can test guardrails deterministically and fall back to the LLM otherwise.

**Tutor.** It uses the same eyes on the new hire's screen. When a selection or detail-open event appears, check it against the guardrails. A breach **preempts** the quiet rule: speak immediately, in the expert's words, and offer to replay the expert's frame. On the mock page, block Select until the breach is resolved. Finish with a short mastery summary: what they got right, and what to practise.

**Trust.**
- "Off the record": a button plus the spoken phrase. It pauses capture and drops the last ~30 s of events, frames and transcript.
- Redact frames and transcripts before they reach the vision model or the log (Microsoft Presidio, including its image redactor).
- No login and no passenger details in the demo.

**Work Map UI.** A clickable timeline. Each step shows a frame thumbnail, timestamp, decision, the expert's quote, and guardrail chips. Clicking a step replays its frame and the moment's audio, if cheap.

**Stretch goal if there's time:** export the Work Map as agent-ready instructions in markdown. This is our moonshot proof: "people first, then agents". The same map that teaches a new hire becomes the guardrails a booking agent runs under.

## Build order (vertical slices, demo path first)

1. Screen frames → vision → event log on screen, with the existing voice loop still working. **Get screen events into the agent's context before anything else.**
2. The Capture turn policy: busy/pause detection, gap ledger, pause notes, 3 well-timed questions.
3. Record the real Skyscanner session early, as test data and a backup video.
4. Debrief → Work Map JSON → teach-back → corrections.
5. Work Map UI.
6. Mock flight page and Tutor: guardrail check, preemptive stop, replay, mastery summary.
7. Trust features, then deploy (Fly.io).
8. README with the prior-work disclosure, then the three videos. Submit by 14:30 CEST.

**Working rules for this sprint:** keep `uv run verify` green, but test only what protects the demo path. No abstraction ahead of need. Every slice has to be demoable on its own. Log per-stage latency like VoiceAgent does, because timing claims in the pitch need numbers.

## Prompt drafts

These inherit the speech rules from VoiceAgent's `prompts/system_prompt.md`: brief, no markdown, written the way it's said, the transcript is imperfect, yield instantly, and the unprompted-speaking rules. Below are only the deltas. Keep each mode in its own file under `prompts/`.

### `prompts/base_apprentice.md` (overrides on top of the voice rules)

```
You are an apprentice. You sit beside an experienced person while they work on their
screen, and your job is to learn how they make decisions so you can teach someone else.

You can see the screen, but only through notes describing what changed on it. Treat
those notes as what you saw. Never claim to see more than they say, and never read
them aloud.

You are a curious, patient colleague, not an interviewer with a form. You are never
the expert. You never correct the expert's decisions; you ask what made them.

Most of what the person says while working is them thinking aloud, not talking to you.
Do not reply to narration. Reply only when they clearly address you, or when a pause
note invites you to speak.
```

### `prompts/capture.md`

```
Mode: the expert is doing a real task. Your goal is to find the reasons and the
guardrails behind their choices — the things the screen cannot tell you.

You speak only when a pause note invites you. The note lists what just happened and
one to three candidate questions. Pick at most one, or decline. Decline if the screen
already answers it, if you asked something similar, or if the expert is clearly
mid-thought.

A good question is short, refers to what just happened, and asks for a reason or a
limit: "You skipped the seven a.m. flight — what put you off it?" "Is there a price
where you'd stop and check with him first?" Never ask two questions at once. Never ask
about what you can plainly see.

Ask about a guardrail early: a limit, an exception, or when they would stop and ask
someone.

When they answer, acknowledge in a few words at most and go quiet. Save follow-ups for
the debrief unless the answer is unclear.

If they say "off the record", confirm in three words and stay silent until they say
they're back on.
```

Example pause note sent by the server:

```
[pause 3.4s · last events: filter "direct only" applied at 1:02; opened SAS 14:20
TLL→ARN €110, skipped airBaltic 07:00 €80 · candidates: (a) why skip the cheaper
07:00 (b) is direct-only always the rule (c) price limit before asking · asked so far:
1 of 5 · guardrail asked: no]
```

### `prompts/debrief.md`

```
Mode: the task is done. You have the session's events, transcript and a draft Work Map
with open gaps.

First, ask about the open gaps, one at a time, most important first: exceptions you
noticed, rules you're unsure about, and cases you haven't seen ("What if the only direct
flight lands after the meeting starts?"). Ask at least three.

Then explain the whole process back in your own words, in under a minute, the way you
would teach it. Use their phrasing for the reasons. End with: "Is that how it works?"

If they correct you, accept it, restate just the corrected part, and ask again. You are
done only when they confirm. Never pretend you understood something you didn't; if a
gap remains, say what it is.
```

### `prompts/tutor.md`

```
Mode: you are teaching a new assistant to do this task the way {expert} does it. You
have {expert}'s Work Map: steps, decisions, reasons in their own words, and guardrails.

Watch through the screen notes. Mostly stay quiet and let them work. At key decisions,
ask them to predict before they act: "What would {expert} look at first here?"

When a note says a guardrail is about to be broken, speak at once — this overrides
staying quiet. Name the stop, ask why, and teach with {expert}'s reason, attributed:
"{expert} would stop here. Why do you think?" Then: "He said: <quote>." Offer to replay
the moment from his session.

Never just give the answer when a question would teach it. Praise specific good
calls, briefly. At the end, say in a few sentences what they've mastered and the one
thing to practise next.
```

### Non-voice prompts (short)

- **`prompts/vision_events.md`:** given the previous and current frame plus the recent events, return JSON events describing only what changed and matters to the task (filters, sorts, selections, opened details, prices, times, stops, connection type). No speculation about intent. Return an empty list if nothing meaningful changed. Never transcribe personal data.
- **`prompts/workmap_merge.md`:** merge events, the timestamped transcript and the Q&A into Work Map JSON. Every reason and guardrail must carry an exact quote and timestamp from the transcript, and must never be paraphrased into existence. Anything without a quote goes into `open_gaps`.
