import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import OnboardingShell from '../../components/OnboardingShell';

const OPTIONS = [
  { id: 'solo',   label: 'Solo',   emoji: '🧳', sub: 'Exploring on your own terms' },
  { id: 'couple', label: 'Couple', emoji: '❤️', sub: 'Experiences for two' },
  { id: 'family', label: 'Family', emoji: '👨‍👩‍👧', sub: 'Adventures the whole family enjoys' },
  { id: 'group',  label: 'Group',  emoji: '👥', sub: 'Planning for a crew' },
];

export default function TravelStyleScreen() {
  const [selected, setSelected] = useState(null);

  const handleContinue = async () => {
    await AsyncStorage.setItem('ob_travel_style', selected);
    router.push('/onboarding/pace');
  };

  return (
    <OnboardingShell
      step={2}
      title="How do you usually travel?"
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
    paddingVertical: 16,
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
