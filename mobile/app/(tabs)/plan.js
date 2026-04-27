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

import { API_BASE } from '../../lib/config.js';

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

const TRIP_VIBES = [
  { id: 'city',    label: 'City Break',    emoji: '🏙️', interests: ['culture','food','architecture','social'], pace: 'balanced' },
  { id: 'nature',  label: 'Nature Escape', emoji: '🌲', interests: ['nature','hiking','photography'],          pace: 'relaxed'  },
  { id: 'history', label: 'History Tour',  emoji: '🏛️', interests: ['history','architecture','culture'],       pace: 'balanced' },
  { id: 'food',    label: 'Food & Culture',emoji: '🍜', interests: ['food','culture','social'],                pace: 'relaxed'  },
  { id: 'beach',   label: 'Beach Getaway', emoji: '🌊', interests: ['nature','photography','social'],          pace: 'relaxed'  },
  { id: 'photo',   label: 'Photo Trip',    emoji: '📷', interests: ['photography','nature','architecture'],    pace: 'packed'   },
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

const TRANSIT_ICONS = { walk: '🚶', uber: '🚕', drive: '🚗', metro: '🚇', arrive: '📍' };

const TYPE_EMOJI = {
  museum: '🏛️', art_gallery: '🎨', attraction: '⭐', park: '🌳',
  nature_reserve: '🌿', beach: '🌊', viewpoint: '🏔️', restaurant: '🍽️',
  cafe: '☕', bar: '🍸', pub: '🍺', historic: '🏰', monument: '🗿',
  castle: '🏯', culture: '🎭', accommodation: '🏨', shopping: '🛍️',
};

// ── Date helpers ──────────────────────────────────────────────────────────────

const formatDate  = d  => d.toISOString().split('T')[0];
const displayDate = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const addDays     = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return formatDate(d); };
const nightsBetween = (a, b) => Math.max(0, Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 86400000));

// ── Animated agent log ────────────────────────────────────────────────────────

function AgentLogView({ steps, destination }) {
  const opacity = useRef(new Animated.Value(1)).current;
  const lastStep = steps[steps.length - 1] || '';

  useEffect(() => {
    Animated.sequence([
      Animated.timing(opacity, { toValue: 0.3, duration: 200, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 1,   duration: 300, useNativeDriver: true }),
    ]).start();
  }, [lastStep]);

  return (
    <View style={st.agentScreen}>
      <Text style={st.agentDest}>{destination}</Text>
      <ActivityIndicator color={ACCENT} size="large" style={{ marginBottom: 28 }} />
      <View style={st.agentLog}>
        {steps.slice(-5).map((s, i) => (
          <Animated.Text
            key={i}
            style={[st.agentStep, i === steps.slice(-5).length - 1 && { opacity, color: '#fff', fontWeight: '700' }]}
          >
            {s}
          </Animated.Text>
        ))}
      </View>
      <Text style={st.agentHint}>Your AI travel agent is working…</Text>
    </View>
  );
}

// ── Result sub-components ─────────────────────────────────────────────────────

function SectionCard({ children, style: extraStyle }) {
  return <View style={[st.sectionCard, extraStyle]}>{children}</View>;
}

function CardTitle({ children }) {
  return <Text style={st.cardTitle}>{children}</Text>;
}

function WeatherBadge({ weather }) {
  if (!weather) return null;
  const icon = weather.is_clear ? '☀️' : weather.description?.toLowerCase().includes('rain') ? '🌧️' : '⛅';
  return (
    <View style={st.weatherBadge}>
      <Text style={st.weatherText}>{icon} {weather.description}  ·  {weather.temp_high_c}° / {weather.temp_low_c}°C</Text>
    </View>
  );
}

function TransitPill({ transit }) {
  if (!transit || transit.mode === 'arrive') return null;
  const icon = TRANSIT_ICONS[transit.mode] || '➡️';
  return (
    <View style={st.transitPill}>
      <Text style={st.transitText}>{icon}  {transit.notes || `${transit.duration_min} min`}</Text>
    </View>
  );
}

function StopCard({ stop, destination }) {
  const emoji = TYPE_EMOJI[stop.poi_type] || (stop.is_meal ? '🍽️' : '📍');

  function openDirections() {
    const q = encodeURIComponent(`${stop.name}, ${destination}`);
    Linking.openURL(`https://maps.google.com/?q=${q}`);
  }

  return (
    <View style={st.stopWrap}>
      <TransitPill transit={stop.transit_from_prev} />
      <View style={st.stopCard}>
        <View style={[st.stopIconCircle, stop.is_meal && st.stopIconMeal]}>
          <Text style={st.stopIconEmoji}>{emoji}</Text>
        </View>
        <View style={st.stopBody}>
          <View style={st.stopHeaderRow}>
            <Text style={st.stopName}>{stop.name}</Text>
            {stop.is_meal && <View style={st.mealBadge}><Text style={st.mealBadgeText}>MEAL</Text></View>}
          </View>
          {stop.arrival_time ? (
            <Text style={st.stopMeta}>
              {stop.arrival_time}
              {stop.duration_min > 0 ? `  ·  ${stop.duration_min} min` : ''}
            </Text>
          ) : null}
          {stop.tip ? <Text style={st.stopTip}>{stop.tip}</Text> : null}
          {stop.poi_type !== 'accommodation' && (
            <Pressable onPress={openDirections}>
              <Text style={st.directionsLink}>📍 Directions</Text>
            </Pressable>
          )}
        </View>
      </View>
    </View>
  );
}

function DayCard({ day, destination }) {
  const [open, setOpen] = useState(true);
  const activityCount = day.stops.filter(s => !s.is_meal && s.poi_type !== 'accommodation').length;
  const mealCount     = day.stops.filter(s => s.is_meal).length;

  return (
    <SectionCard>
      <Pressable style={st.dayHeader} onPress={() => setOpen(o => !o)}>
        <View style={{ flex: 1 }}>
          <Text style={st.dayLabel}>{day.day_label}</Text>
          <Text style={st.dayMeta}>{activityCount} activities · {mealCount} meals</Text>
        </View>
        <WeatherBadge weather={day.weather} />
        <Text style={st.dayChevron}>{open ? '▲' : '▼'}</Text>
      </Pressable>
      {open && day.stops.map((stop, i) => (
        <StopCard key={i} stop={stop} destination={destination} />
      ))}
    </SectionCard>
  );
}

function GettingThereCard({ info, destination }) {
  if (!info) return null;
  return (
    <SectionCard>
      <CardTitle>✈️  Getting There</CardTitle>
      {info.notes ? <Text style={st.cardBody}>{info.notes}</Text> : null}
      <View style={st.linkRow}>
        {info.flights_url && (
          <Pressable style={st.linkBtn} onPress={() => Linking.openURL(info.flights_url)}>
            <Text style={st.linkBtnText}>Search Flights →</Text>
          </Pressable>
        )}
        <Pressable
          style={st.linkBtnSecondary}
          onPress={() => Linking.openURL(`https://maps.google.com/?q=${encodeURIComponent(destination)}`)}
        >
          <Text style={st.linkBtnSecondaryText}>Driving Directions →</Text>
        </Pressable>
      </View>
    </SectionCard>
  );
}

function AccommodationCard({ info }) {
  if (!info) return null;
  return (
    <SectionCard>
      <CardTitle>🏨  Where to Stay</CardTitle>
      {info.recommended_area && (
        <Text style={st.cardBody}>
          <Text style={{ fontWeight: '700' }}>Best area: </Text>
          {info.recommended_area}
          {info.area_reason ? `  —  ${info.area_reason}` : ''}
        </Text>
      )}
      {info.options?.map((opt, i) => (
        <View key={i} style={st.hotelRow}>
          <View style={st.hotelLeft}>
            <Text style={st.hotelName}>{opt.name}</Text>
            <Text style={st.hotelTier}>{opt.tier}</Text>
          </View>
          {opt.est_price_usd_per_night ? (
            <Text style={st.hotelPrice}>${opt.est_price_usd_per_night}/night</Text>
          ) : null}
        </View>
      ))}
      {info.booking_url && (
        <Pressable style={[st.linkBtn, { marginTop: 10 }]} onPress={() => Linking.openURL(info.booking_url)}>
          <Text style={st.linkBtnText}>Search on Booking.com →</Text>
        </Pressable>
      )}
    </SectionCard>
  );
}

function BudgetCard({ budget }) {
  if (!budget) return null;
  const rows = [
    ['🏨 Accommodation', budget.accommodation_usd],
    ['🍽️ Food',          budget.food_usd],
    ['🎟️ Activities',    budget.activities_usd],
    ['🚕 Transport',     budget.transport_usd],
  ].filter(([, v]) => v);

  return (
    <SectionCard>
      <CardTitle>💰  Estimated Budget</CardTitle>
      {rows.map(([label, val]) => (
        <View key={label} style={st.budgetRow}>
          <Text style={st.budgetLabel}>{label}</Text>
          <Text style={st.budgetValue}>${val}</Text>
        </View>
      ))}
      <View style={[st.budgetRow, st.budgetTotal]}>
        <Text style={st.budgetTotalLabel}>Total (per person)</Text>
        <Text style={st.budgetTotalValue}>${budget.total_usd}</Text>
      </View>
      {budget.notes ? <Text style={st.budgetNotes}>{budget.notes}</Text> : null}
    </SectionCard>
  );
}

// ── Form sub-components ───────────────────────────────────────────────────────

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
  const [agentSteps,     setAgentSteps]     = useState([]);
  const [streaming,      setStreaming]      = useState(false);
  const [plan,           setPlan]           = useState(null);
  const [error,          setError]          = useState(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [token,          setToken]          = useState(null);
  const xhrRef = useRef(null);

  // Load profile on mount
  useEffect(() => {
    (async () => {
      try {
        let { data: { session } } = await supabase.auth.getSession();
        if (session?.expires_at && session.expires_at * 1000 < Date.now()) {
          const { data } = await supabase.auth.refreshSession();
          session = data.session;
        }
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
    if (selectedVibe === vibe.id) { setSelectedVibe(null); return; }
    setSelectedVibe(vibe.id);
    setInterests(vibe.interests);
    setPace(vibe.pace);
  }

  function toggleInterest(id) {
    setSelectedVibe(null);
    setInterests(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  }

  const nights = nightsBetween(startDate, endDate);

  async function handleGenerate() {
    if (!destination.trim()) { setError('Enter a destination first.'); return; }
    if (endDate < startDate)  { setError('End date must be after start date.'); return; }
    if (!interests.length)    { setError('Pick at least one interest or a trip vibe.'); return; }

    setStreaming(true);
    setError(null);
    setPlan(null);
    setAgentSteps([]);

    const payload = JSON.stringify({
      destination,
      start_date:          startDate,
      end_date:            endDate,
      interests,
      travel_style:        style,
      pace,
      drive_tolerance_hrs: driveTol,
    });

    // XHR onprogress is the reliable way to read SSE in React Native / Expo Go.
    // fetch+getReader is not supported in the Hermes/Expo fetch polyfill.
    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    let cursor = 0; // tracks how much of responseText we've already parsed

    xhr.open('POST', `${API_BASE}/v1/itinerary/stream`);
    xhr.setRequestHeader('Content-Type', 'application/json');
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    xhr.onprogress = () => {
      const newText = xhr.responseText.slice(cursor);
      cursor = xhr.responseText.length;
      for (const line of newText.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === 'start' || event.type === 'step' || event.type === 'result') {
            setAgentSteps(prev => [...prev, event.message]);
          } else if (event.type === 'complete') {
            setPlan(event.plan);
            setStreaming(false);
          } else if (event.type === 'error') {
            setError(event.message);
            setStreaming(false);
          }
        } catch (_) {}
      }
    };

    xhr.onload = () => {
      // Parse any remaining text that onprogress may have missed
      const remaining = xhr.responseText.slice(cursor);
      for (const line of remaining.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === 'complete') { setPlan(event.plan); }
          if (event.type === 'error')    { setError(event.message); }
        } catch (_) {}
      }
      setStreaming(false);
    };

    xhr.onerror = () => {
      setError('Network error — please check your connection and try again.');
      setStreaming(false);
    };

    xhr.ontimeout = () => {
      setError('Request timed out. Please try again.');
      setStreaming(false);
    };

    xhr.timeout = 120000; // 2 min — agent can take ~30s
    xhr.send(payload);
  }

  // ── Streaming / loading screen ────────────────────────────────────────────
  if (streaming) {
    return (
      <SafeAreaView style={st.safe}>
        <AgentLogView steps={agentSteps} destination={destination} />
      </SafeAreaView>
    );
  }

  // ── Results view ──────────────────────────────────────────────────────────
  if (plan) {
    return (
      <SafeAreaView style={st.safe}>
        <ScrollView contentContainerStyle={st.scroll} showsVerticalScrollIndicator={false}>
          {/* Header */}
          <View style={st.resultsHeader}>
            <Pressable onPress={() => setPlan(null)}>
              <Text style={st.backBtnText}>← New Trip</Text>
            </Pressable>
            <Text style={st.resultsTitle}>{plan.title}</Text>
            <Text style={st.resultsMeta}>
              {plan.destination}  ·  {displayDate(plan.start_date)} – {displayDate(plan.end_date)}
            </Text>
            {plan.summary ? <Text style={st.resultsSummary}>{plan.summary}</Text> : null}
          </View>

          <GettingThereCard info={plan.getting_there} destination={plan.destination} />
          <AccommodationCard info={plan.accommodation} />
          <BudgetCard budget={plan.budget} />

          {plan.days?.map((day, i) => (
            <DayCard key={i} day={day} destination={plan.destination} />
          ))}

          {/* Live Walk CTA */}
          <View style={st.walkCTA}>
            <Text style={st.walkCTATitle}>Ready to explore?</Text>
            <Text style={st.walkCTASub}>
              When you arrive, switch to Live Walk for real-time AI audio stories as you move through your stops.
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
        <Text style={st.sub}>
          Your AI travel agent handles everything — stops, meals, accommodation, commute, and budget.
        </Text>

        {/* Trip vibes */}
        <SectionLabel hint="Pick a vibe to instantly pre-fill your interests and pace">
          What kind of trip?
        </SectionLabel>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 4 }}>
          {TRIP_VIBES.map(v => (
            <Pressable
              key={v.id}
              style={[st.vibeChip, selectedVibe === v.id && st.vibeChipSelected]}
              onPress={() => applyVibe(v)}
            >
              <Text style={st.vibeEmoji}>{v.emoji}</Text>
              <Text style={[st.vibeLabel, selectedVibe === v.id && st.vibeLabelSelected]}>{v.label}</Text>
            </Pressable>
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
        />

        {/* Dates */}
        <SectionLabel>When?</SectionLabel>
        <DateStepper
          label="Start" value={startDate}
          onChange={v => { setStartDate(v); if (v > endDate) setEndDate(v); }}
        />
        <DateStepper label="End" value={endDate} onChange={setEndDate} min={startDate} />
        {nights > 0 && (
          <Text style={st.nightsLabel}>
            {nights} night{nights !== 1 ? 's' : ''}  ·  {nights + 1} day{nights + 1 !== 1 ? 's' : ''}
          </Text>
        )}

        {/* Interests */}
        <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 20, marginBottom: 4 }}>
          <Text style={[st.sectionLabel, { marginTop: 0 }]}>Fine-tune your interests</Text>
          {profileLoading && <ActivityIndicator size="small" color={ACCENT} style={{ marginLeft: 8 }} />}
        </View>
        <Text style={st.sectionHint}>Pre-filled from your profile. Tap to toggle.</Text>
        <View style={st.chipGrid}>
          {ALL_INTERESTS.map(item => (
            <InterestChip
              key={item.id} item={item}
              selected={interests.includes(item.id)}
              onPress={() => toggleInterest(item.id)}
            />
          ))}
        </View>

        {/* Pace */}
        <SectionLabel>Trip pace</SectionLabel>
        <View style={st.pillRow}>
          {PACE_OPTIONS.map(o => <OptionPill key={o.id} item={o} selected={pace === o.id} onPress={() => setPace(o.id)} />)}
        </View>

        {/* Style */}
        <SectionLabel>Travelling as</SectionLabel>
        <View style={st.pillRow}>
          {STYLE_OPTIONS.map(o => <OptionPill key={o.id} item={o} selected={style === o.id} onPress={() => setStyle(o.id)} />)}
        </View>

        {/* Drive tolerance */}
        <SectionLabel>Max drive between stops</SectionLabel>
        <View style={st.driveRow}>
          {[0.5, 1, 2, 4].map(h => (
            <Pressable key={h} style={[st.driveBtn, driveTol === h && st.driveBtnSelected]} onPress={() => setDriveTol(h)}>
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

  sectionLabel: { fontSize: 13, fontWeight: '700', color: '#0F172A', marginTop: 20, marginBottom: 4 },
  sectionHint:  { fontSize: 12, color: '#94A3B8', marginBottom: 8 },

  input: {
    backgroundColor: '#fff', borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 12,
    paddingHorizontal: 14, paddingVertical: 12, fontSize: 16, color: '#0F172A',
  },

  // Vibe chips
  vibeChip: {
    alignItems: 'center', justifyContent: 'center', paddingHorizontal: 16, paddingVertical: 10,
    borderRadius: 20, borderWidth: 1.5, borderColor: '#E2E8F0', backgroundColor: '#fff', marginRight: 8,
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

  // Agent loading screen
  agentScreen: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 32, backgroundColor: '#0F172A' },
  agentDest:   { fontSize: 30, fontWeight: '800', color: '#fff', marginBottom: 32, textAlign: 'center' },
  agentLog:    { width: '100%', gap: 8, marginBottom: 24 },
  agentStep:   { fontSize: 14, color: '#64748B', textAlign: 'center' },
  agentHint:   { fontSize: 12, color: '#334155' },

  // Section cards (results)
  sectionCard: {
    backgroundColor: '#fff', borderRadius: 16, marginBottom: 12,
    shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 6, shadowOffset: { width: 0, height: 1 },
    elevation: 1, overflow: 'hidden',
  },
  cardTitle: { fontSize: 15, fontWeight: '800', color: '#0F172A', padding: 16, paddingBottom: 8 },
  cardBody:  { fontSize: 13, color: '#475569', paddingHorizontal: 16, paddingBottom: 10, lineHeight: 19 },

  // Results header
  resultsHeader: {
    backgroundColor: '#fff', borderRadius: 16, padding: 18, marginBottom: 12,
    shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 8, shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  backBtnText:    { fontSize: 14, color: ACCENT, fontWeight: '600', marginBottom: 12 },
  resultsTitle:   { fontSize: 22, fontWeight: '800', color: '#0F172A', marginBottom: 4 },
  resultsMeta:    { fontSize: 13, color: '#64748B', marginBottom: 8 },
  resultsSummary: { fontSize: 14, color: '#475569', lineHeight: 20 },

  // Getting there
  linkRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingBottom: 14, flexWrap: 'wrap' },
  linkBtn: {
    backgroundColor: ACCENT, paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 10, alignSelf: 'flex-start',
  },
  linkBtnText: { color: '#fff', fontSize: 13, fontWeight: '600' },
  linkBtnSecondary: {
    backgroundColor: '#F1F5F9', paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 10, alignSelf: 'flex-start',
  },
  linkBtnSecondaryText: { color: '#334155', fontSize: 13, fontWeight: '600' },

  // Accommodation
  hotelRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 8, borderTopWidth: 1, borderTopColor: '#F1F5F9',
  },
  hotelLeft:  { flex: 1 },
  hotelName:  { fontSize: 14, fontWeight: '600', color: '#1E293B' },
  hotelTier:  { fontSize: 12, color: '#94A3B8', textTransform: 'capitalize' },
  hotelPrice: { fontSize: 14, fontWeight: '700', color: ACCENT },

  // Budget
  budgetRow: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 7, borderTopWidth: 1, borderTopColor: '#F8FAFC',
  },
  budgetLabel:      { fontSize: 13, color: '#475569' },
  budgetValue:      { fontSize: 13, fontWeight: '600', color: '#1E293B' },
  budgetTotal:      { borderTopWidth: 1, borderTopColor: '#E2E8F0', marginTop: 2, paddingTop: 10 },
  budgetTotalLabel: { fontSize: 14, fontWeight: '700', color: '#0F172A' },
  budgetTotalValue: { fontSize: 16, fontWeight: '800', color: ACCENT },
  budgetNotes:      { fontSize: 12, color: '#94A3B8', paddingHorizontal: 16, paddingBottom: 12, fontStyle: 'italic' },

  // Day header
  dayHeader: {
    flexDirection: 'row', alignItems: 'center', padding: 16,
    borderBottomWidth: 1, borderBottomColor: '#F1F5F9',
  },
  dayLabel:   { fontSize: 15, fontWeight: '700', color: '#0F172A' },
  dayMeta:    { fontSize: 12, color: '#94A3B8', marginTop: 2 },
  dayChevron: { fontSize: 11, color: '#94A3B8', marginLeft: 8 },

  // Weather badge
  weatherBadge: {
    backgroundColor: '#F0FDF4', paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 8, marginRight: 8,
  },
  weatherText: { fontSize: 11, color: '#166534', fontWeight: '600' },

  // Transit pill
  transitPill: {
    alignSelf: 'flex-start', marginLeft: 56, marginBottom: 4,
    backgroundColor: '#F8FAFC', paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 10, borderWidth: 1, borderColor: '#E2E8F0',
  },
  transitText: { fontSize: 11, color: '#64748B' },

  // Stop cards
  stopWrap:  { paddingHorizontal: 16, paddingTop: 12 },
  stopCard:  { flexDirection: 'row', marginBottom: 4 },
  stopIconCircle: {
    width: 36, height: 36, borderRadius: 18,
    backgroundColor: '#EEF2FF', alignItems: 'center', justifyContent: 'center', marginRight: 12,
  },
  stopIconMeal: { backgroundColor: '#FEF3C7' },
  stopIconEmoji: { fontSize: 17 },
  stopBody:  { flex: 1, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: '#F1F5F9' },
  stopHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 2 },
  stopName:  { fontSize: 14, fontWeight: '700', color: '#1E293B', flex: 1 },
  mealBadge: { backgroundColor: '#FEF3C7', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6 },
  mealBadgeText: { fontSize: 10, fontWeight: '700', color: '#92400E' },
  stopMeta:  { fontSize: 12, color: '#94A3B8', marginBottom: 4 },
  stopTip:   { fontSize: 13, color: '#475569', lineHeight: 19, marginBottom: 6 },
  directionsLink: { fontSize: 12, color: ACCENT, fontWeight: '600' },

  // Walk CTA
  walkCTA: { backgroundColor: '#1E1B4B', borderRadius: 16, padding: 20, marginTop: 8 },
  walkCTATitle:   { fontSize: 17, fontWeight: '800', color: '#fff', marginBottom: 6 },
  walkCTASub:     { fontSize: 13, color: '#A5B4FC', lineHeight: 19, marginBottom: 16 },
  walkCTABtn:     { backgroundColor: ACCENT, borderRadius: 10, paddingVertical: 12, alignItems: 'center' },
  walkCTABtnText: { color: '#fff', fontSize: 14, fontWeight: '700' },
});
