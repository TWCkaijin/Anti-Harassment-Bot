/** Obtain a short-lived App Check token for each chat request. No chat text enters Firebase SDK. */
import type { AppCheck } from "firebase/app-check";
import { ApiError } from "@rag/shared/api";

let instance: Promise<AppCheck> | undefined;

function configured(): boolean {
  return Boolean(import.meta.env.VITE_FIREBASE_API_KEY && import.meta.env.VITE_FIREBASE_PROJECT_ID
    && import.meta.env.VITE_FIREBASE_APP_ID && import.meta.env.VITE_APP_CHECK_SITE_KEY);
}

async function getInstance(): Promise<AppCheck> {
  instance ??= (async () => {
    const [appSdk, appCheckSdk] = await Promise.all([import("firebase/app"), import("firebase/app-check")]);
    const app = appSdk.getApps().find(candidate => candidate.name === "client-app-check")
      ?? appSdk.initializeApp({
        apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
        projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
        appId: import.meta.env.VITE_FIREBASE_APP_ID,
      }, "client-app-check");
    return appCheckSdk.initializeAppCheck(app, {
      provider: new appCheckSdk.ReCaptchaEnterpriseProvider(import.meta.env.VITE_APP_CHECK_SITE_KEY),
      isTokenAutoRefreshEnabled: true,
    });
  })();
  try { return await instance; }
  catch (error) { instance = undefined; throw error; }
}

export async function getChatAppCheckToken(signal?: AbortSignal): Promise<string | undefined> {
  signal?.throwIfAborted();
  if (import.meta.env.VITE_APP_CHECK_ENABLED !== "true") return undefined;
  if (!configured()) throw new ApiError(503, "瀏覽器驗證尚未設定", "請稍後再試。", false);
  try {
    const appCheck = await getInstance();
    const { getToken } = await import("firebase/app-check");
    const result = await getToken(appCheck);
    signal?.throwIfAborted();
    if (!result.token) throw new Error("Missing App Check token");
    return result.token;
  } catch {
    signal?.throwIfAborted();
    throw new ApiError(503, "無法完成瀏覽器驗證", "請重新整理頁面後再試。", false);
  }
}
