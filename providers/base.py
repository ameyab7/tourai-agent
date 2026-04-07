from abc import ABC, abstractmethod


class POIProvider(ABC):
    @abstractmethod
    async def search_nearby(self, lat: float, lon: float, radius: float) -> list[dict]:
        ...


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice: str) -> bytes:
        ...
