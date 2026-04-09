# utils/tts.py
#
# Standalone async function for text-to-speech using Microsoft Edge TTS.
# Free, no API key required. Returns raw MP3 bytes.
#
# Default voice: en-US-GuyNeural (natural American male)
# Other options: en-US-JennyNeural (female), en-GB-RyanNeural (British male)

import io
import logging

import edge_tts

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-GuyNeural"


class TTSError(Exception):
    """Raised when Edge TTS synthesis fails."""


async def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Convert text to speech and return raw MP3 bytes.

    Args:
        text: The text to synthesize (must not be empty).
        voice: Edge TTS voice name. Defaults to en-US-GuyNeural.

    Returns:
        MP3 audio as bytes.

    Raises:
        ValueError: If text is empty.
        TTSError: If the Edge TTS service fails or returns empty audio.
    """
    if not text or not text.strip():
        raise ValueError("text must not be empty")

    logger.debug("Synthesizing %d chars with voice '%s'", len(text), voice)

    buffer = io.BytesIO()

    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
    except Exception as e:
        raise TTSError(f"Edge TTS synthesis failed for voice '{voice}': {e}") from e

    audio_bytes = buffer.getvalue()

    if not audio_bytes:
        raise TTSError(
            f"Edge TTS returned empty audio for voice '{voice}'. "
            "The voice name may be invalid."
        )

    logger.debug("Synthesized %d bytes of audio", len(audio_bytes))
    return audio_bytes
