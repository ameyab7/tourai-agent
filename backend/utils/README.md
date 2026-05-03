# Utils Module

External API wrappers and utilities.

```
utils/
├── geoapify_places.py    # Geoapify Places API (POIs)
├── geoapify_buildings.py # Geoapify building data
├── google_places.py       # Google geocoding + disambiguation
├── overpass.py           # OpenStreetMap Overpass API
├── osrm.py               # OSRM routing
├── poi_ranker.py        # POI scoring by interests
├── visibility.py         # Visibility calculations for Live Walk
├── weather.py           # Open-Meteo weather API
├── golden_hour.py       # Golden hour calculations
└── geoutils.py          # Geo utilities (haversine, bearing, etc.)
```

**Key functions:**
- `fetch_pois(lat, lon, radius, api_key)` — Fetch POIs from Geoapify
- `geocode_destination(destination, api_key)` — Geocode with disambiguation
- `get_forecast(lat, lon, dates)` — Weather forecast
- `score_poi(poi, interests, lat, lon)` — POI scoring
- `filter_visible(lat, lon, heading, pois)` — Visibility filter
- `get_light_windows(lat, lon, date)` — Golden/blue hour windows