import React, { useEffect, useRef } from 'react';
import { Animated, Pressable, StyleSheet, Text, View } from 'react-native';

const TYPE_COLORS = {
  tourism: '#4CAF50', historic: '#9C27B0', amenity: '#FF9800',
  leisure: '#00BCD4', building: '#607D8B', man_made: '#795548',
  natural: '#8BC34A', railway: '#F44336', aeroway: '#2196F3', unknown: '#9E9E9E',
};

export default function NowPlayingCard({ poi, onStop }) {
  const slideY = useRef(new Animated.Value(60)).current;
  const pulse  = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    Animated.spring(slideY, {
      toValue: 0, useNativeDriver: true, tension: 80, friction: 11,
    }).start();

    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1,   duration: 600, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 600, useNativeDriver: true }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, []);

  const color = TYPE_COLORS[poi.poi_type] ?? TYPE_COLORS.unknown;

  return (
    <Animated.View style={[styles.card, { transform: [{ translateY: slideY }] }]}>
      <Animated.View style={[styles.dot, { backgroundColor: color, opacity: pulse }]} />
      <View style={styles.textBlock}>
        <Text style={styles.nowLabel}>Now Playing</Text>
        <Text style={styles.name} numberOfLines={1}>{poi.name}</Text>
      </View>
      <Pressable style={styles.stopBtn} onPress={onStop} hitSlop={8}>
        <Text style={styles.stopIcon}>■</Text>
        <Text style={styles.stopLabel}>Stop</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#0F172A',
    borderRadius: 18,
    marginHorizontal: 12,
    marginBottom: 8,
    paddingVertical: 12,
    paddingHorizontal: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 12,
    elevation: 10,
    gap: 12,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    flexShrink: 0,
  },
  textBlock: {
    flex: 1,
  },
  nowLabel: {
    fontSize: 10,
    fontWeight: '600',
    color: '#64748B',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 2,
  },
  name: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  stopBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1E293B',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 5,
  },
  stopIcon: {
    fontSize: 9,
    color: '#EF4444',
  },
  stopLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: '#F1F5F9',
  },
});
