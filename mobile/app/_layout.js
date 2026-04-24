import 'react-native-url-polyfill/auto';
import { Stack } from 'expo-router';
import * as Linking from 'expo-linking';
import { useEffect } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { supabase } from '../lib/supabase';

export default function RootLayout() {
  useEffect(() => {
    // Handle tourai://auth/callback deep links (email confirmation + OAuth)
    const handleUrl = async ({ url }) => {
      if (!url) return;
      const [, fragment] = url.split('#');
      const params       = new URLSearchParams(fragment ?? '');
      const accessToken  = params.get('access_token');
      const refreshToken = params.get('refresh_token');
      if (accessToken && refreshToken) {
        await supabase.auth.setSession({ access_token: accessToken, refresh_token: refreshToken });
      }
    };

    // Cold-start: app opened via deep link
    Linking.getInitialURL().then(url => url && handleUrl({ url }));

    // Warm: app already open, deep link arrives
    const sub = Linking.addEventListener('url', handleUrl);
    return () => sub.remove();
  }, []);

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <Stack screenOptions={{ headerShown: false }} />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
