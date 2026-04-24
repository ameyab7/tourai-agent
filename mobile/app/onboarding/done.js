import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { supabase } from '../../lib/supabase';

const API_BASE = 'https://tourai-agent-production.up.railway.app';

async function getOrCreateDeviceId() {
  let id = await AsyncStorage.getItem('device_id');
  if (!id) {
    id = 'device_' + Math.random().toString(36).slice(2) + '_' + Date.now();
    await AsyncStorage.setItem('device_id', id);
  }
  return id;
}

export default function DoneScreen() {
  const [profile, setProfile]   = useState(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);

  useEffect(() => {
    (async () => {
      const [interests, travelStyle, pace, driveHrs] = await Promise.all([
        AsyncStorage.getItem('ob_interests').then(v => JSON.parse(v || '[]')),
        AsyncStorage.getItem('ob_travel_style'),
        AsyncStorage.getItem('ob_pace'),
        AsyncStorage.getItem('ob_drive_hrs').then(v => parseFloat(v || '2')),
      ]);
      setProfile({ interests, travelStyle, pace, driveHrs });
    })();
  }, []);

  const handleStart = async () => {
    if (!profile) return;
    setLoading(true);
    setError(null);
    try {
      const [deviceId, { data: { session } }] = await Promise.all([
        getOrCreateDeviceId(),
        supabase.auth.getSession(),
      ]);

      const res = await fetch(`${API_BASE}/v1/profile/setup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(session?.access_token
            ? { 'Authorization': `Bearer ${session.access_token}` }
            : {}),
        },
        body: JSON.stringify({
          device_id:           deviceId,
          interests:           profile.interests,
          travel_style:        profile.travelStyle,
          pace:                profile.pace,
          drive_tolerance_hrs: profile.driveHrs,
        }),
      });

      if (!res.ok) throw new Error(`Server error ${res.status}`);

      await AsyncStorage.setItem('onboarding_complete', 'true');
      router.replace('/(tabs)');
    } catch (err) {
      setError('Could not save your profile. Check your connection and try again.');
      setLoading(false);
    }
  };

  const STYLE_LABELS = { solo: 'Solo', couple: 'Couple', family: 'Family', group: 'Group' };
  const PACE_LABELS  = { relaxed: 'Relaxed', balanced: 'Balanced', packed: 'Packed' };

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.center}>
        <Text style={styles.checkmark}>✓</Text>
        <Text style={styles.title}>Your profile is ready</Text>
        <Text style={styles.sub}>
          TourAI will personalise every recommendation around your interests.
        </Text>

        {profile && (
          <View style={styles.summary}>
            <Row label="Interests" value={profile.interests.map(i => i.charAt(0).toUpperCase() + i.slice(1)).join(', ')} />
            <Row label="Travel style" value={STYLE_LABELS[profile.travelStyle]} />
            <Row label="Pace" value={PACE_LABELS[profile.pace]} />
            <Row label="Max drive" value={profile.driveHrs === 6 ? '6 hrs+' : profile.driveHrs === 0 ? 'Stay local' : `${profile.driveHrs} hrs`} />
          </View>
        )}

        {error && <Text style={styles.error}>{error}</Text>}
      </View>

      <View style={styles.footer}>
        <Pressable style={styles.btn} onPress={handleStart} disabled={loading || !profile}>
          {loading
            ? <ActivityIndicator color="#FFFFFF" />
            : <Text style={styles.btnText}>Start Exploring</Text>}
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

function Row({ label, value }) {
  return (
    <View style={rowStyles.row}>
      <Text style={rowStyles.label}>{label}</Text>
      <Text style={rowStyles.value} numberOfLines={1}>{value}</Text>
    </View>
  );
}

const rowStyles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E2E8F0',
  },
  label: {
    fontSize: 14,
    color: '#64748B',
    fontWeight: '500',
  },
  value: {
    fontSize: 14,
    color: '#0F172A',
    fontWeight: '600',
    flex: 1,
    textAlign: 'right',
    marginLeft: 16,
  },
});

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  center: {
    flex: 1,
    paddingHorizontal: 28,
    paddingTop: 60,
  },
  checkmark: {
    fontSize: 52,
    color: '#2563EB',
    marginBottom: 16,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: '#0F172A',
    letterSpacing: -0.5,
    marginBottom: 10,
  },
  sub: {
    fontSize: 15,
    color: '#64748B',
    lineHeight: 23,
    marginBottom: 36,
  },
  summary: {
    backgroundColor: '#F8FAFC',
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingTop: 4,
  },
  error: {
    marginTop: 16,
    fontSize: 13,
    color: '#DC2626',
    lineHeight: 19,
  },
  footer: {
    paddingHorizontal: 24,
    paddingBottom: 20,
    paddingTop: 12,
  },
  btn: {
    backgroundColor: '#0F172A',
    borderRadius: 16,
    paddingVertical: 17,
    alignItems: 'center',
  },
  btnText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
});
