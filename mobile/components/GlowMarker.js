/**
 * GlowMarker — animated pulsing blue dot for POI markers on the map.
 *
 * Props:
 *   color  — hex string (default: '#1A73E8')
 *   size   — diameter of the inner dot in px (default: 14)
 */

import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, View } from 'react-native';

export default function GlowMarker({ color = '#1A73E8', size = 14 }) {
  const pulse1 = useRef(new Animated.Value(0)).current;
  const pulse2 = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const ring = (anim, delay) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.parallel([
            Animated.timing(anim, {
              toValue: 1,
              duration: 1600,
              useNativeDriver: true,
            }),
          ]),
          Animated.timing(anim, {
            toValue: 0,
            duration: 0,
            useNativeDriver: true,
          }),
        ]),
      );

    const a1 = ring(pulse1, 0);
    const a2 = ring(pulse2, 700);
    a1.start();
    a2.start();
    return () => { a1.stop(); a2.stop(); };
  }, [pulse1, pulse2]);

  const ringStyle = (anim) => ({
    transform: [{ scale: anim.interpolate({ inputRange: [0, 1], outputRange: [1, 2.8] }) }],
    opacity: anim.interpolate({ inputRange: [0, 0.3, 1], outputRange: [0, 0.45, 0] }),
  });

  const dotSize = size;
  const ringContainerSize = dotSize * 2.8;
  const offset = (ringContainerSize - dotSize) / 2;

  return (
    <View style={{ width: ringContainerSize, height: ringContainerSize, alignItems: 'center', justifyContent: 'center' }}>
      {/* Outer pulse rings */}
      <Animated.View
        style={[
          styles.ring,
          {
            width: dotSize,
            height: dotSize,
            borderRadius: dotSize / 2,
            borderColor: color,
            position: 'absolute',
          },
          ringStyle(pulse1),
        ]}
      />
      <Animated.View
        style={[
          styles.ring,
          {
            width: dotSize,
            height: dotSize,
            borderRadius: dotSize / 2,
            borderColor: color,
            position: 'absolute',
          },
          ringStyle(pulse2),
        ]}
      />
      {/* Inner solid dot */}
      <View
        style={{
          width: dotSize,
          height: dotSize,
          borderRadius: dotSize / 2,
          backgroundColor: color,
          borderWidth: 2.5,
          borderColor: '#FFFFFF',
          shadowColor: color,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.6,
          shadowRadius: 4,
          elevation: 4,
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  ring: {
    borderWidth: 2,
  },
});
