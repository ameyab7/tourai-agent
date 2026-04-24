/**
 * OnboardingShell — shared layout for every onboarding screen.
 * Renders a progress bar, title/subtitle, scrollable content, and a Continue button.
 */
import { Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

const DARK = '#0F172A';
const BLUE = '#2563EB';
const TOTAL_STEPS = 4;

export default function OnboardingShell({
  step,           // 1-based current step
  title,
  subtitle,
  canContinue,
  onContinue,
  continueLabel = 'Continue',
  children,
}) {
  return (
    <SafeAreaView style={styles.safe}>
      {/* Progress dots */}
      <View style={styles.dots}>
        {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
          <View
            key={i}
            style={[styles.dot, i < step ? styles.dotActive : styles.dotInactive]}
          />
        ))}
      </View>

      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>{title}</Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>

      {/* Content */}
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}>
        {children}
      </ScrollView>

      {/* Continue button */}
      <View style={styles.footer}>
        <Pressable
          style={[styles.continueBtn, !canContinue && styles.continueBtnDisabled]}
          onPress={onContinue}
          disabled={!canContinue}>
          <Text style={styles.continueBtnText}>{continueLabel}</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  dots: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 6,
    paddingTop: 16,
    paddingBottom: 8,
  },
  dot: {
    width: 28,
    height: 4,
    borderRadius: 2,
  },
  dotActive: {
    backgroundColor: BLUE,
  },
  dotInactive: {
    backgroundColor: '#E2E8F0',
  },
  header: {
    paddingHorizontal: 24,
    paddingTop: 24,
    paddingBottom: 8,
  },
  title: {
    fontSize: 26,
    fontWeight: '800',
    color: DARK,
    letterSpacing: -0.5,
    lineHeight: 32,
  },
  subtitle: {
    fontSize: 15,
    color: '#64748B',
    marginTop: 6,
    lineHeight: 22,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    paddingHorizontal: 24,
    paddingTop: 20,
    paddingBottom: 16,
  },
  footer: {
    paddingHorizontal: 24,
    paddingBottom: Platform.OS === 'ios' ? 12 : 20,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#F1F5F9',
  },
  continueBtn: {
    backgroundColor: DARK,
    borderRadius: 16,
    paddingVertical: 17,
    alignItems: 'center',
  },
  continueBtnDisabled: {
    backgroundColor: '#CBD5E1',
  },
  continueBtnText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
});
