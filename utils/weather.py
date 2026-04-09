# utils/weather.py
#
# Fetches current weather conditions from the Open-Meteo API (free, no API key).
#
# Returns a clean dict with:
#   condition     — "clear", "rain", "snow", or "cloudy"
#   temperature_c — current temperature in Celsius
#   feels_like_c  — apparent temperature in Celsius
#   wind_speed_kmh — wind speed in km/h
#   is_daylight   — True if it's currently daytime at the location

import logging

import httpx

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 10


class WeatherError(Exception):
    """Raised when the weather API call fails."""


def _classify_condition(rain: float, snowfall: float, wind_speed: float) -> str:
    """Classify weather into a simple human-readable condition."""
    if snowfall > 0:
        return "snow"
    if rain > 0:
        return "rain"
    if wind_speed > 30:
        return "cloudy"
    return "clear"


async def get_current_weather(lat: float, lon: float) -> dict:
    """Fetch current weather conditions for a GPS coordinate.

    Args:
        lat: Latitude (-90 to 90).
        lon: Longitude (-180 to 180).

    Returns:
        Dict with keys: condition, temperature_c, feels_like_c,
        wind_speed_kmh, is_daylight.

    Raises:
        ValueError: If coordinates are out of range.
        WeatherError: If the API call fails or returns unexpected data.
    """
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Longitude must be between -180 and 180, got {lon}")

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,rain,snowfall,wind_speed_10m,is_day",
        "timezone": "auto",
    }

    logger.debug("Fetching weather for (%.6f, %.6f)", lat, lon)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise WeatherError(f"Open-Meteo API timed out after {TIMEOUT_SECONDS}s")
    except httpx.ConnectError as e:
        raise WeatherError(f"Could not connect to Open-Meteo API: {e}") from e
    except httpx.HTTPStatusError as e:
        raise WeatherError(
            f"Open-Meteo API returned HTTP {e.response.status_code}"
        ) from e

    try:
        data = response.json()
        current = data["current"]
    except Exception as e:
        raise WeatherError(f"Failed to parse Open-Meteo response: {e}") from e

    rain        = current.get("rain", 0) or 0
    snowfall    = current.get("snowfall", 0) or 0
    wind_speed  = current.get("wind_speed_10m", 0) or 0
    temperature = current.get("temperature_2m")
    feels_like  = current.get("apparent_temperature")
    is_day      = current.get("is_day", 1)

    if temperature is None or feels_like is None:
        raise WeatherError("Open-Meteo response missing temperature fields")

    condition = _classify_condition(rain, snowfall, wind_speed)

    logger.debug(
        "Weather at (%.4f, %.4f): %s %.1f°C (feels %.1f°C)",
        lat, lon, condition, temperature, feels_like,
    )

    return {
        "condition":      condition,
        "temperature_c":  temperature,
        "feels_like_c":   feels_like,
        "wind_speed_kmh": wind_speed,
        "is_daylight":    bool(is_day),
    }
