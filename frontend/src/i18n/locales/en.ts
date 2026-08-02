const en = {
  // ── App / General ──
  appTitle: "Anti-Harassment AI",
  appSubtitle: "Warm Protection · Anonymous · Legal Knowledge",
  brandName: "Guardian AI",
  brandSub: "AI Assistant",

  // ── Sidebar ──
  newChat: "New Chat",
  chatHistory: "Chat History",
  noHistory: "No conversation history",
  deleteChat: "Delete chat",
  newConversation: "New conversation",
  messagesCount: (n: number) => `${n} msg${n > 1 ? "s" : ""}`,
  timeJustNow: "Just now",
  timeMinAgo: (n: number) => `${n} min ago`,
  timeHourAgo: (n: number) => `${n}h ago`,
  timeDayAgo: (n: number) => `${n}d ago`,

  // Sidebar bottom
  emergencyContacts: "Emergency",
  counseling: "Counseling",
  settings: "Settings",
  privacyNote: "🔒 Chats saved on your device only",
  privacyNoteSub: "Data persists after closing browser",

  // ── WelcomeHero ──
  heroChip: "AI Assistant Ready",
  heroTitle: "Your peaceful life, ",
  heroTitleHighlight: "we protect",
  heroDesc:
    "Providing immediate, professional integrated support. Whether it's legal counsel, reporting channels, or emotional support — we're here to guide you.",
  heroInputPlaceholder: "How can I help you?",
  suggestLaw: "How to apply for legal aid?",
  suggestReport: "Anonymous reporting channels",
  suggestSelfCare: "How to take care of myself?",

  // ── ChatArea ──
  statusThinking: "AI is thinking…",
  statusConnected: "Connected",
  statusProcessing: "Processing",
  openSidebar: "Open sidebar",

  // ── ChatInput ──
  inputPlaceholder: "Describe your situation or ask a question…",
  sendMessage: "Send message",
  aiDisclaimer: "AI responses are for reference only. For urgent help call",
  hotline113: "113 Hotline",

  // ── MessageItem ──
  ragLabel: "Retrieved databases",
  ragTooltipTitle: "Retrieved sources:",
  ragSourceLaw: "Legal Articles",
  ragSourceJudgment: "Judgments",
  ragSourceRemedy: "Remedies",
  ragSourceUnknown: "Other Sources",
  anonymizedLabel: "Privacy de-identification",

  // ── EmergencyFab ──
  emergency113: "113 Protection Hotline",
  emergency110: "110 Police",

  // ── Settings ──
  settingsTitle: "Settings",
  settingLanguage: "Language",
  settingTheme: "Color Theme",
  settingLocalStorage: "Local Storage",
  settingExport: "Export Conversations",
  themeWarm: "Warm",
  themeCool: "Cool",
  langZhTW: "繁體中文",
  langEn: "English",
  clearAllData: "Clear all conversation data",
  clearAllDataConfirm:
    "Are you sure you want to clear all conversations? This cannot be undone.",
  exportAsJson: "Export as JSON",
  exportAsTxt: "Export as Text",
  close: "Close",
  cancel: "Cancel",
  confirm: "Confirm",
} as const;

export default en;
