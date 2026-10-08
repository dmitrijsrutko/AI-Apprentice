"""ElevenLabs v4 synthesis, over the dialogue socket rather than the TTS one.

Eleven v4 Turbo is not served by the socket this project has spoken to since
Chapter 2: the vendor is explicit that `/text-to-speech/{voice}/stream-input`
carries no `eleven_v3` or `eleven_v4` model. It is served by the Text to Dialogue
socket, which differs in every place that matters — the voice is registered in
the first message instead of sitting in the URL, text travels as `inputs`
entries, a reply ends with `close_socket` rather than an empty text, responses
are snake_case, and the service buffers to its own fixed threshold (~40
characters and 8 words) with no `chunk_length_schedule` to set.

The contract is the sibling's, with one difference: the first words go as one
batch of at least `FIRST_BATCH_CHARS`, and after it each fragment as the
reasoning engine writes it. Whole samples come out, and where a reply is cut is
the service's decision, not this project's. One reply is one socket, which is
why `new_turn` is never sent: a reply is a turn.
"""

import asyncio
import base64
import contextlib
import json
import re
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import websockets

from voice_agent import timing, trace
from voice_agent.config import require_env
from voice_agent.errors import ProviderError
from voice_agent.tts.base import MEDIA_TYPE, Alignment, AudioChunk, whole_samples
from voice_agent.tts.elevenlabs_tts import (
    CLOSE_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    OUTPUT_FORMAT,
    describe,
)

ENDPOINT = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"

DEFAULT_MODEL = "eleven_v4_turbo"
"""The adapter's own fallback. What a conversation speaks with is chosen in
`tts.registry.MENU`. The `eleven_v4` model — the vendor's most expressive, not
its real-time one — is served by this same socket and would be a menu entry
rather than a second adapter."""

SYNC_ALIGNMENT = "true"
"""Asked for, not assumed: the vendor says timing arrives only when
`sync_alignment` is set, and without it "what the user actually heard" after an
interruption degrades to an estimate. Every probe here had it on, so the
negative is the vendor's word rather than this project's measurement."""


FIRST_BATCH_CHARS = 120
"""The first text handed over is at least this long, as one `inputs` message.

The service voices a reply in pieces, and the second always arrives about
1.25 s after the first audio. Fed token by token, its first piece was a fixed
1233 ms of audio, so that join landed at the playhead: any network delay to the
browser left a hole, a click a word or two in (40% of live replies). The first
piece covers what the service holds when it starts. Measured on 12 real
replies, the first piece / the tightest join / first sound:

- tokens:            1233 ms / 8 of 12 with <150 ms to spare / 236 ms
- 80 characters:     2530 ms / 2 of 12 / 307 ms
- 120 characters:    5092 ms / none, at least 1150 ms to spare / 369 ms
- the whole reply:   7653 ms / none / ~395 ms
"""

FIRST_BATCH_WAIT_SECONDS = 0.4
"""The most the first batch waits for text, from its first fragment: a slow or
stalling reasoning engine never holds the voice back longer than this."""

CLAUSE_END = re.compile(r"[.?!,;:]\s|\s—\s")
"""Where speech may pause: punctuation before a space, or a spaced dash, which
replies lean on ("Got it — …")."""


async def first_batch(text: AsyncIterator[str], least: int, wait: float) -> AsyncIterator[str]:
    """The first `least` characters as one piece, then every fragment as written.

    The batch is cut at the last sentence or clause end in its second half, else
    at the last space, so the first piece ends where speech may pause; what is
    past the cut follows at once. The wait is on a task, not on `__anext__`
    itself, so running out of time never cancels the fragment being written.
    """
    fragments = aiter(text)
    held = ""
    started: float | None = None
    pending: asyncio.Future[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(fragments))
            left = None if started is None else wait - (timing.now() - started)
            done, _ = await asyncio.wait({pending}, timeout=None if left is None else max(0, left))
            if not done:
                break  # out of time: send what there is
            try:
                fragment = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            if not fragment:
                continue
            held += fragment
            started = timing.now() if started is None else started
            if len(held) >= least:
                break
        if held:
            # Cut only a batch that closed on length: one that ran out of text
            # or of time goes whole, since nothing more is on its way yet.
            cut = cut_at(held) if len(held) >= least else len(held)
            yield held[:cut]
            if held[cut:]:
                yield held[cut:]
        if pending is not None:
            try:
                fragment = await pending
            except StopAsyncIteration:
                return
            finally:
                pending = None
            if fragment:
                yield fragment
        async for fragment in fragments:
            yield fragment
    finally:
        if pending is not None:
            pending.cancel()


def cut_at(held: str) -> int:
    """Where to end the first batch: after the last sentence or clause end in its
    second half, else after the last space, else nowhere (the whole batch)."""
    ends = [m.end() for m in CLAUSE_END.finditer(held)]
    if ends and ends[-1] >= len(held) // 2:
        return ends[-1]
    space = held.rfind(" ") + 1
    return space if space > 0 else len(held)


def dialogue_alignment(raw: object) -> Alignment | None:
    """The dialogue socket's character timing, relative to the chunk it came on.

    Measured live, 2026-09-30: unlike `stream-input`, every audio message carries
    one, and each is timed from that message's own first sample — the first
    `char_start_times_ms` is 0, which is the contract `heard.py` assumes when it
    offsets every end by the audio sent before the chunk arrived.

    Those ends only roughly match the chunk's own duration: measured, the early
    pieces overrun it by up to ~180 ms and later ones fall short by up to
    ~150 ms, so a karaoke mark can land a fraction of a second after its sound.
    That is the long-segment behaviour `heard.py`'s clamp already exists for.
    """
    if not isinstance(raw, dict):
        return None
    chars, starts, durations = (
        raw.get("chars"),
        raw.get("char_start_times_ms"),
        raw.get("char_durations_ms"),
    )
    if not (isinstance(chars, list) and isinstance(starts, list) and isinstance(durations, list)):
        return None
    if not chars or not len(chars) == len(starts) == len(durations):
        return None
    return Alignment(
        chars="".join(str(c) for c in chars),
        ends_ms=tuple(float(s) + float(d) for s, d in zip(starts, durations, strict=True)),
    )


class ElevenLabsDialogueTTS:
    def __init__(
        self,
        voice: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.provider = "elevenlabs"
        self.voice = voice or DEFAULT_VOICE
        self.model = model or DEFAULT_MODEL
        self._api_key = api_key or require_env("ELEVENLABS_API_KEY")

    @property
    def url(self) -> str:
        return f"{ENDPOINT}?" + urlencode(
            {
                "model_id": self.model,
                "output_format": OUTPUT_FORMAT,
                "sync_alignment": SYNC_ALIGNMENT,
            }
        )

    async def stream(self, text: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        with trace.span(
            "tts",
            {
                "gen_ai.system": self.provider,
                "gen_ai.request.model": self.model,
                "voice": self.voice,
                "media_type": MEDIA_TYPE,
            },
        ):
            said: list[str] = []
            chunks = 0
            sent = 0
            async for chunk in self._stream(text, said):
                chunks += 1
                sent += len(chunk.pcm)
                yield chunk
            trace.event(
                "tts.spoken",
                {"text": "".join(said), "chunks": chunks, "bytes": sent},
            )

    async def _stream(self, text: AsyncIterator[str], said: list[str]) -> AsyncIterator[AudioChunk]:
        async def pump(socket: websockets.ClientConnection) -> None:
            # The first message registers the voice. v4 Turbo allows exactly one
            # per connection, and one reply is one voice.
            await socket.send(json.dumps({"voices": [self.voice]}))
            async for fragment in first_batch(text, FIRST_BATCH_CHARS, FIRST_BATCH_WAIT_SECONDS):
                # An empty text is not this protocol's terminator — `close_socket`
                # is — but an empty `inputs` entry would still be nothing to say.
                if fragment:
                    said.append(fragment)
                    await socket.send(
                        json.dumps({"inputs": [{"text": fragment, "voice_id": self.voice}]})
                    )
            # Flush whatever is still buffered, then close: the reply is over.
            await socket.send(json.dumps({"close_socket": True}))

        try:
            async with websockets.connect(
                self.url,
                additional_headers={"xi-api-key": self._api_key},
                # As in the sibling adapter: a cancelled synthesis must not wait
                # on a close for the default 10 s, holding up the turn that
                # cancelled it. This vendor's sockets do not answer a close.
                close_timeout=CLOSE_TIMEOUT_SECONDS,
            ) as socket:
                task = asyncio.create_task(pump(socket))
                try:
                    async for chunk in whole_samples(self._audio(socket)):
                        yield chunk
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, websockets.WebSocketException):
                        await task
        except websockets.InvalidStatus as exc:
            # Refused at the handshake, with whatever the status carries: no
            # audio was produced, so the wording must name what to check.
            raise ProviderError(
                f"elevenlabs refused the dialogue stream (HTTP {exc.response.status_code}) — check "
                "the voice id with --list-voices, the key's text-to-speech permission, and that "
                f"{self.model} is available on this plan"
            ) from exc
        except (websockets.WebSocketException, OSError) as exc:
            raise ProviderError(f"elevenlabs synthesis interrupted: {exc}") from exc

    async def _audio(self, socket: websockets.ClientConnection) -> AsyncIterator[AudioChunk]:
        async for raw in socket:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            if audio := payload.get("audio"):
                yield AudioChunk(
                    base64.b64decode(audio), dialogue_alignment(payload.get("alignment"))
                )
            elif "error" in payload or "message" in payload:
                raise ProviderError(describe(payload, "the stream was refused"))
            if payload.get("is_final"):
                return
