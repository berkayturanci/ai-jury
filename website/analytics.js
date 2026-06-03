// Firebase Analytics (GA4) for the ai-jury website.
//
// Scope note: this measures **the website only** — anonymous pageviews and
// the GA4 default events (scroll, outbound click, etc.). The `ai-jury` CLI
// itself sends no telemetry; the two are different surfaces.
//
// Setup:
//   1. Firebase Console → create / open the project for this site.
//   2. Project Settings → "Your apps" → add a Web app (or open the existing
//      one) → copy the `firebaseConfig` object shown there.
//   3. Replace the placeholder values below with the real config and commit.
//      The `measurementId` (`G-…`) is what wires up GA4.
//   4. Make sure "Google Analytics" is enabled for the Firebase project so
//      `getAnalytics()` has somewhere to send events.
//
// No other code changes are required — every page loads this module from
// its `<head>`.

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAnalytics, isSupported } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-analytics.js";

const firebaseConfig = {
  apiKey:            "REPLACE_ME_API_KEY",
  authDomain:        "REPLACE_ME.firebaseapp.com",
  projectId:         "REPLACE_ME_PROJECT_ID",
  storageBucket:     "REPLACE_ME.appspot.com",
  messagingSenderId: "REPLACE_ME_SENDER_ID",
  appId:             "REPLACE_ME_APP_ID",
  measurementId:     "G-REPLACE_ME",
};

// Skip init while the placeholder config is still in place so local previews
// and CI artifacts don't try to reach Firebase with bogus credentials.
if (!firebaseConfig.measurementId.includes("REPLACE_ME")) {
  isSupported().then((ok) => {
    if (!ok) return;
    const app = initializeApp(firebaseConfig);
    getAnalytics(app);
  });
}
