import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import OnboardingShell from '../../components/OnboardingShell';

const OPTIONS = [
  { id: 'relaxed',  label: 'Relaxed',  emoji: '🌿', sub: 'Take it slow, linger longer at each stop' },
  { id: 'balanced', label: 'Balanced', emoji: '⚖️', sub: 'Mix of activity and downtime' },
  { id: 'packed',   label: 'Packed',   emoji: '🚀', sub: 'See as much as possible' },
];

export default function PaceScreen() {
  const [selected, setSelected] = useState(null);

  const handleContinue = async () => {
    await AsyncStorage.setItem('ob_pace', selected);
    router.push('/onboarding/drive');
  };

  return (
    <OnboardingShell
      step={3}
      title="What's your travel pace?"
      canContinue={selected !== null}
      onContinue={handleContinue}>

      <View style={styles.list}>
        {OPTIONS.map(opt => {
          const active = selected === opt.id;
          return (
            <Pressable
              key={opt.id}
              style={[styles.card, active && styles.cardActive]}
              onPress={() => setSelected(opt.id)}>
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
    paddingVertical: 20,
    paddingHorizontal: 16,
    gap: 14,
  },
  cardActive: {
    backgroundColor: '#EFF6FF',
    borderColor: '#2563EB',
  },
  emoji: {
    fontSize: 28,
  },
  textBlock: {
    flex: 1,
  },
  label: {
    fontSize: 16,
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
    lineHeight: 18,
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
