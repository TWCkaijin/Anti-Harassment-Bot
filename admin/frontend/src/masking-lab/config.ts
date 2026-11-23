export const TINY_MODEL = "ckiplab/albert-tiny-chinese-ner";
export const TINY_REVISION = "bcb519856ca93a666b1e48a9daef3f88c9b572a0";
export const TINY_ASSET_PATH = `masking-tiny/${TINY_REVISION}/`;
export const RULES_VERSION = "zh-tw-pii-rules-v1";
export const ORT_VERSION = "1.31.1";
export const DETECTION_TIMEOUT_MS = 30_000;
export const ENTITY_TYPES = ["person", "organization", "address", "contact", "identifier"] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];
export interface Entity { text: string; type: EntityType }
