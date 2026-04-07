# nodes/audio_delivery.py
#
# Synthesizes the story text into MP3 audio using EdgeTTS
# and saves it to the output/ folder.

import logging
import os
import re
from state import TourGuideState
from providers.edge_tts_provider import EdgeTTSProvider
from providers.base import TTSProviderError

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
DEFAULT_VOICE = "en-US-GuyNeural"

_tts = EdgeTTSProvider()


def _safe_filename(name: str) -> str:
    """Convert a POI name to a safe filename."""
    return re.sub(r"[^\w\-]", "_", name).strip("_")[:50]


async def audio_delivery(state: TourGuideState) -> dict:
    if not state.get("should_speak"):
        return {"audio_bytes": b""}

    story_text = state.get("story_text", "")
    if not story_text:
        logger.warning("audio_delivery: story_text is empty, skipping synthesis")
        return {"audio_bytes": b""}

    top_poi = state.get("top_poi", {})
    poi_name = top_poi.get("name", "unknown")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"{_safe_filename(poi_name)}.mp3"
    filepath = os.path.join(OUTPUT_DIR, filename)

    logger.debug("Synthesizing audio for '%s' -> %s", poi_name, filepath)

    try:
        audio_bytes = await _tts.synthesize(story_text, voice=DEFAULT_VOICE)
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
        logger.debug("Audio saved: %s (%d bytes)", filepath, len(audio_bytes))
    except TTSProviderError as e:
        logger.error("TTS synthesis failed for '%s': %s", poi_name, e)
        return {"audio_bytes": b""}
    except OSError as e:
        logger.error("Failed to write audio file '%s': %s", filepath, e)
        return {"audio_bytes": b""}

    return {"audio_bytes": audio_bytes, "audio_filepath": filepath}
