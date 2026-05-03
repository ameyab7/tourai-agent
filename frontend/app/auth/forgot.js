import { router } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Platform,
  Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { supabase } from '../../lib/supabase';

export default function ForgotScreen() {
  const [email,   setEmail]   = useState('');
  const [loading, setLoading] = useState(false);
  const [sent,    setSent]    = useState(false);
  const [error,   setError]   = useState(null);

  const handleReset = async () => {
    if (!email) return;
    setLoading(true);
    setError(null);
    const { error: err } = await supabase.auth.resetPasswordForEmail(email);
    setLoading(false);
    if (err) { setError(err.message); } else { setSent(true); }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={styles.inner}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>

        {sent ? (
          <View style={styles.sentBox}>
            <Text style={styles.checkmark}>✓</Text>
            <Text style={styles.title}>Check your email</Text>
            <Text style={styles.sub}>
              We sent a password reset link to {email}
            </Text>
            <Pressable style={styles.btn} onPress={() => router.back()}>
              <Text style={styles.btnText}>Back to Sign In</Text>
            </Pressable>
          </View>
        ) : (
          <>
            <View style={styles.header}>
              <Text style={styles.title}>Forgot password?</Text>
              <Text style={styles.sub}>We'll send a reset link to your email</Text>
            </View>
            <View style={styles.form}>
              <TextInput
                style={styles.input}
                placeholder="Email"
                placeholderTextColor="#94A3B8"
                value={email}
                onChangeText={setEmail}
                autoCapitalize="none"
                keyboardType="email-address"
                autoComplete="email"
              />
              {error && <Text style={styles.error}>{error}</Text>}
              <Pressable
                style={[styles.btn, !email && styles.btnDisabled]}
                onPress={handleReset}
                disabled={loading || !email}>
                {loading
                  ? <ActivityIndicator color="#FFFFFF" />
                  : <Text style={styles.btnText}>Send Reset Link</Text>}
              </Pressable>
            </View>
            <View style={styles.footer}>
              <Pressable onPress={() => router.back()}>
                <Text style={styles.link}>Back to <Text style={styles.linkBold}>Sign In</Text></Text>
              </Pressable>
            </View>
          </>
        )}

      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:  { flex: 1, backgroundColor: '#FFFFFF' },
  inner: { flex: 1, paddingHorizontal: 28, justifyContent: 'center' },
  header: { marginBottom: 40 },
  sentBox: { alignItems: 'center', gap: 14 },
  checkmark: { fontSize: 52, color: '#2563EB' },
  title: {
    fontSize: 32, fontWeight: '800', color: '#0F172A',
    letterSpacing: -0.5, marginBottom: 6, textAlign: 'left',
  },
  sub: { fontSize: 16, color: '#64748B', lineHeight: 24 },
  form: { gap: 14 },
  input: {
    backgroundColor: '#F8FAFC', borderRadius: 14, borderWidth: 1.5,
    borderColor: '#E2E8F0', paddingVertical: 16, paddingHorizontal: 16,
    fontSize: 15, color: '#0F172A',
  },
  error: { fontSize: 13, color: '#DC2626', lineHeight: 19 },
  btn: {
    backgroundColor: '#0F172A', borderRadius: 14,
    paddingVertical: 17, alignItems: 'center', marginTop: 4,
  },
  btnDisabled: { opacity: 0.4 },
  btnText: { color: '#FFFFFF', fontSize: 16, fontWeight: '700' },
  footer: { alignItems: 'center', marginTop: 40 },
  link: { fontSize: 14, color: '#64748B' },
  linkBold: { color: '#2563EB', fontWeight: '600' },
});
