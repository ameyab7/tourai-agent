/**
 * BottomBar — the main persistent bottom card.
 *
 * Shows:
 *   - App wordmark + nearby story count chip
 *   - Current street name (or "Locating…")
 *   - Full-width "Ask" button that opens the question modal
 *
 * Props:
 *   streetName    — string or null
 *   storyCount    — number
 *   onAsk         — async (question: string) => string
 *   historyCount  — number
 *   onHistory     — () => void
 */

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as Speech from 'expo-speech';

const BLUE   = '#2563EB';
const DARK   = '#0F172A';
const MID    = '#64748B';
const SUBTLE = '#F1F5F9';

// ── Ask modal states ───────────────────────────────────────────────────────────

const S = { IDLE: 0, INPUT: 1, LOADING: 2, ANSWER: 3, ERROR: 4 };

// ── Component ──────────────────────────────────────────────────────────────────

export default function BottomBar({ streetName, storyCount, onAsk, historyCount, onHistory }) {
  const [modal, setModal] = useState(S.IDLE);
  const [question, setQuestion]   = useState('');
  const [answer, setAnswer]       = useState('');
  const [errorMsg, setErrorMsg]   = useState('');
  const [speaking, setSpeaking]   = useState(false);

  // Stop any speech when the answer sheet closes
  useEffect(() => {
    if (modal === S.IDLE) { Speech.stop(); setSpeaking(false); }
  }, [modal]);

  const openAsk = () => {
    setQuestion('');
    setAnswer('');
    setErrorMsg('');
    setModal(S.INPUT);
  };

  const submit = async () => {
    const q = question.trim();
    if (!q) return;
    setModal(S.LOADING);
    try {
      const result = await onAsk(q);
      setAnswer(result);
      setModal(S.ANSWER);
      // Auto-play the answer
      setSpeaking(true);
      Speech.speak(result, {
        rate: 0.92,
        onDone:  () => setSpeaking(false),
        onError: () => setSpeaking(false),
      });
    } catch (err) {
      setErrorMsg(err?.message ?? 'Something went wrong.');
      setModal(S.ERROR);
    }
  };

  const dismiss = () => {
    Speech.stop();
    setSpeaking(false);
    setModal(S.IDLE);
    setQuestion('');
    setAnswer('');
    setErrorMsg('');
  };

  const toggleSpeech = () => {
    if (speaking) {
      Speech.stop();
      setSpeaking(false);
    } else {
      Speech.speak(answer, {
        rate: 0.92,
        onDone:  () => setSpeaking(false),
        onError: () => setSpeaking(false),
      });
      setSpeaking(true);
    }
  };

  const isLoading = modal === S.LOADING;
  const showInputSheet  = modal === S.INPUT  || isLoading;
  const showResultSheet = modal === S.ANSWER || modal === S.ERROR;

  return (
    <>
      {/* ── Ask input sheet ─────────────────────────────────────────────────── */}
      <Modal visible={showInputSheet} animationType="slide" transparent onRequestClose={dismiss}>
        <Pressable style={styles.backdrop} onPress={dismiss}>
          <Pressable style={styles.askSheet} onPress={e => e.stopPropagation()}>
            <View style={styles.handle} />
            <Text style={styles.sheetHeading}>Ask about your surroundings</Text>
            <TextInput
              style={styles.textInput}
              placeholder="e.g. What's the history of that building?"
              placeholderTextColor="#94A3B8"
              value={question}
              onChangeText={setQuestion}
              onSubmitEditing={submit}
              editable={!isLoading}
              autoFocus
              returnKeyType="send"
              multiline={false}
            />
            <Pressable
              style={[styles.submitBtn, (!question.trim() || isLoading) && styles.submitBtnDisabled]}
              onPress={submit}
              disabled={!question.trim() || isLoading}>
              {isLoading
                ? <ActivityIndicator color="#FFFFFF" size="small" />
                : <Text style={styles.submitBtnText}>Ask TourAI</Text>}
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

      {/* ── Answer / error sheet ────────────────────────────────────────────── */}
      <Modal visible={showResultSheet} animationType="fade" transparent onRequestClose={dismiss}>
        <Pressable style={styles.backdrop} onPress={dismiss}>
          <Pressable style={styles.askSheet} onPress={e => e.stopPropagation()}>
            <View style={styles.handle} />
            <Text style={styles.questionEcho}>"{question}"</Text>
            {modal === S.ANSWER
              ? <Text style={styles.answerText}>{answer}</Text>
              : <Text style={styles.errorText}>{errorMsg}</Text>}
            <View style={styles.answerActions}>
              {modal === S.ANSWER && (
                <Pressable style={styles.speakBtn} onPress={toggleSpeech}>
                  <Text style={styles.speakBtnText}>
                    {speaking ? '⏹ Stop' : '▶ Play'}
                  </Text>
                </Pressable>
              )}
              <Pressable style={[styles.submitBtn, { flex: 1 }]} onPress={dismiss}>
                <Text style={styles.submitBtnText}>Got it</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {/* ── Persistent bottom card ──────────────────────────────────────────── */}
      <View style={styles.card}>
        <View style={styles.handle} />

        {/* Top row: wordmark + story chip + history button */}
        <View style={styles.topRow}>
          <Text style={styles.wordmark}>TourAI</Text>
          <View style={styles.topRowRight}>
            {storyCount > 0 && (
              <View style={styles.storyChip}>
                <View style={styles.storyDot} />
                <Text style={styles.storyChipText}>
                  {storyCount} {storyCount === 1 ? 'story' : 'stories'} nearby
                </Text>
              </View>
            )}
            {historyCount > 0 && (
              <Pressable style={styles.historyBtn} onPress={onHistory} hitSlop={8}>
                <Text style={styles.historyIcon}>⏱</Text>
                <Text style={styles.historyCount}>{historyCount}</Text>
              </Pressable>
            )}
          </View>
        </View>

        {/* Street name */}
        <Text style={styles.streetName} numberOfLines={1}>
          {streetName ? `On ${streetName}` : 'Locating…'}
        </Text>

        {/* Ask bar */}
        <Pressable style={styles.askBar} onPress={openAsk}>
          <Text style={styles.askBarIcon}>✦</Text>
          <Text style={styles.askBarText}>Ask about your surroundings</Text>
        </Pressable>
      </View>
    </>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  // ── Bottom card ──────────────────────────────────────────────────────────────
  card: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingBottom: Platform.OS === 'ios' ? 34 : 20,
    paddingTop: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -4 },
    shadowOpacity: 0.08,
    shadowRadius: 16,
    elevation: 12,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#CBD5E1',
    alignSelf: 'center',
    marginBottom: 14,
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  topRowRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  historyBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F1F5F9',
    borderRadius: 20,
    paddingHorizontal: 10,
    paddingVertical: 4,
    gap: 4,
  },
  historyIcon: {
    fontSize: 12,
  },
  historyCount: {
    fontSize: 12,
    fontWeight: '700',
    color: DARK,
  },
  wordmark: {
    fontSize: 18,
    fontWeight: '800',
    color: DARK,
    letterSpacing: -0.5,
  },
  storyChip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#EFF6FF',
    borderRadius: 20,
    paddingHorizontal: 10,
    paddingVertical: 4,
    gap: 5,
  },
  storyDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: BLUE,
  },
  storyChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: BLUE,
  },
  streetName: {
    fontSize: 13,
    color: MID,
    marginBottom: 14,
    fontWeight: '500',
  },
  askBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: DARK,
    borderRadius: 16,
    paddingVertical: 15,
    paddingHorizontal: 18,
    gap: 10,
  },
  askBarIcon: {
    fontSize: 16,
    color: '#93C5FD',
  },
  askBarText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#FFFFFF',
    letterSpacing: 0.1,
  },

  // ── Modals ────────────────────────────────────────────────────────────────────
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  askSheet: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 24,
    paddingTop: 8,
    paddingBottom: Platform.OS === 'ios' ? 40 : 28,
  },
  sheetHeading: {
    fontSize: 17,
    fontWeight: '700',
    color: DARK,
    marginBottom: 16,
  },
  textInput: {
    backgroundColor: SUBTLE,
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 15,
    color: DARK,
    marginBottom: 12,
  },
  submitBtn: {
    backgroundColor: DARK,
    borderRadius: 14,
    paddingVertical: 16,
    alignItems: 'center',
  },
  submitBtnDisabled: {
    backgroundColor: '#CBD5E1',
  },
  submitBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
  questionEcho: {
    fontSize: 13,
    color: MID,
    fontStyle: 'italic',
    marginBottom: 14,
    lineHeight: 18,
  },
  answerText: {
    fontSize: 16,
    color: DARK,
    lineHeight: 26,
    marginBottom: 16,
  },
  errorText: {
    fontSize: 15,
    color: '#DC2626',
    lineHeight: 22,
    marginBottom: 16,
  },
  answerActions: {
    flexDirection: 'row',
    gap: 10,
    alignItems: 'center',
  },
  speakBtn: {
    backgroundColor: '#2563EB',
    borderRadius: 14,
    paddingVertical: 16,
    paddingHorizontal: 18,
    alignItems: 'center',
  },
  speakBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
  },
});
