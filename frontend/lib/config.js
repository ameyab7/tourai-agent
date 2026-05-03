// Flip DEV_MODE to true when testing locally with Expo Go.
// Your phone must be on the same WiFi as your Mac.
const DEV_MODE = true;

const LOCAL  = 'http://192.168.1.26:8000';
const PROD   = 'https://tourai-agent-production.up.railway.app';

export const API_BASE = DEV_MODE ? LOCAL : PROD;
