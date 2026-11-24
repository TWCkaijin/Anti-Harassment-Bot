/**
 * The only chat contract the backend accepts. Kept dependency-free so modules that
 * must stay self-contained (clientPrivacy.ts, loaded by the reliability CLI's Node
 * bridge) can import it.
 */
export const CHAT_CONTRACT_VERSION = 4;
