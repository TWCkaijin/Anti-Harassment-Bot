/**
 * Chat UI analytics hook. The UI only reports coarse interaction events; the host
 * app decides whether (and where) to record them. Nothing is recorded until a host
 * registers a tracker, so the admin diagnostic chat stays silent.
 */
export type ChatUiEvent = "resource_action_clicked" | "next_step_selected" | "clarification_submitted";
export type ChatUiTracker = (event: ChatUiEvent, parameters?: Record<string, number | boolean | string>) => void;

let tracker: ChatUiTracker | undefined;

export function setChatUiAnalytics(next: ChatUiTracker | undefined): void {
  tracker = next;
}

export function trackAnalytics(event: ChatUiEvent, parameters?: Record<string, number | boolean | string>): void {
  try {
    tracker?.(event, parameters);
  } catch {
    /* Analytics must never break the chat. */
  }
}
