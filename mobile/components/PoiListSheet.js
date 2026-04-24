import React from 'react';
import {
  FlatList,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

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

function formatDistance(meters) {
  return meters < 1000
    ? `${Math.round(meters)}m`
    : `${(meters / 1000).toFixed(1)}km`;
}

export default function PoiListSheet({ visible, pois, onClose, onSelectPoi }) {
  const sorted = [...pois].sort((a, b) => a.distance_m - b.distance_m);

  const handleSelect = (poi) => {
    onClose();
    // Small delay so the sheet close animation doesn't fight with POIDetail opening
    setTimeout(() => onSelectPoi(poi), 250);
  };

  const renderItem = ({ item, index }) => {
    const color = TYPE_COLORS[item.poi_type] ?? TYPE_COLORS.unknown;
    const label = TYPE_LABELS[item.poi_type] ?? TYPE_LABELS.unknown;

    return (
      <Pressable style={styles.item} onPress={() => handleSelect(item)}>
        <View style={styles.rank}>
          <Text style={styles.rankText}>{index + 1}</Text>
        </View>
        <View style={[styles.colorBar, { backgroundColor: color }]} />
        <View style={styles.textBlock}>
          <Text style={styles.itemName} numberOfLines={1}>{item.name}</Text>
          <Text style={styles.itemType}>{label}</Text>
        </View>
        <View style={styles.itemRight}>
          <Text style={styles.distance}>{formatDistance(item.distance_m)}</Text>
          <Text style={styles.chevron}>›</Text>
        </View>
      </Pressable>
    );
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={e => e.stopPropagation()}>
          <View style={styles.handle} />

          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>
              Nearby{'  '}
              <Text style={styles.count}>{sorted.length}</Text>
            </Text>
            <Pressable onPress={onClose} hitSlop={8}>
              <Text style={styles.closeBtn}>Close</Text>
            </Pressable>
          </View>

          {sorted.length === 0 ? (
            <Text style={styles.empty}>No points of interest visible from here.</Text>
          ) : (
            <FlatList
              data={sorted}
              keyExtractor={item => String(item.id)}
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
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: Platform.OS === 'ios' ? 40 : 28,
    maxHeight: '70%',
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
    marginBottom: 12,
  },
  sheetTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#0F172A',
  },
  count: {
    color: '#2563EB',
  },
  closeBtn: {
    fontSize: 15,
    fontWeight: '600',
    color: '#2563EB',
  },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 13,
    gap: 10,
  },
  rank: {
    width: 22,
    alignItems: 'center',
  },
  rankText: {
    fontSize: 12,
    color: '#94A3B8',
    fontWeight: '600',
  },
  colorBar: {
    width: 3,
    height: 36,
    borderRadius: 2,
    flexShrink: 0,
  },
  textBlock: {
    flex: 1,
  },
  itemName: {
    fontSize: 15,
    fontWeight: '600',
    color: '#0F172A',
  },
  itemType: {
    fontSize: 12,
    color: '#64748B',
    marginTop: 2,
  },
  itemRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingLeft: 8,
  },
  distance: {
    fontSize: 13,
    color: '#94A3B8',
    fontWeight: '500',
  },
  chevron: {
    fontSize: 20,
    color: '#CBD5E1',
    lineHeight: 22,
  },
  separator: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: '#E2E8F0',
    marginLeft: 32,
  },
  empty: {
    fontSize: 14,
    color: '#94A3B8',
    textAlign: 'center',
    marginTop: 40,
    fontStyle: 'italic',
    lineHeight: 22,
  },
});
