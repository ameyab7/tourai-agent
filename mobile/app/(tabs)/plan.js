import React, { useState } from 'react';
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

const API_BASE = 'https://tourai-agent-production.up.railway.app';

// ── Date helpers ──────────────────────────────────────────────────────────────

function formatDate(d) {
  return d.toISOString().split('T')[0]; // YYYY-MM-DD
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

// ── Simple inline date stepper ────────────────────────────────────────────────

function DateStepper({ label, value, onChange, min }) {
  return (
    <View style={st.dateRow}>
      <Text style={st.dateLabel}>{label}</Text>
      <View style={st.stepperRow}>
        <Pressable
          style={st.stepBtn}
          onPress={() => {
            const prev = addDays(value, -1);
            if (!min || prev >= min) onChange(prev);
          }}
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

// ── Stop card ─────────────────────────────────────────────────────────────────

function StopCard({ stop, index }) {
  const isFirst = stop.drive_from_prev_min === 0 && index === 0;
  return (
    <View style={st.stopCard}>
      <View style={st.stopLeft}>
        <View style={st.stopDot} />
        {!isFirst && <View style={st.stopLine} />}
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

// ── Day card ──────────────────────────────────────────────────────────────────

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
          {day.stops.map((stop, i) => (
            <StopCard key={i} stop={stop} index={i} />
          ))}
        </View>
      )}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function PlanScreen() {
  const today     = formatDate(new Date());
  const tomorrow  = addDays(today, 1);

  const [destination, setDestination] = useState('');
  const [startDate,   setStartDate]   = useState(today);
  const [endDate,     setEndDate]     = useState(addDays(today, 2));
  const [loading,     setLoading]     = useState(false);
  const [itinerary,   setItinerary]   = useState(null);
  const [error,       setError]       = useState(null);

  const nights = nightsBetween(startDate, endDate);

  async function handleGenerate() {
    if (!destination.trim()) {
      setError('Enter a destination first.');
      return;
    }
    if (endDate < startDate) {
      setError('End date must be on or after start date.');
      return;
    }
    setLoading(true);
    setError(null);
    setItinerary(null);
    try {
      const resp = await fetch(`${API_BASE}/v1/itinerary`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          destination,
          start_date: startDate,
          end_date:   endDate,
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Error ${resp.status}`);
      }
      const data = await resp.json();
      setItinerary(data);
    } catch (err) {
      setError(err.message || 'Could not generate itinerary. Try again.');
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setItinerary(null);
    setError(null);
  }

  // ── Itinerary results view ─────────────────────────────────────────────────
  if (itinerary) {
    return (
      <SafeAreaView style={st.safe}>
        <ScrollView contentContainerStyle={st.scroll} showsVerticalScrollIndicator={false}>
          {/* Header */}
          <View style={st.resultsHeader}>
            <Pressable style={st.backBtn} onPress={handleReset}>
              <Text style={st.backBtnText}>← New Trip</Text>
            </Pressable>
            <Text style={st.resultsTitle}>{itinerary.title}</Text>
            <Text style={st.resultsMeta}>
              {itinerary.destination}  ·  {displayDate(itinerary.start_date)} – {displayDate(itinerary.end_date)}
            </Text>
            {itinerary.summary ? (
              <Text style={st.resultsSummary}>{itinerary.summary}</Text>
            ) : null}
          </View>

          {/* Days */}
          {itinerary.days.map((day, i) => (
            <DayCard key={i} day={day} />
          ))}

          <View style={{ height: 32 }} />
        </ScrollView>
      </SafeAreaView>
    );
  }

  // ── Input / Loading view ───────────────────────────────────────────────────
  return (
    <SafeAreaView style={st.safe}>
      <ScrollView contentContainerStyle={st.scroll} keyboardShouldPersistTaps="handled">
        <Text style={st.heading}>Plan a Trip</Text>
        <Text style={st.sub}>Enter a destination and dates. TourAI will build a day-by-day itinerary from real local spots.</Text>

        {/* Destination */}
        <Text style={st.fieldLabel}>Where to?</Text>
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
        <DateStepper
          label="Start date"
          value={startDate}
          onChange={v => {
            setStartDate(v);
            if (v > endDate) setEndDate(v);
          }}
        />
        <DateStepper
          label="End date"
          value={endDate}
          onChange={setEndDate}
          min={startDate}
        />

        {nights > 0 && (
          <Text style={st.nightsLabel}>
            {nights} night{nights !== 1 ? 's' : ''}  ·  {nights + 1} day{nights + 1 !== 1 ? 's' : ''}
          </Text>
        )}

        {error ? <Text style={st.errorText}>{error}</Text> : null}

        {/* Generate button */}
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
            Fetching local spots and crafting your trip — this takes about 10 seconds.
          </Text>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const ACCENT = '#6366F1';

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#F8FAFC' },
  scroll: { padding: 20, paddingBottom: 60 },

  heading: { fontSize: 26, fontWeight: '800', color: '#0F172A', marginBottom: 6 },
  sub:     { fontSize: 14, color: '#64748B', lineHeight: 21, marginBottom: 24 },

  fieldLabel: { fontSize: 13, fontWeight: '600', color: '#475569', marginBottom: 6 },
  input: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#E2E8F0',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    color: '#0F172A',
    marginBottom: 20,
  },

  // Date stepper
  dateRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#E2E8F0',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
    marginBottom: 10,
  },
  dateLabel: { fontSize: 13, fontWeight: '600', color: '#475569' },
  stepperRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  stepBtn: {
    width: 32, height: 32,
    borderRadius: 16,
    backgroundColor: '#F1F5F9',
    alignItems: 'center', justifyContent: 'center',
  },
  stepArrow: { fontSize: 18, color: '#334155', lineHeight: 22 },
  dateValue: { fontSize: 14, fontWeight: '600', color: '#0F172A', minWidth: 120, textAlign: 'center' },

  nightsLabel: { fontSize: 13, color: '#64748B', textAlign: 'center', marginVertical: 8 },
  errorText:   { fontSize: 13, color: '#EF4444', textAlign: 'center', marginVertical: 8 },

  generateBtn: {
    backgroundColor: ACCENT,
    borderRadius: 14,
    paddingVertical: 15,
    alignItems: 'center',
    marginTop: 16,
  },
  generateBtnDisabled: { opacity: 0.7 },
  generateBtnText:     { color: '#fff', fontSize: 16, fontWeight: '700' },
  loadingRow:          { flexDirection: 'row', alignItems: 'center' },
  loadingHint: { fontSize: 12, color: '#94A3B8', textAlign: 'center', marginTop: 12, lineHeight: 18 },

  // Results
  resultsHeader: {
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 18,
    marginBottom: 16,
    shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 8, shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  backBtn:        { marginBottom: 12 },
  backBtnText:    { fontSize: 14, color: ACCENT, fontWeight: '600' },
  resultsTitle:   { fontSize: 22, fontWeight: '800', color: '#0F172A', marginBottom: 4 },
  resultsMeta:    { fontSize: 13, color: '#64748B', marginBottom: 8 },
  resultsSummary: { fontSize: 14, color: '#475569', lineHeight: 20 },

  // Day card
  dayCard: {
    backgroundColor: '#fff',
    borderRadius: 14,
    marginBottom: 12,
    overflow: 'hidden',
    shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 6, shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  dayHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#F1F5F9',
  },
  dayLabel:   { fontSize: 15, fontWeight: '700', color: '#0F172A', flex: 1 },
  dayChevron: { fontSize: 11, color: '#94A3B8', marginLeft: 8 },
  stopsContainer: { paddingHorizontal: 16, paddingBottom: 12, paddingTop: 8 },

  // Stop card
  stopCard: { flexDirection: 'row', marginTop: 10 },
  stopLeft: { width: 20, alignItems: 'center', marginRight: 10 },
  stopDot:  { width: 10, height: 10, borderRadius: 5, backgroundColor: ACCENT, marginTop: 4 },
  stopLine: { flex: 1, width: 1.5, backgroundColor: '#E2E8F0', marginTop: 2 },
  stopBody: { flex: 1, paddingBottom: 10 },
  stopHeaderRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginBottom: 2 },
  stopName: { fontSize: 14, fontWeight: '700', color: '#1E293B' },
  stopType: {
    fontSize: 11, fontWeight: '600', color: ACCENT,
    backgroundColor: '#EEF2FF', paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6,
  },
  stopMeta: { fontSize: 12, color: '#94A3B8', marginBottom: 4 },
  stopTip:  { fontSize: 13, color: '#475569', lineHeight: 19 },
});
