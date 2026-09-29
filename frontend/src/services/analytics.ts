/** Optional operational analytics. Never accept conversation text, URLs or identifiers. */
import type { Analytics } from "firebase/analytics";

type AnalyticsSDK = typeof import("firebase/analytics");
const CONSENT_KEY = "harass_bot_analytics_consent";
const PHASES = ["anonymizing", "preparing", "waiting_model", "retrieving", "generating", "guidance", "validating"];
const FIELDS = {
  chat_request_started: ["has_image", "uses_rag"],
  chat_first_token: ["duration_ms", "retry_count"],
  chat_first_guidance: ["duration_ms"],
  chat_stage_completed: ["phase", "duration_ms", "attempt"],
  chat_request_finished: ["outcome", "duration_ms", "first_token_ms", "first_guidance_ms", "retry_count", "http_status", "rag_used", "source_count", "streamed"],
  resource_action_clicked: ["action_type"],
  next_step_selected: [],
  clarification_submitted: ["question_count", "used_other"],
} as const;
type EventName = keyof typeof FIELDS;
type EventParameters = Partial<Record<typeof FIELDS[EventName][number], number | boolean | string>>;
let instance: Analytics | undefined;
let sdk: AnalyticsSDK | undefined;
let initializing: Promise<Analytics | undefined> | undefined;
let consentVersion = 0;

export function isAnalyticsConfigured(): boolean {
  return import.meta.env.VITE_ANALYTICS_ENABLED === "true" && Boolean(
    import.meta.env.VITE_FIREBASE_API_KEY && import.meta.env.VITE_FIREBASE_PROJECT_ID
    && import.meta.env.VITE_FIREBASE_APP_ID && /^G-[A-Z0-9]+$/.test(import.meta.env.VITE_FIREBASE_MEASUREMENT_ID ?? "")
  );
}

export function hasAnalyticsConsent(): boolean {
  try { return localStorage.getItem(CONSENT_KEY) === "granted"; } catch { return false; }
}

function allowed(): boolean {
  return isAnalyticsConfigured() && hasAnalyticsConsent();
}

function updateConsent(enabled: boolean) {
  try {
    sdk?.setConsent({
      analytics_storage: enabled ? "granted" : "denied",
      ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    });
    if (instance) sdk?.setAnalyticsCollectionEnabled(instance, enabled);
  } catch { /* Collection controls must not break settings or chat. */ }
}

async function getInstance(): Promise<Analytics | undefined> {
  if (!allowed()) return undefined;
  if (instance) return instance;
  initializing ??= (async () => {
    try {
      const [appSDK, analyticsSDK] = await Promise.all([import("firebase/app"), import("firebase/analytics")]);
      if (!await analyticsSDK.isSupported() || !allowed()) return undefined;
      sdk = analyticsSDK;
      updateConsent(true);
      const app = appSDK.getApps().find(app => app.name === "usage-analytics") ?? appSDK.initializeApp({
        apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
        projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
        appId: import.meta.env.VITE_FIREBASE_APP_ID,
        measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID,
      }, "usage-analytics");
      instance = sdk.initializeAnalytics(app, { config: {
        send_page_view: false,
        allow_google_signals: false,
        allow_ad_personalization_signals: false,
        page_location: `${window.location.origin}/`,
        page_referrer: "",
        page_title: "Support chat",
      } });
      updateConsent(allowed());
      return instance;
    } catch {
      // Blockers, unsupported browsers and SDK failures must never break chat.
      return undefined;
    }
  })();
  const result = await initializing;
  if (!result) initializing = undefined;
  return result;
}

export function setAnalyticsConsent(enabled: boolean): void {
  consentVersion += 1;
  try { localStorage.setItem(CONSENT_KEY, enabled ? "granted" : "denied"); } catch { enabled = false; }
  updateConsent(enabled && isAnalyticsConfigured());
  if (enabled) void getInstance();
}

export function trackAnalytics(event: EventName, parameters: EventParameters = {}): void {
  if (!allowed() || !(event in FIELDS)) return;
  const version = consentVersion;
  const sanitized: Record<string, number | boolean | string> = {};
  for (const key of FIELDS[event]) {
    const value = parameters[key];
    if (key === "phase") {
      if (typeof value === "string" && PHASES.includes(value)) sanitized[key] = value;
    } else if (key === "outcome") {
      if (["success", "error", "cancelled"].includes(String(value))) sanitized[key] = String(value);
    } else if (key === "action_type") {
      if (value === "tel" || value === "url") sanitized[key] = value;
    } else if (typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value) && value >= 0)) {
      sanitized[key] = value;
    }
  }
  void getInstance().then(analytics => {
    if (!analytics || !sdk || !allowed() || version !== consentVersion) return;
    sdk.logEvent(analytics, event, {
      ...sanitized,
      app_environment: import.meta.env.VITE_ENV === "preview" ? "preview" : import.meta.env.PROD ? "production" : "development",
      ...(import.meta.env.VITE_ANALYTICS_DEBUG === "true" ? { debug_mode: true } : {}),
    });
  }).catch(() => { /* Analytics is always best effort. */ });
}
