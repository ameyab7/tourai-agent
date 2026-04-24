import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Platform,
  Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { supabase } from '../../lib/supabase';

WebBrowser.maybeCompleteAuthSession();

export default function SignInScreen() {
  const [email,    setEmail]    = useState('');
  const [password, setPassword] = useState('');
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState(null);

  const handleSignIn = async () => {
    if (!email || !password) return;
    setLoading(true);
    setError(null);
    const { error: err } = await supabase.auth.signInWithPassword({ email, password });
    if (err) {
      setError(err.message);
      setLoading(false);
    }
    // session listener in app/index.js will redirect automatically
  };

  const handleGoogleSignIn = async () => {
    setLoading(true);
    setError(null);
    const { data, error: err } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options:  { redirectTo: 'tourai://auth/callback', skipBrowserRedirect: true },
    });
    if (err || !data?.url) {
      setError(err?.message ?? 'Could not start Google sign-in');
      setLoading(false);
      return;
    }
    const result = await WebBrowser.openAuthSessionAsync(data.url, 'tourai://auth/callback');
    if (result.type === 'success') {
      const [, fragment] = result.url.split('#');
      const params       = new URLSearchParams(fragment ?? '');
      const accessToken  = params.get('access_token');
      const refreshToken = params.get('refresh_token');
      if (accessToken && refreshToken) {
        const { error: sessErr } = await supabase.auth.setSession({ access_token: accessToken, refresh_token: refreshToken });
        if (!sessErr) {
          const done = await AsyncStorage.getItem('onboarding_complete');
          router.replace(done === 'true' ? '/(tabs)' : '/onboarding');
        }
      }
    }
    setLoading(false);
  };

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={styles.inner}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>

        <View style={styles.header}>
          <Text style={styles.title}>Welcome back</Text>
          <Text style={styles.sub}>Sign in to TourAI</Text>
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
          <TextInput
            style={styles.input}
            placeholder="Password"
            placeholderTextColor="#94A3B8"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoComplete="password"
          />
          {error && <Text style={styles.error}>{error}</Text>}
          <Pressable
            style={[styles.btn, (!email || !password) && styles.btnDisabled]}
            onPress={handleSignIn}
            disabled={loading || !email || !password}>
            {loading
              ? <ActivityIndicator color="#FFFFFF" />
              : <Text style={styles.btnText}>Sign In</Text>}
          </Pressable>
        </View>

        <View style={styles.dividerRow}>
          <View style={styles.dividerLine} />
          <Text style={styles.dividerText}>or</Text>
          <View style={styles.dividerLine} />
        </View>

        <Pressable style={styles.oauthBtn} onPress={handleGoogleSignIn} disabled={loading}>
          <Text style={styles.oauthText}>Continue with Google</Text>
        </Pressable>

        <View style={styles.footer}>
          <Pressable onPress={() => router.push('/auth/forgot')}>
            <Text style={styles.link}>Forgot password?</Text>
          </Pressable>
          <Pressable onPress={() => router.push('/auth/signup')}>
            <Text style={styles.link}>Don't have an account? <Text style={styles.linkBold}>Sign Up</Text></Text>
          </Pressable>
        </View>

      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:  { flex: 1, backgroundColor: '#FFFFFF' },
  inner: { flex: 1, paddingHorizontal: 28, justifyContent: 'center' },
  header: { marginBottom: 40 },
  title: {
    fontSize: 32, fontWeight: '800', color: '#0F172A',
    letterSpacing: -0.5, marginBottom: 6,
  },
  sub: { fontSize: 16, color: '#64748B' },
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
  dividerRow: { flexDirection: 'row', alignItems: 'center', marginTop: 24 },
  dividerLine: { flex: 1, height: StyleSheet.hairlineWidth, backgroundColor: '#E2E8F0' },
  dividerText: { marginHorizontal: 12, fontSize: 13, color: '#94A3B8' },
  oauthBtn: {
    borderWidth: 1.5, borderColor: '#E2E8F0', borderRadius: 14,
    paddingVertical: 15, alignItems: 'center', marginTop: 12,
    backgroundColor: '#FFFFFF',
  },
  oauthText: { fontSize: 15, fontWeight: '600', color: '#0F172A' },
  footer: { gap: 16, alignItems: 'center', marginTop: 32 },
  link: { fontSize: 14, color: '#64748B' },
  linkBold: { color: '#2563EB', fontWeight: '600' },
});
