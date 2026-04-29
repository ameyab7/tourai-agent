import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Dimensions,
  FlatList,
  Linking,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { LinearGradient } from 'expo-linear-gradient';

const { height: SCREEN_H, width: SCREEN_W } = Dimensions.get('window');
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { supabase } from '../../lib/supabase';
import { API_BASE } from '../../lib/config.js';

// ── Reusable animated pressable with spring scale ─────────────────────────────
const AnimPressable = Animated.createAnimatedComponent(Pressable);

function ScalePress({ children, style, onPress, amount = 0.96, haptic = 'light', ...rest }) {
  const scale = useRef(new Animated.Value(1)).current;
  const cfg   = { useNativeDriver: true, speed: 50, bounciness: 3 };

  function pressIn()  { Animated.spring(scale, { toValue: amount, ...cfg }).start(); }
  function pressOut() { Animated.spring(scale, { toValue: 1,      ...cfg }).start(); }

  function handlePress(e) {
    if (haptic === 'medium') Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    else if (haptic === 'light') Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    else if (haptic === 'select') Haptics.selectionAsync();
    onPress?.(e);
  }

  return (
    <AnimPressable
      onPressIn={pressIn}
      onPressOut={pressOut}
      onPress={handlePress}
      style={[{ transform: [{ scale }] }, style]}
      {...rest}
    >
      {children}
    </AnimPressable>
  );
}

// ── Design tokens ─────────────────────────────────────────────────────────────
const C = {
  accent:    '#4F46E5',
  accentSoft:'#EEF2FF',
  accentMid: '#6366F1',
  dark:      '#0F172A',
  card:      '#FFFFFF',
  bg:        '#F1F5F9',
  border:    '#E2E8F0',
  text:      '#1E293B',
  sub:       '#64748B',
  muted:     '#94A3B8',
  green:     '#16A34A',
  orange:    '#EA580C',
  red:       '#DC2626',
  amber:     '#D97706',
  navy:      '#1E1B4B',
};

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
  { id: 'city',    label: 'City Break',     emoji: '🏙️', interests: ['culture','food','architecture','social'], pace: 'balanced' },
  { id: 'nature',  label: 'Nature Escape',  emoji: '🌲', interests: ['nature','hiking','photography'],          pace: 'relaxed'  },
  { id: 'history', label: 'History Tour',   emoji: '🏛️', interests: ['history','architecture','culture'],       pace: 'balanced' },
  { id: 'food',    label: 'Food & Culture', emoji: '🍜', interests: ['food','culture','social'],                pace: 'relaxed'  },
  { id: 'beach',   label: 'Beach Getaway',  emoji: '🌊', interests: ['nature','photography','social'],          pace: 'relaxed'  },
  { id: 'photo',   label: 'Photo Trip',     emoji: '📷', interests: ['photography','nature','architecture'],    pace: 'packed'   },
];

const VIBE_COLORS = {
  city:    { bg: '#EEF2FF', accent: '#4F46E5', sub: 'Culture · Food · Architecture' },
  nature:  { bg: '#F0FDF4', accent: '#16A34A', sub: 'Hiking · Wildlife · Views'     },
  history: { bg: '#FFFBEB', accent: '#B45309', sub: 'Heritage · Landmarks · Art'    },
  food:    { bg: '#FFF7ED', accent: '#EA580C', sub: 'Local eats · Markets · Culture' },
  beach:   { bg: '#ECFEFF', accent: '#0891B2', sub: 'Sun · Water · Relaxation'      },
  photo:   { bg: '#FAF5FF', accent: '#7C3AED', sub: 'Golden hour · Scenery · Shots'  },
};

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

const TYPE_DOT_COLOR = {
  accommodation: '#6366F1', restaurant: '#EA580C', cafe: '#EA580C',
  bar: '#EA580C', pub: '#EA580C', museum: '#7C3AED', art_gallery: '#7C3AED',
  historic: '#B45309', castle: '#B45309', monument: '#B45309',
  park: '#16A34A', nature_reserve: '#16A34A', garden: '#16A34A',
  viewpoint: '#0284C7', beach: '#0891B2', peak: '#0284C7',
  attraction: '#F59E0B', culture: '#8B5CF6',
};

const CROWD_CONFIG = {
  low:    { color: '#16A34A', bg: '#F0FDF4', label: 'Low crowds' },
  medium: { color: '#D97706', bg: '#FFFBEB', label: 'Moderate' },
  high:   { color: '#DC2626', bg: '#FEF2F2', label: 'Busy' },
};

// ── Helpers ───────────────────────────────────────────────────────────────────
const formatDate    = d  => d.toISOString().split('T')[0];
const displayDate   = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const addDays       = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return formatDate(d); };
const nightsBetween = (a, b)   => Math.max(0, Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 86400000));
const dotColor      = stop => stop.is_meal ? C.orange : (TYPE_DOT_COLOR[stop.poi_type] ?? C.sub);

// ── Loading screen ────────────────────────────────────────────────────────────
function LoadingScreen({ steps, destination }) {
  const pulse = useRef(new Animated.Value(0.6)).current;
  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1,   duration: 900, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.6, duration: 900, useNativeDriver: true }),
      ])
    ).start();
  }, []);

  return (
    <View style={ls.screen}>
      <Text style={ls.dest}>{destination}</Text>
      <Animated.View style={[ls.ring, { opacity: pulse }]}>
        <ActivityIndicator color={C.accentMid} size="large" />
      </Animated.View>
      <View style={ls.log}>
        {steps.slice(-4).map((s, i, arr) => {
          const isLast = i === arr.length - 1;
          return (
            <View key={i} style={ls.stepRow}>
              <View style={[ls.stepDot, isLast && ls.stepDotActive]} />
              <Text style={[ls.stepText, isLast && ls.stepTextActive]}>{s}</Text>
            </View>
          );
        })}
      </View>
      <Text style={ls.hint}>Crafting your perfect itinerary…</Text>
    </View>
  );
}

const ls = StyleSheet.create({
  screen:        { flex: 1, backgroundColor: '#080E1A', justifyContent: 'center', alignItems: 'center', padding: 32 },
  dest:          { fontSize: 34, fontWeight: '900', color: '#fff', textAlign: 'center', marginBottom: 40, letterSpacing: -0.5 },
  ring:          { width: 64, height: 64, borderRadius: 32, borderWidth: 2, borderColor: C.accentMid + '40', alignItems: 'center', justifyContent: 'center', marginBottom: 40 },
  log:           { width: '100%', gap: 12, marginBottom: 32 },
  stepRow:       { flexDirection: 'row', alignItems: 'center', gap: 10 },
  stepDot:       { width: 6, height: 6, borderRadius: 3, backgroundColor: '#334155' },
  stepDotActive: { backgroundColor: C.accentMid, width: 8, height: 8, borderRadius: 4 },
  stepText:      { fontSize: 13, color: '#475569', flex: 1 },
  stepTextActive:{ color: '#CBD5E1', fontWeight: '600' },
  hint:          { fontSize: 12, color: '#334155', fontStyle: 'italic' },
});

// ── Timeline stop ─────────────────────────────────────────────────────────────
function TimelineStop({ stop, destination, isLast, dimmed, index = 0 }) {
  const [expanded, setExpanded] = useState(false);

  // Staggered entrance
  const enterY    = useRef(new Animated.Value(18)).current;
  const enterFade = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const delay = index * 55;
    Animated.parallel([
      Animated.timing(enterFade, { toValue: 1, duration: 280, delay, useNativeDriver: true }),
      Animated.timing(enterY,    { toValue: 0, duration: 280, delay, useNativeDriver: true }),
    ]).start();
  }, []);
  const emoji      = TYPE_EMOJI[stop.poi_type] || (stop.is_meal ? '🍽️' : '📍');
  const color      = dotColor(stop);
  const crowd      = stop.crowd_level ? CROWD_CONFIG[stop.crowd_level] : null;
  const transit    = stop.transit_from_prev;
  const hasTransit = transit && transit.mode !== 'arrive';

  function openDirections() {
    Linking.openURL(`https://maps.google.com/?q=${encodeURIComponent(`${stop.name}, ${destination}`)}`);
  }

  function goLiveWalk() {
    router.push('/(tabs)/live-walk');
  }

  return (
    <Animated.View style={[
      tl.row,
      dimmed && tl.rowDimmed,
      { opacity: enterFade, transform: [{ translateY: enterY }] },
    ]}>
      {/* Left: line + dot */}
      <View style={tl.lineCol}>
        {hasTransit && <View style={[tl.line, { backgroundColor: C.border }]} />}
        <View style={[tl.dot, { backgroundColor: color + '18', borderColor: dimmed ? C.border : color }]}>
          <Text style={[tl.dotEmoji, dimmed && { opacity: 0.4 }]}>{emoji}</Text>
        </View>
        {!isLast && <View style={[tl.line, { flex: 1, backgroundColor: C.border }]} />}
      </View>

      {/* Right: content */}
      <View style={tl.content}>
        {hasTransit && (
          <View style={tl.transitRow}>
            <Text style={tl.transitIcon}>{TRANSIT_ICONS[transit.mode] || '➡️'}</Text>
            <Text style={tl.transitText}>{transit.notes || `${transit.duration_min} min`}</Text>
          </View>
        )}

        <ScalePress
          style={[tl.card, dimmed && tl.cardDimmed]}
          amount={0.98}
          haptic="select"
          onPress={() => {
            setExpanded(e => !e);
          }}
          onLongPress={openDirections}
          delayLongPress={500}
        >
          {/* Header row */}
          <View style={tl.cardHeader}>
            <View style={{ flex: 1 }}>
              <View style={tl.nameRow}>
                <Text style={[tl.stopName, dimmed && tl.stopNameDimmed]} numberOfLines={expanded ? 0 : 2}>
                  {stop.name}
                </Text>
              </View>
              <View style={tl.metaRow}>
                {stop.arrival_time ? <Text style={tl.time}>{stop.arrival_time}</Text> : null}
                {stop.arrival_time && stop.duration_min > 0 ? <Text style={tl.timeDot}>·</Text> : null}
                {stop.duration_min > 0 ? <Text style={tl.time}>{stop.duration_min} min</Text> : null}
                {stop.is_meal ? <View style={tl.mealPill}><Text style={tl.mealPillText}>MEAL</Text></View> : null}
                {stop.skip_if_rushed ? <View style={tl.skipPill}><Text style={tl.skipPillText}>OPTIONAL</Text></View> : null}
              </View>
            </View>
            <Text style={tl.chevron}>{expanded ? '▲' : '▼'}</Text>
          </View>

          {/* Expanded detail — crowd + hours collapsed until tap */}
          {expanded && (
            <View style={tl.expandedBody}>
              {stop.tip ? <Text style={tl.tip}>{stop.tip}</Text> : null}

              <View style={tl.pillsRow}>
                {crowd ? (
                  <View style={[tl.pill, { backgroundColor: crowd.bg }]}>
                    <View style={[tl.pillDot, { backgroundColor: crowd.color }]} />
                    <Text style={[tl.pillText, { color: crowd.color }]}>{crowd.label}</Text>
                  </View>
                ) : null}
                {stop.best_time && !stop.is_meal ? (
                  <View style={tl.pill}>
                    <Text style={tl.pillText}>⏰ {stop.best_time}</Text>
                  </View>
                ) : null}
              </View>

              {stop.opening_hours_note && !stop.is_meal ? (
                <Text style={tl.hours}>🕐 {stop.opening_hours_note}</Text>
              ) : null}

              <View style={tl.stopActions}>
                {stop.poi_type !== 'accommodation' ? (
                  <Pressable style={tl.actionBtn} onPress={openDirections}>
                    <Text style={tl.actionBtnText}>📍 Open in Maps</Text>
                  </Pressable>
                ) : null}
                <Pressable style={[tl.actionBtn, tl.actionBtnPrimary]} onPress={goLiveWalk}>
                  <Text style={tl.actionBtnPrimaryText}>🎧 I'm here now</Text>
                </Pressable>
              </View>
            </View>
          )}
        </ScalePress>
      </View>
    </Animated.View>
  );
}

const tl = StyleSheet.create({
  row:        { flexDirection: 'row', marginBottom: 4 },
  lineCol:    { width: 40, alignItems: 'center' },
  dot: {
    width: 30, height: 30, borderRadius: 15, borderWidth: 1.5,
    alignItems: 'center', justifyContent: 'center', zIndex: 1,
  },
  dotEmoji:   { fontSize: 14 },
  line:       { width: 2, minHeight: 10 },
  content:    { flex: 1, paddingLeft: 8, paddingBottom: 12 },

  transitRow: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 6 },
  transitIcon:{ fontSize: 12, color: C.muted },
  transitText:{ fontSize: 12, color: C.muted, flex: 1 },

  card: {
    backgroundColor: C.card, borderRadius: 12,
    borderWidth: 1, borderColor: C.border,
    overflow: 'hidden',
  },
  cardHeader: { flexDirection: 'row', alignItems: 'flex-start', padding: 12 },
  nameRow:    { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 },
  stopName:   { fontSize: 14, fontWeight: '700', color: C.text, flex: 1 },
  metaRow:    { flexDirection: 'row', alignItems: 'center', gap: 5, flexWrap: 'wrap' },
  time:       { fontSize: 11, color: C.muted, fontWeight: '500' },
  timeDot:    { fontSize: 11, color: C.muted },
  chevron:    { fontSize: 9, color: C.muted, marginLeft: 8, marginTop: 4 },

  mealPill:     { backgroundColor: '#FEF3C7', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  mealPillText: { fontSize: 9, fontWeight: '700', color: '#92400E' },
  skipPill:     { backgroundColor: '#F1F5F9', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  skipPillText: { fontSize: 9, fontWeight: '600', color: C.sub },

  expandedBody: { borderTopWidth: 1, borderTopColor: C.border, padding: 12, gap: 8 },
  tip:          { fontSize: 13, color: C.sub, lineHeight: 20 },
  pillsRow:     { flexDirection: 'row', gap: 6, flexWrap: 'wrap' },
  pill: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    backgroundColor: '#F8FAFC', borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 4,
  },
  pillDot:    { width: 6, height: 6, borderRadius: 3 },
  pillText:   { fontSize: 11, fontWeight: '600', color: C.sub },
  hours:      { fontSize: 11, color: C.muted },

  stopActions:           { flexDirection: 'row', gap: 8, marginTop: 4, flexWrap: 'wrap' },
  actionBtn:             { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 8, borderWidth: 1, borderColor: C.border, backgroundColor: C.bg },
  actionBtnText:         { fontSize: 12, fontWeight: '600', color: C.sub },
  actionBtnPrimary:      { backgroundColor: C.accent, borderColor: C.accent },
  actionBtnPrimaryText:  { fontSize: 12, fontWeight: '700', color: '#fff' },

  rowDimmed:      { opacity: 0.35 },
  cardDimmed:     { borderColor: C.border },
  stopNameDimmed: { color: C.muted },
});

// ── Result components ─────────────────────────────────────────────────────────
function HighlightsCard({ highlights, onHighlightTap }) {
  if (!highlights?.length) return null;
  return (
    <LinearGradient
      colors={['#1E1B4B', '#312E81']}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={rc.highlightsCard}
    >
      <Text style={rc.highlightsEye}>CAN'T MISS</Text>
      <Text style={rc.highlightsTitle}>Your friend will ask if you went here</Text>
      <View style={rc.highlightsList}>
        {highlights.map((h, i) => (
          <ScalePress
            key={i}
            style={rc.highlightRow}
            amount={0.97}
            haptic="light"
            onPress={() => onHighlightTap?.(h.name)}
          >
            <Text style={rc.hlEmoji}>{h.emoji || '⭐'}</Text>
            <View style={{ flex: 1 }}>
              <Text style={rc.hlName}>{h.name}</Text>
              <Text style={rc.hlWhy}>{h.why_cant_skip}</Text>
            </View>
            <Text style={rc.hlArrow}>›</Text>
          </ScalePress>
        ))}
      </View>
    </LinearGradient>
  );
}

function InfoCard({ icon, title, children }) {
  return (
    <View style={rc.infoCard}>
      <View style={rc.infoCardHeader}>
        <Text style={rc.infoCardIcon}>{icon}</Text>
        <Text style={rc.infoCardTitle}>{title}</Text>
      </View>
      <View style={rc.infoCardBody}>{children}</View>
    </View>
  );
}

function GettingThereCard({ info, destination }) {
  if (!info) return null;
  return (
    <InfoCard icon="✈️" title="Getting There">
      {info.notes ? <Text style={rc.bodyText}>{info.notes}</Text> : null}
      <View style={rc.btnRow}>
        {info.flights_url ? (
          <Pressable style={rc.btnPrimary} onPress={() => Linking.openURL(info.flights_url)}>
            <Text style={rc.btnPrimaryText}>Search Flights →</Text>
          </Pressable>
        ) : null}
        <Pressable
          style={rc.btnSecondary}
          onPress={() => Linking.openURL(`https://maps.google.com/?q=${encodeURIComponent(destination)}`)}
        >
          <Text style={rc.btnSecondaryText}>Driving Directions →</Text>
        </Pressable>
      </View>
    </InfoCard>
  );
}

function AccommodationCard({ info }) {
  if (!info) return null;
  return (
    <InfoCard icon="🏨" title="Where to Stay">
      {info.recommended_area ? (
        <Text style={rc.bodyText}>
          <Text style={{ fontWeight: '700' }}>Best area: </Text>
          {info.recommended_area}
          {info.area_reason ? `  —  ${info.area_reason}` : ''}
        </Text>
      ) : null}
      {info.options?.map((opt, i) => (
        <View key={i} style={rc.hotelRow}>
          <View style={{ flex: 1 }}>
            <Text style={rc.hotelName}>{opt.name}</Text>
            <Text style={rc.hotelTier}>{opt.tier}</Text>
          </View>
          {opt.est_price_usd_per_night ? (
            <Text style={rc.hotelPrice}>${opt.est_price_usd_per_night}/night</Text>
          ) : null}
        </View>
      ))}
      {info.booking_url ? (
        <Pressable style={[rc.btnPrimary, { marginTop: 8 }]} onPress={() => Linking.openURL(info.booking_url)}>
          <Text style={rc.btnPrimaryText}>Search on Booking.com →</Text>
        </Pressable>
      ) : null}
    </InfoCard>
  );
}

function BudgetCard({ budget }) {
  if (!budget) return null;
  const rows = [
    ['🏨  Accommodation', budget.accommodation_usd],
    ['🍽️  Food',          budget.food_usd],
    ['🎟️  Activities',    budget.activities_usd],
    ['🚕  Transport',     budget.transport_usd],
  ].filter(([, v]) => v);

  return (
    <InfoCard icon="💰" title="Estimated Budget">
      {rows.map(([label, val]) => (
        <View key={label} style={rc.budgetRow}>
          <Text style={rc.budgetLabel}>{label}</Text>
          <Text style={rc.budgetVal}>${val}</Text>
        </View>
      ))}
      <View style={rc.budgetTotalRow}>
        <Text style={rc.budgetTotalLabel}>Total (per person)</Text>
        <Text style={rc.budgetTotalVal}>${budget.total_usd}</Text>
      </View>
      {budget.notes ? <Text style={rc.budgetNotes}>{budget.notes}</Text> : null}
    </InfoCard>
  );
}

function DayTabContent({ day, destination }) {
  // Auto-surface rain plan when weather is not clear
  const [showRain,    setShowRain]    = useState(day.weather ? !day.weather.is_clear : false);
  const [runningLate, setRunningLate] = useState(false);

  const skippableCount = day.stops.filter(s => s.skip_if_rushed).length;
  const activityCount  = day.stops.filter(s => !s.is_meal && s.poi_type !== 'accommodation').length;
  const mealCount      = day.stops.filter(s => s.is_meal).length;

  const w = day.weather;
  const weatherIcon = !w ? null
    : w.is_clear                                       ? '☀️'
    : w.description?.toLowerCase().includes('rain')   ? '🌧️'
    : w.description?.toLowerCase().includes('cloud')  ? '⛅'
    : w.description?.toLowerCase().includes('snow')   ? '❄️'
    : '🌤️';

  const weatherColors = !w ? null
    : w.is_clear
      ? { bg: '#F0FDF4', text: '#166534', border: '#BBF7D0', sub: '#4ADE80' }
      : w.description?.toLowerCase().includes('rain')
        ? { bg: '#EFF6FF', text: '#1D4ED8', border: '#BFDBFE', sub: '#60A5FA' }
        : { bg: '#FAFAFA', text: '#374151', border: '#E5E7EB', sub: '#9CA3AF' };

  return (
    <View>
      {/* ── Sticky weather bar ── */}
      {w && weatherColors ? (
        <View style={[rc.weatherBar, { backgroundColor: weatherColors.bg, borderColor: weatherColors.border }]}>
          <Text style={rc.weatherBarIcon}>{weatherIcon}</Text>
          <View style={{ flex: 1 }}>
            <Text style={[rc.weatherBarDesc, { color: weatherColors.text }]}>
              {w.description ?? 'Weather update'}
            </Text>
            {w.temp_high_c != null ? (
              <Text style={[rc.weatherBarTemp, { color: weatherColors.sub }]}>
                High {w.temp_high_c}°  ·  Low {w.temp_low_c}°
              </Text>
            ) : null}
          </View>
          {skippableCount > 0 ? (
            <Pressable
              style={[rc.lateBtn, runningLate && rc.lateBtnOn]}
              onPress={() => setRunningLate(r => !r)}
            >
              <Text style={[rc.lateBtnText, runningLate && rc.lateBtnTextOn]}>
                {runningLate ? '✓ Late mode' : '⏱ Running late?'}
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      {/* ── Day summary row ── */}
      <View style={rc.dayMeta}>
        <View style={{ flex: 1 }}>
          <Text style={rc.dayLabel}>{day.day_label}</Text>
          <Text style={rc.dayCount}>
            {activityCount} stops · {mealCount} meals
            {runningLate && skippableCount > 0 ? `  ·  ${skippableCount} can skip` : ''}
          </Text>
        </View>
        {/* Running late button when no weather bar */}
        {!w && skippableCount > 0 ? (
          <Pressable
            style={[rc.lateBtn, runningLate && rc.lateBtnOn]}
            onPress={() => setRunningLate(r => !r)}
          >
            <Text style={[rc.lateBtnText, runningLate && rc.lateBtnTextOn]}>
              {runningLate ? '✓ Late mode' : '⏱ Running late?'}
            </Text>
          </Pressable>
        ) : null}
      </View>

      {/* ── Rain plan (auto-expanded when not clear) ── */}
      {day.rain_plan ? (
        <>
          <Pressable style={rc.rainRow} onPress={() => setShowRain(r => !r)}>
            <View style={{ flex: 1 }}>
              <Text style={rc.rainLabel}>🌧  Rain backup plan</Text>
              {!showRain ? (
                <Text style={rc.rainPeek} numberOfLines={1}>{day.rain_plan}</Text>
              ) : null}
            </View>
            <Text style={rc.rainChevron}>{showRain ? '▲' : '▼'}</Text>
          </Pressable>
          {showRain ? (
            <View style={rc.rainBody}>
              <Text style={rc.rainText}>{day.rain_plan}</Text>
            </View>
          ) : null}
        </>
      ) : null}

      {/* ── Running late banner ── */}
      {runningLate ? (
        <View style={rc.lateBanner}>
          <Text style={rc.lateBannerText}>
            Showing {skippableCount} optional stop{skippableCount !== 1 ? 's' : ''} you can skip — others dimmed
          </Text>
        </View>
      ) : null}

      {/* ── Timeline ── */}
      <View style={{ paddingTop: 8 }}>
        {day.stops.map((stop, i) => (
          <TimelineStop
            key={i}
            index={i}
            stop={stop}
            destination={destination}
            isLast={i === day.stops.length - 1}
            dimmed={runningLate && !stop.skip_if_rushed && !stop.is_meal && stop.poi_type !== 'accommodation'}
          />
        ))}
      </View>
    </View>
  );
}

const rc = StyleSheet.create({
  // Highlights
  highlightsCard: {
    backgroundColor: C.navy, borderRadius: 16, padding: 18, marginBottom: 12,
  },
  highlightsEye:   { fontSize: 10, fontWeight: '700', color: '#6366F1', letterSpacing: 2, marginBottom: 4 },
  highlightsTitle: { fontSize: 15, fontWeight: '800', color: '#fff', marginBottom: 14 },
  highlightsList:  { gap: 10 },
  highlightRow:    { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: '#ffffff0d', borderRadius: 10, padding: 10 },
  hlEmoji:         { fontSize: 22 },
  hlName:          { fontSize: 13, fontWeight: '700', color: '#fff', marginBottom: 2 },
  hlWhy:           { fontSize: 11, color: '#94A3B8', lineHeight: 16 },
  hlArrow:         { fontSize: 18, color: '#475569' },

  // Info card
  infoCard:       { backgroundColor: C.card, borderRadius: 14, marginBottom: 10, borderWidth: 1, borderColor: C.border, overflow: 'hidden' },
  infoCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, paddingTop: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: C.border },
  infoCardIcon:   { fontSize: 16 },
  infoCardTitle:  { fontSize: 14, fontWeight: '700', color: C.text },
  infoCardBody:   { padding: 16, gap: 6 },

  bodyText: { fontSize: 13, color: C.sub, lineHeight: 20 },

  btnRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 6 },
  btnPrimary: {
    backgroundColor: C.accent, paddingHorizontal: 14, paddingVertical: 9,
    borderRadius: 8, alignSelf: 'flex-start',
  },
  btnPrimaryText:   { color: '#fff', fontSize: 12, fontWeight: '600' },
  btnSecondary: {
    backgroundColor: C.bg, paddingHorizontal: 14, paddingVertical: 9,
    borderRadius: 8, alignSelf: 'flex-start', borderWidth: 1, borderColor: C.border,
  },
  btnSecondaryText: { color: C.text, fontSize: 12, fontWeight: '600' },

  hotelRow:   { flexDirection: 'row', alignItems: 'center', paddingVertical: 6, borderTopWidth: 1, borderTopColor: C.bg },
  hotelName:  { fontSize: 13, fontWeight: '600', color: C.text },
  hotelTier:  { fontSize: 11, color: C.muted, textTransform: 'capitalize' },
  hotelPrice: { fontSize: 14, fontWeight: '700', color: C.accent },

  budgetRow:      { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 5 },
  budgetLabel:    { fontSize: 13, color: C.sub },
  budgetVal:      { fontSize: 13, fontWeight: '600', color: C.text },
  budgetTotalRow: { flexDirection: 'row', justifyContent: 'space-between', paddingTop: 10, borderTopWidth: 1, borderTopColor: C.border, marginTop: 4 },
  budgetTotalLabel:{ fontSize: 14, fontWeight: '700', color: C.text },
  budgetTotalVal: { fontSize: 16, fontWeight: '800', color: C.accent },
  budgetNotes:    { fontSize: 11, color: C.muted, fontStyle: 'italic', marginTop: 4 },

  // Sticky weather bar
  weatherBar: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12,
  },
  weatherBarIcon: { fontSize: 24 },
  weatherBarDesc: { fontSize: 13, fontWeight: '700' },
  weatherBarTemp: { fontSize: 11, fontWeight: '500', marginTop: 1 },

  // Running late button
  lateBtn: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
    borderWidth: 1.5, borderColor: C.border, backgroundColor: C.bg,
  },
  lateBtnOn:      { borderColor: C.amber, backgroundColor: '#FFFBEB' },
  lateBtnText:    { fontSize: 11, fontWeight: '700', color: C.sub },
  lateBtnTextOn:  { color: C.amber },

  // Running late banner
  lateBanner: { backgroundColor: '#FFFBEB', borderRadius: 8, padding: 10, marginBottom: 8 },
  lateBannerText: { fontSize: 12, color: C.amber, fontWeight: '600', textAlign: 'center' },

  // Day tab header
  dayMeta:  { flexDirection: 'row', alignItems: 'center', paddingBottom: 12, marginBottom: 4, borderBottomWidth: 1, borderBottomColor: C.border },
  dayLabel: { fontSize: 18, fontWeight: '800', color: C.text },
  dayCount: { fontSize: 12, color: C.muted, marginTop: 2 },

  // Rain
  rainRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: '#EFF6FF', borderRadius: 10, padding: 12, marginBottom: 4,
  },
  rainLabel:   { fontSize: 13, fontWeight: '600', color: '#1D4ED8', marginBottom: 2 },
  rainPeek:    { fontSize: 11, color: '#3B82F6', fontStyle: 'italic' },
  rainChevron: { fontSize: 10, color: '#93C5FD', marginLeft: 8 },
  rainBody:    { backgroundColor: '#EFF6FF', borderRadius: 10, padding: 12, marginBottom: 8 },
  rainText:    { fontSize: 13, color: '#1E40AF', lineHeight: 20 },
});

// ── Form stylesheet ───────────────────────────────────────────────────────────
const f = StyleSheet.create({
  // Page header
  pageHeader: { paddingHorizontal: 20, paddingTop: 24, paddingBottom: 20 },
  pageTitle:  { fontSize: 28, fontWeight: '900', color: C.text, letterSpacing: -0.5 },
  pageSub:    { fontSize: 13, color: C.sub, lineHeight: 19, marginTop: 4 },

  // Destination search card
  destCard: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: C.card, borderRadius: 16,
    paddingHorizontal: 16, paddingVertical: 14,
    borderWidth: 1.5, borderColor: C.border,
    shadowColor: '#000', shadowOpacity: 0.06, shadowRadius: 10,
    shadowOffset: { width: 0, height: 3 }, elevation: 3,
  },
  destSearchIcon: { fontSize: 18, color: C.muted },
  destInput:      { flex: 1, fontSize: 17, fontWeight: '600', color: C.text, padding: 0 },

  // Form sections container
  formBody: { paddingHorizontal: 20 },

  // Section scaffolding
  section:      { marginBottom: 24 },
  sectionRow:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  sectionLabel: { fontSize: 10, fontWeight: '700', color: C.muted, letterSpacing: 2 },
  sectionHint:  { fontSize: 11, color: C.accent, fontWeight: '600' },

  // Vibe cards
  vibeCard: {
    width: 120, minHeight: 128, borderRadius: 16, padding: 14,
    borderWidth: 1.5, borderColor: 'transparent', justifyContent: 'flex-end',
  },
  vibeCardEmoji:     { fontSize: 36, marginBottom: 10 },
  vibeCardLabel:     { fontSize: 13, fontWeight: '800', color: C.text, marginBottom: 3 },
  vibeCardSub:       { fontSize: 10, color: C.muted, lineHeight: 14 },
  vibeCardCheck: {
    position: 'absolute', top: 10, right: 10,
    width: 22, height: 22, borderRadius: 11,
    alignItems: 'center', justifyContent: 'center',
  },
  vibeCardCheckText: { fontSize: 12, color: '#fff', fontWeight: '800' },

  // Date card
  dateCard:      { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, flexDirection: 'row', overflow: 'hidden' },
  dateHalf:      { flex: 1, paddingVertical: 18, paddingHorizontal: 12, alignItems: 'center' },
  dateSide:      { fontSize: 9, fontWeight: '700', color: C.muted, letterSpacing: 2.5, marginBottom: 6 },
  dateDisplay:   { fontSize: 22, fontWeight: '800', color: C.text, letterSpacing: -0.5 },
  dateYear:      { fontSize: 11, color: C.muted, marginTop: 2, marginBottom: 12 },
  dateStepRow:   { flexDirection: 'row', gap: 8 },
  dateStepper:   { width: 34, height: 34, borderRadius: 17, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center' },
  dateStepArrow: { fontSize: 22, color: C.text, lineHeight: 28 },
  dateDivider:   { width: 1, backgroundColor: C.border, marginVertical: 14 },
  dateMid:       { width: 72, alignItems: 'center', justifyContent: 'center' },
  nightsNum:     { fontSize: 26, fontWeight: '900', color: C.accent, letterSpacing: -1 },
  nightsLbl:     { fontSize: 9, color: C.muted, fontWeight: '700', letterSpacing: 1, marginTop: 2 },

  // Interest chips
  chipGrid:    { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 12, paddingVertical: 9,
    borderRadius: 24, borderWidth: 1.5, borderColor: C.border, backgroundColor: C.card,
  },
  chipOn:      { backgroundColor: C.accentSoft, borderColor: C.accent },
  chipEmoji:   { fontSize: 14 },
  chipLabel:   { fontSize: 13, fontWeight: '600', color: C.sub },
  chipLabelOn: { color: C.accent },

  // Preferences card
  prefCard:      { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, overflow: 'hidden' },
  prefRow:       { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingVertical: 12, gap: 10 },
  prefRowLabel:  { fontSize: 12, fontWeight: '700', color: C.sub, width: 76 },
  prefOptions:   { flexDirection: 'row', gap: 6, flex: 1, flexWrap: 'wrap' },
  prefDivider:   { height: 1, backgroundColor: C.border },
  prefChip: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 7, borderRadius: 8,
    borderWidth: 1.5, borderColor: C.border, backgroundColor: C.bg,
  },
  prefChipOn:      { borderColor: C.accent, backgroundColor: C.accentSoft },
  prefChipEmoji:   { fontSize: 13 },
  prefChipText:    { fontSize: 12, fontWeight: '600', color: C.sub },
  prefChipTextOn:  { color: C.accent },

  // Generate button
  genBtn:     { backgroundColor: C.dark, borderRadius: 16, paddingVertical: 18, alignItems: 'center', marginTop: 8, gap: 5 },
  genBtnMain: { color: '#fff', fontSize: 17, fontWeight: '800', letterSpacing: 0.3 },
  genBtnSub:  { color: '#64748B', fontSize: 12 },
});

// ── Main screen ───────────────────────────────────────────────────────────────
export default function PlanScreen() {
  const today = formatDate(new Date());

  const [destination,    setDestination]    = useState('');
  const [startDate,      setStartDate]      = useState(today);
  const [endDate,        setEndDate]        = useState(addDays(today, 2));
  const [interests,      setInterests]      = useState([]);
  const [pace,           setPace]           = useState('balanced');
  const [travelStyle,    setTravelStyle]    = useState('solo');
  const [driveTol,       setDriveTol]       = useState(2);
  const [selectedVibe,   setSelectedVibe]   = useState(null);
  const [agentSteps,     setAgentSteps]     = useState([]);
  const [streaming,      setStreaming]      = useState(false);
  const [plan,           setPlan]           = useState(null);
  const [showReveal,     setShowReveal]     = useState(false);
  const [activeDay,      setActiveDay]      = useState(0);
  const [error,          setError]          = useState(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [token,          setToken]          = useState(null);

  const xhrRef      = useRef(null);
  const tabListRef  = useRef(null);
  const revealFade  = useRef(new Animated.Value(0)).current;
  const revealSlide = useRef(new Animated.Value(50)).current;

  useEffect(() => {
    if (showReveal) {
      revealFade.setValue(0);
      revealSlide.setValue(50);
      Animated.parallel([
        Animated.timing(revealFade,  { toValue: 1, duration: 500, useNativeDriver: true }),
        Animated.timing(revealSlide, { toValue: 0, duration: 500, useNativeDriver: true }),
      ]).start();
    }
  }, [showReveal]);

  function handleExplore() {
    Animated.timing(revealFade, { toValue: 0, duration: 300, useNativeDriver: true })
      .start(() => setShowReveal(false));
  }

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
          if (p.travel_style)        setTravelStyle(p.travel_style);
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
    if (!interests.length)    { setError('Pick at least one interest or trip vibe.'); return; }

    setStreaming(true); setError(null); setPlan(null); setAgentSteps([]);

    const payload = JSON.stringify({
      destination, start_date: startDate, end_date: endDate,
      interests, travel_style: travelStyle, pace, drive_tolerance_hrs: driveTol,
    });

    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    let cursor = 0, lineBuffer = '';

    xhr.open('POST', `${API_BASE}/v1/itinerary/stream`);
    xhr.setRequestHeader('Content-Type', 'application/json');
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    function processLine(line) {
      if (!line.startsWith('data: ')) return;
      try {
        const event = JSON.parse(line.slice(6));
        if (['start','step','result'].includes(event.type)) {
          setAgentSteps(prev => [...prev, event.message]);
        } else if (event.type === 'complete') {
          setPlan(event.plan); setStreaming(false); setShowReveal(true);
        } else if (event.type === 'error') {
          setError(event.message); setStreaming(false);
        }
      } catch (_) {}
    }

    xhr.onprogress = () => {
      lineBuffer += xhr.responseText.slice(cursor);
      cursor = xhr.responseText.length;
      const lines = lineBuffer.split('\n');
      lineBuffer = lines.pop() ?? '';
      for (const line of lines) processLine(line);
    };
    xhr.onload = () => {
      lineBuffer += xhr.responseText.slice(cursor);
      for (const line of lineBuffer.split('\n')) processLine(line);
      lineBuffer = ''; setStreaming(false);
    };
    xhr.onerror   = () => { setError('Network error — check your connection.'); setStreaming(false); };
    xhr.ontimeout = () => { setError('Request timed out. Please try again.'); setStreaming(false); };
    xhr.timeout   = 120000;
    xhr.send(payload);
  }

  // ── Loading ───────────────────────────────────────────────────────────────
  if (streaming) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: '#080E1A' }}>
        <LoadingScreen steps={agentSteps} destination={destination} />
      </SafeAreaView>
    );
  }

  // ── Results ───────────────────────────────────────────────────────────────
  if (plan) {
    const planNights = nightsBetween(plan.start_date, plan.end_date);
    const planPlaces = plan.days?.reduce((n, d) => n + d.stops.filter(s => !s.is_meal && s.poi_type !== 'accommodation').length, 0) ?? 0;
    const planMeals  = plan.days?.reduce((n, d) => n + d.stops.filter(s => s.is_meal).length, 0) ?? 0;
    const tabs = ['Overview', ...(plan.days?.map((_, i) => `Day ${i + 1}`) ?? [])];

    function goToTab(index) {
      setActiveDay(index);
      tabListRef.current?.scrollToIndex({ index, animated: true });
    }

    function handleHighlightTap(name) {
      const dayIndex = plan.days?.findIndex(d =>
        d.stops.some(s => s.name.toLowerCase().includes(name.toLowerCase()))
      ) ?? -1;
      if (dayIndex >= 0) goToTab(dayIndex + 1);
    }

    async function handleShare() {
      const hlLines = plan.highlights?.map(h => `  ${h.emoji || '⭐'} ${h.name}`).join('\n') ?? '';
      const dayLines = plan.days?.map((d, i) => {
        const names = d.stops
          .filter(s => !s.is_meal && s.poi_type !== 'accommodation')
          .slice(0, 4)
          .map(s => s.name)
          .join(' · ');
        return `  Day ${i + 1}: ${names}`;
      }).join('\n') ?? '';

      const msg = [
        `✈️ ${plan.title}`,
        `📍 ${plan.destination}`,
        `📅 ${displayDate(plan.start_date)} – ${displayDate(plan.end_date)}  (${planNights} nights)`,
        '',
        `🔥 Can't miss:`,
        hlLines,
        '',
        `📋 The plan:`,
        dayLines,
        '',
        `Planned with TourAI 🗺️`,
      ].join('\n');

      try {
        await Share.share({ message: msg, title: plan.title });
      } catch (_) {}
    }

    async function handleSave() {
      if (!token) { Alert.alert('Sign in required', 'Sign in to save trips to your profile.'); return; }
      try {
        const { data: { user } } = await supabase.auth.getUser();
        const { error: dbErr } = await supabase.from('saved_trips').upsert({
          user_id:    user.id,
          title:      plan.title,
          destination:plan.destination,
          start_date: plan.start_date,
          end_date:   plan.end_date,
          plan_json:  plan,
          saved_at:   new Date().toISOString(),
        });
        if (dbErr) throw dbErr;
        Alert.alert('Saved!', 'Your trip is saved to your profile.');
      } catch (e) {
        Alert.alert('Could not save', 'Try again later.');
      }
    }

    return (
      <SafeAreaView style={s.safe}>
        {/* Header */}
        <View style={s.header}>
          <View style={s.headerTopRow}>
            <Pressable onPress={() => { setPlan(null); setActiveDay(0); }}>
              <Text style={s.backBtnText}>← New Trip</Text>
            </Pressable>
            <View style={s.headerActions}>
              <Pressable style={s.headerActionBtn} onPress={handleSave}>
                <Text style={s.headerActionText}>💾 Save</Text>
              </Pressable>
              <Pressable style={[s.headerActionBtn, s.headerActionBtnAccent]} onPress={handleShare}>
                <Text style={s.headerActionTextAccent}>↗ Share</Text>
              </Pressable>
            </View>
          </View>
          <Text style={s.headerTitle} numberOfLines={1}>{plan.title}</Text>
          <Text style={s.headerSub}>{plan.destination}  ·  {displayDate(plan.start_date)} – {displayDate(plan.end_date)}</Text>
        </View>

        {/* Tab bar */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={s.tabBar}
          contentContainerStyle={s.tabBarInner}
        >
          {tabs.map((label, i) => (
            <Pressable key={i} style={[s.tab, activeDay === i && s.tabActive]} onPress={() => goToTab(i)}>
              <Text style={[s.tabText, activeDay === i && s.tabTextActive]}>{label}</Text>
            </Pressable>
          ))}
        </ScrollView>

        {/* Pages */}
        <FlatList
          ref={tabListRef}
          style={{ flex: 1, backgroundColor: C.bg }}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          keyExtractor={(_, i) => String(i)}
          data={tabs}
          initialNumToRender={2}
          getItemLayout={(_, index) => ({ length: SCREEN_W, offset: SCREEN_W * index, index })}
          onMomentumScrollEnd={e => {
            const index = Math.round(e.nativeEvent.contentOffset.x / SCREEN_W);
            setActiveDay(index);
          }}
          renderItem={({ index }) => (
            <ScrollView
              style={{ width: SCREEN_W }}
              contentContainerStyle={s.pageContent}
              showsVerticalScrollIndicator={false}
            >
              {index === 0 ? (
                <>
                  <HighlightsCard highlights={plan.highlights} onHighlightTap={handleHighlightTap} />
                  <GettingThereCard info={plan.getting_there} destination={plan.destination} />
                  <AccommodationCard info={plan.accommodation} />
                  <BudgetCard budget={plan.budget} />
                  <View style={s.liveCTA}>
                    <Text style={s.liveCTATitle}>Ready to explore on foot?</Text>
                    <Text style={s.liveCTASub}>Switch to Live Walk when you arrive — AI audio stories at every stop.</Text>
                    <Pressable style={s.liveCTABtn} onPress={() => router.push('/(tabs)/live-walk')}>
                      <Text style={s.liveCTABtnText}>Start Live Walk →</Text>
                    </Pressable>
                  </View>
                </>
              ) : (
                <DayTabContent day={plan.days[index - 1]} destination={plan.destination} />
              )}
              <View style={{ height: 48 }} />
            </ScrollView>
          )}
        />

        {/* Reveal overlay */}
        {showReveal && (
          <Animated.View style={[s.revealBg, { opacity: revealFade }]}>
            <Animated.View style={[s.revealBox, { transform: [{ translateY: revealSlide }] }]}>
              <Text style={s.revealEye}>YOUR TRIP TO</Text>
              <Text style={s.revealDest}>{plan.destination.toUpperCase()}</Text>
              <Text style={s.revealTitle}>{plan.title}</Text>

              <View style={s.revealDates}>
                <Text style={s.revealDate}>{displayDate(plan.start_date)}</Text>
                <View style={s.revealDateDivider} />
                <Text style={s.revealDate}>{displayDate(plan.end_date)}</Text>
              </View>

              <View style={s.revealStats}>
                {[
                  [planNights, planNights === 1 ? 'night' : 'nights'],
                  [planPlaces, 'places'],
                  [planMeals,  'meals'],
                ].map(([num, lbl], i, arr) => (
                  <React.Fragment key={lbl}>
                    <View style={s.revealStat}>
                      <Text style={s.revealStatNum}>{num}</Text>
                      <Text style={s.revealStatLbl}>{lbl}</Text>
                    </View>
                    {i < arr.length - 1 ? <View style={s.revealStatDiv} /> : null}
                  </React.Fragment>
                ))}
              </View>

              {plan.summary ? (
                <Text style={s.revealSummary} numberOfLines={3}>{plan.summary}</Text>
              ) : null}

              <Pressable style={s.revealCTA} onPress={handleExplore}>
                <Text style={s.revealCTAText}>Explore your trip →</Text>
              </Pressable>
              <Pressable onPress={handleExplore} style={{ marginTop: 18 }}>
                <Text style={s.revealSkip}>Skip</Text>
              </Pressable>
            </Animated.View>
          </Animated.View>
        )}
      </SafeAreaView>
    );
  }

  // ── Form ──────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: C.bg }}>
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: 48 }}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ── Header ── */}
        <View style={f.pageHeader}>
          <Text style={f.pageTitle}>Plan a Trip</Text>
          <Text style={f.pageSub}>AI handles stops, meals, accommodation, commute, and budget.</Text>
        </View>

        {/* ── Destination search card ── */}
        <View style={{ paddingHorizontal: 20, marginBottom: 28 }}>
          <View style={f.destCard}>
            <Text style={f.destSearchIcon}>🔍</Text>
            <TextInput
              style={f.destInput}
              value={destination}
              onChangeText={setDestination}
              placeholder="City, landmark, or region…"
              placeholderTextColor={C.muted}
              returnKeyType="done"
              autoCapitalize="words"
              selectionColor={C.accentMid}
            />
          </View>
        </View>

        {/* ── Form sections ── */}
        <View style={f.formBody}>

          {/* Trip vibe */}
          <View style={f.section}>
            <View style={f.sectionRow}>
              <Text style={f.sectionLabel}>TRIP VIBE</Text>
              <Text style={f.sectionHint}>tap to pre-fill</Text>
            </View>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: 10, paddingBottom: 4 }}
            >
              {TRIP_VIBES.map(v => {
                const cfg = VIBE_COLORS[v.id];
                const on  = selectedVibe === v.id;
                return (
                  <Pressable
                    key={v.id}
                    style={[f.vibeCard, { backgroundColor: cfg.bg }, on && { borderColor: cfg.accent, borderWidth: 2 }]}
                    onPress={() => applyVibe(v)}
                  >
                    <Text style={f.vibeCardEmoji}>{v.emoji}</Text>
                    <Text style={[f.vibeCardLabel, on && { color: cfg.accent }]}>{v.label}</Text>
                    <Text style={f.vibeCardSub}>{cfg.sub}</Text>
                    {on && (
                      <View style={[f.vibeCardCheck, { backgroundColor: cfg.accent }]}>
                        <Text style={f.vibeCardCheckText}>✓</Text>
                      </View>
                    )}
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>

          {/* Dates */}
          <View style={f.section}>
            <Text style={f.sectionLabel}>WHEN?</Text>
            <View style={{ marginTop: 12 }}>
              <View style={f.dateCard}>
                {/* FROM */}
                <View style={f.dateHalf}>
                  <Text style={f.dateSide}>FROM</Text>
                  <Text style={f.dateDisplay}>
                    {new Date(startDate + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  </Text>
                  <Text style={f.dateYear}>{new Date(startDate + 'T12:00:00').getFullYear()}</Text>
                  <View style={f.dateStepRow}>
                    <Pressable style={f.dateStepper} onPress={() => {
                      const p = addDays(startDate, -1); setStartDate(p);
                    }}>
                      <Text style={f.dateStepArrow}>‹</Text>
                    </Pressable>
                    <Pressable style={f.dateStepper} onPress={() => {
                      const n = addDays(startDate, 1); setStartDate(n);
                      if (n > endDate) setEndDate(n);
                    }}>
                      <Text style={f.dateStepArrow}>›</Text>
                    </Pressable>
                  </View>
                </View>

                {/* Nights center */}
                <View style={f.dateDivider} />
                <View style={f.dateMid}>
                  <Text style={f.nightsNum}>{nights}</Text>
                  <Text style={f.nightsLbl}>{nights === 1 ? 'NIGHT' : 'NIGHTS'}</Text>
                </View>
                <View style={f.dateDivider} />

                {/* TO */}
                <View style={f.dateHalf}>
                  <Text style={f.dateSide}>TO</Text>
                  <Text style={f.dateDisplay}>
                    {new Date(endDate + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  </Text>
                  <Text style={f.dateYear}>{new Date(endDate + 'T12:00:00').getFullYear()}</Text>
                  <View style={f.dateStepRow}>
                    <Pressable style={f.dateStepper} onPress={() => {
                      const p = addDays(endDate, -1); if (p >= startDate) setEndDate(p);
                    }}>
                      <Text style={f.dateStepArrow}>‹</Text>
                    </Pressable>
                    <Pressable style={f.dateStepper} onPress={() => setEndDate(addDays(endDate, 1))}>
                      <Text style={f.dateStepArrow}>›</Text>
                    </Pressable>
                  </View>
                </View>
              </View>
            </View>
          </View>

          {/* Interests */}
          <View style={f.section}>
            <View style={f.sectionRow}>
              <Text style={f.sectionLabel}>INTERESTS</Text>
              {profileLoading
                ? <ActivityIndicator size="small" color={C.accent} />
                : <Text style={f.sectionHint}>
                    {interests.length > 0 ? `${interests.length} selected` : 'from your profile'}
                  </Text>
              }
            </View>
            <View style={f.chipGrid}>
              {ALL_INTERESTS.map(item => {
                const on = interests.includes(item.id);
                return (
                  <Pressable key={item.id} style={[f.chip, on && f.chipOn]} onPress={() => toggleInterest(item.id)}>
                    <Text style={f.chipEmoji}>{item.emoji}</Text>
                    <Text style={[f.chipLabel, on && f.chipLabelOn]}>{item.label}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          {/* Preferences */}
          <View style={f.section}>
            <Text style={f.sectionLabel}>PREFERENCES</Text>
            <View style={[f.prefCard, { marginTop: 12 }]}>

              <View style={f.prefRow}>
                <Text style={f.prefRowLabel}>Pace</Text>
                <View style={f.prefOptions}>
                  {PACE_OPTIONS.map(o => (
                    <Pressable key={o.id} style={[f.prefChip, pace === o.id && f.prefChipOn]} onPress={() => setPace(o.id)}>
                      <Text style={f.prefChipEmoji}>{o.emoji}</Text>
                      <Text style={[f.prefChipText, pace === o.id && f.prefChipTextOn]}>{o.label}</Text>
                    </Pressable>
                  ))}
                </View>
              </View>

              <View style={f.prefDivider} />

              <View style={f.prefRow}>
                <Text style={f.prefRowLabel}>Going as</Text>
                <View style={f.prefOptions}>
                  {STYLE_OPTIONS.map(o => (
                    <Pressable key={o.id} style={[f.prefChip, travelStyle === o.id && f.prefChipOn]} onPress={() => setTravelStyle(o.id)}>
                      <Text style={f.prefChipEmoji}>{o.emoji}</Text>
                      <Text style={[f.prefChipText, travelStyle === o.id && f.prefChipTextOn]}>{o.label}</Text>
                    </Pressable>
                  ))}
                </View>
              </View>

              <View style={f.prefDivider} />

              <View style={f.prefRow}>
                <Text style={f.prefRowLabel}>Max drive</Text>
                <View style={f.prefOptions}>
                  {[
                    { val: 0.5, label: '30 min' },
                    { val: 1,   label: '1 hr'   },
                    { val: 2,   label: '2 hrs'  },
                    { val: 4,   label: '4 hrs'  },
                  ].map(({ val, label }) => (
                    <Pressable key={val} style={[f.prefChip, driveTol === val && f.prefChipOn]} onPress={() => setDriveTol(val)}>
                      <Text style={[f.prefChipText, driveTol === val && f.prefChipTextOn]}>{label}</Text>
                    </Pressable>
                  ))}
                </View>
              </View>

            </View>
          </View>

          {error ? <Text style={s.errorText}>{error}</Text> : null}

          <Pressable style={f.genBtn} onPress={handleGenerate}>
            <Text style={f.genBtnMain}>Plan My Trip  ✦</Text>
            <Text style={f.genBtnSub}>AI crafts your full itinerary in ~20 seconds</Text>
          </Pressable>

        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

// ── Main styles ───────────────────────────────────────────────────────────────
const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },

  errorText: { fontSize: 13, color: C.red, textAlign: 'center', marginBottom: 16 },

  // Results header
  header: {
    backgroundColor: C.card, paddingHorizontal: 20, paddingTop: 10, paddingBottom: 12,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  headerTopRow:       { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  backBtnText:        { fontSize: 14, color: C.accent, fontWeight: '600' },
  headerActions:      { flexDirection: 'row', gap: 8 },
  headerActionBtn:    { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: C.border, backgroundColor: C.bg },
  headerActionText:   { fontSize: 12, fontWeight: '600', color: C.sub },
  headerActionBtnAccent: { backgroundColor: C.accent, borderColor: C.accent },
  headerActionTextAccent:{ fontSize: 12, fontWeight: '700', color: '#fff' },
  headerTitle: { fontSize: 21, fontWeight: '900', color: C.text, letterSpacing: -0.3 },
  headerSub:   { fontSize: 12, color: C.sub, marginTop: 3 },

  // Tab bar
  tabBar:      { backgroundColor: C.card, borderBottomWidth: 1, borderBottomColor: C.border, flexGrow: 0, flexShrink: 0 },
  tabBarInner: { paddingHorizontal: 14, paddingVertical: 8, gap: 6 },
  tab:         { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20 },
  tabActive:   { backgroundColor: C.accent },
  tabText:     { fontSize: 13, fontWeight: '600', color: C.sub },
  tabTextActive:{ color: '#fff' },

  // Page content
  pageContent: { padding: 16, paddingBottom: 40 },

  // Live walk CTA
  liveCTA:       { backgroundColor: C.navy, borderRadius: 16, padding: 20, marginTop: 8 },
  liveCTATitle:  { fontSize: 16, fontWeight: '800', color: '#fff', marginBottom: 6 },
  liveCTASub:    { fontSize: 13, color: '#A5B4FC', lineHeight: 19, marginBottom: 16 },
  liveCTABtn:    { backgroundColor: C.accent, borderRadius: 10, paddingVertical: 12, alignItems: 'center' },
  liveCTABtnText:{ color: '#fff', fontSize: 14, fontWeight: '700' },

  // Reveal overlay
  revealBg: {
    position: 'absolute', top: 0, left: 0, right: 0, height: SCREEN_H,
    backgroundColor: '#06090F', justifyContent: 'center', alignItems: 'center', padding: 32,
  },
  revealBox:     { width: '100%', alignItems: 'center' },
  revealEye:     { fontSize: 10, fontWeight: '700', color: C.accentMid, letterSpacing: 3, marginBottom: 12 },
  revealDest:    { fontSize: 40, fontWeight: '900', color: '#fff', letterSpacing: -1, textAlign: 'center', marginBottom: 8 },
  revealTitle:   { fontSize: 15, fontWeight: '400', color: '#94A3B8', textAlign: 'center', marginBottom: 28, lineHeight: 22 },
  revealDates:   { flexDirection: 'row', alignItems: 'center', gap: 16, marginBottom: 28 },
  revealDate:    { fontSize: 14, fontWeight: '600', color: '#CBD5E1' },
  revealDateDivider: { width: 24, height: 1, backgroundColor: '#1E293B' },
  revealStats:   { flexDirection: 'row', alignItems: 'center', backgroundColor: '#0F172A', borderRadius: 16, paddingVertical: 16, paddingHorizontal: 24, marginBottom: 24, width: '100%' },
  revealStat:    { flex: 1, alignItems: 'center' },
  revealStatNum: { fontSize: 26, fontWeight: '800', color: '#fff' },
  revealStatLbl: { fontSize: 11, color: '#475569', fontWeight: '600', marginTop: 2 },
  revealStatDiv: { width: 1, height: 32, backgroundColor: '#1E293B' },
  revealSummary: { fontSize: 13, color: '#64748B', textAlign: 'center', lineHeight: 20, marginBottom: 32 },
  revealCTA:     {
    backgroundColor: C.accent, borderRadius: 14, paddingVertical: 16, paddingHorizontal: 40,
    shadowColor: C.accent, shadowOpacity: 0.35, shadowRadius: 14, shadowOffset: { width: 0, height: 6 },
    elevation: 8,
  },
  revealCTAText: { color: '#fff', fontSize: 16, fontWeight: '800', letterSpacing: 0.3 },
  revealSkip:    { fontSize: 13, color: '#334155', fontWeight: '500' },
});
