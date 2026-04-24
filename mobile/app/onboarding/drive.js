import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import OnboardingShell from '../../components/OnboardingShell';

const STEPS = [
  { hrs: 0,   label: 'Stay local',  sub: 'Within your city' },
  { hrs: 1,   label: 'Up to 1 hr',  sub: 'Quick day trips' },
  { hrs: 2,   label: 'Up to 2 hrs', sub: 'Weekend range' },
  { hrs: 3,   label: 'Up to 3 hrs', sub: 'Regional explorer' },
  { hrs: 4,   label: 'Up to 4 hrs', sub: 'Long day trip' },
  { hrs: 5,   label: 'Up to 5 hrs', sub: 'Road tripper' },
  { hrs: 6,   label: '6 hrs+',      sub: 'Anywhere the road goes' },
];

export default function DriveScreen() {
  const [selected, setSelected] = useState(2);

  const handleContinue = async () => {
    await AsyncStorage.setItem('ob_drive_hrs', String(STEPS[selected].hrs));
    router.push('/onboarding/done');
  };

  return (
    <OnboardingShell
      step={4}
      title="How far will you drive?"
      subtitle="Sets the radius for trip recommendations."
      canContinue={true}
      onContinue={handleContinue}
      continueLabel="Build my profile →">

      {/* Visual value display */}
      <View style={styles.valueBox}>
        <Text style={styles.valueLabel}>{STEPS[selected].label}</Text>
        <Text style={styles.valueSub}>{STEPS[selected].sub}</Text>
      </View>

      {/* Segmented selector */}
      <View style={styles.segments}>
        {STEPS.map((step, i) => (
          <Pressable
            key={i}
            style={[styles.seg, i === selected && styles.segActive]}
            onPress={() => setSelected(i)}>
            <Text style={[styles.segText, i === selected && styles.segTextActive]}>
              {step.hrs === 0 ? '0' : step.hrs === 6 ? '6+' : step.hrs}
            </Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.segLabels}>
        <Text style={styles.segHint}>hours</Text>
      </View>
    </OnboardingShell>
  );
}

const styles = StyleSheet.create({
  valueBox: {
    alignItems: 'center',
    paddingVertical: 32,
  },
  valueLabel: {
    fontSize: 36,
    fontWeight: '800',
    color: '#0F172A',
    letterSpacing: -1,
  },
  valueSub: {
    fontSize: 15,
    color: '#64748B',
    marginTop: 6,
  },
  segments: {
    flexDirection: 'row',
    backgroundColor: '#F1F5F9',
    borderRadius: 14,
    padding: 4,
    gap: 2,
  },
  seg: {
    flex: 1,
    paddingVertical: 12,
    alignItems: 'center',
    borderRadius: 10,
  },
  segActive: {
    backgroundColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 4,
    elevation: 2,
  },
  segText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#94A3B8',
  },
  segTextActive: {
    color: '#0F172A',
  },
  segLabels: {
    alignItems: 'center',
    marginTop: 8,
  },
  segHint: {
    fontSize: 12,
    color: '#CBD5E1',
  },
});
