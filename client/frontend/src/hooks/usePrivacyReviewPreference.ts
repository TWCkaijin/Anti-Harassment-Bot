import { parseFlag, usePersistentPreference } from "./usePersistentPreference";

export function usePrivacyReviewPreference() {
  const [confirmPrivacyBeforeSending, updateConfirmPrivacy, privacyReviewSaveFailed] = usePersistentPreference("harass_bot_confirm_privacy_before_sending", parseFlag);
  return { confirmPrivacyBeforeSending, updateConfirmPrivacy, privacyReviewSaveFailed };
}
