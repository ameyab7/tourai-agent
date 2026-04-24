import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

export default function HomeScreen() {
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.center}>
        <Text style={styles.wordmark}>TourAI</Text>
        <Text style={styles.tagline}>Discover · Plan · Explore</Text>
        <Text style={styles.coming}>Home screen — coming in Phase 4</Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  center: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 32,
  },
  wordmark: {
    fontSize: 36,
    fontWeight: '800',
    color: '#0F172A',
    letterSpacing: -1,
  },
  tagline: {
    fontSize: 15,
    color: '#64748B',
    fontWeight: '500',
  },
  coming: {
    fontSize: 12,
    color: '#CBD5E1',
    marginTop: 32,
  },
});
