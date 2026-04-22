"""
tests/load_test.py — Load testing script for TourAI API.

Usage:
    python tests/load_test.py                        # all scenarios (safe mode)
    python tests/load_test.py --scenario warm        # single scenario
    python tests/load_test.py --cold                 # include cold-cache scenarios
    python tests/load_test.py --url http://localhost:8000  # local server

⚠️  Cold-cache scenarios hit Geoapify for every unique location.
    100 cold requests = ~3% of the 3,000/day free quota.
    By default only warm-cache scenarios run. Pass --cold to enable all.

Scenarios:
    baseline        Single request — establish p50/p95/p99 baseline
    warm_burst      100 concurrent requests to cached coordinates
    walking         20 users × 10 GPS steps (realistic steady-state)
    heading_fan     50 users same location, spread across 16 heading buckets
    sustained_ramp  Ramp from 10 → 100 concurrent users over 60s
    mixed_traffic   Realistic endpoint mix (70% pois / 20% street / 10% health)
    cold_burst      100 users × unique city locations (Geoapify cold misses) [--cold]
    rate_limit      Verify 429 behaviour under >100 rpm from same IP  [--cold]
"""

import argparse
import asyncio
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_URL = "https://tourai-agent-production.up.railway.app"
TIMEOUT     = 30   # seconds per request

# ---------------------------------------------------------------------------
# Real downtown coordinates — known to have POIs
# ---------------------------------------------------------------------------

CITIES = [
    # (name,  lat,       lon,       heading)
    ("Dallas",        32.7787, -96.8083,  90.0),
    ("New York",      40.7484, -73.9967,  45.0),
    ("Chicago",       41.8781, -87.6298, 180.0),
    ("San Francisco", 37.7749,-122.4194, 270.0),
    ("Boston",        42.3601, -71.0589,   0.0),
    ("Washington DC", 38.8899, -77.0091,  90.0),
    ("London",        51.5074,  -0.1278, 135.0),
    ("Paris",         48.8566,   2.3522,  45.0),
    ("Berlin",        52.5200,  13.4050, 270.0),
    ("Tokyo",         35.6762, 139.6503,  90.0),
    ("Sydney",       -33.8688, 151.2093, 180.0),
    ("Amsterdam",     52.3676,   4.9041,  45.0),
    ("Rome",          41.9028,  12.4964,  90.0),
    ("Barcelona",     41.3851,   2.1734, 270.0),
    ("Vienna",        48.2082,  16.3738,   0.0),
    ("Prague",        50.0755,  14.4378, 135.0),
    ("Budapest",      47.4979,  19.0402,  90.0),
    ("Lisbon",        38.7169,  -9.1395, 180.0),
    ("Copenhagen",    55.6761,  12.5683,  45.0),
    ("Stockholm",     59.3293,  18.0686, 270.0),
]

DALLAS_LAT = 32.7787
DALLAS_LON = -96.8083


def _walk_steps(
    lat: float, lon: float, heading: float, steps: int, step_m: float = 15.0
) -> list[tuple[float, float, float]]:
    """Generate GPS points along a heading — simulates a user walking."""
    points = []
    R = 6371000
    for i in range(steps):
        d = i * step_m / R
        h = math.radians(heading)
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        new_lat = math.degrees(
            math.asin(math.sin(lat_r) * math.cos(d) +
                      math.cos(lat_r) * math.sin(d) * math.cos(h))
        )
        new_lon = math.degrees(
            lon_r + math.atan2(
                math.sin(h) * math.sin(d) * math.cos(lat_r),
                math.cos(d) - math.sin(lat_r) * math.sin(math.radians(new_lat))
            )
        )
        points.append((new_lat, new_lon, heading))
    return points


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    status:     int
    elapsed_ms: float
    cache_hit:  bool   = False
    error:      str    = ""


@dataclass
class ScenarioResult:
    name:    str
    results: list[RequestResult] = field(default_factory=list)

    def add(self, r: RequestResult) -> None:
        self.results.append(r)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success(self) -> int:
        return sum(1 for r in self.results if 200 <= r.status < 300)

    @property
    def errors(self) -> int:
        return self.total - self.success

    @property
    def error_rate(self) -> float:
        return self.errors / max(self.total, 1) * 100

    @property
    def cache_hits(self) -> int:
        return sum(1 for r in self.results if r.cache_hit)

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_hits / max(self.total, 1) * 100

    @property
    def status_codes(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    def latency(self, pct: float) -> float:
        times = sorted(r.elapsed_ms for r in self.results if r.status == 200)
        if not times:
            return 0.0
        idx = max(0, int(math.ceil(pct / 100 * len(times))) - 1)
        return times[idx]

    @property
    def rps(self) -> float:
        if not self.results:
            return 0.0
        times = [r.elapsed_ms for r in self.results]
        # Approximate: total requests / total wall time (assumes concurrent execution)
        return self.total / (sum(times) / 1000 / max(self.total, 1)) if times else 0.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _post_visible_pois(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    heading: float,
) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            "/v1/visible-pois",
            json={"latitude": lat, "longitude": lon, "heading": heading},
        )
        elapsed = (time.perf_counter() - t0) * 1000
        cache_hit = False
        if resp.status_code == 200:
            cache_hit = resp.json().get("cache_hit", False)
        return RequestResult(status=resp.status_code, elapsed_ms=elapsed, cache_hit=cache_hit)
    except httpx.TimeoutException:
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=0, elapsed_ms=elapsed, error="timeout")
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=0, elapsed_ms=elapsed, error=str(e))


async def _get_street(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp = await client.get("/v1/current-street", params={"lat": lat, "lon": lon})
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=resp.status_code, elapsed_ms=elapsed)
    except httpx.TimeoutException:
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=0, elapsed_ms=elapsed, error="timeout")
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=0, elapsed_ms=elapsed, error=str(e))


async def _get_health(client: httpx.AsyncClient) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp  = await client.get("/health")
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=resp.status_code, elapsed_ms=elapsed)
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return RequestResult(status=0, elapsed_ms=elapsed, error=str(e))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

async def scenario_baseline(client: httpx.AsyncClient) -> ScenarioResult:
    """Single request — establish clean baseline latency."""
    result = ScenarioResult("baseline — single request")
    r = await _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)
    result.add(r)
    return result


async def scenario_warm_burst(client: httpx.AsyncClient) -> ScenarioResult:
    """
    100 concurrent requests to the same coordinates.
    First request warms the cache; the rest should all be cache hits.
    Tests: cache throughput, no race conditions under concurrency.
    """
    result = ScenarioResult("warm burst — 100 concurrent (same location)")

    # Warm the cache first
    await _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)

    tasks = [
        _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)
        for _ in range(100)
    ]
    wall_start = time.perf_counter()
    responses  = await asyncio.gather(*tasks)
    wall_ms    = (time.perf_counter() - wall_start) * 1000

    for r in responses:
        result.add(r)

    result._wall_ms = wall_ms  # type: ignore[attr-defined]
    return result


async def scenario_walking(client: httpx.AsyncClient) -> ScenarioResult:
    """
    20 users each walking 10 GPS steps through Dallas (~150m total each).
    Steps are ~15m apart — crosses ~2 cache grid cells per walk.
    Tests: realistic sustained load, partial cache hits as users move.
    """
    result = ScenarioResult("walking — 20 users × 10 steps (150m walk)")

    async def _user_walk(user_id: int) -> list[RequestResult]:
        # Spread users slightly so they aren't all on the exact same path
        offset = user_id * 0.0001
        steps  = _walk_steps(
            DALLAS_LAT + offset, DALLAS_LON + offset,
            heading=90.0, steps=10, step_m=15.0
        )
        results = []
        for lat, lon, hdg in steps:
            r = await _post_visible_pois(client, lat, lon, hdg)
            results.append(r)
            await asyncio.sleep(0.1)   # 100ms between GPS pings per user
        return results

    wall_start  = time.perf_counter()
    all_results = await asyncio.gather(*[_user_walk(i) for i in range(20)])
    wall_ms     = (time.perf_counter() - wall_start) * 1000

    for user_results in all_results:
        for r in user_results:
            result.add(r)

    result._wall_ms = wall_ms  # type: ignore[attr-defined]
    return result


async def scenario_heading_fan(client: httpx.AsyncClient) -> ScenarioResult:
    """
    50 users at the same location, spread across all 16 heading buckets.
    POI data is shared (same grid cell → same POI cache key).
    Visibility results differ per heading bucket — tests partial cache hits.
    """
    result = ScenarioResult("heading fan — 50 users, 16 directions (same location)")

    # 16 buckets × 22.5° each
    headings = [i * 22.5 for i in range(16)]
    tasks = [
        _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, headings[i % 16])
        for i in range(50)
    ]
    responses = await asyncio.gather(*tasks)
    for r in responses:
        result.add(r)

    return result


async def scenario_sustained_ramp(client: httpx.AsyncClient) -> ScenarioResult:
    """
    Ramp from 10 → 100 concurrent users over 5 waves.
    Simulates organic traffic growth during peak hours.
    Tests: server stability under increasing load.
    """
    result = ScenarioResult("sustained ramp — 10 → 100 users (5 waves)")

    # Warm cache first
    await _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)

    for wave, concurrency in enumerate([10, 25, 50, 75, 100]):
        tasks = [
            _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)
            for _ in range(concurrency)
        ]
        responses = await asyncio.gather(*tasks)
        for r in responses:
            result.add(r)

        ok  = sum(1 for r in responses if r.status == 200)
        p95 = sorted(r.elapsed_ms for r in responses if r.status == 200)
        p95_val = p95[int(0.95 * len(p95)) - 1] if p95 else 0
        print(f"    wave {wave + 1}: {concurrency:>3} concurrent  "
              f"ok={ok}/{concurrency}  p95={p95_val:.0f}ms")

        await asyncio.sleep(1)   # brief pause between waves

    return result


async def scenario_mixed_traffic(client: httpx.AsyncClient) -> ScenarioResult:
    """
    Realistic endpoint mix over 100 requests:
      70% POST /v1/visible-pois
      20% GET  /v1/current-street
      10% GET  /health
    Tests: real traffic distribution, ensures all endpoints hold up.
    """
    result = ScenarioResult("mixed traffic — 70% pois / 20% street / 10% health")

    # Warm the POI cache
    await _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)

    tasks: list[Any] = []
    for i in range(100):
        roll = i % 10
        if roll < 7:
            tasks.append(_post_visible_pois(client, DALLAS_LAT, DALLAS_LON, float(i * 3.6 % 360)))
        elif roll < 9:
            tasks.append(_get_street(client, DALLAS_LAT, DALLAS_LON))
        else:
            tasks.append(_get_health(client))

    responses = await asyncio.gather(*tasks)
    for r in responses:
        result.add(r)

    return result


async def scenario_cold_burst(client: httpx.AsyncClient) -> ScenarioResult:
    """
    100 concurrent requests across 20 different cities — all cache misses.
    Hits Geoapify 20 times (one per unique grid cell).
    ⚠️  Uses ~20 Geoapify API credits.
    Tests: worst-case throughput when cache is cold.
    """
    result = ScenarioResult("cold burst — 20 cities × 5 users (cache misses)")

    tasks = []
    for i in range(100):
        city = CITIES[i % len(CITIES)]
        _, lat, lon, hdg = city
        # Offset slightly so each of the 5 users per city is in the same grid cell
        tasks.append(_post_visible_pois(client, lat, lon, hdg))

    wall_start = time.perf_counter()
    responses  = await asyncio.gather(*tasks)
    wall_ms    = (time.perf_counter() - wall_start) * 1000

    for r in responses:
        result.add(r)

    result._wall_ms = wall_ms  # type: ignore[attr-defined]
    return result


async def scenario_rate_limit(client: httpx.AsyncClient) -> ScenarioResult:
    """
    110 rapid requests from the same IP.
    Expects 429s after ~100 requests within 60 seconds.
    NOTE: Railway proxies all traffic through internal IPs — the rate limiter
    may treat all Railway traffic as one IP. This tests that behaviour.
    """
    result = ScenarioResult("rate limit — 110 rapid requests (expect 429s)")

    # Fire 110 requests as fast as possible
    tasks = [
        _post_visible_pois(client, DALLAS_LAT, DALLAS_LON, 90.0)
        for _ in range(110)
    ]
    responses = await asyncio.gather(*tasks)
    for r in responses:
        result.add(r)

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _bar(value: float, max_val: float, width: int = 20) -> str:
    filled = int(value / max_val * width) if max_val > 0 else 0
    return "█" * filled + "░" * (width - filled)


def _print_result(r: ScenarioResult) -> None:
    ok_times = sorted(r2.elapsed_ms for r2 in r.results if r2.status == 200)

    p50  = statistics.median(ok_times)         if ok_times else 0
    p95  = ok_times[int(0.95 * len(ok_times)) - 1] if ok_times else 0
    p99  = ok_times[int(0.99 * len(ok_times)) - 1] if ok_times else 0
    mean = statistics.mean(ok_times)           if ok_times else 0
    mn   = min(ok_times, default=0)
    mx   = max(ok_times, default=0)

    wall_ms = getattr(r, "_wall_ms", None)

    print(f"\n{'─' * 60}")
    print(f"  {r.name}")
    print(f"{'─' * 60}")
    print(f"  Requests   : {r.total}  ✓ {r.success}  ✗ {r.errors}  "
          f"error_rate={r.error_rate:.1f}%")
    print(f"  Cache hits : {r.cache_hits}/{r.total} ({r.cache_hit_rate:.0f}%)")
    print(f"  Status codes: {dict(sorted(r.status_codes.items()))}")
    if wall_ms:
        actual_rps = r.total / (wall_ms / 1000)
        print(f"  Throughput : {actual_rps:.1f} req/s  (wall={wall_ms:.0f}ms)")
    print()
    print(f"  Latency (successful requests only):")
    print(f"    min  : {mn:>7.0f}ms")
    print(f"    mean : {mean:>7.0f}ms  {_bar(mean, mx)}")
    print(f"    p50  : {p50:>7.0f}ms  {_bar(p50,  mx)}")
    print(f"    p95  : {p95:>7.0f}ms  {_bar(p95,  mx)}")
    print(f"    p99  : {p99:>7.0f}ms  {_bar(p99,  mx)}")
    print(f"    max  : {mx:>7.0f}ms  {_bar(mx,   mx)}")

    # Alert thresholds
    alerts = []
    if r.error_rate > 5:
        alerts.append(f"⚠️  error rate {r.error_rate:.1f}% > 5% threshold")
    if p95 > 2000:
        alerts.append(f"⚠️  p95 {p95:.0f}ms > 2000ms threshold")
    if r.cache_hit_rate < 30 and r.total > 10:
        alerts.append(f"⚠️  cache hit rate {r.cache_hit_rate:.0f}% < 30%")
    for alert in alerts:
        print(f"  {alert}")

    if not alerts:
        print("  ✅ All thresholds passed")


def _print_summary(results: list[ScenarioResult]) -> None:
    print(f"\n{'═' * 60}")
    print("  SUMMARY")
    print(f"{'═' * 60}")
    print(f"  {'Scenario':<42} {'ok%':>4}  {'p95':>6}  {'cache%':>6}")
    print(f"  {'─' * 42} {'────':>4}  {'──────':>6}  {'──────':>6}")
    for r in results:
        ok_times = sorted(r2.elapsed_ms for r2 in r.results if r2.status == 200)
        p95 = ok_times[int(0.95 * len(ok_times)) - 1] if ok_times else 0
        ok_pct = r.success / max(r.total, 1) * 100
        name   = r.name[:42]
        flag   = "✅" if r.error_rate <= 5 and p95 <= 2000 else "⚠️ "
        print(f"  {flag} {name:<42} {ok_pct:>3.0f}%  {p95:>5.0f}ms  {r.cache_hit_rate:>5.0f}%")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SAFE_SCENARIOS = [
    "baseline",
    "warm_burst",
    "walking",
    "heading_fan",
    "sustained_ramp",
    "mixed_traffic",
]

COLD_SCENARIOS = [
    "cold_burst",
    "rate_limit",
]


async def run(url: str, scenarios: list[str]) -> None:
    print(f"\nTourAI Load Test")
    print(f"Target  : {url}")
    print(f"Scenarios: {', '.join(scenarios)}")
    print(f"Time    : {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    async with httpx.AsyncClient(
        base_url=url,
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
    ) as client:

        # Verify the server is up before starting
        try:
            health = await client.get("/health")
            health.raise_for_status()
            print(f"\n✅ Server healthy: {health.json().get('status')}")
        except Exception as e:
            print(f"\n❌ Server unreachable: {e}")
            sys.exit(1)

        all_results: list[ScenarioResult] = []
        scenario_map = {
            "baseline":       scenario_baseline,
            "warm_burst":     scenario_warm_burst,
            "walking":        scenario_walking,
            "heading_fan":    scenario_heading_fan,
            "sustained_ramp": scenario_sustained_ramp,
            "mixed_traffic":  scenario_mixed_traffic,
            "cold_burst":     scenario_cold_burst,
            "rate_limit":     scenario_rate_limit,
        }

        for name in scenarios:
            fn = scenario_map.get(name)
            if not fn:
                print(f"\n⚠️  Unknown scenario: {name}")
                continue

            print(f"\n▶  Running: {name} ...")
            result = await fn(client)
            _print_result(result)
            all_results.append(result)

        _print_summary(all_results)


def main() -> None:
    parser = argparse.ArgumentParser(description="TourAI load tester")
    parser.add_argument("--url",      default=DEFAULT_URL, help="API base URL")
    parser.add_argument("--scenario", default="all",       help="Scenario name or 'all'")
    parser.add_argument("--cold",     action="store_true", help="Include cold-cache scenarios (uses Geoapify credits)")
    args = parser.parse_args()

    if args.scenario == "all":
        scenarios = SAFE_SCENARIOS + (COLD_SCENARIOS if args.cold else [])
    else:
        scenarios = [args.scenario]

    if any(s in scenarios for s in COLD_SCENARIOS):
        print("⚠️  Cold-cache scenarios enabled — will consume ~20 Geoapify API credits.")

    asyncio.run(run(args.url, scenarios))


if __name__ == "__main__":
    main()
