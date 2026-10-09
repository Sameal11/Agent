"""Current weather and a short forecast from Open-Meteo (free, no API key).

A data API instead of scraping weather sites: those block automated clients, render their
numbers with JavaScript, and change layout often. Open-Meteo returns measured/modelled
values as JSON, so the model can state the actual conditions instead of handing the user
a list of links.
"""
from __future__ import annotations

import json
import urllib.parse

from linux_mcp.schemas import ToolResult, WeatherArgs
from linux_mcp.utils import http

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as listed at https://open-meteo.com/en/docs
WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snowfall", 73: "moderate snowfall", 75: "heavy snowfall", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 97: "heavy thunderstorm",
    99: "thunderstorm with heavy hail",
}

_CURRENT = "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"


def _get_json(url: str, params: dict) -> dict:
    resp = http.request(f"{url}?{urllib.parse.urlencode(params)}", timeout=10)
    if resp.status != 200:
        raise http.FetchError("connection", f"HTTP {resp.status} from {url}")
    return json.loads(resp.text())


def _condition(code) -> str:
    return WMO_CODES.get(code, f"unknown (WMO code {code})")


def find_place(location: str) -> dict | None:
    """'Chennai' or 'Chennai, India' / 'Springfield, Illinois'. The geocoder matches only a
    place name, so text after the first comma is used to pick among same-named places."""
    name, _, qualifier = (part.strip() for part in location.partition(","))
    results = _get_json(GEOCODE_URL, {"name": name, "count": 10, "language": "en", "format": "json"}
                        ).get("results") or []
    if qualifier:
        q = qualifier.lower()
        matching = [r for r in results if any(
            q in str(r.get(k, "")).lower() for k in ("country", "country_code", "admin1", "admin2"))]
        results = matching or results
    return results[0] if results else None


def get_weather(args: WeatherArgs) -> ToolResult:
    try:
        place = find_place(args.location)
        if place is None:
            return ToolResult(ok=False, error=(
                f"No place called '{args.location}' was found. Check the spelling or use the "
                "nearest city name."))
        data = _get_json(FORECAST_URL, {
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": _CURRENT, "daily": _DAILY, "timezone": "auto", "forecast_days": args.days,
        })
    except (http.FetchError, OSError, ValueError, KeyError) as e:
        return ToolResult(ok=False, error=f"Weather service unavailable: {e}")

    cur, units = data.get("current", {}), data.get("current_units", {})
    daily = data.get("daily", {})
    forecast = [
        {"date": day, "condition": _condition(code), "min_c": lo, "max_c": hi, "rain_chance_pct": rain}
        for day, code, lo, hi, rain in zip(
            daily.get("time", []), daily.get("weather_code", []), daily.get("temperature_2m_min", []),
            daily.get("temperature_2m_max", []), daily.get("precipitation_probability_max", []))
    ]
    where = ", ".join(p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p)
    return ToolResult(ok=True, data={
        "location": where,
        "observed_at": f"{cur.get('time')} ({data.get('timezone')})",
        "current": {
            "condition": _condition(cur.get("weather_code")),
            "temperature": f"{cur.get('temperature_2m')}{units.get('temperature_2m', '')}",
            "feels_like": f"{cur.get('apparent_temperature')}{units.get('apparent_temperature', '')}",
            "humidity": f"{cur.get('relative_humidity_2m')}%",
            "wind": f"{cur.get('wind_speed_10m')} {units.get('wind_speed_10m', '')}".strip(),
            "precipitation": f"{cur.get('precipitation')} {units.get('precipitation', '')}".strip(),
        },
        "forecast": forecast,
        "source": "open-meteo.com",
    })


TOOL_SPEC = {
    "name": "get_weather",
    "description": "Current weather and a daily forecast for a city (temperature, feels-like, humidity, "
                   "wind, conditions, rain chance). Use this for any weather question and answer with "
                   "the numbers; do not search the web for weather.",
    "args_model": WeatherArgs,
    "handler": get_weather,
}
