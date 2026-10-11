"""The agent's opening line, spoken like any other reply."""

import asyncio

from voice_agent import timing
from voice_agent.channel import Channel
from voice_agent.conversation import Conversation
from voice_agent.heard import Spoken
from voice_agent.tts import TTS
from voice_agent.turn import Speech


async def begin_greeting(
    channel: Channel, conversation: Conversation, speaker: TTS | None, text: str
) -> tuple[Spoken | None, asyncio.Task[None] | None]:
    """Greet a conversation that has not started yet, without waiting for the
    voice to be made. Returns the greeting's voice, which the user can talk
    over like any other, and the task that finishes making it.

    Not awaited by the socket: while a greeting is synthesized (2-4 s), the
    page's own messages — its `listen_start` above all — must still be read."""
    # Nothing awaited between this check and the append: two tabs opening the
    # same link would otherwise both pass it and both greet.
    if not text or conversation.messages or conversation.ended:
        return None, None
    message = conversation.add_assistant(text)
    conversation.opening = message
    await channel.send_json({"type": "greeting", "text": text})
    if speaker is None:
        return None, None
    speech = Speech(channel, speaker, timing.now(), Spoken(message=message))
    speech.say(text)
    speech.finish()

    async def made() -> None:
        try:
            await speech.done()
        finally:
            await speech.cancel()  # a no-op once done; stops synthesis if the socket went

    return speech.voice, asyncio.create_task(made())


async def greet(
    channel: Channel, conversation: Conversation, speaker: TTS | None, text: str
) -> Spoken | None:
    """`begin_greeting`, waited out: for callers with nothing to read meanwhile."""
    voice, making = await begin_greeting(channel, conversation, speaker, text)
    if making is not None:
        await making
    return voice
