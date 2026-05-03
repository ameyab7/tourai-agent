import { Tabs } from 'expo-router';
import { useEffect, useRef } from 'react';
import { Animated, Platform, Pressable, Text } from 'react-native';
import * as Haptics from 'expo-haptics';

function TabIcon({ emoji, focused }) {
  const scale = useRef(new Animated.Value(focused ? 1.15 : 1)).current;

  useEffect(() => {
    Animated.spring(scale, {
      toValue:        focused ? 1.15 : 1,
      useNativeDriver: true,
      speed:           24,
      bounciness:      8,
    }).start();
  }, [focused]);

  return (
    <Animated.Text style={{ fontSize: 18, opacity: focused ? 1 : 0.45, transform: [{ scale }] }}>
      {emoji}
    </Animated.Text>
  );
}

function HapticTabButton(props) {
  return (
    <Pressable
      {...props}
      onPress={e => {
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        props.onPress?.(e);
      }}
    />
  );
}

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarButton: HapticTabButton,
        tabBarStyle: {
          backgroundColor: '#FFFFFF',
          borderTopColor:  '#E2E8F0',
          borderTopWidth:  1,
          paddingTop:      6,
          height: Platform.OS === 'ios' ? 82 : 60,
        },
        tabBarActiveTintColor:   '#4F46E5',
        tabBarInactiveTintColor: '#94A3B8',
        tabBarLabelStyle: {
          fontSize:    11,
          fontWeight:  '600',
          marginBottom: Platform.OS === 'ios' ? 0 : 6,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Home',
          tabBarIcon: ({ focused }) => <TabIcon emoji="🏠" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="plan"
        options={{
          title: 'Plan',
          tabBarIcon: ({ focused }) => <TabIcon emoji="🗺️" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="live-walk"
        options={{
          title: 'Live Walk',
          tabBarIcon: ({ focused }) => <TabIcon emoji="🧭" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ focused }) => <TabIcon emoji="👤" focused={focused} />,
        }}
      />
    </Tabs>
  );
}
