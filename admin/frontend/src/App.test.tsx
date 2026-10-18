import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ApiError } from "./services/api";
import { login, logout, restoreSession, SESSION_EXPIRED_EVENT } from "./services/session";

vi.mock("./services/session", () => ({ login: vi.fn(), logout: vi.fn(), restoreSession: vi.fn(), SESSION_EXPIRED_EVENT: "admin-session-expired" }));
vi.mock("./components/AdminPanel", () => ({ default: ({ onLogout }: { onLogout: () => void }) => <section aria-label="已登入的管理工具"><textarea aria-label="本機測試紀錄" defaultValue="" /><button onClick={onLogout}>登出</button></section> }));
const session = { authenticated: true as const, csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z", environment: "preview" };
beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); vi.mocked(restoreSession).mockRejectedValue(new ApiError(401, "Unauthenticated")); vi.mocked(login).mockResolvedValue(session); vi.mocked(logout).mockResolvedValue(); });

describe("standalone admin authentication", () => {
  it("locks login controls until authentication fails and then permits retry", async () => {
    let reject!: (error: Error) => void;
    vi.mocked(login).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
    render(<App />);
    const password = await screen.findByLabelText("管理密碼");
    fireEvent.change(password, { target: { value: "synthetic-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(password).toBeDisabled();
    const pending = screen.getByRole("button", { name: "正在登入…" });
    expect(pending).toBeDisabled();
    expect(pending).toHaveAttribute("aria-busy", "true");
    fireEvent.submit(password.closest("form")!);
    expect(login).toHaveBeenCalledOnce();
    await act(async () => reject(new ApiError(401, "密碼錯誤")));
    expect(password).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("無法登入");
    fireEvent.change(password, { target: { value: "retry-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(await screen.findByLabelText("已登入的管理工具")).toBeInTheDocument();
  });
  it("does not mount protected tools before backend password verification", async () => {
    render(<App />);
    const password = await screen.findByLabelText("管理密碼");
    expect(screen.queryByLabelText("已登入的管理工具")).not.toBeInTheDocument();
    fireEvent.change(password, { target: { value: "secret-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(await screen.findByLabelText("已登入的管理工具")).toBeInTheDocument();
    expect(login).toHaveBeenCalledExactlyOnceWith("secret-password");
    expect(localStorage.length).toBe(0);
  });
  it("keeps local test data through expiration and explicit reauthentication", async () => {
    vi.mocked(restoreSession).mockResolvedValue(session);
    render(<App />);
    const draft = await screen.findByLabelText("本機測試紀錄");
    fireEvent.change(draft, { target: { value: "尚未下載的測試結果" } });
    act(() => window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT)));
    expect(screen.getByLabelText("管理密碼")).toBeInTheDocument();
    expect(draft).toHaveValue("尚未下載的測試結果");
    vi.mocked(login).mockResolvedValue({ ...session, csrf_token: "csrf-2" });
    fireEvent.change(screen.getByLabelText("管理密碼"), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    await waitFor(() => expect(screen.queryByLabelText("管理密碼")).not.toBeInTheDocument());
    expect(screen.getByLabelText("本機測試紀錄")).toHaveValue("尚未下載的測試結果");
  });
  it("waits for server logout before clearing the active interface", async () => {
    vi.mocked(restoreSession).mockResolvedValue(session);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "登出" }));
    await waitFor(() => expect(logout).toHaveBeenCalledOnce());
    expect(await screen.findByLabelText("管理密碼")).toBeInTheDocument();
    expect(screen.queryByLabelText("已登入的管理工具")).not.toBeInTheDocument();
  });
});
