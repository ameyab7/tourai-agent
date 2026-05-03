import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import OnboardingShell from '../../components/OnboardingShell';

const OPTIONS = [
  { hrs: 0, label: 'Stick close to home',  emoji: '🏠', sub: 'Local spots only' },
  { hrs: 2, label: 'Up to 2 hours',        emoji: '🚗', sub: 'Easy day trips' },
  { hrs: 4, label: 'Half-day road trip',   emoji: '🛣️', sub: 'Worth the drive' },
  { hrs: 6, label: "I'll drive anywhere",  emoji: '🗺️', sub: 'No limit' },
];

export default function DriveScreen() {
  const [selected, setSelected] = useState(null);

  const handleContinue = async () => {
    await AsyncStorage.setItem('ob_drive_hrs', String(OPTIONS[selected].hrs));
    router.push('/onboarding/done');
  };

  return (
    <OnboardingShell
      step={4}
      title="How far would you drive for a great weekend?"
      canContinue={selected !== null}
      onContinue={handleContinue}
      continueLabel="Build my profile →">

      <View style={styles.list}>
        {OPTIONS.map((opt, i) => {
          const active = selected === i;
          return (
            <Pressable
              key={i}
              style={[styles.card, active && styles.cardActive]}
              onPress={() => setSelected(i)}>
              <Text style={styles.emoji}>{opt.emoji}</Text>
              <View style={styles.textBlock}>
                <Text style={[styles.label, active && styles.labelActive]}>{opt.label}</Text>
                <Text style={styles.sub}>{opt.sub}</Text>
              </View>
              <View style={[styles.radio, active && styles.radioActive]}>
                {active && <View style={styles.radioDot} />}
              </View>
            </Pressable>
          );
        })}
      </View>
    </OnboardingShell>
  );
}

const styles = StyleSheet.create({
  list: {
    gap: 12,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F8FAFC',
    borderRadius: 16,
    borderWidth: 2,
    borderColor: '#E2E8F0',
    paddingVertical: 18,
    paddingHorizontal: 16,
    gap: 14,
  },
  cardActive: {
    backgroundColor: '#EFF6FF',
    borderColor: '#2563EB',
  },
  emoji: {
    fontSize: 26,
  },
  textBlock: {
    flex: 1,
  },
  label: {
    fontSize: 15,
    fontWeight: '700',
    color: '#0F172A',
    marginBottom: 2,
  },
  labelActive: {
    color: '#2563EB',
  },
  sub: {
    fontSize: 13,
    color: '#64748B',
  },
  radio: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: '#CBD5E1',
    alignItems: 'center',
    justifyContent: 'center',
  },
  radioActive: {
    borderColor: '#2563EB',
  },
  radioDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#2563EB',
  },
});
