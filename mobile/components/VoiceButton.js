import React, { useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

const STATES = {
  IDLE: 'idle',
  INPUT: 'input',
  PROCESSING: 'processing',
  ANSWER: 'answer',
  ERROR: 'error',
};

/**
 * VoiceButton — opens a text-input modal to ask questions about surroundings.
 * Shows the answer from /v1/ask in a popup.
 *
 * Props:
 *   onAsk — async (question: string) => string
 */
export default function VoiceButton({ onAsk }) {
  const [uiState, setUiState] = useState(STATES.IDLE);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [errorMsg, setErrorMsg] = useState('');

  const handlePress = () => {
    setQuestion('');
    setAnswer('');
    setErrorMsg('');
    setUiState(STATES.INPUT);
  };

  const handleSubmit = async () => {
    const q = question.trim();
    if (!q) return;
    setUiState(STATES.PROCESSING);
    try {
      const result = await onAsk(q);
      setAnswer(result);
      setUiState(STATES.ANSWER);
    } catch (err) {
      setErrorMsg(err?.message ?? 'Could not get an answer.');
      setUiState(STATES.ERROR);
    }
  };

  const handleDismiss = () => {
    setUiState(STATES.IDLE);
    setQuestion('');
    setAnswer('');
    setErrorMsg('');
  };

  const isProcessing = uiState === STATES.PROCESSING;
  const showInput = uiState === STATES.INPUT || isProcessing;
  const showResult = uiState === STATES.ANSWER || uiState === STATES.ERROR;

  return (
    <>
      {/* Ask modal */}
      <Modal visible={showInput} animationType="slide" transparent onRequestClose={handleDismiss}>
        <Pressable style={styles.backdrop} onPress={handleDismiss}>
          <Pressable style={styles.sheet} onPress={e => e.stopPropagation()}>
            <Text style={styles.sheetTitle}>Ask about your surroundings</Text>
            <TextInput
              style={styles.input}
              placeholder="e.g. What's that building?"
              placeholderTextColor="#999"
              value={question}
              onChangeText={setQuestion}
              onSubmitEditing={handleSubmit}
              editable={!isProcessing}
              autoFocus
              returnKeyType="send"
            />
            <Pressable
              style={[styles.askButton, (!question.trim() || isProcessing) && styles.askButtonDisabled]}
              onPress={handleSubmit}
              disabled={!question.trim() || isProcessing}>
              {isProcessing
                ? <ActivityIndicator color="#FFFFFF" size="small" />
                : <Text style={styles.askButtonText}>Ask</Text>
              }
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Answer / error popup */}
      <Modal visible={showResult} animationType="fade" transparent onRequestClose={handleDismiss}>
        <Pressable style={styles.backdrop} onPress={handleDismiss}>
          <Pressable style={styles.sheet} onPress={e => e.stopPropagation()}>
            <Text style={styles.questionLabel}>"{question}"</Text>
            {uiState === STATES.ANSWER
              ? <Text style={styles.answerText}>{answer}</Text>
              : <Text style={styles.errorText}>⚠️ {errorMsg}</Text>
            }
            <Pressable style={styles.askButton} onPress={handleDismiss}>
              <Text style={styles.askButtonText}>Got it</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Floating button */}
      <View style={styles.container}>
        <Pressable onPress={handlePress} style={styles.button}>
          <Text style={styles.buttonIcon}>💬</Text>
        </Pressable>
        <Text style={styles.label}>What's that building?</Text>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    paddingBottom: 8,
  },
  button: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: '#1A73E8',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 4,
    shadowColor: '#1A73E8',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
    elevation: 6,
  },
  buttonIcon: { fontSize: 26 },
  label: { fontSize: 12, color: '#666', fontWeight: '500' },
  // Modal
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'flex-end',
    paddingHorizontal: 16,
    paddingBottom: 32,
  },
  sheet: {
    backgroundColor: '#FFFFFF',
    borderRadius: 20,
    padding: 24,
  },
  sheetTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1A1A1A',
    marginBottom: 14,
  },
  input: {
    borderWidth: 1,
    borderColor: '#E0E0E0',
    borderRadius: 10,
    padding: 12,
    fontSize: 15,
    color: '#1A1A1A',
    marginBottom: 14,
  },
  askButton: {
    backgroundColor: '#1A73E8',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
  },
  askButtonDisabled: {
    backgroundColor: '#B0BEC5',
  },
  askButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
  questionLabel: {
    fontSize: 14,
    color: '#888',
    fontStyle: 'italic',
    marginBottom: 12,
  },
  answerText: {
    fontSize: 16,
    color: '#1A1A1A',
    lineHeight: 24,
    marginBottom: 20,
  },
  errorText: {
    fontSize: 15,
    color: '#C62828',
    marginBottom: 20,
  },
});
