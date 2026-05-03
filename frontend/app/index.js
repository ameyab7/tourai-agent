import AsyncStorage from '@react-native-async-storage/async-storage';
import { Redirect } from 'expo-router';
import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { supabase } from '../lib/supabase';

export default function RootIndex() {
  const [dest, setDest] = useState(null);

  useEffect(() => {
    async function resolve() {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) { setDest('/auth'); return; }

      const done = await AsyncStorage.getItem('onboarding_complete');
      setDest(done === 'true' ? '/(tabs)' : '/onboarding');
    }

    resolve();

    // Keep listening so sign-in/sign-out on auth screens redirects automatically
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (_event, session) => {
        if (!session) {
          setDest('/auth');
        } else {
          const done = await AsyncStorage.getItem('onboarding_complete');
          setDest(done === 'true' ? '/(tabs)' : '/onboarding');
        }
      }
    );
    return () => subscription.unsubscribe();
  }, []);

  if (!dest) return <View style={{ flex: 1, backgroundColor: '#FFFFFF' }} />;
  return <Redirect href={dest} />;
}
