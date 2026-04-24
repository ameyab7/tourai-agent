import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import OnboardingShell from '../../components/OnboardingShell';

const INTERESTS = [
  { id: 'photography',  label: 'Photography',   emoji: '📷' },
  { id: 'food',         label: 'Food & Dining',  emoji: '🍜' },
  { id: 'history',      label: 'History',        emoji: '🏛️' },
  { id: 'hiking',       label: 'Hiking',         emoji: '🥾' },
  { id: 'cars',         label: 'Cars',           emoji: '🚗' },
  { id: 'stargazing',   label: 'Stargazing',     emoji: '⭐' },
  { id: 'architecture', label: 'Architecture',   emoji: '🏗️' },
  { id: 'nature',       label: 'Nature',         emoji: '🌿' },
  { id: 'art',          label: 'Art',            emoji: '🎨' },
  { id: 'music',        label: 'Music',          emoji: '🎵' },
];

export default function InterestsScreen() {
  const [selected, setSelected] = useState(new Set());

  const toggle = (id) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleContinue = async () => {
    await AsyncStorage.setItem('ob_interests', JSON.stringify([...selected]));
    router.push('/onboarding/style');
  };

  return (
    <OnboardingShell
      step={1}
      title="What do you love to explore?"
      subtitle="Pick as many as you like. This shapes every recommendation."
      canContinue={selected.size > 0}
      onContinue={handleContinue}>

      <View style={styles.grid}>
        {INTERESTS.map(item => {
          const active = selected.has(item.id);
          return (
            <Pressable
              key={item.id}
              style={[styles.card, active && styles.cardActive]}
              onPress={() => toggle(item.id)}>
              <Text style={styles.emoji}>{item.emoji}</Text>
              <Text style={[styles.label, active && styles.labelActive]}>
                {item.label}
              </Text>
              {active && <View style={styles.checkDot} />}
            </Pressable>
          );
        })}
      </View>
    </OnboardingShell>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  card: {
    width: '47%',
    backgroundColor: '#F8FAFC',
    borderRadius: 16,
    borderWidth: 2,
    borderColor: '#E2E8F0',
    paddingVertical: 18,
    paddingHorizontal: 14,
    alignItems: 'flex-start',
    position: 'relative',
  },
  cardActive: {
    backgroundColor: '#EFF6FF',
    borderColor: '#2563EB',
  },
  emoji: {
    fontSize: 28,
    marginBottom: 8,
  },
  label: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0F172A',
  },
  labelActive: {
    color: '#2563EB',
  },
  checkDot: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#2563EB',
  },
});
