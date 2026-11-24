/** Conversation shapes shared by the client chat and the admin diagnostic chat. */
import type {
  ActionButton,
  AnalysisEntry,
  ChatGuidance,
  ChatResponse,
  Clarification,
  ClarificationAnswer,
  DebugToolCall,
  RagInfo,
  ReasoningEntry,
} from "../api";
import type { ClarificationDraft } from "../caseFacts";
import type { ProcessingTrace } from "./processingSteps";

/** Local display metadata; content remains the complete model-visible message. */
export interface ReplyContext {
  answers: Array<{ question: string; answer: string }>;
  clarificationAnswer?: ClarificationAnswer;
  factsRevision?: number;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  anonymized?: boolean;
  ragUsed?: RagInfo;
  isError?: boolean;
  isCancelled?: boolean;
  isStreaming?: boolean;
  streamingGuidance?: ChatGuidance;
  interruptionReason?: string;
  emotion?: string; // 加入的情緒標籤
  emotionColor?: string; // 情緒對應的顏色
  suggestedReplies?: string[];
  actionButtons?: ActionButton[];
  interactionMode?: "answer" | "clarify";
  clarifyingQuestions?: string[];
  debugToolCalls?: DebugToolCall[];
  imageUrl?: string; // 圖片預覽網址 (僅 frontend 顯示用)
  replyContext?: ReplyContext;
  clarification?: Clarification | null;
  factsRevision?: number;
  contractVersion?: 2 | 3 | 4;
  superseded?: boolean;
  answerSections?: ChatResponse["answer_sections"];
  processingTrace?: ProcessingTrace;
  analysis?: AnalysisEntry[];
  /** Provider reasoning is ephemeral: never saved, exported or sent as history. */
  reasoning?: ReasoningEntry[];
  clarificationDraft?: ClarificationDraft;
  requestKind?: "request" | "clarification" | "regeneration";
  originRequestId?: string;
}
