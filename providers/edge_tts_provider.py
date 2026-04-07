# providers/edge_tts_provider.py
#
# Text-to-speech provider using Microsoft Edge TTS (free, no API key required).
#
# What it does:
#   1. Takes a story text string and a voice name
#   2. Streams audio from Microsoft's Edge TTS service
#   3. Returns raw MP3 bytes (for playback) or writes to a file (for saving)
#
# Default voice: en-US-GuyNeural — natural-sounding American male voice.
# Other good voices: en-US-JennyNeural (female), en-GB-RyanNeural (British male)

import io
import logging

import edge_tts

from providers.base import TTSProvider, TTSProviderError

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-GuyNeural"


class EdgeTTSProvider(TTSProvider):
    async def synthesize(self, text: str, voice: str = DEFAULT_VOICE) -> bytes:
        """Convert text to speech and return raw MP3 bytes.

        Args:
            text: The text to synthesize (must not be empty).
            voice: Edge TTS voice name. Defaults to en-US-GuyNeural.

        Returns:
            MP3 audio as bytes.

        Raises:
            ValueError: If text is empty.
            TTSProviderError: If the Edge TTS service fails.
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
            raise TTSProviderError(
                f"Edge TTS synthesis failed for voice '{voice}': {e}"
            ) from e

        audio_bytes = buffer.getvalue()

        if not audio_bytes:
            raise TTSProviderError(
                f"Edge TTS returned empty audio for voice '{voice}'. "
                "The voice name may be invalid."
            )

        logger.debug("Synthesized %d bytes of audio", len(audio_bytes))
        return audio_bytes

    async def save_to_file(self, text: str, filepath: str, voice: str = DEFAULT_VOICE) -> None:
        """Synthesize text and save the MP3 to disk.

        Args:
            text: The text to synthesize (must not be empty).
            filepath: Destination file path (e.g. "output.mp3").
            voice: Edge TTS voice name. Defaults to en-US-GuyNeural.

        Raises:
            ValueError: If text is empty.
            TTSProviderError: If the Edge TTS service fails.
            OSError: If the file cannot be written.
        """
        audio_bytes = await self.synthesize(text, voice)

        with open(filepath, "wb") as f:
            f.write(audio_bytes)

        logger.debug("Audio saved to '%s' (%d bytes)", filepath, len(audio_bytes))
