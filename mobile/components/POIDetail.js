/**
 * POIDetail — bottom-sheet modal shown when user taps a POI marker.
 *
 * Shows curated, human-readable information extracted from OSM tags.
 * Hides internal OSM fields (osm_id, osm_type, ref:*, *:wikidata, etc.).
 *
 * Props:
 *   poi     — POI object from /v1/visible-pois or null
 *   visible — boolean
 *   onClose — () => void
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as Speech from 'expo-speech';

// ── Type display config ────────────────────────────────────────────────────────

const TYPE_LABELS = {
  tourism:  'Tourism',
  historic: 'Historic',
  amenity:  'Amenity',
  leisure:  'Leisure',
  building: 'Building',
  man_made: 'Landmark',
  natural:  'Nature',
  railway:  'Transit',
  aeroway:  'Airport',
  unknown:  'Point of Interest',
};

const TYPE_COLORS = {
  tourism:  '#4CAF50',
  historic: '#9C27B0',
  amenity:  '#FF9800',
  leisure:  '#00BCD4',
  building: '#607D8B',
  man_made: '#795548',
  natural:  '#8BC34A',
  railway:  '#F44336',
  aeroway:  '#2196F3',
  unknown:  '#9E9E9E',
};

// ── Tag extraction ─────────────────────────────────────────────────────────────

/**
 * Maps raw OSM tag keys to human-readable labels.
 * Keys not in this list are hidden.
 */
const TAG_LABEL = {
  description:       'About',
  opening_hours:     'Hours',
  phone:             'Phone',
  'contact:phone':   'Phone',
  website:           'Website',
  'contact:website': 'Website',
  wikipedia:         'Wikipedia',
  fee:               'Entry',
  cuisine:           'Cuisine',
  operator:          'Operated by',
  architect:         'Architect',
  artist_name:       'Artist',
  artwork_type:      'Artwork type',
  material:          'Material',
  start_date:        'Built',
  height:            'Height',
  heritage:          'Heritage grade',
  memorial:          'Memorial type',
  religion:          'Religion',
  denomination:      'Denomination',
  sport:             'Sport',
  surface:           'Surface',
  access:            'Access',
  wheelchair:        'Wheelchair',
  diet_vegan:        'Vegan options',
  diet_vegetarian:   'Vegetarian options',
};

/** Format address from addr:* tags into a single string */
function buildAddress(tags) {
  const num    = tags['addr:housenumber'];
  const street = tags['addr:street'];
  const city   = tags['addr:city'];
  const state  = tags['addr:state'];
  const parts  = [num && street ? `${num} ${street}` : street, city, state].filter(Boolean);
  return parts.length ? parts.join(', ') : null;
}

/** Decide whether a tag value looks like a URL */
function isUrl(value) {
  return typeof value === 'string' && (value.startsWith('http') || value.startsWith('www.'));
}

/** Extract a Wikipedia article title from a tag like "en:Dallas Holocaust Museum" */
function wikiUrl(value) {
  if (!value) return null;
  const m = String(value).match(/^([a-z]{2}):(.+)$/);
  if (m) return `https://${m[1]}.wikipedia.org/wiki/${encodeURIComponent(m[2])}`;
  return null;
}

/** Format the fee tag nicely */
function formatFee(value) {
  if (!value) return null;
  if (value === 'yes') return 'Paid entry';
  if (value === 'no')  return 'Free entry';
  return String(value);
}

/**
 * Returns an array of { label, value, isLink } objects for display.
 * Skips internal/technical OSM fields.
 */
function extractInfo(tags) {
  if (!tags) return [];
  const rows = [];
  const seen = new Set();

  const add = (label, value, isLink = false) => {
    if (!value || seen.has(label)) return;
    seen.add(label);
    rows.push({ label, value: String(value), isLink });
  };

  // Address — combine addr:* fields
  const address = buildAddress(tags);
  if (address) add('Address', address);

  // Curated tag fields
  for (const [key, label] of Object.entries(TAG_LABEL)) {
    const raw = tags[key];
    if (!raw) continue;

    if (key === 'fee') {
      add(label, formatFee(raw));
    } else if (key === 'wikipedia') {
      add(label, 'Open Wikipedia', true, wikiUrl(raw));
    } else if (isUrl(String(raw))) {
      add(label, String(raw), true);
    } else {
      add(label, String(raw));
    }
  }

  // Website fallback
  if (!seen.has('Website') && tags.website) add('Website', tags.website, true);

  return rows;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatDistance(meters) {
  return meters < 1000 ? `${Math.round(meters)}m away` : `${(meters / 1000).toFixed(1)}km away`;
}

function openLink(url) {
  const href = url.startsWith('http') ? url : `https://${url}`;
  Linking.openURL(href).catch(() => {});
}

function openDirections(lat, lon, name) {
  const label = encodeURIComponent(name);
  const url = Platform.OS === 'ios'
    ? `maps://?daddr=${lat},${lon}&dirflg=w`
    : `geo:${lat},${lon}?q=${lat},${lon}(${label})`;
  Linking.openURL(url).catch(() => {
    // Fallback to Google Maps web
    Linking.openURL(`https://maps.google.com/?daddr=${lat},${lon}&travelmode=walking`);
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

const STORY_STATES  = { IDLE: 0, LOADING: 1, LOADED: 2, ERROR: 3 };
const REPORT_STATES = { IDLE: 0, SENDING: 1, DONE: 2, ERROR: 3 };

export default function POIDetail({ poi, visible, onClose, onFetchStory, onReport }) {
  const [storyState, setStoryState]   = useState(STORY_STATES.IDLE);
  const [story, setStory]             = useState('');
  const [speaking, setSpeaking]       = useState(false);
  const [reportState, setReportState] = useState(REPORT_STATES.IDLE);
  const fetchedForRef                 = useRef(null);
  const reportTimerRef                = useRef(null);

  // Auto-fetch story when sheet opens for a new POI
  useEffect(() => {
    if (!visible || !poi || !onFetchStory) return;
    if (fetchedForRef.current === String(poi.id)) return;
    fetchedForRef.current = String(poi.id);
    setStory('');
    setSpeaking(false);
    setStoryState(STORY_STATES.LOADING);
    onFetchStory(poi)
      .then(text => { setStory(text); setStoryState(STORY_STATES.LOADED); })
      .catch(() => setStoryState(STORY_STATES.ERROR));
  }, [visible, poi?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Stop speech + reset report state when sheet closes
  useEffect(() => {
    if (!visible) {
      Speech.stop();
      setSpeaking(false);
      clearTimeout(reportTimerRef.current);
      setReportState(REPORT_STATES.IDLE);
    }
  }, [visible]);

  const handleReport = async () => {
    if (reportState !== REPORT_STATES.IDLE || !onReport) return;
    setReportState(REPORT_STATES.SENDING);
    try {
      await onReport(poi);
      setReportState(REPORT_STATES.DONE);
      reportTimerRef.current = setTimeout(() => setReportState(REPORT_STATES.IDLE), 3000);
    } catch {
      setReportState(REPORT_STATES.ERROR);
      reportTimerRef.current = setTimeout(() => setReportState(REPORT_STATES.IDLE), 3000);
    }
  };

  const toggleSpeech = () => {
    if (speaking) {
      Speech.stop();
      setSpeaking(false);
    } else {
      Speech.speak(story, {
        rate: 0.92,
        onDone:  () => setSpeaking(false),
        onError: () => setSpeaking(false),
      });
      setSpeaking(true);
    }
  };

  if (!poi) return null;

  const color    = TYPE_COLORS[poi.poi_type] ?? TYPE_COLORS.unknown;
  const label    = TYPE_LABELS[poi.poi_type] ?? TYPE_LABELS.unknown;
  const infoRows = extractInfo(poi.tags);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={e => e.stopPropagation()}>

          {/* Drag handle */}
          <View style={styles.handle} />

          {/* Type pill */}
          <View style={[styles.typePill, { backgroundColor: color }]}>
            <Text style={styles.typePillText}>{label}</Text>
          </View>

          {/* Name */}
          <Text style={styles.name}>{poi.name}</Text>

          {/* Distance */}
          <Text style={styles.distance}>{formatDistance(poi.distance_m)}</Text>

          {/* Story */}
          <View style={styles.storyBox}>
            {storyState === STORY_STATES.LOADING && (
              <View style={styles.storyLoading}>
                <ActivityIndicator size="small" color="#2563EB" />
                <Text style={styles.storyLoadingText}>Generating story…</Text>
              </View>
            )}
            {storyState === STORY_STATES.LOADED && (
              <View>
                <Text style={styles.storyText}>{story}</Text>
                <Pressable style={styles.speakBtn} onPress={toggleSpeech}>
                  <Text style={styles.speakBtnText}>
                    {speaking ? '⏹ Stop' : '▶ Listen'}
                  </Text>
                </Pressable>
              </View>
            )}
          </View>

          {/* Info rows */}
          {infoRows.length > 0 ? (
            <ScrollView style={styles.scroll} showsVerticalScrollIndicator={false}>
              {infoRows.map(({ label: rowLabel, value, isLink }) => (
                <View key={rowLabel} style={styles.infoRow}>
                  <Text style={styles.infoLabel}>{rowLabel}</Text>
                  {isLink ? (
                    <Pressable onPress={() => openLink(value)} style={styles.linkContainer}>
                      <Text style={styles.infoLink} numberOfLines={1}>{value}</Text>
                    </Pressable>
                  ) : (
                    <Text style={styles.infoValue} numberOfLines={3}>{value}</Text>
                  )}
                </View>
              ))}
            </ScrollView>
          ) : (
            <Text style={styles.noInfo}>No additional details available.</Text>
          )}

          {/* Action buttons */}
          <View style={styles.actionRow}>
            <Pressable
              style={styles.directionsBtn}
              onPress={() => openDirections(poi.lat, poi.lon, poi.name)}>
              <Text style={styles.directionsBtnText}>Get Directions</Text>
            </Pressable>
            <Pressable style={styles.closeButton} onPress={onClose}>
              <Text style={styles.closeText}>Close</Text>
            </Pressable>
          </View>

          {/* False-positive report button */}
          <Pressable
            style={[
              styles.reportBtn,
              reportState === REPORT_STATES.DONE  && styles.reportBtnDone,
              reportState === REPORT_STATES.ERROR && styles.reportBtnError,
            ]}
            onPress={handleReport}
            disabled={reportState !== REPORT_STATES.IDLE}>
            <Text style={styles.reportBtnText}>
              {reportState === REPORT_STATES.IDLE    && "Can't see this? Report"}
              {reportState === REPORT_STATES.SENDING && 'Sending…'}
              {reportState === REPORT_STATES.DONE    && 'Reported ✓'}
              {reportState === REPORT_STATES.ERROR   && 'Failed — tap to retry'}
            </Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

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
    paddingHorizontal: 24,
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
    marginBottom: 18,
  },
  typePill: {
    alignSelf: 'flex-start',
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginBottom: 10,
  },
  typePillText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  name: {
    fontSize: 21,
    fontWeight: '700',
    color: '#1A1A1A',
    marginBottom: 4,
    lineHeight: 26,
  },
  distance: {
    fontSize: 14,
    color: '#888',
    marginBottom: 18,
  },
  storyBox: {
    backgroundColor: '#F0F4FF',
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 14,
    marginBottom: 16,
    minHeight: 52,
    justifyContent: 'center',
    borderLeftWidth: 3,
    borderLeftColor: '#2563EB',
  },
  storyLoading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  storyLoadingText: {
    fontSize: 13,
    color: '#2563EB',
    fontStyle: 'italic',
  },
  storyText: {
    fontSize: 14,
    color: '#1E293B',
    lineHeight: 22,
    fontStyle: 'italic',
    marginBottom: 10,
  },
  speakBtn: {
    alignSelf: 'flex-start',
    backgroundColor: '#2563EB',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 7,
  },
  speakBtnText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '700',
  },
  scroll: {
    maxHeight: 180,
    marginBottom: 16,
  },
  infoRow: {
    flexDirection: 'row',
    paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#EFEFEF',
    alignItems: 'flex-start',
  },
  infoLabel: {
    width: 110,
    fontSize: 13,
    color: '#999',
    fontWeight: '500',
    paddingTop: 1,
  },
  infoValue: {
    flex: 1,
    fontSize: 13,
    color: '#222',
    lineHeight: 18,
  },
  linkContainer: {
    flex: 1,
  },
  infoLink: {
    flex: 1,
    fontSize: 13,
    color: '#1A73E8',
    textDecorationLine: 'underline',
    lineHeight: 18,
  },
  noInfo: {
    fontSize: 14,
    color: '#AAA',
    fontStyle: 'italic',
    marginBottom: 16,
  },
  actionRow: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 4,
  },
  directionsBtn: {
    flex: 1,
    backgroundColor: '#0F172A',
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
  },
  directionsBtnText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: 0.2,
  },
  closeButton: {
    flex: 1,
    backgroundColor: '#F1F5F9',
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
  },
  closeText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0F172A',
  },
  reportBtn: {
    marginTop: 10,
    paddingVertical: 11,
    borderRadius: 12,
    backgroundColor: '#FEF2F2',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#FECACA',
  },
  reportBtnDone: {
    backgroundColor: '#F0FDF4',
    borderColor: '#BBF7D0',
  },
  reportBtnError: {
    backgroundColor: '#FFF7ED',
    borderColor: '#FED7AA',
  },
  reportBtnText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#DC2626',
  },
});
