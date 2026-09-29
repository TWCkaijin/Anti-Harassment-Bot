const zhTW = {
  // ── App / 通用 ──
  appTitle: "性騷擾防治智能 AI",
  appSubtitle: "溫暖守護 · 本地紀錄 · 法律知識庫",
  brandName: "溫暖守護 AI",
  brandSub: "屏東縣政府性騷擾治理政策Agent",

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
  privacyNote: "對話紀錄保存在此裝置",
  privacyNoteSub: "提問內容會傳至服務處理",

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
  anonymizedLabel: "已遮蔽部分文字識別資訊",

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
  showEmotionLabels: "顯示情緒標籤",
  emotionLabelsNote: "標籤由 AI 推測，並非心理評估。關閉只隱藏標籤，不刪除對話。",
  preferenceSaveFailed: "此設定暫時只在本次使用有效，無法寫入裝置。",
  caseSummary: "目前了解的情況",
  caseSummaryNote: "只整理判斷所需資訊，請勿填姓名、電話或精確地址。摘要保存在此裝置，提問時會傳送給服務處理。",
  caseSummaryEmpty: "尚未整理必要資訊，您可以直接開始對話。",
  caseSummaryEdit: "修改摘要",
  caseSummarySave: "儲存摘要",
  caseSummarySaveAnswer: "儲存並重新回答",
  caseFactNotAsked: "尚未提供",
  caseFactProvided: "已提供",
  caseFactUnknown: "不確定",
  caseFactDeclined: "暫不提供",
  caseFactProposed: "待您確認的資訊",
  caseFactUseProposal: "採用此內容",
  caseFactValue: "內容",
  caseFactInvalid: "已提供的內容須為 1 至 300 個字元。",
  clarificationTitle: "先確認一個會影響處理方向的問題",
  clarificationOther: "自行補充",
  clarificationSubmit: "送出回覆",
  clarificationHide: "隱藏問題，直接輸入",
  clarificationShow: "顯示問題",
  supersededAnswer: "摘要已更正，這則回覆依據較早的資訊。",
  storageUnavailable: "無法保存到此裝置。目前內容仍在此分頁，請先匯出重要內容；重新整理可能遺失。",
  storageInvalid: "部分本地紀錄無法讀取，為保留原資料，暫停覆寫。可先匯出可讀取的內容，再清除紀錄重新開始。",
  storageFuture: "本地紀錄來自較新的版本，已停止覆寫。請使用較新版本開啟。",
  incompatibleSummary: "目前服務尚未支援這份情境摘要。為避免忽略您更正的資訊，這段對話暫停傳送；您可以開啟新對話。原摘要仍保留在此裝置。",
  answerDirection: "可能的處理方向",
  answerBasis: "參考依據",
  answerNextSteps: "下一步",
  caseFactLabels: {
    subject_role: "您的角色", other_role: "對方的角色", relationship: "雙方關係", work_related: "是否與工作有關",
    internship_related: "是否與實習有關", education_related: "是否與教育活動有關", behavior: "行為類型", ongoing: "是否持續發生",
    event_time: "事件大約時間", age_group: "年齡區間", city: "縣市", desired_help: "希望獲得的協助",
  },
} as const;

export type TranslationKeys = keyof typeof zhTW;
export default zhTW;
