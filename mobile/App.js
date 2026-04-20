/**
 * TourAI — walking tour guide (Expo Go)
 *
 * Every 5 seconds:
 *   1. Get GPS position + compass heading (expo-location)
 *   2. POST /v1/visible-pois → update blue markers on map
 *   3. Show street badge + story count badge
 *
 * Tap a blue marker → POIDetail modal
 * Voice button → ask a question about your surroundings
 */

import { registerRootComponent } from 'expo';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AppState,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import * as Location from 'expo-location';
import { StatusBar } from 'expo-status-bar';
import MapView, { Marker, Polyline, PROVIDER_DEFAULT } from 'react-native-maps';

import BottomBar from './components/BottomBar';
import GlowMarker from './components/GlowMarker';
import POIDetail from './components/POIDetail';

// DEV ONLY — remove these imports for production
import { useSimulateWalk, SimulateWalkPanel } from './dev/SimulateWalk';
import SimUserMarker from './dev/SimUserMarker';

// ── Config ──────────────────────────────────────────────────────────────────

const API_BASE          = 'https://tourai-agent-production.up.railway.app';
const POLL_INTERVAL_MS  = 5000;
const DEFAULT_RADIUS    = 500;
const MIN_MOVE_M        = 8;   // skip poll if moved less than this
const FORCE_POLL_MS     = 30000; // always repoll after this long even if stationary

// Equirectangular distance — accurate enough for small thresholds
function roughDistM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * (Math.PI / 180) * Math.cos((lat1 + lat2) / 2 * (Math.PI / 180));
  const y = (lat2 - lat1) * (Math.PI / 180);
  return R * Math.sqrt(x * x + y * y);
}

// ── API helpers ──────────────────────────────────────────────────────────────

async function fetchVisiblePois(lat, lon, heading) {
  const resp = await fetch(`${API_BASE}/v1/visible-pois`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      latitude: lat,
      longitude: lon,
      heading,
      radius: DEFAULT_RADIUS,
    }),
  });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return resp.json();
}

async function fetchStory(poi) {
  // Fix 9 — 6-second client-side timeout so the spinner never hangs indefinitely
  const controller = new AbortController();
  const timeoutId  = setTimeout(() => controller.abort(), 6000);
  try {
    const resp = await fetch(`${API_BASE}/v1/story`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        poi_id:    String(poi.id),
        poi_name:  poi.name,
        poi_type:  poi.poi_type,
        tags:      poi.tags ?? {},
        latitude:  poi.lat,
        longitude: poi.lon,
      }),
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    const data = await resp.json();
    return data.story;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function reportFalsePositive(poi, currentLat, currentLon, currentHeading, street) {
  // Use the location+heading from when the dot appeared, not current position.
  // This gives an accurate diagnosis of why the filter showed the POI.
  const lat     = poi._poll_lat     ?? currentLat;
  const lon     = poi._poll_lon     ?? currentLon;
  const heading = poi._poll_heading ?? currentHeading;

  const resp = await fetch(`${API_BASE}/v1/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      latitude:     lat,
      longitude:    lon,
      heading:      heading,
      poi_id:       poi.id,
      poi_name:     poi.name,
      poi_lat:      poi.lat,
      poi_lon:      poi.lon,
      poi_tags:     poi.tags ?? {},
      poi_geometry: [],
      user_says:    'NO',
      user_street:  street ?? null,
      note:         'Reported via app — user cannot see this POI',
    }),
  });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return resp.json();
}

async function askQuestion(question, lat, lon, nearbyPois) {
  const resp = await fetch(`${API_BASE}/v1/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      latitude: lat,
      longitude: lon,
      context: {
        nearby_pois: (nearbyPois ?? []).slice(0, 5).map(p => ({
          name: p.name,
          type: p.poi_type,
          distance_m: Math.round(p.distance_m),
        })),
      },
    }),
  });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  const data = await resp.json();
  return data.answer ?? 'No answer received.';
}

// ── Main component ───────────────────────────────────────────────────────────

export default function App() {
  const [location, setLocation] = useState(null);
  const [visiblePois, setVisiblePois] = useState([]);
  const [streetName, setStreetName] = useState(null);
  const [selectedPoi, setSelectedPoi] = useState(null);
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [error, setError] = useState(null);

  const headingRef       = useRef(0);
  const lastPollHeadingRef = useRef(0);    // heading at the time of last poll
  const locationRef      = useRef(null);
  const intervalRef      = useRef(null);
  const appStateRef      = useRef(AppState.currentState);
  const mapRef           = useRef(null);
  const lastPollLocRef   = useRef(null);   // stationary detection
  const lastPollTimeRef  = useRef(0);
  const trackTimerRef    = useRef(null);   // fix 7 — tracksViewChanges timer

  // Fix 7 — tracksViewChanges: true while markers are new, false after 2 pulse cycles
  const [trackMarkers, setTrackMarkers] = useState(true);

  // DEV ONLY — simulation hook, active-guard ref, and current position for marker
  const sim = useSimulateWalk();                          // DEV ONLY
  const simActiveRef  = useRef(false);                    // DEV ONLY
  const [simPos, setSimPos] = useState(null);             // DEV ONLY — { lat, lon, heading }

  // ── Keep location ref in sync ────────────────────────────────────────────

  useEffect(() => {
    locationRef.current = location;
  }, [location]);

  // Fix 7 — re-enable view tracking whenever the marker set changes, then freeze
  useEffect(() => {
    setTrackMarkers(true);
    clearTimeout(trackTimerRef.current);
    trackTimerRef.current = setTimeout(() => setTrackMarkers(false), 3500);
    return () => clearTimeout(trackTimerRef.current);
  }, [visiblePois]);

  // ── API poll ─────────────────────────────────────────────────────────────

  // force=true bypasses the stationary check (used by sim steps + foreground resume)
  const pollApi = useCallback(async (force = false) => {
    const loc = locationRef.current;
    if (!loc) return;

    // Fix 3 — skip if stationary and polled recently
    if (!force) {
      const now  = Date.now();
      const last = lastPollLocRef.current;
      if (last && (now - lastPollTimeRef.current) < FORCE_POLL_MS) {
        const moved = roughDistM(last.lat, last.lon, loc.lat, loc.lon);
        if (moved < MIN_MOVE_M) return;
      }
    }
    lastPollLocRef.current  = loc;
    lastPollTimeRef.current = Date.now();

    try {
      const pollHeading = headingRef.current;
      const pollLoc     = { ...loc };
      const data = await fetchVisiblePois(pollLoc.lat, pollLoc.lon, pollHeading);
      lastPollHeadingRef.current = pollHeading;
      // Tag each POI with the location+heading at the time it was shown
      const pois = (data.visible_pois ?? []).map(p => ({
        ...p,
        _poll_lat:     pollLoc.lat,
        _poll_lon:     pollLoc.lon,
        _poll_heading: pollHeading,
      }));
      setVisiblePois(pois);
      setStreetName(data.street_name ?? null);
      setError(null);
    } catch (err) {
      console.warn('API poll failed:', err.message);
    }
  }, []);

  // ── Bootstrap: permissions + location watching ────────────────────────────

  useEffect(() => {
    let positionSub = null;
    let headingSub = null;

    (async () => {
      // Location permission
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission required. Enable in Settings → TourAI → Location.');
        return;
      }
      setPermissionGranted(true);

      // First fix immediately
      const pos = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.High,
      });
      const loc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      locationRef.current = loc;
      setLocation(loc);
      mapRef.current?.animateToRegion(
        {
          latitude: loc.lat,
          longitude: loc.lon,
          latitudeDelta: 0.005,
          longitudeDelta: 0.005,
        },
        800,
      );
      pollApi();

      // Continuous position watch — skipped when simulation is active (DEV ONLY guard)
      positionSub = await Location.watchPositionAsync(
        {
          accuracy: Location.Accuracy.High,
          timeInterval: 4000,
          distanceInterval: 5,
        },
        pos => {
          if (simActiveRef.current) return; // DEV ONLY — ignore real GPS during simulation
          const newLoc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
          locationRef.current = newLoc;
          setLocation(newLoc);
          setError(null);
        },
      );

      // Compass heading — only use trueHeading (requires motion to compute).
      // magHeading is too noisy when stationary; keeping last known value is better.
      // If heading swings >60° since last poll, clear stale dots immediately so
      // POIs that were in FOV don't linger after the user turns away.
      headingSub = await Location.watchHeadingAsync(hdg => {
        if (hdg.trueHeading < 0) return;
        const prev = headingRef.current;
        headingRef.current = hdg.trueHeading;
        const delta = Math.abs(((hdg.trueHeading - prev) + 540) % 360 - 180);
        const pollDelta = Math.abs(((hdg.trueHeading - lastPollHeadingRef.current) + 540) % 360 - 180);
        if (delta > 5 && pollDelta > 60) {
          setVisiblePois([]);  // clear stale dots — next poll will repopulate
        }
      });

      // 5-second poll interval
      intervalRef.current = setInterval(pollApi, POLL_INTERVAL_MS);
    })();

    return () => {
      positionSub?.remove();
      headingSub?.remove();
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Resume on foreground ──────────────────────────────────────────────────

  useEffect(() => {
    const sub = AppState.addEventListener('change', next => {
      if (appStateRef.current.match(/inactive|background/) && next === 'active') {
        pollApi(true); // force refresh when coming back to foreground
      }
      appStateRef.current = next;
    });
    return () => sub.remove();
  }, [pollApi]);

  // ── DEV ONLY — simulation handlers ───────────────────────────────────────
  // Called by SimulateWalk on each route step — overrides real GPS location.
  const handleSimStep = useCallback(({ lat, lon, heading }) => {
    simActiveRef.current = true;
    headingRef.current = heading;
    locationRef.current = { lat, lon };
    setLocation({ lat, lon });
    setSimPos({ lat, lon, heading });           // DEV ONLY — update marker position
    mapRef.current?.animateToRegion(
      { latitude: lat, longitude: lon, latitudeDelta: 0.005, longitudeDelta: 0.005 },
      400,
    );
    pollApi(true);
  }, [pollApi]);

  // Called when simulation ends — resumes real GPS, clears sim marker.
  const handleSimStop = useCallback(() => {
    simActiveRef.current = false;
    setSimPos(null);                            // DEV ONLY — remove marker
  }, []);

  // Register step callback with the sim hook so it fires on each route step.
  useEffect(() => {                              // DEV ONLY
    sim.setOnStep(handleSimStep);                // DEV ONLY
  }, [sim.setOnStep, handleSimStep]);            // DEV ONLY
  // ── END DEV ONLY ──────────────────────────────────────────────────────────

  // ── Voice ask handler ─────────────────────────────────────────────────────

  const handleAsk = useCallback(async question => {
    const loc = locationRef.current;
    if (!loc) throw new Error('Location not available yet.');
    return askQuestion(question, loc.lat, loc.lon, visiblePois);
  }, [visiblePois]);

  const handleReport = useCallback(async (poi) => {
    const loc = locationRef.current;
    if (!loc) throw new Error('Location not available');
    return reportFalsePositive(poi, loc.lat, loc.lon, headingRef.current, streetName);
  }, [streetName]);

  // Fix 8 — in-memory story cache so re-tapping a marker skips the network call
  const storyCacheRef = useRef(new Map());
  const handleFetchStory = useCallback(async poi => {
    const key = String(poi.id);
    if (storyCacheRef.current.has(key)) return storyCacheRef.current.get(key);
    const story = await fetchStory(poi);
    storyCacheRef.current.set(key, story);
    return story;
  }, []);

  // ── Render ────────────────────────────────────────────────────────────────

  const initialRegion = {
    latitude: location?.lat ?? 32.7787,
    longitude: location?.lon ?? -96.8083,
    latitudeDelta: 0.005,
    longitudeDelta: 0.005,
  };

  return (
    <SafeAreaProvider>
    <SafeAreaView style={styles.safe}>
      <StatusBar style="dark" />

      {/* Map fills the screen */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={PROVIDER_DEFAULT}
        initialRegion={initialRegion}
        showsUserLocation={!simPos}
        showsMyLocationButton={false}
        showsCompass={false}
        onPress={e => sim.handleMapPress(e.nativeEvent.coordinate)}>
        {/* DEV ONLY — tap map to set start/end points */}

        {/* POI markers — glowing animated dots */}
        {/* tracksViewChanges: true for 3.5s so pulse animates, then false to save re-renders */}
        {visiblePois.map(poi => (
          <Marker
            key={String(poi.id)}
            coordinate={{ latitude: poi.lat, longitude: poi.lon }}
            onPress={() => setSelectedPoi(poi)}
            anchor={{ x: 0.5, y: 0.5 }}
            tracksViewChanges={trackMarkers}>
            <GlowMarker />
          </Marker>
        ))}

        {/* DEV ONLY — simulated route polyline */}
        {sim.polyline.length > 0 && (
          <Polyline
            coordinates={sim.polyline}
            strokeColor="#1A73E8"
            strokeWidth={3}
            lineDashPattern={[6, 4]}
          />
        )}

        {/* DEV ONLY — start marker */}
        {sim.startPoint && (
          <Marker
            coordinate={{ latitude: sim.startPoint.lat, longitude: sim.startPoint.lon }}
            pinColor="#34A853"
            title="Start"
          />
        )}

        {/* DEV ONLY — end marker */}
        {sim.endPoint && (
          <Marker
            coordinate={{ latitude: sim.endPoint.lat, longitude: sim.endPoint.lon }}
            pinColor="#EA4335"
            title="Destination"
          />
        )}
        {/* DEV ONLY — simulated user position marker */}
        {simPos && (
          <Marker
            coordinate={{ latitude: simPos.lat, longitude: simPos.lon }}
            anchor={{ x: 0.5, y: 0.5 }}
            tracksViewChanges={false}
            zIndex={99}>
            <SimUserMarker heading={simPos.heading} />
          </Marker>
        )}
        {/* END DEV ONLY */}
      </MapView>

      {/* Error banner — only shown for hard errors like permission denied */}
      {error ? (
        <View style={styles.errorBanner} pointerEvents="none">
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      {/* Bottom bar — street name + story count + ask button */}
      <BottomBar
        streetName={streetName}
        storyCount={visiblePois.length}
        onAsk={handleAsk}
      />

      {/* POI detail modal */}
      <POIDetail
        poi={selectedPoi}
        visible={selectedPoi !== null}
        onClose={() => setSelectedPoi(null)}
        onFetchStory={handleFetchStory}
        onReport={handleReport}
      />

      {/* DEV ONLY — remove <SimulateWalkPanel> for production */}
      <SimulateWalkPanel
        state={sim.state}
        stepIndex={sim.stepIndex}
        totalSteps={sim.totalSteps}
        routeInfo={sim.routeInfo}
        error={sim.error}
        startPlanning={sim.startPlanning}
        startWalk={sim.startWalk}
        stopWalk={() => { sim.stopWalk(); handleSimStop(); }}
        reset={() => { sim.reset(); handleSimStop(); }}
      />
      {/* END DEV ONLY */}
    </SafeAreaView>
    </SafeAreaProvider>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  map: {
    ...StyleSheet.absoluteFillObject,
  },
  errorBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 20,
    left: 16,
    right: 16,
    backgroundColor: '#FEF2F2',
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderLeftWidth: 4,
    borderLeftColor: '#DC2626',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 4,
  },
  errorText: {
    color: '#DC2626',
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '500',
  },
});

registerRootComponent(App);
