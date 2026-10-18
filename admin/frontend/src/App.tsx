import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import AdminPanel from "./components/AdminPanel";
import { ApiError } from "./services/api";
import {
  login,
  logout,
  restoreSession,
  SESSION_EXPIRED_EVENT,
  type AdminSession,
} from "./services/session";

export default function App({ children }: { children?: ReactNode }) {
  const [session, setSession] = useState<AdminSession | null>(null);
  const [unlocked, setUnlocked] = useState(false);
  const [checking, setChecking] = useState(true);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hasSession = useRef(false);

  useEffect(() => {
    let active = true;
    const expired = () => {
      if (hasSession.current) {
        setUnlocked(false);
        setError("登入已到期，請重新輸入密碼。本機測試結果仍保留。");
      }
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    void restoreSession()
      .then((value) => {
        if (active) {
          hasSession.current = true;
          setSession(value);
          setUnlocked(true);
        }
      })
      .catch((failure) => {
        if (active && (!(failure instanceof ApiError) || failure.status !== 401)) {
          setError("無法連線至管理後端，請稍後重試。");
        }
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
      window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
    };
  }, []);

  const signIn = async (event: FormEvent) => {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError("");
    const submitted = password;
    setPassword("");
    try {
      const value = await login(submitted);
      hasSession.current = true;
      setSession(value);
      setUnlocked(true);
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? (failure.detail ?? "無法登入，請確認密碼或稍後重試。")
          : "無法連線至管理後端。",
      );
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await logout();
      hasSession.current = false;
      setSession(null);
      setUnlocked(false);
      setError("");
    } catch {
      setError("登出尚未完成，請檢查連線後重試。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-dvh w-full bg-background text-on-surface">
      {session && (
        <div inert={!unlocked} aria-hidden={!unlocked}>
          {children ? (
            <>
              <header className="flex justify-between border-b border-outline/30 p-4">
                <a href="/" className="text-primary underline">
                  回管理後台
                </a>
                <button
                  onClick={() => {
                    void signOut();
                  }}
                >
                  登出
                </button>
              </header>
              {children}
            </>
          ) : (
            <AdminPanel
              session={session}
              unlocked={unlocked}
              onLogout={() => {
                void signOut();
              }}
            />
          )}
        </div>
      )}
      {!unlocked && (
        <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-background p-6">
          <form
            onSubmit={signIn}
            className="w-full max-w-md space-y-5 rounded-xl border border-outline/30 bg-white p-8 shadow-sm"
          >
            <div>
              <p className="text-sm font-medium text-primary">溫暖守護</p>
              <h1 className="mt-2 text-2xl font-bold">管理後台</h1>
              <p className="mt-2 text-sm text-on-surface/65">
                登入後管理系統設定、測試與檢索資料。
              </p>
            </div>
            {checking ? (
              <p role="status">正在確認登入狀態…</p>
            ) : (
              <>
                <label className="block text-sm font-medium">
                  管理密碼
                  <input
                    autoFocus
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    disabled={busy}
                    maxLength={1024}
                    className="input mt-2 w-full disabled:cursor-wait disabled:opacity-60"
                    required
                  />
                </label>
                <button
                  type="submit"
                  disabled={busy || !password}
                  aria-busy={busy}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-3 font-semibold text-white disabled:opacity-50"
                >
                  {busy && (
                    <span
                      aria-hidden="true"
                      className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white motion-reduce:animate-none"
                    />
                  )}
                  <span aria-live="polite">{busy ? "正在登入…" : "登入"}</span>
                </button>
              </>
            )}
            {error && (
              <p role="alert" className="text-sm text-error">
                {error}
              </p>
            )}
          </form>
        </div>
      )}
      {unlocked && error && (
        <p
          role="alert"
          className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-error-container p-3 text-error"
        >
          {error}
        </p>
      )}
    </main>
  );
}
