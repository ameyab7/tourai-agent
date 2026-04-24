import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import * as Location from 'expo-location';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, Animated, Dimensions, FlatList,
  Modal, Pressable, RefreshControl, ScrollView,
  StyleSheet, Text, TouchableWithoutFeedback, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { supabase } from '../../lib/supabase';

const API_BASE    = 'https://tourai-agent-production.up.railway.app';
const { height: SCREEN_H } = Dimensions.get('window');
const MOOD_KEY    = 'home_mood';
const MOOD_DATE_KEY = 'home_mood_date';

const MOODS = [
  { id: 'adventurous', label: 'Adventurous', emoji: '🧗' },
  { id: 'relaxed',     label: 'Relaxed',     emoji: '☕' },
  { id: 'spontaneous', label: 'Spontaneous', emoji: '✨' },
  { id: 'social',      label: 'Social',      emoji: '🎉' },
  { id: 'photography', label: 'Photography', emoji: '📷' },
];

const CATEGORY_COLORS = {
  park:           '#16A34A',
  museum:         '#7C3AED',
  restaurant:     '#EA580C',
  cafe:           '#92400E',
  viewpoint:      '#0284C7',
  historic:       '#B45309',
  nature_reserve: '#15803D',
  art_gallery:    '#DB2777',
  pub:            '#CA8A04',
  marketplace:    '#DC2626',
  beach:          '#0891B2',
  default:        '#475569',
};

const CATEGORY_EMOJI = {
  park:           '🌳',
  museum:         '🏛️',
  restaurant:     '🍽️',
  cafe:           '☕',
  viewpoint:      '🏔️',
  historic:       '🏰',
  nature_reserve: '🌿',
  art_gallery:    '🎨',
  pub:            '🍺',
  marketplace:    '🛒',
  beach:          '🏖️',
  default:        '📍',
};

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Mood bottom sheet
// ---------------------------------------------------------------------------
function MoodSheet({ visible, onSelect }) {
  const slideAnim = useRef(new Animated.Value(SCREEN_H)).current;

  useEffect(() => {
    Animated.spring(slideAnim, {
      toValue:         visible ? 0 : SCREEN_H,
      useNativeDriver: true,
      bounciness:      4,
    }).start();
  }, [visible]);

  return (
    <Modal visible={visible} transparent animationType="none" statusBarTranslucent>
      <TouchableWithoutFeedback>
        <View style={sheet.overlay}>
          <Animated.View style={[sheet.container, { transform: [{ translateY: slideAnim }] }]}>
            <View style={sheet.handle} />
            <Text style={sheet.title}>How are you feeling today?</Text>
            <Text style={sheet.sub}>We'll tailor recommendations to your mood</Text>
            <View style={sheet.grid}>
              {MOODS.map(m => (
                <Pressable key={m.id} style={sheet.moodBtn} onPress={() => onSelect(m.id)}>
                  <Text style={sheet.moodEmoji}>{m.emoji}</Text>
                  <Text style={sheet.moodLabel}>{m.label}</Text>
                </Pressable>
              ))}
            </View>
          </Animated.View>
        </View>
      </TouchableWithoutFeedback>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Recommendation card
// ---------------------------------------------------------------------------
function RecommendationCard({ card }) {
  const color = CATEGORY_COLORS[card.poi_type] ?? CATEGORY_COLORS.default;
  const emoji = CATEGORY_EMOJI[card.poi_type]  ?? CATEGORY_EMOJI.default;
  const distLabel = card.distance_km < 1
    ? `${Math.round(card.distance_km * 1000)}m away`
    : `${card.distance_km.toFixed(1)}km away`;

  const light = card.conditions?.light_window;
  const badge = card.conditions?.light_active
    ? `${light} now`
    : light && card.conditions?.light_mins_away <= 60
      ? `${light} in ${card.conditions.light_mins_away}m`
      : card.conditions?.weather ?? null;

  return (
    <View style={[rcard.card, { borderTopColor: color }]}>
      <View style={[rcard.iconBox, { backgroundColor: color + '18' }]}>
        <Text style={rcard.icon}>{emoji}</Text>
      </View>
      <Text style={rcard.name} numberOfLines={1}>{card.name}</Text>
      <Text style={rcard.reason} numberOfLines={2}>{card.reason}</Text>
      <View style={rcard.footer}>
        <Text style={rcard.dist}>{distLabel}</Text>
        {badge && (
          <View style={[rcard.badge, { backgroundColor: color + '18' }]}>
            <Text style={[rcard.badgeText, { color }]}>{badge}</Text>
          </View>
        )}
      </View>
      <Pressable
        style={[rcard.walkBtn, { backgroundColor: color }]}
        onPress={() => router.push('/(tabs)/live-walk')}
      >
        <Text style={rcard.walkBtnText}>Walk here now</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Home screen
// ---------------------------------------------------------------------------
export default function HomeScreen() {
  const [mood,         setMood]         = useState(null);
  const [showSheet,    setShowSheet]    = useState(false);
  const [cards,        setCards]        = useState([]);
  const [loading,      setLoading]      = useState(false);
  const [refreshing,   setRefreshing]   = useState(false);
  const [error,        setError]        = useState(null);
  const [conditions,   setConditions]   = useState(null);
  const [userName,     setUserName]     = useState('');
  const [location,     setLocation]     = useState(null);

  // Load mood, user, and GPS in parallel on mount — then auto-fetch
  useEffect(() => {
    (async () => {
      const [savedMood, savedDate, { data: { user } }, locResult] = await Promise.all([
        AsyncStorage.getItem(MOOD_KEY),
        AsyncStorage.getItem(MOOD_DATE_KEY),
        supabase.auth.getUser(),
        Location.requestForegroundPermissionsAsync(),
      ]);

      if (user?.user_metadata?.full_name) {
        setUserName(user.user_metadata.full_name.split(' ')[0]);
      } else if (user?.email) {
        setUserName(user.email.split('@')[0]);
      }

      let loc = null;
      if (locResult.status === 'granted') {
        const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
        loc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        setLocation(loc);
      }

      if (savedMood && savedDate === todayStr()) {
        setMood(savedMood);
        // Mood already known — fetch immediately without waiting for mood state to propagate
        fetchRecommendations(false, savedMood, loc);
      } else {
        setShowSheet(true);
      }
    })();
  }, []);

  // Fetch after mood is selected from the sheet
  useEffect(() => {
    if (mood && location !== undefined) fetchRecommendations(false, mood, location);
  }, [mood]);

  const fetchRecommendations = useCallback(async (isRefresh = false, currentMood = mood, currentLoc = location) => {
    if (!currentMood) return;
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) { setError('Not signed in'); return; }

      const lat = currentLoc?.lat ?? 37.7749;
      const lon = currentLoc?.lon ?? -122.4194;

      const res = await fetch(`${API_BASE}/v1/recommendations`, {
        method:  'POST',
        headers: {
          'Content-Type':  'application/json',
          'Authorization': `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ lat, lon, mood: currentMood, radius_km: 5, limit: 15 }),
      });

      if (!res.ok) {
        const body = await res.text();
        console.error('[Recommendations] HTTP', res.status, body);
        throw new Error(`Server error ${res.status}`);
      }
      const data = await res.json();
      setCards(data.cards);
      setConditions(data.conditions);
    } catch (err) {
      console.error('[Recommendations] error:', err.message);
      setError('Could not load recommendations. Pull down to retry.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [mood]);

  const handleMoodSelect = async (selectedMood) => {
    setShowSheet(false);
    await AsyncStorage.setItem(MOOD_KEY, selectedMood);
    await AsyncStorage.setItem(MOOD_DATE_KEY, todayStr());
    setMood(selectedMood);
    fetchRecommendations(false, selectedMood, location);
  };

  const currentMood = MOODS.find(m => m.id === mood);

  return (
    <SafeAreaView style={styles.safe}>
      <MoodSheet visible={showSheet} onSelect={handleMoodSelect} />

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => fetchRecommendations(true)} />
        }
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.greeting}>
              {userName ? `Hey, ${userName}` : 'Good to see you'}
            </Text>
            <Text style={styles.subGreeting}>What do you want to discover?</Text>
          </View>
          {currentMood && (
            <Pressable style={styles.moodChip} onPress={() => setShowSheet(true)}>
              <Text style={styles.moodChipText}>{currentMood.emoji} {currentMood.label}</Text>
            </Pressable>
          )}
        </View>

        {/* Conditions strip */}
        {conditions && (
          <View style={styles.conditionsStrip}>
            <Text style={styles.conditionItem}>🌡 {conditions.temperature_c}°C</Text>
            <Text style={styles.conditionItem}>☁️ {conditions.weather}</Text>
            {conditions.light_window && (
              <Text style={styles.conditionItem}>
                ✨ {conditions.light_active
                  ? `${conditions.light_window} now`
                  : conditions.light_mins_away <= 90
                    ? `${conditions.light_window} in ${conditions.light_mins_away}m`
                    : conditions.light_window}
              </Text>
            )}
          </View>
        )}

        {/* Cards */}
        <Text style={styles.sectionTitle}>Recommended for you</Text>

        {loading && (
          <View style={styles.center}>
            <ActivityIndicator size="large" color="#0F172A" />
          </View>
        )}

        {error && !loading && (
          <View style={styles.center}>
            <Text style={styles.errorText}>{error}</Text>
          </View>
        )}

        {!loading && !error && cards.length > 0 && (
          <FlatList
            data={cards}
            keyExtractor={c => c.id}
            renderItem={({ item }) => <RecommendationCard card={item} />}
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.cardList}
            snapToInterval={220}
            decelerationRate="fast"
          />
        )}

        {!loading && !error && cards.length === 0 && mood && (
          <View style={styles.center}>
            <Text style={styles.emptyText}>No places found nearby.{'\n'}Try increasing the radius.</Text>
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: '#FFFFFF' },
  scroll: { paddingBottom: 32 },
  header: {
    flexDirection:  'row',
    justifyContent: 'space-between',
    alignItems:     'flex-start',
    paddingHorizontal: 20,
    paddingTop:     16,
    paddingBottom:  12,
  },
  greeting:    { fontSize: 24, fontWeight: '800', color: '#0F172A', letterSpacing: -0.5 },
  subGreeting: { fontSize: 14, color: '#64748B', marginTop: 2 },
  moodChip: {
    backgroundColor: '#F1F5F9',
    borderRadius:    20,
    paddingVertical:  6,
    paddingHorizontal: 12,
  },
  moodChipText: { fontSize: 13, fontWeight: '600', color: '#0F172A' },
  conditionsStrip: {
    flexDirection:  'row',
    gap:            12,
    paddingHorizontal: 20,
    paddingVertical:   10,
    backgroundColor:   '#F8FAFC',
    marginHorizontal:  20,
    borderRadius:      12,
    marginBottom:      16,
  },
  conditionItem: { fontSize: 12, color: '#475569', fontWeight: '500' },
  sectionTitle: {
    fontSize: 17, fontWeight: '700', color: '#0F172A',
    paddingHorizontal: 20, marginBottom: 12,
  },
  cardList:  { paddingHorizontal: 20, gap: 12 },
  center:    { alignItems: 'center', paddingVertical: 48, paddingHorizontal: 32 },
  errorText: { fontSize: 14, color: '#64748B', textAlign: 'center', lineHeight: 22 },
  emptyText: { fontSize: 14, color: '#94A3B8', textAlign: 'center', lineHeight: 22 },
});

const sheet = StyleSheet.create({
  overlay: {
    flex:            1,
    justifyContent:  'flex-end',
    backgroundColor: 'rgba(0,0,0,0.35)',
  },
  container: {
    backgroundColor:    '#FFFFFF',
    borderTopLeftRadius:  24,
    borderTopRightRadius: 24,
    paddingHorizontal:    24,
    paddingBottom:        40,
    paddingTop:           12,
  },
  handle: {
    width: 40, height: 4,
    backgroundColor: '#E2E8F0',
    borderRadius:    2,
    alignSelf:       'center',
    marginBottom:    20,
  },
  title: { fontSize: 22, fontWeight: '800', color: '#0F172A', marginBottom: 6 },
  sub:   { fontSize: 14, color: '#64748B', marginBottom: 24 },
  grid:  { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  moodBtn: {
    width:           '30%',
    flexGrow:        1,
    backgroundColor: '#F8FAFC',
    borderRadius:    16,
    paddingVertical: 16,
    alignItems:      'center',
    gap:             6,
    borderWidth:     1.5,
    borderColor:     '#E2E8F0',
  },
  moodEmoji: { fontSize: 28 },
  moodLabel: { fontSize: 12, fontWeight: '600', color: '#334155' },
});

const rcard = StyleSheet.create({
  card: {
    width:           208,
    backgroundColor: '#FFFFFF',
    borderRadius:    16,
    padding:         16,
    borderWidth:     1.5,
    borderColor:     '#E2E8F0',
    borderTopWidth:  3,
    gap:             6,
  },
  iconBox: {
    width: 44, height: 44,
    borderRadius: 12,
    alignItems:   'center',
    justifyContent: 'center',
    marginBottom: 4,
  },
  icon:   { fontSize: 22 },
  name:   { fontSize: 15, fontWeight: '700', color: '#0F172A' },
  reason: { fontSize: 12, color: '#64748B', lineHeight: 18 },
  footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 },
  dist:       { fontSize: 11, color: '#94A3B8', fontWeight: '500' },
  badge:      { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2 },
  badgeText:  { fontSize: 10, fontWeight: '700' },
  walkBtn: {
    marginTop:     10,
    borderRadius:  10,
    paddingVertical: 10,
    alignItems:    'center',
  },
  walkBtnText: { color: '#FFFFFF', fontSize: 13, fontWeight: '700' },
});
