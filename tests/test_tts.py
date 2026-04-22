import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers.edge_tts_provider import EdgeTTSProvider
from providers.base import TTSProviderError

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

TEST_TEXT = "This is a test of the TourAI tour guide."
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "test_output.mp3")


async def main():
    provider = EdgeTTSProvider()

    print(f"\n{'='*60}")
    print(f"  TTS Test — EdgeTTSProvider")
    print(f"  Text  : {TEST_TEXT}")
    print(f"  Voice : en-US-GuyNeural")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"{'='*60}\n")

    try:
        await provider.save_to_file(
            text=TEST_TEXT,
            filepath=OUTPUT_FILE,
            voice="en-US-GuyNeural",
        )
    except TTSProviderError as e:
        print(f"  ERROR: {e}")
        return
    except OSError as e:
        print(f"  ERROR writing file: {e}")
        return

    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"  Success! Saved {size_kb:.1f} KB to {OUTPUT_FILE}")
    print(f"  Play it with: open {OUTPUT_FILE}")
    print()


asyncio.run(main())
