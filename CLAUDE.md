# CLAUDE.md


---

## Product Roadmap — PRD Integration Plan

The current codebase is the **Live Walk** premium feature inside a larger TourAI app. The PRD describes the full app. Everything built so far maps to one locked screen in the final product.

### Target app structure

```
TourAI App
├── Free Tier
│   ├── Onboarding          — swipe-based interest + travel style capture
│   ├── Home / Discover     — mood check-in + condition-aware recommendations
│   ├── Trip Planner        — itinerary generator, drive splitting, multi-day
│   └── Profile / Settings  — taste profile, preferences, subscription status
└── Premium (Live Walk)     — everything currently built (GPS tour guide)
```

---

### Phase 1 — Navigation Foundation
**Status: ✅ COMPLETE**
**Effort: ~3 days**

The app is a single screen today. Nothing else can be built until navigation exists.

- [x] Add `expo-router` to `mobile/`
- [x] Create tab layout: Home, Live Walk, Plan, Profile
- [x] Move current `App.js` map screen → `mobile/app/(tabs)/live-walk.js`
- [x] Add placeholder screens for each tab so the shell compiles
- [x] Add a top-level `_layout.js` with GestureHandlerRootView + SafeAreaProvider
- [x] Verify Live Walk still works identically after the move

---

### Phase 2 — Onboarding Flow
**Status: ✅ COMPLETE**
**Effort: ~1 week**

Shown once on first launch. Captures explicit interests, travel style, pace, and drive tolerance, then POSTs to the backend profile API.

**Mobile (`mobile/app/onboarding/`):**
- [x] Interest selection screen — 10 category cards in 2-col grid, multi-select (`onboarding/index.js`)
- [x] Travel style screen — Solo / Couple / Family / Group (`onboarding/style.js`)
- [x] Pace preference screen — Relaxed / Balanced / Packed (`onboarding/pace.js`)
- [x] Drive tolerance screen — 4 anchored options: "Stick close to home" / "Up to 2 hours" / "Half-day road trip" / "I'll drive anywhere" (`onboarding/drive.js`)
- [x] Completion screen — profile summary card + "Start Exploring" CTA (`onboarding/done.js`)

**Backend (`api/routes/profile.py`):**
- [x] `POST /v1/profile/setup` — accepts onboarding payload, upserts to `_profiles` dict + `profiles.json`
- [x] `GET /v1/profile/{device_id}` — returns profile or 404
- N/A: "Extend `profile_manager.py`" — that file belonged to the old LangGraph architecture; the FastAPI profile route serves the same purpose directly

---

### Phase 3 — Supabase Auth + Persistent Profiles
**Status: NOT STARTED**
**Effort: ~1 week**

Required before freemium gating. Without auth there is no concept of a user account or subscription.

**Backend:**
- [ ] Add Supabase Python client to `requirements.txt`
- [ ] Create Supabase project, configure `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` env vars
- [ ] Define `profiles` table: `user_id, interests, travel_style, pace, drive_tolerance_hrs, created_at, updated_at`
- [ ] Migrate `profile_manager.py` — replace JSON file reads/writes with Supabase queries
- [ ] Add auth middleware to FastAPI — validate Supabase JWT on protected routes

**Mobile:**
- [ ] Add `@supabase/supabase-js` + `expo-secure-store` (token storage)
- [ ] Auth screens: Sign Up, Log In, Forgot Password (email + Google + Apple)
- [ ] Persist session token securely; attach as `Authorization: Bearer` header on all API calls
- [ ] Show auth gate before Onboarding on first launch; skip if already signed in
- [ ] Email confirmation deep link — add `tourai://auth/callback` to Supabase redirect URLs + handle in-app (currently disabled for dev; required before App Store submission)

---

### Phase 4 — Home / Discover Screen
**Status: ✅ COMPLETE**
**Effort: ~2 weeks**

The main free-tier entry point. Replaces the current "drop straight into map" UX.

**Mood check-in:**
- [ ] Bottom sheet shown at start of every session (or when mood hasn't been set today)
- [ ] 5 moods: Adventurous / Relaxed / Spontaneous / Social / Focused (photography)
- [ ] Mood stored in session state; passed as context to recommendation engine

**Recommendation cards:**
- [ ] Horizontal scroll of personalized POI cards — not a map, not a list of everything nearby
- [ ] Each card: photo (Google Places), name, why-it-matches-you blurb, distance/drive time, conditions badge (golden hour in 2h, clear skies, low crowds)
- [ ] "Plan a trip here" CTA → Trip Planner; "Walk here now" CTA → Live Walk (premium gate)

**Backend (`api/routes/recommendations.py`):**
- [ ] `POST /v1/recommendations` — accepts `{ lat, lon, mood, radius_km, limit }`
- [ ] Pulls nearby POIs, scores by interest match + mood + current conditions (weather, time of day)
- [ ] Returns ranked cards with a `reason` field ("matches your interest in car photography + golden hour in 90 min")

**Condition signals to wire in:**
- [ ] Time of day → golden hour / blue hour window (calculate from lat/lon + sunset time)
- [ ] Weather → current conditions from existing `utils/weather.py`
- [ ] Crowd proxy → time-of-week heuristic (weekend morning vs. Saturday afternoon)

---

### Phase 5 — Premium Gate on Live Walk
**Status: ✅ COMPLETE (mock RevenueCat — swap key when ready)**
**Effort: ~3 days (after Phase 3)**

> ⚠️ **Pre-production checklist — do this before App Store submission:**
> 1. Create RevenueCat account at revenuecat.com, add iOS app (`com.tourai.app`)
> 2. Define products in App Store Connect: `tourai_monthly` ($7.99) and `tourai_annual` ($59.99)
> 3. In `mobile/lib/purchases.js`: set `MOCK_MODE = false` and replace `REVENUECAT_API_KEY`
> 4. Run `npm install react-native-purchases` + `npx expo prebuild` to link native module
> 5. In `mobile/lib/purchases.js`: revert `active = true` back to `active = val === 'true'`
> 6. Add Apple Sign In (required by App Store if offering other social login — see Known Issues #2)
> 7. Test full purchase flow on a physical device (simulator cannot process payments)

- [ ] Add RevenueCat SDK to mobile for subscription management (iOS + Android in-app purchase)
- [ ] Define products: Monthly $7.99 / Annual $59.99
- [ ] Paywall screen: feature comparison, "Start Free Trial" CTA
- [ ] Gate the Live Walk tab — non-premium users see paywall instead of map
- [ ] Free tier gets 1 preview walk (15 minutes, then soft paywall prompt)
- [ ] Backend: add `is_premium` check on `/v1/story` endpoint (premium-only narrative depth)

---

### Phase 6 — Trip Itinerary Generator
**Status: NOT STARTED**
**Effort: ~3 weeks**

The largest new surface in the PRD. A separate planning mode for multi-day trips.

**Mobile (`mobile/app/(tabs)/plan.js`):**
- [x] Destination text input
- [x] Date range stepper (start + end, no native module needed)
- [x] Nights / days count label
- [x] "Plan My Trip" button → calls `/v1/itinerary`
- [x] Loading state with helpful hint text
- [x] Generated itinerary view: collapsible day cards with timeline stops
- [ ] Each stop: photo header (Phase 7), booking link, save / export itinerary

**Backend (`api/routes/itinerary.py`):**
- [x] `POST /v1/itinerary` — accepts `{ destination, start_date, end_date, interests, travel_style, pace, drive_tolerance_hrs }`
- [x] Pull user profile for interests, pace, drive tolerance (via optional JWT)
- [x] Fetch POIs from Overpass for destination area (5 km radius, 3 mirrors)
- [x] Groq LLM (llama-3.3-70b) generates narrative itinerary with timing, drive splits, insider tips
- [x] Drive splitting: flags legs exceeding tolerance with overnight-stop note in tip
- [x] Returns structured JSON: `{ title, summary, days: [{ date, day_label, stops }] }`

**Google Places / Geocoding:**
- [x] Add `GOOGLE_PLACES_API_KEY` env var to `api/config.py`
- [x] `utils/google_places.py` — Nominatim geocoding (free) + Google Places photo URL helper
- [ ] Photo enrichment on stop cards (Phase 7)

---

### Phase 7 — Map Enhancements
**Status: NOT STARTED**
**Effort: ~1 week**

- [ ] Golden hour overlay on Live Walk map — colour-shift the map tint when within 30 min of golden hour (photography mood only)
- [ ] Itinerary route overlay — show the planned drive route as a polyline when navigating an itinerary stop
- [ ] POI card photos — swap the current text-only POIDetail sheet for one with a Google Places photo header
- [ ] Crowd / best-time badge on POI cards ("Best before 9 AM on weekends")

---

### Phase 8 — Astronomy / Moon Phase Signal
**Status: NOT STARTED**
**Effort: ~3 days**

- [ ] Integrate Astronomy API (or Open-Meteo's astronomy endpoint) for moon phase + Milky Way visibility window
- [ ] Add `utils/astronomy.py` — returns `{ moon_phase, moon_illumination, milky_way_visible, best_viewing_window }`
- [ ] Wire into recommendation engine: boost stargazing / astrophotography POIs when conditions are good
- [ ] Surface on Home screen cards: "New moon this Friday — ideal for Milky Way at Enchanted Rock"

---

### Phase 9 — Web Platform
**Status: NOT STARTED**
**Effort: ~3 weeks**

- [ ] Bootstrap `web/` with Next.js 14 (App Router)
- [ ] Shared Supabase auth (same JWT, same backend)
- [ ] Trip Planner page — full itinerary builder optimised for desktop
- [ ] SEO destination pages — static-generated pages for top destinations (Dallas, Austin, Big Bend, etc.)
- [ ] Embed interactive map using `react-map-gl` + Mapbox
- [ ] Responsive design: mobile web gets simplified view; desktop gets side-by-side map + itinerary

---

### Phase 10 — Freemium Polish + Analytics
**Status: NOT STARTED**
**Effort: ~1 week**

- [ ] Enforce free tier limits: 3 saved itineraries/month, no Live Walk, standard POI cards only
- [ ] Upgrade prompts at the right friction points (not random — only when hitting a limit)
- [ ] Add PostHog (or Amplitude) for event tracking: session start, itinerary created, paywall seen, upgrade, walk started
- [ ] D7/D30 retention dashboard
- [ ] A/B test paywall copy and pricing

---

---

### Known Issues / Pre-launch Fixes

| # | Issue | Context | Fix before launch |
|---|-------|---------|-------------------|
| 1 | Email confirmation disabled in Supabase | Free tier shared SMTP is rate-limited to 2 emails/hour — confirmation emails don't reliably arrive during dev/testing. Disabled for now. | Re-enable "Confirm email" in Supabase → Authentication → Providers → Email, and configure a custom SMTP provider (e.g. Resend, SendGrid) before App Store submission |
| 2 | Apple Sign In not implemented | No Apple Developer account ($99/year) at time of build | Required by App Store rules if any other social login is offered — implement before submission using `expo-apple-authentication` |
| 3 | `tourai://auth/callback` email confirmation deep link untested | Could not test end-to-end due to issue #1 | Test after fixing SMTP — confirm email link opens app and signs user in |

---

### Current implementation status summary

| Phase | Feature | Status |
|-------|---------|--------|
| — | Live Walk (GPS tour, POI visibility, storytelling, TTS) | ✅ Complete |
| — | Visibility ground truth tooling (`eval_visibility.py`, `rejected_pois`) | ✅ Complete |
| — | Now Playing card, Story History sheet, POI List sheet | ✅ Complete |
| 1 | Navigation shell (Expo Router) | ✅ Complete |
| 2 | Onboarding flow | ✅ Complete |
| 3 | Supabase Auth + persistent profiles | ✅ Complete (see Known Issues #1–3) |
| 4 | Home / Discover screen + mood check-in + GPS + Walk CTA | ✅ Complete |
| 5 | Premium gate (RevenueCat) | ✅ Complete (mock mode) |
| 6 | Trip Itinerary Generator | ✅ Complete |
| 7 | Map enhancements (golden hour, photos) | ⬜ Not started |
| 8 | Astronomy / moon phase | ⬜ Not started |
| 9 | Web platform (Next.js) | ⬜ Not started |
| 10 | Freemium polish + analytics | ⬜ Not started |
