import type { ChatRequest, MessageItem } from "../services/api";

export const MAX_HISTORY_MESSAGES = 40;
export const MAX_USER_MESSAGE_CHARACTERS = 2_000;
export const MAX_USER_HISTORY_CHARACTERS = MAX_USER_MESSAGE_CHARACTERS;
export const MAX_ASSISTANT_HISTORY_CHARACTERS = 6_000;
export const MAX_HISTORY_TOTAL_CHARACTERS = 12_000;
export const USER_MESSAGE_TOO_LONG_ERROR = `訊息不可超過 ${MAX_USER_MESSAGE_CHARACTERS.toLocaleString("en-US")} 個字元`;

interface HistoryCandidate {
  role: MessageItem["role"];
  content: string;
  isError?: boolean;
  isCancelled?: boolean;
  imageUrl?: string;
}

interface HistoryTurn {
  messages: MessageItem[];
  characterCount: number;
}

export function getUserMessageValidationError(userInput: string): string | null {
  return userInput.trim().length > MAX_USER_MESSAGE_CHARACTERS
    ? USER_MESSAGE_TOO_LONG_ERROR
    : null;
}

function buildSerializableTurns(messages: readonly HistoryCandidate[]): HistoryTurn[] {
  const turns: HistoryTurn[] = [];
  let currentUser: HistoryCandidate | null = null;
  let assistantCandidates: HistoryCandidate[] = [];

  const finishTurn = () => {
    if (!currentUser) return;

    const turnMessages: MessageItem[] = [];
    const userContent = currentUser.content.trim();
    const userIsSerializable =
      !currentUser.isError &&
      Boolean(userContent) &&
      userContent.length <= MAX_USER_HISTORY_CHARACTERS;

    if (userIsSerializable) {
      turnMessages.push({ role: "user", content: userContent });

      // A cancelled request still contributes the person's meaningful text, but
      // cancellation/error/blank assistant bubbles are never model context.
      const assistant = currentUser.isCancelled
        ? undefined
        : assistantCandidates.find((candidate) => {
            const content = candidate.content.trim();
            return !candidate.isError &&
              !candidate.isCancelled &&
              Boolean(content) &&
              content.length <= MAX_ASSISTANT_HISTORY_CHARACTERS;
          });
      if (assistant) {
        turnMessages.push({ role: "assistant", content: assistant.content.trim() });
      }
    }

    turns.push({
      messages: turnMessages,
      characterCount: turnMessages.reduce(
        (total, message) => total + message.content.length,
        0,
      ),
    });
    currentUser = null;
    assistantCandidates = [];
  };

  for (const message of messages) {
    if (message.role === "user") {
      finishTurn();
      currentUser = message;
      continue;
    }
    if (currentUser) assistantCandidates.push(message);
  }
  finishTurn();

  return turns;
}

/**
 * Build API-safe history as whole user turns. A turn with no serializable user
 * (for example an image-only turn) is dropped together with its assistant so an
 * orphan assistant can never enter model context.
 */
export function buildChatHistory(
  messages: readonly HistoryCandidate[],
  totalCharacterBudget = MAX_HISTORY_TOTAL_CHARACTERS,
): MessageItem[] {
  if (totalCharacterBudget <= 0) return [];

  const turns = buildSerializableTurns(messages);
  const selectedTurns: MessageItem[][] = [];
  let remainingCharacters = totalCharacterBudget;
  let remainingMessages = MAX_HISTORY_MESSAGES;

  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn.messages.length === 0) continue;

    // Select a genuinely contiguous suffix of whole serializable turns. Never
    // backfill an older small turn after a newer turn exceeds either budget.
    if (
      turn.characterCount > remainingCharacters ||
      turn.messages.length > remainingMessages
    ) {
      break;
    }
    selectedTurns.push(turn.messages);
    remainingCharacters -= turn.characterCount;
    remainingMessages -= turn.messages.length;
  }

  return selectedTurns.reverse().flat();
}

export function createChatRequest(
  messages: readonly HistoryCandidate[],
  userInput: string,
  imageBase64?: string,
): ChatRequest {
  const validationError = getUserMessageValidationError(userInput);
  if (validationError) throw new RangeError(validationError);

  return {
    message: userInput.trim(),
    history: buildChatHistory(messages),
    use_rag: true,
    image_base64: imageBase64,
  };
}
