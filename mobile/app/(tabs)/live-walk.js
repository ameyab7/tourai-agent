import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AppState,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as Location from 'expo-location';
import * as Speech from 'expo-speech';
import { StatusBar } from 'expo-status-bar';
import { router } from 'expo-router';
import MapView, { Marker, Polyline, PROVIDER_DEFAULT } from 'react-native-maps';
import { isPremium } from '../../lib/purchases';

import BottomBar from '../../components/BottomBar';
import GlowMarker from '../../components/GlowMarker';
import NowPlayingCard from '../../components/NowPlayingCard';
import POIDetail from '../../components/POIDetail';
import PoiListSheet from '../../components/PoiListSheet';
import StoryHistorySheet from '../../components/StoryHistorySheet';

// DEV ONLY
import { useSimulateWalk, SimulateWalkPanel } from '../../dev/SimulateWalk';
import SimUserMarker from '../../dev/SimUserMarker';

// ── Config ───────────────────────────────────────────────────────────────────

const API_BASE         = 'https://tourai-agent-production.up.railway.app';
const POLL_INTERVAL_MS = 5000;
const DEFAULT_RADIUS   = 500;
const MIN_MOVE_M       = 8;
const FORCE_POLL_MS    = 30000;

function roughDistM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * (Math.PI / 180) * Math.cos((lat1 + lat2) / 2 * (Math.PI / 180));
  const y = (lat2 - lat1) * (Math.PI / 180);
  return R * Math.sqrt(x * x + y * y);
}

// ── API helpers ───────────────────────────────────────────────────────────────

async function fetchVisiblePois(lat, lon, heading) {
  const resp = await fetch(`${API_BASE}/v1/visible-pois`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ latitude: lat, longitude: lon, heading, radius: DEFAULT_RADIUS }),
  });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return resp.json();
}

async function fetchStory(poi) {
  const controller = new AbortController();
  const timeoutId  = setTimeout(() => controller.abort(), 6000);
  try {
    const resp = await fetch(`${API_BASE}/v1/story`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        poi_id:   String(poi.id),
        poi_name: poi.name,
        poi_type: poi.poi_type,
        tags:     poi.tags ?? {},
        latitude: poi.lat,
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
  const lat     = poi._poll_lat     ?? currentLat;
  const lon     = poi._poll_lon     ?? currentLon;
  const heading = poi._poll_heading ?? currentHeading;
  const resp = await fetch(`${API_BASE}/v1/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      latitude: lat, longitude: lon, heading,
      poi_id: poi.id, poi_name: poi.name,
      poi_lat: poi.lat, poi_lon: poi.lon,
      poi_tags: poi.tags ?? {}, poi_geometry: [],
      user_says: 'NO', user_street: street ?? null,
      note: 'Reported via app — user cannot see this POI',
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
      question, latitude: lat, longitude: lon,
      context: {
        nearby_pois: (nearbyPois ?? []).slice(0, 5).map(p => ({
          name: p.name, type: p.poi_type, distance_m: Math.round(p.distance_m),
        })),
      },
    }),
  });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  const data = await resp.json();
  return data.answer ?? 'No answer received.';
}

// ── Constants ────────────────────────────────────────────────────────────────
const PREVIEW_DURATION_MS = 15 * 60 * 1000; // 15 minutes

// ── Screen ────────────────────────────────────────────────────────────────────

export default function LiveWalkScreen() {
  const [premium,        setPremium]        = useState(null); // null = checking
  const [previewExpired, setPreviewExpired] = useState(false);
  const previewTimerRef = useRef(null);

  // Check premium status on mount
  useEffect(() => {
    isPremium().then(ok => {
      setPremium(ok);
      if (!ok) {
        // Start 15-min preview countdown
        previewTimerRef.current = setTimeout(() => setPreviewExpired(true), PREVIEW_DURATION_MS);
      }
    });
    return () => clearTimeout(previewTimerRef.current);
  }, []);

  // Show nothing while checking
  if (premium === null) return <View style={{ flex: 1, backgroundColor: '#000' }} />;

  // Non-premium + preview expired → hard paywall
  if (!premium && previewExpired) {
    return (
      <SafeAreaView style={gate.safe}>
        <View style={gate.box}>
          <Text style={gate.emoji}>🔒</Text>
          <Text style={gate.title}>Your preview has ended</Text>
          <Text style={gate.sub}>
            Upgrade to Premium to keep exploring with AI-powered audio tours.
          </Text>
          <Pressable style={gate.btn} onPress={() => router.push('/paywall')}>
            <Text style={gate.btnText}>See Premium Plans</Text>
          </Pressable>
          <Pressable style={gate.backBtn} onPress={() => router.replace('/(tabs)')}>
            <Text style={gate.backText}>Go back to Home</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  const [location, setLocation]     = useState(null);
  const [visiblePois, setVisiblePois] = useState([]);
  const [streetName, setStreetName] = useState(null);
  const [selectedPoi, setSelectedPoi] = useState(null);
  const [error, setError]           = useState(null);

  const [nowPlaying, setNowPlaying]     = useState({ poi: null, story: null, active: false });
  const [storyHistory, setStoryHistory] = useState([]);
  const [showHistory, setShowHistory]   = useState(false);
  const [showPoiList, setShowPoiList]   = useState(false);
  const [trackMarkers, setTrackMarkers] = useState(true);

  const headingRef         = useRef(0);
  const lastPollHeadingRef = useRef(0);
  const locationRef        = useRef(null);
  const intervalRef        = useRef(null);
  const appStateRef        = useRef(AppState.currentState);
  const mapRef             = useRef(null);
  const lastPollLocRef     = useRef(null);
  const lastPollTimeRef    = useRef(0);
  const trackTimerRef      = useRef(null);
  const storyCacheRef      = useRef(new Map());

  // DEV ONLY
  const sim          = useSimulateWalk();
  const simActiveRef = useRef(false);
  const [simPos, setSimPos] = useState(null);

  useEffect(() => { locationRef.current = location; }, [location]);

  useEffect(() => {
    setTrackMarkers(true);
    clearTimeout(trackTimerRef.current);
    trackTimerRef.current = setTimeout(() => setTrackMarkers(false), 3500);
    return () => clearTimeout(trackTimerRef.current);
  }, [visiblePois]);

  const pollApi = useCallback(async (force = false) => {
    const loc = locationRef.current;
    if (!loc) return;
    if (!force) {
      const now  = Date.now();
      const last = lastPollLocRef.current;
      if (last && (now - lastPollTimeRef.current) < FORCE_POLL_MS) {
        if (roughDistM(last.lat, last.lon, loc.lat, loc.lon) < MIN_MOVE_M) return;
      }
    }
    lastPollLocRef.current  = loc;
    lastPollTimeRef.current = Date.now();
    try {
      const pollHeading = headingRef.current;
      const pollLoc     = { ...loc };
      const data = await fetchVisiblePois(pollLoc.lat, pollLoc.lon, pollHeading);
      lastPollHeadingRef.current = pollHeading;
      const pois = (data.visible_pois ?? []).map(p => ({
        ...p,
        _poll_lat: pollLoc.lat, _poll_lon: pollLoc.lon, _poll_heading: pollHeading,
      }));
      setVisiblePois(pois);
      setStreetName(data.street_name ?? null);
      setError(null);
    } catch (err) {
      console.error('[LiveWalk] poll failed:', err.message);
    }
  }, []);

  useEffect(() => {
    let positionSub = null;
    let headingSub  = null;
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission required. Enable in Settings → TourAI → Location.');
        return;
      }
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const loc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      locationRef.current = loc;
      setLocation(loc);
      mapRef.current?.animateToRegion(
        { latitude: loc.lat, longitude: loc.lon, latitudeDelta: 0.005, longitudeDelta: 0.005 },
        800,
      );
      pollApi();

      positionSub = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.High, timeInterval: 4000, distanceInterval: 5 },
        pos => {
          if (simActiveRef.current) return;
          const newLoc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
          locationRef.current = newLoc;
          setLocation(newLoc);
          setError(null);
        },
      );

      headingSub = await Location.watchHeadingAsync(hdg => {
        if (hdg.trueHeading < 0 || simActiveRef.current) return;
        const prev = headingRef.current;
        headingRef.current = hdg.trueHeading;
        const delta     = Math.abs(((hdg.trueHeading - prev) + 540) % 360 - 180);
        const pollDelta = Math.abs(((hdg.trueHeading - lastPollHeadingRef.current) + 540) % 360 - 180);
        if (delta > 5 && pollDelta > 60) setVisiblePois([]);
      });

      intervalRef.current = setInterval(pollApi, POLL_INTERVAL_MS);
    })();
    return () => {
      positionSub?.remove();
      headingSub?.remove();
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const sub = AppState.addEventListener('change', next => {
      if (appStateRef.current.match(/inactive|background/) && next === 'active') pollApi(true);
      appStateRef.current = next;
    });
    return () => sub.remove();
  }, [pollApi]);

  // DEV ONLY — simulation handlers
  const handleSimStep = useCallback(({ lat, lon, heading }) => {
    simActiveRef.current = true;
    headingRef.current   = heading;
    locationRef.current  = { lat, lon };
    setLocation({ lat, lon });
    setSimPos({ lat, lon, heading });
    mapRef.current?.animateToRegion(
      { latitude: lat, longitude: lon, latitudeDelta: 0.005, longitudeDelta: 0.005 }, 400,
    );
    pollApi(true);
  }, [pollApi]);

  const handleSimStop = useCallback(() => {
    simActiveRef.current = false;
    setSimPos(null);
  }, []);

  useEffect(() => { sim.setOnStep(handleSimStep); }, [sim.setOnStep, handleSimStep]); // DEV ONLY

  // ── Handlers ─────────────────────────────────────────────────────────────────

  const handleAsk = useCallback(async question => {
    const loc = locationRef.current;
    if (!loc) throw new Error('Location not available yet.');
    return askQuestion(question, loc.lat, loc.lon, visiblePois);
  }, [visiblePois]);

  const handleReport = useCallback(async poi => {
    const loc = locationRef.current;
    if (!loc) throw new Error('Location not available');
    return reportFalsePositive(poi, loc.lat, loc.lon, headingRef.current, streetName);
  }, [streetName]);

  const handleFetchStory = useCallback(async poi => {
    const key = String(poi.id);
    if (storyCacheRef.current.has(key)) return storyCacheRef.current.get(key);
    const story = await fetchStory(poi);
    storyCacheRef.current.set(key, story);
    setStoryHistory(prev =>
      prev.some(e => String(e.poi.id) === key)
        ? prev
        : [{ poi, story, timestamp: Date.now() }, ...prev],
    );
    return story;
  }, []);

  const handleRequestPlay = useCallback((poi, story) => {
    Speech.stop();
    Speech.speak(story, {
      rate: 0.92,
      onDone:  () => setNowPlaying(p => p.poi?.id === poi.id ? { poi: null, story: null, active: false } : p),
      onError: () => setNowPlaying(p => p.poi?.id === poi.id ? { poi: null, story: null, active: false } : p),
    });
    setNowPlaying({ poi, story, active: true });
  }, []);

  const handleRequestStop = useCallback(() => {
    Speech.stop();
    setNowPlaying({ poi: null, story: null, active: false });
  }, []);

  const handleOpenHistory = useCallback(() => {
    if (nowPlaying.active) handleRequestStop();
    setShowHistory(true);
  }, [nowPlaying.active, handleRequestStop]);

  // ── Render ────────────────────────────────────────────────────────────────────

  const initialRegion = {
    latitude:      location?.lat ?? 32.7787,
    longitude:     location?.lon ?? -96.8083,
    latitudeDelta: 0.005,
    longitudeDelta: 0.005,
  };

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="dark" />

      {/* Soft paywall banner for free-tier preview */}
      {!premium && (
        <Pressable style={gate.banner} onPress={() => router.push('/paywall')}>
          <Text style={gate.bannerText}>⏱ Free preview · Tap to unlock full access</Text>
        </Pressable>
      )}

      <MapView
        ref={mapRef}
        style={styles.map}
        provider={PROVIDER_DEFAULT}
        initialRegion={initialRegion}
        showsUserLocation={!simPos}
        showsMyLocationButton={false}
        showsCompass={false}
        onPress={e => sim.handleMapPress(e.nativeEvent.coordinate)}>

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

        {/* DEV ONLY */}
        {sim.polyline.length > 0 && (
          <Polyline coordinates={sim.polyline} strokeColor="#1A73E8" strokeWidth={3} lineDashPattern={[6, 4]} />
        )}
        {sim.startPoint && (
          <Marker coordinate={{ latitude: sim.startPoint.lat, longitude: sim.startPoint.lon }} pinColor="#34A853" title="Start" />
        )}
        {sim.endPoint && (
          <Marker coordinate={{ latitude: sim.endPoint.lat, longitude: sim.endPoint.lon }} pinColor="#EA4335" title="Destination" />
        )}
        {simPos && (
          <Marker coordinate={{ latitude: simPos.lat, longitude: simPos.lon }} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false} zIndex={99}>
            <SimUserMarker heading={simPos.heading} />
          </Marker>
        )}
        {/* END DEV ONLY */}
      </MapView>

      {error && (
        <View style={styles.errorBanner} pointerEvents="none">
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      <Pressable style={styles.listToggleBtn} onPress={() => setShowPoiList(true)}>
        <Text style={styles.listToggleIcon}>≡</Text>
      </Pressable>

      <View style={styles.bottomStack}>
        {nowPlaying.active && (
          <NowPlayingCard poi={nowPlaying.poi} onStop={handleRequestStop} />
        )}
        <BottomBar
          streetName={streetName}
          storyCount={visiblePois.length}
          onAsk={handleAsk}
          historyCount={storyHistory.length}
          onHistory={handleOpenHistory}
        />
      </View>

      <POIDetail
        poi={selectedPoi}
        visible={selectedPoi !== null}
        onClose={() => setSelectedPoi(null)}
        onFetchStory={handleFetchStory}
        onReport={handleReport}
        isPlaying={nowPlaying.active && nowPlaying.poi?.id === selectedPoi?.id}
        onRequestPlay={handleRequestPlay}
        onRequestStop={handleRequestStop}
      />

      <StoryHistorySheet
        visible={showHistory}
        history={storyHistory}
        onClose={() => setShowHistory(false)}
      />

      <PoiListSheet
        visible={showPoiList}
        pois={visiblePois}
        onClose={() => setShowPoiList(false)}
        onSelectPoi={setSelectedPoi}
      />

      {/* DEV ONLY */}
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
  bottomStack: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
  },
  listToggleBtn: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 58 : 16,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#FFFFFF',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.12,
    shadowRadius: 6,
    elevation: 5,
  },
  listToggleIcon: {
    fontSize: 20,
    color: '#0F172A',
    lineHeight: 22,
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

const gate = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: '#FFFFFF' },
  box:     { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 32, gap: 16 },
  emoji:   { fontSize: 52 },
  title:   { fontSize: 24, fontWeight: '800', color: '#0F172A', textAlign: 'center' },
  sub:     { fontSize: 15, color: '#64748B', textAlign: 'center', lineHeight: 23 },
  btn: {
    backgroundColor: '#0F172A', borderRadius: 14,
    paddingVertical: 16, paddingHorizontal: 32, marginTop: 8,
  },
  btnText:  { color: '#FFFFFF', fontSize: 16, fontWeight: '700' },
  backBtn:  { paddingVertical: 8 },
  backText: { fontSize: 14, color: '#94A3B8' },
  banner: {
    backgroundColor: '#0F172A',
    paddingVertical: 10,
    paddingHorizontal: 16,
    alignItems: 'center',
  },
  bannerText: { color: '#FFFFFF', fontSize: 12, fontWeight: '600' },
});
