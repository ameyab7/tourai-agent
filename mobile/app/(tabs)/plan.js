import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { supabase } from '../../lib/supabase';

const API_BASE = 'https://tourai-agent-production.up.railway.app';

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

// ── Date helpers ──────────────────────────────────────────────────────────────

function formatDate(d) {
  return d.toISOString().split('T')[0];
}

function displayDate(isoStr) {
  const d = new Date(isoStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function addDays(isoStr, n) {
  const d = new Date(isoStr + 'T12:00:00');
  d.setDate(d.getDate() + n);
  return formatDate(d);
}

function nightsBetween(start, end) {
  const d0 = new Date(start + 'T12:00:00');
  const d1 = new Date(end + 'T12:00:00');
  return Math.max(0, Math.round((d1 - d0) / 86400000));
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({ children }) {
  return <Text style={st.sectionLabel}>{children}</Text>;
}

function DateStepper({ label, value, onChange, min }) {
  return (
    <View style={st.dateRow}>
      <Text style={st.dateLabel}>{label}</Text>
      <View style={st.stepperRow}>
        <Pressable
          style={st.stepBtn}
          onPress={() => { const p = addDays(value, -1); if (!min || p >= min) onChange(p); }}
        >
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

function InterestChip({ item, selected, onPress }) {
  return (
    <Pressable
      style={[st.chip, selected && st.chipSelected]}
      onPress={onPress}
    >
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

function StopCard({ stop, index }) {
  return (
    <View style={st.stopCard}>
      <View style={st.stopLeft}>
        <View style={st.stopDot} />
        {index > 0 && <View style={st.stopLine} />}
      </View>
      <View style={st.stopBody}>
        <View style={st.stopHeaderRow}>
          <Text style={st.stopName}>{stop.name}</Text>
          <Text style={st.stopType}>{stop.poi_type}</Text>
        </View>
        {stop.arrival_time ? (
          <Text style={st.stopMeta}>
            {stop.arrival_time}
            {stop.duration_min > 0 ? `  ·  ${stop.duration_min} min` : ''}
            {stop.drive_from_prev_min > 0 ? `  ·  ${stop.drive_from_prev_min} min drive` : ''}
          </Text>
        ) : null}
        {stop.tip ? <Text style={st.stopTip}>{stop.tip}</Text> : null}
      </View>
    </View>
  );
}

function DayCard({ day }) {
  const [open, setOpen] = useState(true);
  return (
    <View style={st.dayCard}>
      <Pressable style={st.dayHeader} onPress={() => setOpen(o => !o)}>
        <Text style={st.dayLabel}>{day.day_label}</Text>
        <Text style={st.dayChevron}>{open ? '▲' : '▼'}</Text>
      </Pressable>
      {open && (
        <View style={st.stopsContainer}>
          {day.stops.map((stop, i) => <StopCard key={i} stop={stop} index={i} />)}
        </View>
      )}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function PlanScreen() {
  const today = formatDate(new Date());

  const [destination,  setDestination]  = useState('');
  const [startDate,    setStartDate]    = useState(today);
  const [endDate,      setEndDate]      = useState(addDays(today, 2));
  const [interests,    setInterests]    = useState([]);
  const [pace,         setPace]         = useState('balanced');
  const [style,        setStyle]        = useState('solo');
  const [driveTol,     setDriveTol]     = useState(2);
  const [loading,      setLoading]      = useState(false);
  const [profileLoading, setProfileLoading] = useState(true);
  const [itinerary,    setItinerary]    = useState(null);
  const [error,        setError]        = useState(null);
  const [token,        setToken]        = useState(null);

  // Load user profile on mount
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
          if (p.interests?.length)    setInterests(p.interests);
          if (p.pace)                 setPace(p.pace);
          if (p.travel_style)         setStyle(p.travel_style);
          if (p.drive_tolerance_hrs)  setDriveTol(p.drive_tolerance_hrs);
        }
      } catch (_) {}
      setProfileLoading(false);
    })();
  }, []);

  function toggleInterest(id) {
    setInterests(prev =>
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  }

  const nights = nightsBetween(startDate, endDate);

  async function handleGenerate() {
    if (!destination.trim()) { setError('Enter a destination first.'); return; }
    if (endDate < startDate) { setError('End date must be after start date.'); return; }
    if (interests.length === 0) { setError('Pick at least one interest.'); return; }

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

  // ── Results view ──────────────────────────────────────────────────────────
  if (itinerary) {
    return (
      <SafeAreaView style={st.safe}>
        <ScrollView contentContainerStyle={st.scroll} showsVerticalScrollIndicator={false}>
          <View style={st.resultsHeader}>
            <Pressable style={st.backBtn} onPress={() => setItinerary(null)}>
              <Text style={st.backBtnText}>← New Trip</Text>
            </Pressable>
            <Text style={st.resultsTitle}>{itinerary.title}</Text>
            <Text style={st.resultsMeta}>
              {itinerary.destination}  ·  {displayDate(itinerary.start_date)} – {displayDate(itinerary.end_date)}
            </Text>
            {itinerary.summary ? <Text style={st.resultsSummary}>{itinerary.summary}</Text> : null}

            {/* Show which interests were used */}
            {interests.length > 0 && (
              <View style={st.interestTagRow}>
                <Text style={st.interestTagLabel}>Tailored for: </Text>
                {interests.map(id => {
                  const item = ALL_INTERESTS.find(i => i.id === id);
                  return item ? (
                    <View key={id} style={st.interestTag}>
                      <Text style={st.interestTagText}>{item.emoji} {item.label}</Text>
                    </View>
                  ) : null;
                })}
              </View>
            )}
          </View>

          {itinerary.days.map((day, i) => <DayCard key={i} day={day} />)}
          <View style={{ height: 32 }} />
        </ScrollView>
      </SafeAreaView>
    );
  }

  // ── Input form ────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={st.safe}>
      <ScrollView contentContainerStyle={st.scroll} keyboardShouldPersistTaps="handled">
        <Text style={st.heading}>Plan a Trip</Text>
        <Text style={st.sub}>
          TourAI builds a day-by-day itinerary around your interests. Adjust anything below before generating.
        </Text>

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
          <SectionLabel>What do you love?</SectionLabel>
          {profileLoading && <ActivityIndicator size="small" color="#6366F1" style={{ marginLeft: 8 }} />}
        </View>
        <Text style={st.sectionHint}>Pre-filled from your profile. Toggle to adjust for this trip.</Text>
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

        {/* Generate */}
        <Pressable
          style={[st.generateBtn, loading && st.generateBtnDisabled]}
          onPress={handleGenerate}
          disabled={loading}
        >
          {loading ? (
            <View style={st.loadingRow}>
              <ActivityIndicator color="#fff" size="small" />
              <Text style={st.generateBtnText}>  Building your itinerary…</Text>
            </View>
          ) : (
            <Text style={st.generateBtnText}>Plan My Trip ✦</Text>
          )}
        </Pressable>
        {loading && (
          <Text style={st.loadingHint}>
            Finding the best local spots for your interests — about 10 seconds.
          </Text>
        )}

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
  sub:     { fontSize: 14, color: '#64748B', lineHeight: 21, marginBottom: 24 },

  sectionHeaderRow: { flexDirection: 'row', alignItems: 'center', marginTop: 20 },
  sectionLabel: { fontSize: 13, fontWeight: '700', color: '#0F172A', marginTop: 20, marginBottom: 4 },
  sectionHint:  { fontSize: 12, color: '#94A3B8', marginBottom: 10 },

  input: {
    backgroundColor: '#fff',
    borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 12,
    paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 16, color: '#0F172A', marginBottom: 4,
  },

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
  stepArrow: { fontSize: 18, color: '#334155', lineHeight: 22 },
  dateValue: { fontSize: 14, fontWeight: '600', color: '#0F172A', minWidth: 120, textAlign: 'center' },
  nightsLabel: { fontSize: 13, color: '#64748B', textAlign: 'center', marginVertical: 6 },

  // Interest chips
  chipGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 4 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 20, borderWidth: 1.5, borderColor: '#E2E8F0',
    backgroundColor: '#fff',
  },
  chipSelected:      { backgroundColor: '#EEF2FF', borderColor: ACCENT },
  chipEmoji:         { fontSize: 14 },
  chipLabel:         { fontSize: 13, fontWeight: '600', color: '#475569' },
  chipLabelSelected: { color: ACCENT },

  // Option pills (pace / style)
  pillRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginBottom: 4 },
  pill: {
    flex: 1, minWidth: 80,
    flexDirection: 'row', alignItems: 'center', gap: 8,
    padding: 10, borderRadius: 12,
    borderWidth: 1.5, borderColor: '#E2E8F0', backgroundColor: '#fff',
  },
  pillSelected:      { borderColor: ACCENT, backgroundColor: '#EEF2FF' },
  pillEmoji:         { fontSize: 18 },
  pillLabel:         { fontSize: 13, fontWeight: '700', color: '#334155' },
  pillLabelSelected: { color: ACCENT },
  pillSub:           { fontSize: 11, color: '#94A3B8' },

  // Drive tolerance
  driveRow: { flexDirection: 'row', gap: 8, marginBottom: 4 },
  driveBtn: {
    flex: 1, paddingVertical: 10, borderRadius: 10,
    borderWidth: 1.5, borderColor: '#E2E8F0', backgroundColor: '#fff',
    alignItems: 'center',
  },
  driveBtnSelected:    { borderColor: ACCENT, backgroundColor: '#EEF2FF' },
  driveBtnText:        { fontSize: 13, fontWeight: '600', color: '#475569' },
  driveBtnTextSelected: { color: ACCENT },

  errorText: { fontSize: 13, color: '#EF4444', textAlign: 'center', marginTop: 12 },

  generateBtn: {
    backgroundColor: ACCENT, borderRadius: 14,
    paddingVertical: 15, alignItems: 'center', marginTop: 20,
  },
  generateBtnDisabled: { opacity: 0.7 },
  generateBtnText:     { color: '#fff', fontSize: 16, fontWeight: '700' },
  loadingRow:          { flexDirection: 'row', alignItems: 'center' },
  loadingHint: { fontSize: 12, color: '#94A3B8', textAlign: 'center', marginTop: 10, lineHeight: 18 },

  // Results
  resultsHeader: {
    backgroundColor: '#fff', borderRadius: 16, padding: 18, marginBottom: 16,
    shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 8, shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  backBtn:        { marginBottom: 12 },
  backBtnText:    { fontSize: 14, color: ACCENT, fontWeight: '600' },
  resultsTitle:   { fontSize: 22, fontWeight: '800', color: '#0F172A', marginBottom: 4 },
  resultsMeta:    { fontSize: 13, color: '#64748B', marginBottom: 8 },
  resultsSummary: { fontSize: 14, color: '#475569', lineHeight: 20, marginBottom: 12 },

  interestTagRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', marginTop: 4, gap: 6 },
  interestTagLabel: { fontSize: 12, color: '#94A3B8' },
  interestTag: { backgroundColor: '#EEF2FF', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10 },
  interestTagText: { fontSize: 12, color: ACCENT, fontWeight: '600' },

  // Day / stop cards
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
  dayLabel:       { fontSize: 15, fontWeight: '700', color: '#0F172A', flex: 1 },
  dayChevron:     { fontSize: 11, color: '#94A3B8', marginLeft: 8 },
  stopsContainer: { paddingHorizontal: 16, paddingBottom: 12, paddingTop: 8 },

  stopCard:      { flexDirection: 'row', marginTop: 10 },
  stopLeft:      { width: 20, alignItems: 'center', marginRight: 10 },
  stopDot:       { width: 10, height: 10, borderRadius: 5, backgroundColor: ACCENT, marginTop: 4 },
  stopLine:      { flex: 1, width: 1.5, backgroundColor: '#E2E8F0', marginTop: 2 },
  stopBody:      { flex: 1, paddingBottom: 10 },
  stopHeaderRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginBottom: 2 },
  stopName:      { fontSize: 14, fontWeight: '700', color: '#1E293B' },
  stopType: {
    fontSize: 11, fontWeight: '600', color: ACCENT,
    backgroundColor: '#EEF2FF', paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6,
  },
  stopMeta: { fontSize: 12, color: '#94A3B8', marginBottom: 4 },
  stopTip:  { fontSize: 13, color: '#475569', lineHeight: 19 },
});
