// DEV ONLY — SimUserMarker.js
//
// Shows the simulated user's position on the map as an orange dot
// with a heading arrow, similar to Google Maps navigation mode.

import React from 'react';
import { StyleSheet, View } from 'react-native';

const SIZE   = 22;
const ARROW  = 10;

export default function SimUserMarker({ heading = 0 }) {
  return (
    <View style={styles.wrapper}>
      {/* Heading arrow — rotated triangle above the dot */}
      <View style={[styles.arrowWrapper, { transform: [{ rotate: `${heading}deg` }] }]}>
        <View style={styles.arrow} />
      </View>

      {/* Position dot */}
      <View style={styles.dot}>
        <View style={styles.dotInner} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    alignItems: 'center',
    justifyContent: 'center',
    width: SIZE + 16,
    height: SIZE + 16,
  },
  // Arrow points up (north) by default; rotated by heading prop
  arrowWrapper: {
    position: 'absolute',
    top: 0,
    alignItems: 'center',
    width: SIZE + 16,
    height: SIZE + 16,
  },
  arrow: {
    width: 0,
    height: 0,
    borderLeftWidth: ARROW / 2,
    borderRightWidth: ARROW / 2,
    borderBottomWidth: ARROW,
    borderLeftColor: 'transparent',
    borderRightColor: 'transparent',
    borderBottomColor: '#F97316', // orange
    marginTop: 0,
  },
  dot: {
    width: SIZE,
    height: SIZE,
    borderRadius: SIZE / 2,
    backgroundColor: '#F97316',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#F97316',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.6,
    shadowRadius: 6,
    elevation: 5,
    borderWidth: 2.5,
    borderColor: '#FFFFFF',
  },
  dotInner: {
    width: SIZE * 0.4,
    height: SIZE * 0.4,
    borderRadius: SIZE * 0.2,
    backgroundColor: '#FFFFFF',
  },
});
