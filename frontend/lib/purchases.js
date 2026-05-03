/**
 * lib/purchases.js — RevenueCat wrapper with mock mode.
 *
 * To go live:
 *   1. npm install react-native-purchases
 *   2. Set MOCK_MODE = false
 *   3. Replace REVENUECAT_API_KEY with your real key from revenuecat.com
 *   4. Run `npx expo prebuild` to link the native module
 *   5. Revert `active = true` back to `active = val === 'true'`
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const MOCK_MODE = true;

const MOCK_PREMIUM_KEY = 'mock_is_premium';

export const PRODUCTS = [
  {
    id:          'tourai_monthly',
    title:       'Monthly',
    priceString: '$7.99/mo',
    price:       7.99,
    packageType: 'MONTHLY',
  },
  {
    id:          'tourai_annual',
    title:       'Annual',
    priceString: '$59.99/yr',
    price:       59.99,
    packageType: 'ANNUAL',
    savings:     'Save 37%',
  },
];

async function getMockCustomerInfo() {
  // Temporarily returning premium=true so the gate doesn't block during dev.
  // Change `active` back to `val === 'true'` when ready to test the paywall.
  const val    = await AsyncStorage.getItem(MOCK_PREMIUM_KEY);
  const active = true; // val === 'true'
  return {
    entitlements: {
      active: active ? { premium: { isActive: true } } : {},
    },
  };
}

export async function configurePurchases() {
  if (!MOCK_MODE) {
    // Swap to live when react-native-purchases is installed
    // const Purchases = require('react-native-purchases').default;
    // Purchases.configure({ apiKey: 'appl_REPLACE_WITH_YOUR_KEY' });
  }
}

export async function isPremium() {
  try {
    if (MOCK_MODE) {
      const info = await getMockCustomerInfo();
      return !!info.entitlements.active?.premium?.isActive;
    }
    // Live: const info = await require('react-native-purchases').default.getCustomerInfo();
    // return !!info.entitlements.active?.premium?.isActive;
    return false;
  } catch {
    return false;
  }
}

export async function purchasePackage(pkg) {
  if (MOCK_MODE) {
    await AsyncStorage.setItem(MOCK_PREMIUM_KEY, 'true');
    return true;
  }
  // Live: const { customerInfo } = await require('react-native-purchases').default.purchasePackage(pkg);
  // return !!customerInfo.entitlements.active?.premium?.isActive;
  return false;
}

export async function restorePurchases() {
  if (MOCK_MODE) {
    const info = await getMockCustomerInfo();
    return !!info.entitlements.active?.premium?.isActive;
  }
  // Live: const info = await require('react-native-purchases').default.restorePurchases();
  // return !!info.entitlements.active?.premium?.isActive;
  return false;
}

export async function _devSetPremium(value) {
  if (MOCK_MODE) await AsyncStorage.setItem(MOCK_PREMIUM_KEY, value ? 'true' : 'false');
}
