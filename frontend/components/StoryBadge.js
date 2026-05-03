import React from 'react';
import {StyleSheet, Text, View} from 'react-native';

/**
 * StoryBadge — shows "🎧 N stories nearby" when count > 0.
 * Hidden when count is 0 or not provided.
 */
export default function StoryBadge({count}) {
  if (!count || count <= 0) return null;

  return (
    <View style={styles.badge}>
      <Text style={styles.text}>
        🎧 {count} {count === 1 ? 'story' : 'stories'} nearby
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    backgroundColor: '#1A73E8',
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 7,
    alignSelf: 'flex-start',
    shadowColor: '#000',
    shadowOffset: {width: 0, height: 2},
    shadowOpacity: 0.15,
    shadowRadius: 4,
    elevation: 3,
  },
  text: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
    letterSpacing: 0.2,
  },
});
