import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({
  initializeApp: vi.fn(() => ({ name: "usage-analytics" })),
  getApps: vi.fn(() => []),
  initializeAnalytics: vi.fn(() => ({ app: {} })),
  isSupported: vi.fn(async () => true),
  setConsent: vi.fn(), setAnalyticsCollectionEnabled: vi.fn(), logEvent: vi.fn<(analytics: unknown, event: string, parameters: unknown) => void>(),
}));
vi.mock("firebase/app", () => mock);
vi.mock("firebase/analytics", () => mock);

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mock.isSupported.mockResolvedValue(true);
  mock.logEvent.mockReset();
  localStorage.clear();
  vi.stubEnv("VITE_ANALYTICS_ENABLED", "true");
  vi.stubEnv("VITE_FIREBASE_API_KEY", "public-web-key");
  vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "test-project");
  vi.stubEnv("VITE_FIREBASE_APP_ID", "1:123:web:test");
  vi.stubEnv("VITE_FIREBASE_MEASUREMENT_ID", "G-TEST123");
});
afterEach(() => vi.unstubAllEnvs());

async function settled() { await new Promise(resolve => setTimeout(resolve, 10)); }

describe("optional Firebase analytics", () => {
  it("does not initialize or replay pre-consent events", async () => {
    const analytics = await import("./analytics");
    analytics.trackAnalytics("chat_request_started", { has_image: true });
    await settled();
    expect(mock.initializeApp).not.toHaveBeenCalled();
    analytics.setAnalyticsConsent(true);
    await vi.waitFor(() => expect(mock.initializeAnalytics).toHaveBeenCalledOnce());
    expect(mock.logEvent).not.toHaveBeenCalled();
  });

  it("requires complete enabled configuration even after opt-in", async () => {
    vi.stubEnv("VITE_FIREBASE_MEASUREMENT_ID", "");
    const analytics = await import("./analytics");
    analytics.setAnalyticsConsent(true);
    analytics.trackAnalytics("next_step_selected");
    await settled();
    expect(analytics.isAnalyticsConfigured()).toBe(false);
    expect(mock.initializeApp).not.toHaveBeenCalled();
  });

  it("sends only allowed metrics and disables automatic page views and advertising", async () => {
    const analytics = await import("./analytics");
    analytics.setAnalyticsConsent(true);
    analytics.trackAnalytics("chat_request_finished", {
      outcome: "success", duration_ms: 1250, retry_count: 1,
      message: "private", session_id: "private", emotion: "private", url: "https://private.test/",
    } as never);
    await vi.waitFor(() => expect(mock.logEvent).toHaveBeenCalledOnce());
    expect(mock.logEvent.mock.calls[0][2]).toEqual({
      outcome: "success", duration_ms: 1250, retry_count: 1, app_environment: "development",
    });
    expect(mock.initializeAnalytics).toHaveBeenCalledWith(expect.anything(), { config: expect.objectContaining({
      send_page_view: false, allow_google_signals: false, allow_ad_personalization_signals: false,
      page_referrer: "", page_location: "http://localhost/",
    }) });
    expect(mock.setConsent).toHaveBeenCalledWith({
      analytics_storage: "granted", ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    });
  });

  it("drops invalid values and resource labels instead of sending them", async () => {
    const analytics = await import("./analytics");
    analytics.setAnalyticsConsent(true);
    analytics.trackAnalytics("resource_action_clicked", { action_type: "private phone", label: "private" } as never);
    analytics.trackAnalytics("chat_stage_completed", { phase: "private", duration_ms: -1, attempt: Infinity });
    await vi.waitFor(() => expect(mock.logEvent).toHaveBeenCalledTimes(2));
    for (const call of mock.logEvent.mock.calls) expect(call[2]).toEqual({ app_environment: "development" });
  });

  it("honors withdrawal while initialization is pending and never replays the event", async () => {
    let resolve!: (supported: boolean) => void;
    mock.isSupported.mockReturnValue(new Promise<boolean>(r => { resolve = r; }));
    const analytics = await import("./analytics");
    analytics.setAnalyticsConsent(true);
    analytics.trackAnalytics("next_step_selected");
    await vi.waitFor(() => expect(mock.isSupported).toHaveBeenCalledOnce());
    analytics.setAnalyticsConsent(false);
    resolve(true);
    await settled();
    expect(mock.initializeAnalytics).not.toHaveBeenCalled();
    expect(mock.logEvent).not.toHaveBeenCalled();
  });

  it("stops sending after withdrawal", async () => {
    const analytics = await import("./analytics");
    analytics.setAnalyticsConsent(true);
    analytics.trackAnalytics("next_step_selected");
    await vi.waitFor(() => expect(mock.logEvent).toHaveBeenCalledOnce());
    analytics.setAnalyticsConsent(false);
    analytics.trackAnalytics("next_step_selected");
    await settled();
    expect(mock.logEvent).toHaveBeenCalledOnce();
    expect(mock.setAnalyticsCollectionEnabled).toHaveBeenLastCalledWith(expect.anything(), false);
  });

  it("ignores unsupported browsers and SDK failures", async () => {
    mock.isSupported.mockRejectedValue(new Error("blocked"));
    const analytics = await import("./analytics");
    expect(() => analytics.setAnalyticsConsent(true)).not.toThrow();
    expect(() => analytics.trackAnalytics("next_step_selected")).not.toThrow();
    await settled();
    expect(mock.logEvent).not.toHaveBeenCalled();
  });
});
