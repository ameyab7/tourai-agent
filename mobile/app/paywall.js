import { router } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator, Pressable, ScrollView,
  StyleSheet, Text, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { isPremium, purchasePackage, restorePurchases, PRODUCTS } from '../lib/purchases';

const FEATURES = [
  { emoji: '🗺️',  title: 'Live Walk',          desc: 'GPS-guided audio tour as you explore',     premium: true },
  { emoji: '🎙️',  title: 'AI Storytelling',     desc: 'Rich narratives about every place you pass', premium: true },
  { emoji: '📅',  title: 'Unlimited Itineraries', desc: 'Plan as many trips as you want',           premium: true },
  { emoji: '✨',  title: 'Golden Hour Alerts',   desc: 'Perfect timing for photography spots',      premium: true },
  { emoji: '🏠',  title: 'Home Screen',          desc: 'Personalised daily recommendations',        premium: false },
  { emoji: '🔍',  title: 'Discover',             desc: 'Browse nearby places by mood',              premium: false },
];

export default function PaywallScreen() {
  const [selected,  setSelected]  = useState('tourai_annual');
  const [loading,   setLoading]   = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error,     setError]     = useState(null);

  const monthly = PRODUCTS.find(p => p.packageType === 'MONTHLY');
  const annual  = PRODUCTS.find(p => p.packageType === 'ANNUAL');

  const handlePurchase = async () => {
    setLoading(true);
    setError(null);
    const pkg = PRODUCTS.find(p => p.id === selected);
    try {
      const ok = await purchasePackage(pkg);
      if (ok) router.replace('/(tabs)/live-walk');
      else    setError('Purchase not completed.');
    } catch (err) {
      setError(err.message ?? 'Purchase failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleRestore = async () => {
    setRestoring(true);
    setError(null);
    try {
      const ok = await restorePurchases();
      if (ok) router.replace('/(tabs)/live-walk');
      else    setError('No active subscription found.');
    } catch {
      setError('Could not restore purchases.');
    } finally {
      setRestoring(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>

        {/* Header */}
        <Pressable style={styles.closeBtn} onPress={() => router.back()}>
          <Text style={styles.closeText}>✕</Text>
        </Pressable>

        <Text style={styles.badge}>PREMIUM</Text>
        <Text style={styles.title}>Unlock the full{'\n'}TourAI experience</Text>
        <Text style={styles.sub}>Everything you need to explore like a local</Text>

        {/* Feature list */}
        <View style={styles.features}>
          {FEATURES.map(f => (
            <View key={f.title} style={styles.featureRow}>
              <Text style={styles.featureEmoji}>{f.emoji}</Text>
              <View style={styles.featureText}>
                <Text style={styles.featureTitle}>{f.title}</Text>
                <Text style={styles.featureDesc}>{f.desc}</Text>
              </View>
              <Text style={f.premium ? styles.checkPremium : styles.checkFree}>
                {f.premium ? '★' : '✓'}
              </Text>
            </View>
          ))}
        </View>

        {/* Pricing toggle */}
        <View style={styles.plans}>
          {[annual, monthly].map(p => (
            <Pressable
              key={p.id}
              style={[styles.planCard, selected === p.id && styles.planCardSelected]}
              onPress={() => setSelected(p.id)}
            >
              {p.savings && (
                <View style={styles.savingsBadge}>
                  <Text style={styles.savingsText}>{p.savings}</Text>
                </View>
              )}
              <Text style={[styles.planTitle, selected === p.id && styles.planTitleSelected]}>
                {p.title}
              </Text>
              <Text style={[styles.planPrice, selected === p.id && styles.planPriceSelected]}>
                {p.priceString}
              </Text>
            </Pressable>
          ))}
        </View>

        {error && <Text style={styles.error}>{error}</Text>}

        {/* CTA */}
        <Pressable style={styles.cta} onPress={handlePurchase} disabled={loading}>
          {loading
            ? <ActivityIndicator color="#FFFFFF" />
            : <Text style={styles.ctaText}>Start Free Trial</Text>}
        </Pressable>

        <Text style={styles.legal}>
          7-day free trial, then billed at the selected rate. Cancel anytime.
          Subscription auto-renews unless cancelled 24h before period ends.
        </Text>

        <Pressable onPress={handleRestore} disabled={restoring} style={styles.restoreBtn}>
          {restoring
            ? <ActivityIndicator size="small" color="#94A3B8" />
            : <Text style={styles.restoreText}>Restore purchases</Text>}
        </Pressable>

      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: '#FFFFFF' },
  scroll: { paddingHorizontal: 24, paddingBottom: 40 },

  closeBtn: { alignSelf: 'flex-end', padding: 8, marginTop: 8 },
  closeText: { fontSize: 18, color: '#94A3B8' },

  badge: {
    alignSelf:       'flex-start',
    backgroundColor: '#FEF3C7',
    color:           '#92400E',
    fontSize:        11,
    fontWeight:      '800',
    letterSpacing:   1.2,
    paddingHorizontal: 10,
    paddingVertical:   4,
    borderRadius:    6,
    marginTop:       16,
    marginBottom:    16,
    overflow:        'hidden',
  },
  title: {
    fontSize:    32,
    fontWeight:  '800',
    color:       '#0F172A',
    letterSpacing: -0.5,
    lineHeight:  40,
    marginBottom: 8,
  },
  sub: { fontSize: 15, color: '#64748B', marginBottom: 28 },

  features:     { gap: 16, marginBottom: 28 },
  featureRow:   { flexDirection: 'row', alignItems: 'center', gap: 12 },
  featureEmoji: { fontSize: 22, width: 32, textAlign: 'center' },
  featureText:  { flex: 1 },
  featureTitle: { fontSize: 14, fontWeight: '700', color: '#0F172A' },
  featureDesc:  { fontSize: 12, color: '#64748B', marginTop: 1 },
  checkPremium: { fontSize: 16, color: '#F59E0B', fontWeight: '700' },
  checkFree:    { fontSize: 14, color: '#94A3B8' },

  plans: { flexDirection: 'row', gap: 12, marginBottom: 20 },
  planCard: {
    flex:            1,
    borderWidth:     1.5,
    borderColor:     '#E2E8F0',
    borderRadius:    16,
    padding:         16,
    alignItems:      'center',
    position:        'relative',
  },
  planCardSelected: { borderColor: '#0F172A', backgroundColor: '#F8FAFC' },
  savingsBadge: {
    position:        'absolute',
    top:             -10,
    backgroundColor: '#0F172A',
    borderRadius:    6,
    paddingHorizontal: 8,
    paddingVertical:   3,
  },
  savingsText:       { color: '#FFFFFF', fontSize: 10, fontWeight: '700' },
  planTitle:         { fontSize: 13, fontWeight: '600', color: '#64748B', marginTop: 8 },
  planTitleSelected: { color: '#0F172A' },
  planPrice:         { fontSize: 18, fontWeight: '800', color: '#64748B', marginTop: 4 },
  planPriceSelected: { color: '#0F172A' },

  error: { fontSize: 13, color: '#DC2626', textAlign: 'center', marginBottom: 12 },

  cta: {
    backgroundColor: '#0F172A',
    borderRadius:    16,
    paddingVertical: 18,
    alignItems:      'center',
    marginBottom:    14,
  },
  ctaText: { color: '#FFFFFF', fontSize: 16, fontWeight: '800' },

  legal: {
    fontSize:   11,
    color:      '#94A3B8',
    textAlign:  'center',
    lineHeight: 17,
    marginBottom: 16,
  },
  restoreBtn:  { alignItems: 'center', paddingVertical: 8 },
  restoreText: { fontSize: 13, color: '#64748B' },
});
