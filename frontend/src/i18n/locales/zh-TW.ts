const zhTW = {
  // ── App / 通用 ──
  appTitle: "性騷擾防治智能 AI",
  appSubtitle: "溫暖守護 · 匿名安全 · 法律知識庫",
  brandName: "溫暖守護 AI",
  brandSub: "AI 助理",

  // ── Sidebar ──
  newChat: "開啟新對話",
  chatHistory: "對話紀錄",
  noHistory: "尚無對話紀錄",
  deleteChat: "刪除對話",
  newConversation: "新的對話",
  messagesCount: (n: number) => `${n} 則`,
  timeJustNow: "剛剛",
  timeMinAgo: (n: number) => `${n} 分鐘前`,
  timeHourAgo: (n: number) => `${n} 小時前`,
  timeDayAgo: (n: number) => `${n} 天前`,

  // Sidebar 底部
  emergencyContacts: "緊急聯絡",
  counseling: "心理諮商",
  settings: "設定",
  privacyNote: "對話僅保存於您的裝置",
  privacyNoteSub: "關閉瀏覽器後不影響紀錄",

  // ── WelcomeHero ──
  heroChip: "AI 助理已就緒",
  heroTitle: "您的平靜生活，",
  heroTitleHighlight: "我們守護",
  heroDesc:
    "提供即時、專業的整合性協助。不論是法律諮詢、通報管道或情緒支持，我們都在這裡為您引路。",
  heroInputPlaceholder: "我有什麼可以幫您的？",
  suggestLaw: "如何申請法律扶助？",
  suggestReport: "匿名通報的管道",
  suggestSelfCare: "如何照顧自己身心？",

  // ── ChatArea ──
  statusThinking: "AI 正在思考中…",
  statusConnected: "已連線",
  statusProcessing: "處理中",
  openSidebar: "開啟側邊欄",

  // ── ChatInput ──
  inputPlaceholder: "請描述您的狀況或提出問題…",
  sendMessage: "傳送訊息",
  aiDisclaimer: "AI 的回應僅供參考，如需緊急協助請撥打",
  hotline113: "113 保護專線",

  // ── MessageItem ──
  ragLabel: "已檢索資料庫",
  ragTooltipTitle: "檢索依據：",
  ragSourceLaw: "法律條文",
  ragSourceJudgment: "歷史判決",
  ragSourceRemedy: "救濟管道",
  ragSourceUnknown: "其他資料",
  anonymizedLabel: "隱私去識別化保護",

  // ── EmergencyFab ──
  emergency113: "113 保護專線",
  emergency110: "110 報案專線",

  // ── Settings ──
  settingsTitle: "設定",
  settingLanguage: "語言",
  settingTheme: "色彩組合",
  settingLocalStorage: "本地紀錄設定",
  settingExport: "匯出對話紀錄",
  themeWarm: "暖色系",
  themeCool: "冷色系",
  langZhTW: "繁體中文",
  langEn: "English",
  clearAllData: "清除所有對話紀錄",
  clearAllDataConfirm: "確定要清除所有對話紀錄嗎？此操作無法復原。",
  exportAsJson: "匯出為 JSON",
  exportAsTxt: "匯出為純文字",
  close: "關閉",
  cancel: "取消",
  confirm: "確認",
} as const;

export type TranslationKeys = keyof typeof zhTW;
export default zhTW;
