from abc import ABC, abstractmethod


class POIProviderError(Exception):
    """Raised when a POI provider fails to fetch or parse results."""


class TTSProviderError(Exception):
    """Raised when a TTS provider fails to synthesize audio."""


class POIProvider(ABC):
    @abstractmethod
    async def search_nearby(self, lat: float, lon: float, radius: float) -> list[dict]:
        """Search for points of interest near the given coordinates.

        Args:
            lat: Latitude in decimal degrees (-90 to 90).
            lon: Longitude in decimal degrees (-180 to 180).
            radius: Search radius in meters (must be positive).

        Returns:
            List of POI dicts with keys: id, name, lat, lon, tags, poi_type.

        Raises:
            POIProviderError: If the provider fails to fetch or parse results.
            ValueError: If the input coordinates or radius are invalid.
        """
        ...


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice: str) -> bytes:
        """Synthesize speech from text.

        Args:
            text: The text to convert to speech (must not be empty).
            voice: Provider-specific voice identifier.

        Returns:
            Raw audio bytes.

        Raises:
            TTSProviderError: If the provider fails to synthesize audio.
            ValueError: If text is empty.
        """
        ...
