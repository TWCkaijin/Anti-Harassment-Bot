import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import SettingsPanel from "./SettingsPanel";
import { I18nProvider } from "../i18n";
import { hasAnalyticsConsent, isAnalyticsConfigured, setAnalyticsConsent } from "../services/analytics";
vi.mock("../services/analytics", () => ({
  hasAnalyticsConsent: vi.fn(() => false), isAnalyticsConfigured: vi.fn(() => true),
  setAnalyticsConsent: vi.fn(),
}));
beforeEach(() => vi.clearAllMocks());
function settings() {
  return render(<I18nProvider><SettingsPanel isOpen onClose={vi.fn()} sessions={[]} onClearAll={vi.fn()} /></I18nProvider>);
}
it("allows explicit opt-in and withdrawal from settings", () => {
  settings();
  const checkbox = screen.getByRole("checkbox", { name: "分享使用統計（選用）" });
  expect(checkbox).not.toBeChecked();
  vi.mocked(hasAnalyticsConsent).mockReturnValue(true);
  fireEvent.click(checkbox);
  expect(setAnalyticsConsent).toHaveBeenLastCalledWith(true);
  expect(checkbox).toBeChecked();
  vi.mocked(hasAnalyticsConsent).mockReturnValue(false);
  fireEvent.click(checkbox);
  expect(setAnalyticsConsent).toHaveBeenLastCalledWith(false);
  expect(checkbox).not.toBeChecked();
});
it("does not advertise a nonconfigured feature", () => {
  vi.mocked(isAnalyticsConfigured).mockReturnValue(false);
  settings();
  expect(screen.queryByRole("checkbox", { name: "分享使用統計（選用）" })).not.toBeInTheDocument();
});
