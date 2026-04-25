/**
 * lib/purchases.js — RevenueCat wrapper with mock mode.
 *
 * To go live:
 *   1. npm install react-native-purchases
 *   2. Set MOCK_MODE = false
 *   3. Replace REVENUECAT_API_KEY with your real key from revenuecat.com
 *   4. Run `npx expo prebuild` to link the native module
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const MOCK_MODE          = true;
const REVENUECAT_API_KEY = 'appl_REPLACE_WITH_YOUR_KEY';

// AsyncStorage key used in mock mode to persist premium status across restarts
const MOCK_PREMIUM_KEY = 'mock_is_premium';

// ---------------------------------------------------------------------------
// Mock implementation — mirrors the RevenueCat SDK surface
// ---------------------------------------------------------------------------

const Mock = {
  async configure() {
    // no-op in mock
  },

  async getCustomerInfo() {
    const val = await AsyncStorage.getItem(MOCK_PREMIUM_KEY);
    const active = val === 'true';
    return {
      entitlements: {
        active: active ? { premium: { isActive: true } } : {},
      },
    };
  },

  async purchasePackage(pkg) {
    // Simulate a successful purchase
    await AsyncStorage.setItem(MOCK_PREMIUM_KEY, 'true');
    return await Mock.getCustomerInfo();
  },

  async restorePurchases() {
    return await Mock.getCustomerInfo();
  },

  // Dev helper — toggle premium without going through purchase flow
  async _devSetPremium(value) {
    await AsyncStorage.setItem(MOCK_PREMIUM_KEY, value ? 'true' : 'false');
  },

  PRODUCTS: [
    {
      id:             'tourai_monthly',
      title:          'Monthly',
      priceString:    '$7.99/mo',
      price:          7.99,
      packageType:    'MONTHLY',
    },
    {
      id:             'tourai_annual',
      title:          'Annual',
      priceString:    '$59.99/yr',
      price:          59.99,
      packageType:    'ANNUAL',
      savings:        'Save 37%',
    },
  ],
};

// ---------------------------------------------------------------------------
// Live implementation — thin wrapper around react-native-purchases
// ---------------------------------------------------------------------------

const Live = {
  async configure() {
    const Purchases = require('react-native-purchases').default;
    Purchases.configure({ apiKey: REVENUECAT_API_KEY });
  },

  async getCustomerInfo() {
    const Purchases = require('react-native-purchases').default;
    return Purchases.getCustomerInfo();
  },

  async purchasePackage(pkg) {
    const Purchases = require('react-native-purchases').default;
    const { customerInfo } = await Purchases.purchasePackage(pkg);
    return customerInfo;
  },

  async restorePurchases() {
    const Purchases = require('react-native-purchases').default;
    return Purchases.restorePurchases();
  },

  PRODUCTS: null, // fetched dynamically from RevenueCat dashboard
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

const impl = MOCK_MODE ? Mock : Live;

export async function configurePurchases() {
  await impl.configure();
}

export async function isPremium() {
  try {
    const info = await impl.getCustomerInfo();
    return !!info.entitlements.active?.premium?.isActive;
  } catch {
    return false;
  }
}

export async function purchasePackage(pkg) {
  const info = await impl.purchasePackage(pkg);
  return !!info.entitlements.active?.premium?.isActive;
}

export async function restorePurchases() {
  const info = await impl.restorePurchases();
  return !!info.entitlements.active?.premium?.isActive;
}

export const PRODUCTS = impl.PRODUCTS;

// Dev-only toggle (mock mode only)
export const _devSetPremium = MOCK_MODE ? Mock._devSetPremium : null;
