// =============================================================================
// DEV ONLY — SimulateWalk.js
//
// Provides a realistic walk simulation:
//   1. Tap map to set start point
//   2. Tap map to set destination
//   3. OSRM calculates the real walking route
//   4. App steps along the route at walking pace (every 12s = ~15m/step)
//
// Exports:
//   useSimulateWalk()     — hook, call in App.js
//   SimulateWalkPanel     — floating UI panel, render in App.js
//
// TO REMOVE FOR PRODUCTION:
//   1. Delete this file (mobile/dev/SimulateWalk.js)
//   2. In App.js remove all lines marked  // DEV ONLY
// =============================================================================

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STEP_DISTANCE_M  = 15;   // meters between each simulated GPS step
const STEP_INTERVAL_MS = 12000; // milliseconds between steps (12s ≈ casual walking pace)
const API_BASE         = 'https://tourai-agent-production.up.railway.app';

// ---------------------------------------------------------------------------
// Geo helpers
// ---------------------------------------------------------------------------

/** Haversine distance in metres between two lat/lon points */
function haversineM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * (Math.PI / 180);
  const dLon = (lon2 - lon1) * (Math.PI / 180);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * (Math.PI / 180)) *
    Math.cos(lat2 * (Math.PI / 180)) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** Compass bearing (0–360) from point A to point B */
function bearing(lat1, lon1, lat2, lon2) {
  const dLon = (lon2 - lon1) * (Math.PI / 180);
  const lat1R = lat1 * (Math.PI / 180);
  const lat2R = lat2 * (Math.PI / 180);
  const y = Math.sin(dLon) * Math.cos(lat2R);
  const x =
    Math.cos(lat1R) * Math.sin(lat2R) -
    Math.sin(lat1R) * Math.cos(lat2R) * Math.cos(dLon);
  return (Math.atan2(y, x) * (180 / Math.PI) + 360) % 360;
}

/**
 * Takes OSRM GeoJSON coordinates ([lon, lat] pairs) and returns an array of
 * { lat, lon, heading } steps spaced STEP_DISTANCE_M metres apart.
 */
function interpolateRoute(coords) {
  const steps = [];
  let remainder = 0;

  for (let i = 0; i < coords.length - 1; i++) {
    const [lon1, lat1] = coords[i];
    const [lon2, lat2] = coords[i + 1];
    const segDist = haversineM(lat1, lon1, lat2, lon2);
    const hdg     = bearing(lat1, lon1, lat2, lon2);

    let traveled = remainder;
    while (traveled <= segDist) {
      const t = traveled / segDist;
      steps.push({
        lat:     lat1 + t * (lat2 - lat1),
        lon:     lon1 + t * (lon2 - lon1),
        heading: hdg,
      });
      traveled += STEP_DISTANCE_M;
    }
    remainder = traveled - segDist;
  }

  // Always include the final point
  const last = coords[coords.length - 1];
  steps.push({ lat: last[1], lon: last[0], heading: steps.at(-1)?.heading ?? 0 });

  return steps;
}

// ---------------------------------------------------------------------------
// OSRM route fetcher
// ---------------------------------------------------------------------------

async function fetchRoute(start, end) {
  const url =
    `${API_BASE}/v1/route` +
    `?from_lat=${start.lat}&from_lon=${start.lon}` +
    `&to_lat=${end.lat}&to_lon=${end.lon}`;

  console.log('[SimWalk] fetchRoute →', url);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000); // 15s timeout

  let resp;
  try {
    resp = await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }

  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(`Route API ${resp.status}: ${body}`);
  }

  const data = await resp.json();
  console.log('[SimWalk] route response code:', data.code, 'routes:', data.routes?.length);

  if (data.code !== 'Ok' || !data.routes?.length) {
    throw new Error(`No route found (code: ${data.code})`);
  }

  const coords = data.routes[0].geometry.coordinates; // [lon, lat][]
  const result = {
    steps:     interpolateRoute(coords),
    polyline:  coords.map(([lon, lat]) => ({ latitude: lat, longitude: lon })),
    distanceM: Math.round(data.routes[0].distance),
    durationS: Math.round(data.routes[0].duration),
  };
  console.log('[SimWalk] route ready — steps:', result.steps.length, 'dist:', result.distanceM, 'm');
  return result;
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

const STATE = {
  IDLE:           'idle',
  SELECT_START:   'select_start',
  SELECT_END:     'select_end',
  ROUTE_LOADING:  'route_loading',
  ROUTE_READY:    'route_ready',
  WALKING:        'walking',
  DONE:           'done',
};

// ---------------------------------------------------------------------------
// useSimulateWalk hook
// ---------------------------------------------------------------------------

/**
 * Call this hook in App.js.
 *
 * Returns:
 *   sim.state          — current STATE value
 *   sim.startPoint     — { lat, lon } or null
 *   sim.endPoint       — { lat, lon } or null
 *   sim.polyline       — array of { latitude, longitude } for <Polyline>
 *   sim.stepIndex      — current step index (during walk)
 *   sim.totalSteps     — total steps in route
 *   sim.routeInfo      — { distanceM, durationS } or null
 *   sim.handleMapPress — (coordinate: { latitude, longitude }) => void
 *                        pass to MapView onPress
 *   sim.startWalk      — () => void
 *   sim.stopWalk       — () => void
 *   sim.reset          — () => void
 *   sim.onStep         — callback prop: ({ lat, lon, heading }) => void
 *                        Set this in App.js to update locationRef/headingRef
 *   sim.setOnStep      — (fn) => void  — register the App.js callback
 */
export function useSimulateWalk() {
  const [state, setState]         = useState(STATE.IDLE);
  const [startPoint, setStart]    = useState(null);
  const [endPoint, setEnd]        = useState(null);
  const [polyline, setPolyline]   = useState([]);
  const [stepIndex, setStepIndex] = useState(0);
  const [routeInfo, setRouteInfo] = useState(null);
  const [error, setError]         = useState(null);

  const stepsRef    = useRef([]);
  const intervalRef = useRef(null);
  const indexRef    = useRef(0);
  const onStepRef   = useRef(null); // App.js registers its callback here

  // Register App.js callback
  const setOnStep = useCallback(fn => { onStepRef.current = fn; }, []);

  // ── Map tap handler ────────────────────────────────────────────────────────
  const handleMapPress = useCallback(async ({ latitude, longitude }) => {
    if (state === STATE.SELECT_START) {
      setStart({ lat: latitude, lon: longitude });
      setState(STATE.SELECT_END);

    } else if (state === STATE.SELECT_END) {
      const end = { lat: latitude, lon: longitude };
      setEnd(end);
      setState(STATE.ROUTE_LOADING);
      setError(null);

      try {
        const route = await fetchRoute(startPoint, end);
        stepsRef.current = route.steps;
        setPolyline(route.polyline);
        setRouteInfo({ distanceM: route.distanceM, durationS: route.durationS });
        setState(STATE.ROUTE_READY);
      } catch (err) {
        console.error('[SimWalk] fetchRoute failed:', err.message, err);
        const msg = err.name === 'AbortError'
          ? 'Route timed out (OSRM server unreachable). Tap to retry.'
          : `Could not get route: ${err.message}. Tap to retry.`;
        setError(msg);
        setState(STATE.SELECT_END);
      }
    }
  }, [state, startPoint]);

  // ── Walk controls ──────────────────────────────────────────────────────────
  const startWalk = useCallback(() => {
    if (!stepsRef.current.length) return;
    indexRef.current = 0;
    setStepIndex(0);
    setState(STATE.WALKING);

    // Fire first step immediately
    onStepRef.current?.(stepsRef.current[0]);

    intervalRef.current = setInterval(() => {
      indexRef.current += 1;
      if (indexRef.current >= stepsRef.current.length) {
        clearInterval(intervalRef.current);
        setState(STATE.DONE);
        return;
      }
      setStepIndex(indexRef.current);
      onStepRef.current?.(stepsRef.current[indexRef.current]);
    }, STEP_INTERVAL_MS);
  }, []);

  const stopWalk = useCallback(() => {
    clearInterval(intervalRef.current);
    setState(STATE.ROUTE_READY);
  }, []);

  const reset = useCallback(() => {
    clearInterval(intervalRef.current);
    setStart(null);
    setEnd(null);
    setPolyline([]);
    setRouteInfo(null);
    setError(null);
    setStepIndex(0);
    stepsRef.current = [];
    indexRef.current = 0;
    setState(STATE.IDLE);
  }, []);

  // Cleanup on unmount
  useEffect(() => () => clearInterval(intervalRef.current), []);

  const startPlanning = useCallback(() => {
    reset();
    setState(STATE.SELECT_START);
  }, [reset]);

  return {
    state,
    startPoint,
    endPoint,
    polyline,
    stepIndex,
    totalSteps: stepsRef.current.length,
    routeInfo,
    error,
    handleMapPress,
    startPlanning,
    startWalk,
    stopWalk,
    reset,
    setOnStep,
  };
}

// ---------------------------------------------------------------------------
// SimulateWalkPanel — floating control UI
// ---------------------------------------------------------------------------

const INSTRUCTIONS = {
  [STATE.IDLE]:          null,
  [STATE.SELECT_START]:  '📍 Tap the map to set your START point',
  [STATE.SELECT_END]:    '🏁 Tap the map to set your DESTINATION',
  [STATE.ROUTE_LOADING]: '⏳ Calculating route…',
  [STATE.ROUTE_READY]:   null,
  [STATE.WALKING]:       null,
  [STATE.DONE]:          '🎉 Walk complete!',
};

function formatDistance(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`;
}

function formatDuration(s) {
  const min = Math.round(s / 60);
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)}h ${min % 60}m`;
}

/**
 * SimulateWalkPanel — render this anywhere in App.js (outside MapView).
 *
 * Props: spread the return value of useSimulateWalk()
 */
export function SimulateWalkPanel({
  state,
  stepIndex,
  totalSteps,
  routeInfo,
  error,
  startPlanning,
  startWalk,
  stopWalk,
  reset,
}) {
  const hint = INSTRUCTIONS[state];

  return (
    <View style={styles.container} pointerEvents="box-none">

      {/* Instruction banner above the button */}
      {hint ? (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>{hint}</Text>
        </View>
      ) : null}

      {/* Error banner */}
      {error ? (
        <View style={[styles.banner, styles.bannerError]}>
          <Text style={styles.bannerText}>{error}</Text>
        </View>
      ) : null}

      {/* Control pill */}
      <View style={styles.pill} pointerEvents="box-none">

        {state === STATE.IDLE && (
          <Pressable onPress={startPlanning}>
            <Text style={styles.idleHint}>🚶 Tap to plan a walk</Text>
          </Pressable>
        )}

        {state === STATE.ROUTE_LOADING && (
          <ActivityIndicator color="#1A73E8" size="small" />
        )}

        {state === STATE.ROUTE_READY && routeInfo && (
          <View style={styles.row}>
            <View style={styles.routeInfo}>
              <Text style={styles.routeInfoText}>
                {formatDistance(routeInfo.distanceM)}  ·  {formatDuration(routeInfo.durationS)}
              </Text>
              <Text style={styles.routeInfoSub}>
                {totalSteps} steps · {Math.round(STEP_INTERVAL_MS / 1000)}s each
              </Text>
            </View>
            <Pressable style={styles.btnGreen} onPress={startWalk}>
              <Text style={styles.btnText}>▶ Start</Text>
            </Pressable>
            <Pressable style={styles.btnGray} onPress={reset}>
              <Text style={styles.btnText}>✕</Text>
            </Pressable>
          </View>
        )}

        {state === STATE.WALKING && (
          <View style={styles.row}>
            <Text style={styles.progressText}>
              Step {stepIndex + 1} / {totalSteps}
            </Text>
            <View style={styles.progressBar}>
              <View
                style={[
                  styles.progressFill,
                  { width: `${((stepIndex + 1) / totalSteps) * 100}%` },
                ]}
              />
            </View>
            <Pressable style={styles.btnRed} onPress={stopWalk}>
              <Text style={styles.btnText}>⏹ Stop</Text>
            </Pressable>
          </View>
        )}

        {state === STATE.DONE && (
          <View style={styles.row}>
            <Text style={styles.doneText}>Walk complete 🎉</Text>
            <Pressable style={styles.btnGray} onPress={reset}>
              <Text style={styles.btnText}>Reset</Text>
            </Pressable>
          </View>
        )}
      </View>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    bottom: 175,
    left: 16,
    right: 16,
    alignItems: 'center',
    gap: 8,
  },
  banner: {
    backgroundColor: 'rgba(26,115,232,0.92)',
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 10,
    alignSelf: 'stretch',
  },
  bannerError: {
    backgroundColor: 'rgba(211,47,47,0.92)',
  },
  bannerText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
    textAlign: 'center',
  },
  pill: {
    backgroundColor: 'rgba(255,255,255,0.95)',
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 12,
    alignSelf: 'stretch',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.12,
    shadowRadius: 6,
    elevation: 4,
  },
  idleHint: {
    color: '#888',
    fontSize: 13,
    textAlign: 'center',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  routeInfo: {
    flex: 1,
  },
  routeInfoText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#1A1A1A',
  },
  routeInfoSub: {
    fontSize: 11,
    color: '#888',
    marginTop: 2,
  },
  progressText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#1A1A1A',
    minWidth: 75,
  },
  progressBar: {
    flex: 1,
    height: 6,
    backgroundColor: '#E0E0E0',
    borderRadius: 3,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#1A73E8',
    borderRadius: 3,
  },
  doneText: {
    flex: 1,
    fontSize: 14,
    fontWeight: '600',
    color: '#1A1A1A',
  },
  btnGreen: {
    backgroundColor: '#1A73E8',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  btnRed: {
    backgroundColor: '#D32F2F',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  btnGray: {
    backgroundColor: '#757575',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  btnText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '700',
  },
});
