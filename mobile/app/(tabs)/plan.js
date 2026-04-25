import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { supabase } from '../../lib/supabase';

const API_BASE = 'https://tourai-agent-production.up.railway.app';

// ── Constants ─────────────────────────────────────────────────────────────────

const ALL_INTERESTS = [
  { id: 'history',      label: 'History',      emoji: '🏛️' },
  { id: 'nature',       label: 'Nature',        emoji: '🌿' },
  { id: 'photography',  label: 'Photography',   emoji: '📷' },
  { id: 'food',         label: 'Food',          emoji: '🍽️' },
  { id: 'architecture', label: 'Architecture',  emoji: '🏗️' },
  { id: 'culture',      label: 'Culture',       emoji: '🎭' },
  { id: 'hiking',       label: 'Hiking',        emoji: '🥾' },
  { id: 'social',       label: 'Social',        emoji: '🎉' },
  { id: 'shopping',     label: 'Shopping',      emoji: '🛍️' },
  { id: 'sports',       label: 'Sports',        emoji: '⚽' },
];

// Quick trip vibes — selecting one pre-fills interests and pace
const TRIP_VIBES = [
  { id: 'city',    label: 'City Break',     emoji: '🏙️', interests: ['culture', 'food', 'architecture', 'social'], pace: 'balanced' },
  { id: 'nature',  label: 'Nature Escape',  emoji: '🌲', interests: ['nature', 'hiking', 'photography'],           pace: 'relaxed'  },
  { id: 'history', label: 'History Tour',   emoji: '🏛️', interests: ['history', 'architecture', 'culture'],        pace: 'balanced' },
  { id: 'food',    label: 'Food & Culture', emoji: '🍜', interests: ['food', 'culture', 'social'],                 pace: 'relaxed'  },
  { id: 'beach',   label: 'Beach Getaway',  emoji: '🌊', interests: ['nature', 'photography', 'social'],           pace: 'relaxed'  },
  { id: 'photo',   label: 'Photo Trip',     emoji: '📷', interests: ['photography', 'nature', 'architecture'],     pace: 'packed'   },
];

const PACE_OPTIONS = [
  { id: 'relaxed',  label: 'Relaxed',  sub: '2–3 stops/day', emoji: '🌅' },
  { id: 'balanced', label: 'Balanced', sub: '3–4 stops/day', emoji: '☀️' },
  { id: 'packed',   label: 'Packed',   sub: '4–5 stops/day', emoji: '⚡' },
];

const STYLE_OPTIONS = [
  { id: 'solo',   label: 'Solo',   emoji: '🧍' },
  { id: 'couple', label: 'Couple', emoji: '👫' },
  { id: 'family', label: 'Family', emoji: '👨‍👩‍👧' },
  { id: 'group',  label: 'Group',  emoji: '👥' },
];

// Messages cycled during the loading wait
const LOADING_MESSAGES = [
  'Exploring the area for hidden gems…',
  'Matching spots to your interests…',
  "Checking what's worth your time…",
  'Crafting Day 1…',
  'Adding insider tips…',
  'Putting the finishing touches…',
];

// POI type → emoji for stop cards
const TYPE_EMOJI = {
  museum: '🏛️', art_gallery: '🎨', gallery: '🎨', attraction: '⭐',
  park: '🌳', nature_reserve: '🌿', beach: '🌊', viewpoint: '🏔️',
  restaurant: '🍽️', cafe: '☕', bar: '🍸', pub: '🍺',
  historic: '🏰', monument: '🗿', memorial: '🕊️', castle: '🏯', ruins: '🧱',
  culture: '🎭', cinema: '🎬', theatre: '🎭', theme_park: '🎡',
  shopping: '🛍️', mall: '🛍️', sports_centre: '⚽', stadium: '🏟️',
};

// ── Date helpers ──────────────────────────────────────────────────────────────

function formatDate(d) { return d.toISOString().split('T')[0]; }
function displayDate(iso) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
function addDays(iso, n) {
  const d = new Date(iso + 'T12:00:00');
  d.setDate(d.getDate() + n);
  return formatDate(d);
}
function nightsBetween(a, b) {
  return Math.max(0, Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 86400000));
}

// ── Animated loading messages ─────────────────────────────────────────────────

function LoadingView() {
  const [msgIdx, setMsgIdx] = useState(0);
  const opacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const cycle = () => {
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0, duration: 400, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 1, duration: 400, useNativeDriver: true }),
      ]).start();
      setMsgIdx(i => (i + 1) % LOADING_MESSAGES.length);
    };
    const t = setInterval(cycle, 2200);
    return () => clearInterval(t);
  }, []);

  return (
    <View style={st.loadingBox}>
      <ActivityIndicator color={ACCENT} size="large" />
      <Animated.Text style={[st.loadingMsg, { opacity }]}>
        {LOADING_MESSAGES[msgIdx]}
      </Animated.Text>
      <Text style={st.loadingHint}>Usually takes about 10 seconds</Text>
    </View>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({ children, hint }) {
  return (
    <View style={{ marginTop: 20, marginBottom: hint ? 2 : 8 }}>
      <Text style={st.sectionLabel}>{children}</Text>
      {hint ? <Text style={st.sectionHint}>{hint}</Text> : null}
    </View>
  );
}

function DateStepper({ label, value, onChange, min }) {
  return (
    <View style={st.dateRow}>
      <Text style={st.dateLabel}>{label}</Text>
      <View style={st.stepperRow}>
        <Pressable style={st.stepBtn} onPress={() => { const p = addDays(value, -1); if (!min || p >= min) onChange(p); }}>
          <Text style={st.stepArrow}>‹</Text>
        </Pressable>
        <Text style={st.dateValue}>{displayDate(value)}</Text>
        <Pressable style={st.stepBtn} onPress={() => onChange(addDays(value, 1))}>
          <Text style={st.stepArrow}>›</Text>
        </Pressable>
      </View>
    </View>
  );
}

function VibeChip({ vibe, selected, onPress }) {
  return (
    <Pressable style={[st.vibeChip, selected && st.vibeChipSelected]} onPress={onPress}>
      <Text style={st.vibeEmoji}>{vibe.emoji}</Text>
      <Text style={[st.vibeLabel, selected && st.vibeLabelSelected]}>{vibe.label}</Text>
    </Pressable>
  );
}

function InterestChip({ item, selected, onPress }) {
  return (
    <Pressable style={[st.chip, selected && st.chipSelected]} onPress={onPress}>
      <Text style={st.chipEmoji}>{item.emoji}</Text>
      <Text style={[st.chipLabel, selected && st.chipLabelSelected]}>{item.label}</Text>
    </Pressable>
  );
}

function OptionPill({ item, selected, onPress }) {
  return (
    <Pressable style={[st.pill, selected && st.pillSelected]} onPress={onPress}>
      <Text style={st.pillEmoji}>{item.emoji}</Text>
      <View>
        <Text style={[st.pillLabel, selected && st.pillLabelSelected]}>{item.label}</Text>
        {item.sub ? <Text style={st.pillSub}>{item.sub}</Text> : null}
      </View>
    </Pressable>
  );
}

// ── Stop card ─────────────────────────────────────────────────────────────────

function StopCard({ stop, destination }) {
  const emoji = TYPE_EMOJI[stop.poi_type] || '📍';

  function openDirections() {
    const query = encodeURIComponent(`${stop.name}, ${destination}`);
    Linking.openURL(`https://maps.google.com/?q=${query}`);
  }

  return (
    <View style={st.stopCard}>
      <View style={st.stopIconCol}>
        <View style={st.stopIconCircle}>
          <Text style={st.stopIconEmoji}>{emoji}</Text>
        </View>
        <View style={st.stopLine} />
      </View>
      <View style={st.stopBody}>
        <Text style={st.stopName}>{stop.name}</Text>
        {stop.arrival_time ? (
          <Text style={st.stopMeta}>
            {stop.arrival_time}
            {stop.duration_min > 0 ? `  ·  ${stop.duration_min} min` : ''}
            {stop.drive_from_prev_min > 0 ? `  ·  🚗 ${stop.drive_from_prev_min} min` : ''}
          </Text>
        ) : null}
        {stop.tip ? <Text style={st.stopTip}>{stop.tip}</Text> : null}
        <Pressable style={st.directionsBtn} onPress={openDirections}>
          <Text style={st.directionsBtnText}>📍 Directions</Text>
        </Pressable>
      </View>
    </View>
  );
}

// ── Day card ──────────────────────────────────────────────────────────────────

function DayCard({ day, destination }) {
  const [open, setOpen] = useState(true);
  return (
    <View style={st.dayCard}>
      <Pressable style={st.dayHeader} onPress={() => setOpen(o => !o)}>
        <View>
          <Text style={st.dayLabel}>{day.day_label}</Text>
          <Text style={st.dayStopCount}>{day.stops.length} stop{day.stops.length !== 1 ? 's' : ''}</Text>
        </View>
        <Text style={st.dayChevron}>{open ? '▲' : '▼'}</Text>
      </Pressable>
      {open && (
        <View style={st.stopsContainer}>
          {day.stops.map((stop, i) => (
            <StopCard key={i} stop={stop} destination={destination} />
          ))}
        </View>
      )}
    </View>
  );
}

// ── Trip summary bar ──────────────────────────────────────────────────────────

function TripSummaryBar({ itinerary, interests }) {
  const totalStops = itinerary.days.reduce((n, d) => n + d.stops.length, 0);
  const nights = nightsBetween(itinerary.start_date, itinerary.end_date);

  return (
    <View style={st.summaryBar}>
      <SummaryPill emoji="🗓️" label={`${nights + 1} days`} />
      <SummaryPill emoji="📍" label={`${totalStops} stops`} />
      {interests.slice(0, 2).map(id => {
        const item = ALL_INTERESTS.find(i => i.id === id);
        return item ? <SummaryPill key={id} emoji={item.emoji} label={item.label} /> : null;
      })}
    </View>
  );
}

function SummaryPill({ emoji, label }) {
  return (
    <View style={st.summaryPill}>
      <Text style={st.summaryPillText}>{emoji} {label}</Text>
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function PlanScreen() {
  const today = formatDate(new Date());

  const [destination,    setDestination]    = useState('');
  const [startDate,      setStartDate]      = useState(today);
  const [endDate,        setEndDate]        = useState(addDays(today, 2));
  const [interests,      setInterests]      = useState([]);
  const [pace,           setPace]           = useState('balanced');
  const [style,          setStyle]          = useState('solo');
  const [driveTol,       setDriveTol]       = useState(2);
  const [selectedVibe,   setSelectedVibe]   = useState(null);
  const [loading,        setLoading]        = useState(false);
  const [profileLoading, setProfileLoading] = useState(true);
  const [itinerary,      setItinerary]      = useState(null);
  const [error,          setError]          = useState(null);
  const [token,          setToken]          = useState(null);

  // Load profile on mount
  useEffect(() => {
    (async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) { setProfileLoading(false); return; }
        setToken(session.access_token);
        const res = await fetch(`${API_BASE}/v1/profile/${session.user.id}`, {
          headers: { Authorization: `Bearer ${session.access_token}` },
        });
        if (res.ok) {
          const p = await res.json();
          if (p.interests?.length)   setInterests(p.interests);
          if (p.pace)                setPace(p.pace);
          if (p.travel_style)        setStyle(p.travel_style);
          if (p.drive_tolerance_hrs) setDriveTol(p.drive_tolerance_hrs);
        }
      } catch (_) {}
      setProfileLoading(false);
    })();
  }, []);

  function applyVibe(vibe) {
    if (selectedVibe === vibe.id) {
      setSelectedVibe(null);
    } else {
      setSelectedVibe(vibe.id);
      setInterests(vibe.interests);
      setPace(vibe.pace);
    }
  }

  function toggleInterest(id) {
    setSelectedVibe(null); // custom selection clears the vibe
    setInterests(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  }

  const nights = nightsBetween(startDate, endDate);

  async function handleGenerate() {
    if (!destination.trim()) { setError('Enter a destination first.'); return; }
    if (endDate < startDate)  { setError('End date must be after start date.'); return; }
    if (!interests.length)    { setError('Pick at least one interest or a trip vibe.'); return; }

    setLoading(true);
    setError(null);
    setItinerary(null);

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const resp = await fetch(`${API_BASE}/v1/itinerary`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          destination,
          start_date:          startDate,
          end_date:            endDate,
          interests,
          travel_style:        style,
          pace,
          drive_tolerance_hrs: driveTol,
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Error ${resp.status}`);
      }
      setItinerary(await resp.json());
    } catch (err) {
      setError(err.message || 'Could not generate itinerary. Try again.');
    } finally {
      setLoading(false);
    }
  }

  // ── Loading screen ────────────────────────────────────────────────────────
  if (loading) {
    return (
      <SafeAreaView style={st.safe}>
        <View style={st.loadingScreen}>
          <Text style={st.loadingDest}>{destination}</Text>
          <LoadingView />
        </View>
      </SafeAreaView>
    );
  }

  // ── Results view ──────────────────────────────────────────────────────────
  if (itinerary) {
    return (
      <SafeAreaView style={st.safe}>
        <ScrollView contentContainerStyle={st.scroll} showsVerticalScrollIndicator={false}>
          {/* Header */}
          <View style={st.resultsHeader}>
            <Pressable style={st.backBtn} onPress={() => setItinerary(null)}>
              <Text style={st.backBtnText}>← New Trip</Text>
            </Pressable>
            <Text style={st.resultsTitle}>{itinerary.title}</Text>
            <Text style={st.resultsMeta}>
              {itinerary.destination}  ·  {displayDate(itinerary.start_date)} – {displayDate(itinerary.end_date)}
            </Text>
            {itinerary.summary ? <Text style={st.resultsSummary}>{itinerary.summary}</Text> : null}
            <TripSummaryBar itinerary={itinerary} interests={interests} />
          </View>

          {/* Days */}
          {itinerary.days.map((day, i) => (
            <DayCard key={i} day={day} destination={itinerary.destination} />
          ))}

          {/* Start Live Walk CTA */}
          <View style={st.walkCTABox}>
            <Text style={st.walkCTATitle}>Ready to explore?</Text>
            <Text style={st.walkCTASub}>
              When you're at {itinerary.destination}, switch to Live Walk for real-time audio stories as you move through these spots.
            </Text>
            <Pressable style={st.walkCTABtn} onPress={() => router.push('/(tabs)/live-walk')}>
              <Text style={st.walkCTABtnText}>Start Live Walk →</Text>
            </Pressable>
          </View>

          <View style={{ height: 40 }} />
        </ScrollView>
      </SafeAreaView>
    );
  }

  // ── Input form ────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={st.safe}>
      <ScrollView contentContainerStyle={st.scroll} keyboardShouldPersistTaps="handled">
        <Text style={st.heading}>Plan a Trip</Text>
        <Text style={st.sub}>Tell TourAI what excites you and it'll build a personalised day-by-day itinerary.</Text>

        {/* Trip vibe quick-select */}
        <SectionLabel hint="Pick a vibe to instantly pre-fill your interests and pace">
          What kind of trip?
        </SectionLabel>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={st.vibeScroll}>
          {TRIP_VIBES.map(v => (
            <VibeChip key={v.id} vibe={v} selected={selectedVibe === v.id} onPress={() => applyVibe(v)} />
          ))}
        </ScrollView>

        {/* Destination */}
        <SectionLabel>Where to?</SectionLabel>
        <TextInput
          style={st.input}
          value={destination}
          onChangeText={setDestination}
          placeholder="City, landmark, or region…"
          placeholderTextColor="#94A3B8"
          returnKeyType="done"
          autoCapitalize="words"
          editable={!loading}
        />

        {/* Dates */}
        <SectionLabel>When?</SectionLabel>
        <DateStepper
          label="Start"
          value={startDate}
          onChange={v => { setStartDate(v); if (v > endDate) setEndDate(v); }}
        />
        <DateStepper label="End" value={endDate} onChange={setEndDate} min={startDate} />
        {nights > 0 && (
          <Text style={st.nightsLabel}>
            {nights} night{nights !== 1 ? 's' : ''}  ·  {nights + 1} day{nights + 1 !== 1 ? 's' : ''}
          </Text>
        )}

        {/* Interests */}
        <View style={st.sectionHeaderRow}>
          <Text style={st.sectionLabel}>Fine-tune your interests</Text>
          {profileLoading && <ActivityIndicator size="small" color={ACCENT} style={{ marginLeft: 8, marginTop: 20 }} />}
        </View>
        <Text style={st.sectionHint}>Pre-filled from your profile. Tap to toggle.</Text>
        <View style={st.chipGrid}>
          {ALL_INTERESTS.map(item => (
            <InterestChip
              key={item.id}
              item={item}
              selected={interests.includes(item.id)}
              onPress={() => toggleInterest(item.id)}
            />
          ))}
        </View>

        {/* Pace */}
        <SectionLabel>Trip pace</SectionLabel>
        <View style={st.pillRow}>
          {PACE_OPTIONS.map(o => (
            <OptionPill key={o.id} item={o} selected={pace === o.id} onPress={() => setPace(o.id)} />
          ))}
        </View>

        {/* Travel style */}
        <SectionLabel>Travelling as</SectionLabel>
        <View style={st.pillRow}>
          {STYLE_OPTIONS.map(o => (
            <OptionPill key={o.id} item={o} selected={style === o.id} onPress={() => setStyle(o.id)} />
          ))}
        </View>

        {/* Drive tolerance */}
        <SectionLabel>Max drive between stops</SectionLabel>
        <View style={st.driveRow}>
          {[0.5, 1, 2, 4].map(h => (
            <Pressable
              key={h}
              style={[st.driveBtn, driveTol === h && st.driveBtnSelected]}
              onPress={() => setDriveTol(h)}
            >
              <Text style={[st.driveBtnText, driveTol === h && st.driveBtnTextSelected]}>
                {h < 1 ? '30 min' : `${h}h`}
              </Text>
            </Pressable>
          ))}
        </View>

        {error ? <Text style={st.errorText}>{error}</Text> : null}

        <Pressable style={st.generateBtn} onPress={handleGenerate}>
          <Text style={st.generateBtnText}>Plan My Trip ✦</Text>
        </Pressable>

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const ACCENT = '#6366F1';

const st = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: '#F8FAFC' },
  scroll: { padding: 20, paddingBottom: 60 },

  heading: { fontSize: 26, fontWeight: '800', color: '#0F172A', marginBottom: 6 },
  sub:     { fontSize: 14, color: '#64748B', lineHeight: 21, marginBottom: 8 },

  sectionHeaderRow: { flexDirection: 'row', alignItems: 'center' },
  sectionLabel:     { fontSize: 13, fontWeight: '700', color: '#0F172A', marginTop: 20, marginBottom: 4 },
  sectionHint:      { fontSize: 12, color: '#94A3B8', marginBottom: 8 },

  input: {
    backgroundColor: '#fff',
    borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 12,
    paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 16, color: '#0F172A',
  },

  // Vibe chips
  vibeScroll: { marginBottom: 4 },
  vibeChip: {
    alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: 16, paddingVertical: 10,
    borderRadius: 20, borderWidth: 1.5, borderColor: '#E2E8F0',
    backgroundColor: '#fff', marginRight: 8,
  },
  vibeChipSelected: { backgroundColor: '#EEF2FF', borderColor: ACCENT },
  vibeEmoji:        { fontSize: 20, marginBottom: 2 },
  vibeLabel:        { fontSize: 12, fontWeight: '600', color: '#475569' },
  vibeLabelSelected: { color: ACCENT },

  // Date stepper
  dateRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: '#fff', borderWidth: 1, borderColor: '#E2E8F0',
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10, marginBottom: 8,
  },
  dateLabel:  { fontSize: 13, fontWeight: '600', color: '#475569' },
  stepperRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  stepBtn: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: '#F1F5F9', alignItems: 'center', justifyContent: 'center',
  },
  stepArrow:   { fontSize: 18, color: '#334155', lineHeight: 22 },
  dateValue:   { fontSize: 14, fontWeight: '600', color: '#0F172A', minWidth: 120, textAlign: 'center' },
  nightsLabel: { fontSize: 13, color: '#64748B', textAlign: 'center', marginVertical: 6 },

  // Interest chips
  chipGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 20, borderWidth: 1.5, borderColor: '#E2E8F0', backgroundColor: '#fff',
  },
  chipSelected:      { backgroundColor: '#EEF2FF', borderColor: ACCENT },
  chipEmoji:         { fontSize: 14 },
  chipLabel:         { fontSize: 13, fontWeight: '600', color: '#475569' },
  chipLabelSelected: { color: ACCENT },

  // Pace / style pills
  pillRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  pill: {
    flex: 1, minWidth: 80, flexDirection: 'row', alignItems: 'center', gap: 8,
    padding: 10, borderRadius: 12, borderWidth: 1.5, borderColor: '#E2E8F0', backgroundColor: '#fff',
  },
  pillSelected:      { borderColor: ACCENT, backgroundColor: '#EEF2FF' },
  pillEmoji:         { fontSize: 18 },
  pillLabel:         { fontSize: 13, fontWeight: '700', color: '#334155' },
  pillLabelSelected: { color: ACCENT },
  pillSub:           { fontSize: 11, color: '#94A3B8' },

  // Drive tolerance
  driveRow: { flexDirection: 'row', gap: 8 },
  driveBtn: {
    flex: 1, paddingVertical: 10, borderRadius: 10,
    borderWidth: 1.5, borderColor: '#E2E8F0', backgroundColor: '#fff', alignItems: 'center',
  },
  driveBtnSelected:     { borderColor: ACCENT, backgroundColor: '#EEF2FF' },
  driveBtnText:         { fontSize: 13, fontWeight: '600', color: '#475569' },
  driveBtnTextSelected: { color: ACCENT },

  errorText:       { fontSize: 13, color: '#EF4444', textAlign: 'center', marginTop: 12 },
  generateBtn:     { backgroundColor: ACCENT, borderRadius: 14, paddingVertical: 15, alignItems: 'center', marginTop: 20 },
  generateBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },

  // Loading screen
  loadingScreen: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 32 },
  loadingDest:   { fontSize: 28, fontWeight: '800', color: '#0F172A', marginBottom: 40, textAlign: 'center' },
  loadingBox:    { alignItems: 'center', gap: 16 },
  loadingMsg:    { fontSize: 16, color: '#475569', fontWeight: '600', textAlign: 'center' },
  loadingHint:   { fontSize: 13, color: '#94A3B8' },

  // Results header
  resultsHeader: {
    backgroundColor: '#fff', borderRadius: 16, padding: 18, marginBottom: 16,
    shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 8, shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  backBtn:        { marginBottom: 12 },
  backBtnText:    { fontSize: 14, color: ACCENT, fontWeight: '600' },
  resultsTitle:   { fontSize: 22, fontWeight: '800', color: '#0F172A', marginBottom: 4 },
  resultsMeta:    { fontSize: 13, color: '#64748B', marginBottom: 8 },
  resultsSummary: { fontSize: 14, color: '#475569', lineHeight: 20, marginBottom: 14 },

  // Summary bar
  summaryBar: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  summaryPill: {
    backgroundColor: '#F1F5F9', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12,
  },
  summaryPillText: { fontSize: 12, fontWeight: '600', color: '#334155' },

  // Day cards
  dayCard: {
    backgroundColor: '#fff', borderRadius: 14, marginBottom: 12, overflow: 'hidden',
    shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 6, shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  dayHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: '#F1F5F9',
  },
  dayLabel:      { fontSize: 15, fontWeight: '700', color: '#0F172A' },
  dayStopCount:  { fontSize: 12, color: '#94A3B8', marginTop: 2 },
  dayChevron:    { fontSize: 11, color: '#94A3B8' },
  stopsContainer: { padding: 16 },

  // Stop cards
  stopCard:      { flexDirection: 'row', marginBottom: 16 },
  stopIconCol:   { width: 36, alignItems: 'center', marginRight: 12 },
  stopIconCircle: {
    width: 34, height: 34, borderRadius: 17,
    backgroundColor: '#EEF2FF', alignItems: 'center', justifyContent: 'center',
  },
  stopIconEmoji: { fontSize: 16 },
  stopLine:      { flex: 1, width: 1.5, backgroundColor: '#E2E8F0', marginTop: 4 },
  stopBody:      { flex: 1 },
  stopName:      { fontSize: 14, fontWeight: '700', color: '#1E293B', marginBottom: 2 },
  stopMeta:      { fontSize: 12, color: '#94A3B8', marginBottom: 6 },
  stopTip:       { fontSize: 13, color: '#475569', lineHeight: 19, marginBottom: 8 },
  directionsBtn: { alignSelf: 'flex-start' },
  directionsBtnText: { fontSize: 12, color: ACCENT, fontWeight: '600' },

  // Walk CTA
  walkCTABox: {
    backgroundColor: '#1E1B4B', borderRadius: 16, padding: 20, marginTop: 8,
  },
  walkCTATitle:    { fontSize: 17, fontWeight: '800', color: '#fff', marginBottom: 6 },
  walkCTASub:      { fontSize: 13, color: '#A5B4FC', lineHeight: 19, marginBottom: 16 },
  walkCTABtn:      { backgroundColor: ACCENT, borderRadius: 10, paddingVertical: 12, alignItems: 'center' },
  walkCTABtnText:  { color: '#fff', fontSize: 14, fontWeight: '700' },
});
