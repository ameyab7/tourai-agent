import * as SecureStore from 'expo-secure-store';
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL      = 'https://gumgoctccasmejupqjfc.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd1bWdvY3RjY2FzbWVqdXBxamZjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY5OTI5MzcsImV4cCI6MjA5MjU2ODkzN30.rm0n_uXnSrzlsQy4GRQH2OBofT2QA6qn7FtMBCLydSU';

// SecureStore keys must be ≤2048 bytes; Supabase tokens are ~1KB so we chunk large values.
const SecureStoreAdapter = {
  getItem: async (key) => {
    const count = await SecureStore.getItemAsync(`${key}_chunks`);
    if (count !== null) {
      const chunks = await Promise.all(
        Array.from({ length: parseInt(count) }, (_, i) =>
          SecureStore.getItemAsync(`${key}_chunk_${i}`)
        )
      );
      return chunks.join('');
    }
    return SecureStore.getItemAsync(key);
  },
  setItem: async (key, value) => {
    if (value.length > 2048) {
      const size    = 2048;
      const chunks  = Math.ceil(value.length / size);
      await SecureStore.setItemAsync(`${key}_chunks`, String(chunks));
      await Promise.all(
        Array.from({ length: chunks }, (_, i) =>
          SecureStore.setItemAsync(`${key}_chunk_${i}`, value.slice(i * size, (i + 1) * size))
        )
      );
    } else {
      await SecureStore.setItemAsync(key, value);
    }
  },
  removeItem: async (key) => {
    const count = await SecureStore.getItemAsync(`${key}_chunks`);
    if (count !== null) {
      await Promise.all(
        Array.from({ length: parseInt(count) }, (_, i) =>
          SecureStore.deleteItemAsync(`${key}_chunk_${i}`)
        )
      );
      await SecureStore.deleteItemAsync(`${key}_chunks`);
    } else {
      await SecureStore.deleteItemAsync(key);
    }
  },
};

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    storage:            SecureStoreAdapter,
    autoRefreshToken:   true,
    persistSession:     true,
    detectSessionInUrl: false,
  },
  global: {
    fetch: fetch.bind(globalThis),
  },
});
