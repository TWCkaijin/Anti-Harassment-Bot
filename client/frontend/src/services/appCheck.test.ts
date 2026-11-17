import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  getApps: vi.fn(() => []),
  initializeApp: vi.fn(() => ({ name: "client-app-check" })),
  initializeAppCheck: vi.fn((...args: unknown[]) => {
    if (args.length !== 2) throw new Error("Expected app and provider options");
    return { app: "client-app-check" };
  }),
  getToken: vi.fn(),
}));
vi.mock("firebase/app", () => ({ getApps: sdk.getApps, initializeApp: sdk.initializeApp }));
vi.mock("firebase/app-check", () => ({
  ReCaptchaEnterpriseProvider: class { readonly siteKey: string; constructor(siteKey: string) { this.siteKey = siteKey; } },
  initializeAppCheck: sdk.initializeAppCheck,
  getToken: sdk.getToken,
}));

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("VITE_APP_CHECK_ENABLED", "false");
  vi.stubEnv("VITE_APP_CHECK_SITE_KEY", "");
  vi.stubEnv("VITE_FIREBASE_API_KEY", "");
  vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "");
  vi.stubEnv("VITE_FIREBASE_APP_ID", "");
  sdk.getToken.mockReset();
  sdk.initializeApp.mockClear();
  sdk.initializeAppCheck.mockClear();
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("chat App Check", () => {
  it("does not initialize the SDK when the rollout flag is off", async () => {
    const { getChatAppCheckToken } = await import("./appCheck");
    expect(await getChatAppCheckToken()).toBeUndefined();
    expect(sdk.initializeAppCheck).not.toHaveBeenCalled();
  });
  it("fails closed when an enabled site is missing its public configuration", async () => {
    vi.stubEnv("VITE_APP_CHECK_ENABLED", "true");
    const { getChatAppCheckToken } = await import("./appCheck");
    await expect(getChatAppCheckToken()).rejects.toMatchObject({ status: 503 });
    expect(sdk.initializeAppCheck).not.toHaveBeenCalled();
  });
  it("never starts a chat fetch when enabled App Check lacks a site key", async () => {
    vi.stubEnv("VITE_APP_CHECK_ENABLED", "true");
    vi.stubEnv("VITE_FIREBASE_API_KEY", "public-test-api-key");
    vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "synthetic-project");
    vi.stubEnv("VITE_FIREBASE_APP_ID", "synthetic-app");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { approvePrivacyDraft, createPrivacyDraft } = await import("./clientPrivacy");
    const { sendChat } = await import("./api");
    const request = approvePrivacyDraft(createPrivacyDraft({ message: "合成問題", history: [], use_rag: false }));
    await expect(sendChat(request)).rejects.toMatchObject({ status: 503 });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("obtains a token for the configured web app without sending chat text to the SDK", async () => {
    vi.stubEnv("VITE_APP_CHECK_ENABLED", "true");
    vi.stubEnv("VITE_APP_CHECK_SITE_KEY", "public-test-site-key");
    vi.stubEnv("VITE_FIREBASE_API_KEY", "public-test-api-key");
    vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "synthetic-project");
    vi.stubEnv("VITE_FIREBASE_APP_ID", "synthetic-app");
    sdk.getToken.mockResolvedValue({ token: "synthetic-app-check-token" });
    const { getChatAppCheckToken } = await import("./appCheck");
    expect(await getChatAppCheckToken()).toBe("synthetic-app-check-token");
    expect(sdk.initializeAppCheck).toHaveBeenCalledOnce();
    expect(sdk.initializeAppCheck.mock.calls[0][1]).toEqual({
      provider: expect.objectContaining({ siteKey: "public-test-site-key" }),
      isTokenAutoRefreshEnabled: true,
    });
    expect(sdk.getToken).toHaveBeenCalledWith({ app: "client-app-check" });
  });
  it("does not permit chat to continue after token acquisition fails", async () => {
    vi.stubEnv("VITE_APP_CHECK_ENABLED", "true");
    vi.stubEnv("VITE_APP_CHECK_SITE_KEY", "public-test-site-key");
    vi.stubEnv("VITE_FIREBASE_API_KEY", "public-test-api-key");
    vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "synthetic-project");
    vi.stubEnv("VITE_FIREBASE_APP_ID", "synthetic-app");
    sdk.getToken.mockRejectedValue(new Error("synthetic SDK error"));
    const { getChatAppCheckToken } = await import("./appCheck");
    await expect(getChatAppCheckToken()).rejects.toMatchObject({ status: 503, retryable: false });
  });
});
