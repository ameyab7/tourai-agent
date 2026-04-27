"""utils/weather.py — Current weather + multi-day forecast via Open-Meteo (no API key)."""

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


async def get_forecast(lat: float, lon: float, dates: list[str]) -> list[dict]:
    """Return daily weather for each requested date (up to 16 days ahead).

    Each entry: date, temp_high_c, temp_low_c, description, is_clear,
                is_rainy, sunrise_iso, sunset_iso.
    Dates not found in the forecast window are silently omitted.
    """
    params = {
        "latitude":      lat,
        "longitude":     lon,
        "daily":         "temperature_2m_max,temperature_2m_min,weather_code,sunrise,sunset",
        "timezone":      "auto",
        "forecast_days": 16,
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(_BASE, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []

    daily    = data.get("daily", {})
    times    = daily.get("time", [])
    date_set = set(dates)
    result   = []
    n        = len(times)
    for i, d in enumerate(times):
        if d not in date_set:
            continue
        def _get(key, default=None):
            arr = daily.get(key, [])
            return arr[i] if i < len(arr) else default
        code = _get("weather_code", 0)
        result.append({
            "date":        d,
            "temp_high_c": round(_get("temperature_2m_max", 0), 1),
            "temp_low_c":  round(_get("temperature_2m_min", 0), 1),
            "description": _WMO_DESCRIPTION.get(code, "Unknown"),
            "is_clear":    code in (0, 1, 2),
            "is_rainy":    code in (51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99),
            "sunrise_iso": _get("sunrise", ""),
            "sunset_iso":  _get("sunset", ""),
        })
    return result
