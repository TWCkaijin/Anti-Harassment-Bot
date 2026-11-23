import { useState } from "react";

const STORAGE_KEY = "harass_bot_confirm_privacy_before_sending";

export function usePrivacyReviewPreference() {
  const [confirmPrivacyBeforeSending, setConfirmPrivacyBeforeSending] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) === "true"; }
    catch { return false; }
  });
  const [privacyReviewSaveFailed, setPrivacyReviewSaveFailed] = useState(false);

  const updateConfirmPrivacy = (enabled: boolean) => {
    setConfirmPrivacyBeforeSending(enabled);
    try {
      localStorage.setItem(STORAGE_KEY, String(enabled));
      setPrivacyReviewSaveFailed(false);
    } catch { setPrivacyReviewSaveFailed(true); }
  };

  return { confirmPrivacyBeforeSending, updateConfirmPrivacy, privacyReviewSaveFailed };
}
