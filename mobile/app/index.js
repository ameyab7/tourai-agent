import AsyncStorage from '@react-native-async-storage/async-storage';
import { Redirect } from 'expo-router';
import { useEffect, useState } from 'react';
import { View } from 'react-native';

export default function RootIndex() {
  const [dest, setDest] = useState(null);

  useEffect(() => {
    AsyncStorage.getItem('onboarding_complete').then(val => {
      setDest(val === 'true' ? '/(tabs)' : '/onboarding');
    });
  }, []);

  if (!dest) return <View style={{ flex: 1, backgroundColor: '#FFFFFF' }} />;
  return <Redirect href={dest} />;
}
