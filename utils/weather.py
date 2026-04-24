"""utils/weather.py — Current weather + sunrise/sunset via Open-Meteo (no API key)."""

import httpx

_BASE = "https://api.open-meteo.com/v1/forecast"

_WMO_DESCRIPTION = {
    0:  "Clear sky",
    1:  "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


async def get_conditions(lat: float, lon: float) -> dict:
    """
    Returns:
        temperature_c, weather_code, description, is_clear,
        sunrise_iso, sunset_iso
    """
    params = {
        "latitude":       lat,
        "longitude":      lon,
        "current":        "temperature_2m,weather_code",
        "daily":          "sunrise,sunset",
        "timezone":       "auto",
        "forecast_days":  1,
    }
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(_BASE, params=params)
        r.raise_for_status()
        data = r.json()

    code        = data["current"]["weather_code"]
    temperature = data["current"]["temperature_2m"]
    sunrise     = data["daily"]["sunrise"][0]
    sunset      = data["daily"]["sunset"][0]

    return {
        "temperature_c": temperature,
        "weather_code":  code,
        "description":   _WMO_DESCRIPTION.get(code, "Unknown"),
        "is_clear":      code in (0, 1, 2),
        "sunrise_iso":   sunrise,
        "sunset_iso":    sunset,
    }
