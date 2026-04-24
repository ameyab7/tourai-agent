import React, { useState } from 'react';
import {
  FlatList,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as Speech from 'expo-speech';

const TYPE_COLORS = {
  tourism: '#4CAF50', historic: '#9C27B0', amenity: '#FF9800',
  leisure: '#00BCD4', building: '#607D8B', man_made: '#795548',
  natural: '#8BC34A', railway: '#F44336', aeroway: '#2196F3', unknown: '#9E9E9E',
};

const TYPE_LABELS = {
  tourism: 'Tourism', historic: 'Historic', amenity: 'Amenity',
  leisure: 'Leisure', building: 'Building', man_made: 'Landmark',
  natural: 'Nature', railway: 'Transit', aeroway: 'Airport', unknown: 'POI',
};

function timeAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60)    return 'Just now';
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export default function StoryHistorySheet({ visible, history, onClose }) {
  const [playingId, setPlayingId] = useState(null);

  const handleClose = () => {
    Speech.stop();
    setPlayingId(null);
    onClose();
  };

  const togglePlay = (entry) => {
    const id = String(entry.poi.id);
    if (playingId === id) {
      Speech.stop();
      setPlayingId(null);
    } else {
      Speech.stop();
      Speech.speak(entry.story, {
        rate: 0.92,
        onDone:  () => setPlayingId(null),
        onError: () => setPlayingId(null),
      });
      setPlayingId(id);
    }
  };

  const renderItem = ({ item }) => {
    const id       = String(item.poi.id);
    const color    = TYPE_COLORS[item.poi.poi_type] ?? TYPE_COLORS.unknown;
    const label    = TYPE_LABELS[item.poi.poi_type] ?? TYPE_LABELS.unknown;
    const isActive = playingId === id;

    return (
      <View style={styles.item}>
        <View style={styles.itemHeader}>
          <View style={[styles.typePill, { backgroundColor: color }]}>
            <Text style={styles.typePillText}>{label}</Text>
          </View>
          <Text style={styles.timeAgo}>{timeAgo(item.timestamp)}</Text>
        </View>
        <Text style={styles.poiName}>{item.poi.name}</Text>
        <Text style={styles.storyPreview} numberOfLines={3}>{item.story}</Text>
        <Pressable
          style={[styles.playBtn, isActive && styles.playBtnActive]}
          onPress={() => togglePlay(item)}>
          <Text style={[styles.playBtnText, isActive && styles.playBtnTextActive]}>
            {isActive ? '⏹  Stop' : '▶  Replay'}
          </Text>
        </Pressable>
      </View>
    );
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={handleClose}>
      <Pressable style={styles.backdrop} onPress={handleClose}>
        <Pressable style={styles.sheet} onPress={e => e.stopPropagation()}>
          <View style={styles.handle} />

          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>Story History</Text>
            <Pressable onPress={handleClose} hitSlop={8}>
              <Text style={styles.doneBtn}>Done</Text>
            </Pressable>
          </View>

          {history.length === 0 ? (
            <Text style={styles.empty}>
              No stories yet.{'\n'}Tap a glowing dot on the map to hear a story.
            </Text>
          ) : (
            <FlatList
              data={history}
              keyExtractor={item => String(item.poi.id)}
              renderItem={renderItem}
              showsVerticalScrollIndicator={false}
              contentContainerStyle={{ paddingBottom: 20 }}
              ItemSeparatorComponent={() => <View style={styles.separator} />}
            />
          )}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: Platform.OS === 'ios' ? 40 : 28,
    maxHeight: '75%',
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#CBD5E1',
    alignSelf: 'center',
    marginBottom: 14,
  },
  sheetHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sheetTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#0F172A',
  },
  doneBtn: {
    fontSize: 15,
    fontWeight: '600',
    color: '#2563EB',
  },
  item: {
    paddingVertical: 14,
  },
  itemHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  typePill: {
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  typePillText: {
    color: '#FFFFFF',
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  timeAgo: {
    fontSize: 12,
    color: '#94A3B8',
  },
  poiName: {
    fontSize: 15,
    fontWeight: '700',
    color: '#0F172A',
    marginBottom: 5,
  },
  storyPreview: {
    fontSize: 13,
    color: '#475569',
    lineHeight: 19,
    fontStyle: 'italic',
    marginBottom: 10,
  },
  playBtn: {
    alignSelf: 'flex-start',
    backgroundColor: '#F1F5F9',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  playBtnActive: {
    backgroundColor: '#EFF6FF',
  },
  playBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#0F172A',
  },
  playBtnTextActive: {
    color: '#2563EB',
  },
  separator: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: '#E2E8F0',
  },
  empty: {
    fontSize: 14,
    color: '#94A3B8',
    textAlign: 'center',
    marginTop: 48,
    lineHeight: 22,
    fontStyle: 'italic',
  },
});
